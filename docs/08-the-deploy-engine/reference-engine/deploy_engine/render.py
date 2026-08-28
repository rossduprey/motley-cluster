# SPDX-License-Identifier: Apache-2.0
"""Turn a template plus a catalog entry into a list of Kubernetes objects.

Two design decisions here are load-bearing, and both come out of README.md
section 6:

1. **Substitution happens after parsing, never on the raw text.** The template
   is parsed as YAML first, and tokens are replaced in the resulting *values*.
   Comments do not survive parsing, so the failure where a multi-line
   certificate was substituted into a comment and broke out of it is not a bug
   we fixed — it is a bug this design cannot express.

2. **Overrides replace; they never append** (except `initContainers`, where
   ordering makes appending the correct behaviour). The one time we merged by
   appending, we produced manifests with duplicate keys that the API server
   refused, and no pod was ever created.
"""

from __future__ import annotations

import copy
import re
from typing import Any

import yaml

from .catalog import Entry
from .errors import TemplateError

#: The complete token vocabulary. A template may use these and nothing else.
TOKENS = (
    "APP_NAME",
    "APP_NAMESPACE",
    "APP_TITLE",
    "APP_DOMAIN",
    "APP_IMAGE",
    "APP_PORT",
    "WAVE_NUMBER",
    "CA_CERT",
)

_TOKEN_RE = re.compile("|".join(sorted(TOKENS, key=len, reverse=True)))

WORKLOAD_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet", "CronJob"})

#: Descriptions are truncated because they end up in labels and record files,
#: and a paragraph in a label is a paragraph in every dashboard forever.
TITLE_MAX = 40


def build_context(entry: Entry, cluster_domain: str, ca_cert: str) -> dict[str, Any]:
    """The values every token resolves to. Nothing here is optional."""
    return {
        "APP_NAME": entry.name,
        "APP_NAMESPACE": entry.name,
        "APP_TITLE": entry.description[:TITLE_MAX],
        "APP_DOMAIN": f"{entry.name}.{cluster_domain}",
        "APP_IMAGE": entry.image,
        "APP_PORT": entry.port,
        "WAVE_NUMBER": entry.wave,
        "CA_CERT": ca_cert,
    }


def substitute(node: Any, context: dict[str, Any]) -> Any:
    """Replace tokens throughout a parsed structure, preserving types.

    A string that is *exactly* one token becomes that token's real value, with
    its real type — so `port: APP_PORT` yields an integer, not the string "8080"
    that a text-substituting engine would produce and that the API server would
    then reject.
    """
    if isinstance(node, dict):
        return {
            substitute(key, context): substitute(value, context)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [substitute(item, context) for item in node]
    if isinstance(node, str):
        if node in context:
            return context[node]
        return _TOKEN_RE.sub(lambda m: str(context[m.group(0)]), node)
    return node


def _find_unresolved(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _find_unresolved(key, found)
            _find_unresolved(value, found)
    elif isinstance(node, list):
        for item in node:
            _find_unresolved(item, found)
    elif isinstance(node, str):
        found.update(_TOKEN_RE.findall(node))


def parse_template(text: str, template_name: str) -> list[dict[str, Any]]:
    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as exc:
        raise TemplateError(f"template {template_name!r} is not valid YAML: {exc}") from exc
    if not docs:
        raise TemplateError(f"template {template_name!r} contains no objects")
    for doc in docs:
        if not isinstance(doc, dict):
            raise TemplateError(
                f"template {template_name!r} contains a document that is not a mapping"
            )
    return docs


# --------------------------------------------------------------------------
# Overrides
# --------------------------------------------------------------------------


def _pod_spec(workload: dict[str, Any]) -> dict[str, Any]:
    """The pod spec, wherever this kind of workload happens to keep it."""
    spec = workload.get("spec", {})
    if workload.get("kind") == "CronJob":
        spec = spec.get("jobTemplate", {}).get("spec", {})
    return spec.get("template", {}).get("spec", {})


def _main_workload(docs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") in WORKLOAD_KINDS and doc.get("metadata", {}).get("name") == name:
            return doc
    raise TemplateError(
        f"template has no workload named {name!r}. Overrides apply to the main "
        "workload only — the one whose name is the service name."
    )


def _main_container(docs: list[dict[str, Any]], name: str) -> dict[str, Any]:
    pod = _pod_spec(_main_workload(docs, name))
    for container in pod.get("containers", []):
        if container.get("name") == name:
            return container
    raise TemplateError(
        f"the workload {name!r} has no container named {name!r}; overrides would "
        "have nowhere to land"
    )


def _replace_by_name(existing: list[Any], incoming: list[Any]) -> list[Any]:
    """Merge two lists of named objects, with `incoming` winning outright.

    Order is stable: template entries keep their positions, additions go last.
    Nothing is ever duplicated — which is the entire lesson of this function.
    """
    merged = list(existing)
    index = {
        item.get("name"): position
        for position, item in enumerate(merged)
        if isinstance(item, dict) and "name" in item
    }
    for item in incoming:
        key = item.get("name") if isinstance(item, dict) else None
        if key is not None and key in index:
            merged[index[key]] = item
        else:
            if key is not None:
                index[key] = len(merged)
            merged.append(item)
    return merged


def apply_overrides(
    docs: list[dict[str, Any]], entry: Entry, node_class_label: str
) -> list[dict[str, Any]]:
    """Apply catalog overrides to the MAIN workload only.

    A template that ships a database sidecar owns that sidecar's spec entirely.
    This is not guessable, which is why README section 4 states it and why this
    function does exactly one workload's worth of work.
    """
    docs = copy.deepcopy(docs)
    over = entry.overrides
    if not over:
        return docs

    container = _main_container(docs, entry.name)
    pod = _pod_spec(_main_workload(docs, entry.name))

    if "resourceOverrides" in over:
        container["resources"] = over["resourceOverrides"]

    if "env" in over:
        container["env"] = _replace_by_name(container.get("env", []), over["env"])

    if "secretEnv" in over:
        secret_entries = [
            {
                "name": var,
                "valueFrom": {
                    "secretKeyRef": {"name": f"{entry.name}-secret", "key": key}
                },
            }
            for var, key in over["secretEnv"].items()
        ]
        container["env"] = _replace_by_name(container.get("env", []), secret_entries)

    if "volumeMounts" in over:
        container["volumeMounts"] = _replace_by_name(
            container.get("volumeMounts", []), over["volumeMounts"]
        )

    if "dataMount" in over:
        for mount in container.get("volumeMounts", []):
            if mount.get("name") == "data":
                mount["mountPath"] = over["dataMount"]

    if "volumes" in over:
        pod["volumes"] = _replace_by_name(pod.get("volumes", []), over["volumes"])

    if "securityContext" in over:
        pod["securityContext"] = over["securityContext"]

    if "nodeSelector" in over:
        pod["nodeSelector"] = over["nodeSelector"]

    if "nodeClass" in over and over["nodeClass"] != "any":
        # A CLASS, not a hostname. Pinning a service to a named machine turns a
        # scheduling problem into a hard stop — see docs/03-storage section 8.
        selector = dict(pod.get("nodeSelector", {}))
        selector[node_class_label] = str(over["nodeClass"])
        pod["nodeSelector"] = selector

    if "initContainers" in over:
        # The one place appending is right: a template's own init step still has
        # to run before a service's extra one.
        pod["initContainers"] = list(pod.get("initContainers", [])) + list(
            over["initContainers"]
        )

    if "storageSize" in over:
        for doc in docs:
            if (
                doc.get("kind") == "PersistentVolumeClaim"
                and doc.get("metadata", {}).get("name") == f"{entry.name}-data"
            ):
                doc["spec"]["resources"]["requests"]["storage"] = over["storageSize"]

    for cm_name, data in over.get("configMap", {}).items():
        docs.append(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": cm_name, "namespace": entry.name},
                "data": data,
            }
        )

    return docs


def render(
    template_text: str,
    entry: Entry,
    cluster_domain: str,
    ca_cert: str,
    node_class_label: str,
) -> list[dict[str, Any]]:
    """Template + entry -> objects. Nothing here touches git or the cluster."""
    docs = parse_template(template_text, entry.template)
    context = build_context(entry, cluster_domain, ca_cert)
    docs = substitute(docs, context)

    unresolved: set[str] = set()
    _find_unresolved(docs, unresolved)
    if unresolved:
        # Only reachable if a token has no context value, which would mean this
        # module and build_context() disagree. Loudly, then, rather than shipping
        # the literal word APP_NAME into someone's cluster.
        raise TemplateError(
            f"template {entry.template!r} left tokens unresolved: "
            f"{', '.join(sorted(unresolved))}"
        )

    return apply_overrides(docs, entry, node_class_label)
