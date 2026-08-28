# SPDX-License-Identifier: Apache-2.0
"""Check the rendered manifest BEFORE it is committed.

This module is cheap and it is the difference between "the deploy failed with
a readable error" and "the deploy succeeded, no pod was ever created, and we
spent three days watching a graph that was never going to move."

None of these checks need a cluster. That is deliberate: a validation step that
requires cluster credentials is a validation step that gets skipped.
"""

from __future__ import annotations

from typing import Any

from .catalog import Entry
from .errors import ValidationError

#: Objects every service gets, whether or not anyone remembered them.
#: docs/06-deploying-services section 1 is the argument for this list.
REQUIRED_KINDS = ("Namespace", "Service")

#: Substrings that mean a fetch returned something useless and a fallback got
#: written where a real value belonged. Ours said "CA cert not available" and
#: sat in every service for weeks.
POISON_MARKERS = ("not available", "PLACEHOLDER", "changeme", "TODO")


def _walk(node: Any, path: str = "$"):
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk(item, f"{path}[{index}]")


def _check_shape(docs: list[dict[str, Any]]) -> list[str]:
    problems = []
    for index, doc in enumerate(docs):
        where = f"document {index}"
        for field in ("apiVersion", "kind"):
            if not doc.get(field):
                problems.append(f"{where}: missing {field}")
        name = doc.get("metadata", {}).get("name")
        if not name:
            problems.append(f"{where} ({doc.get('kind', '?')}): missing metadata.name")
    return problems


def _check_namespaces(docs: list[dict[str, Any]], name: str) -> list[str]:
    """Everything belongs to this service's namespace, and nothing else's.

    The engine writes into one namespace by construction. A template that names
    another one is either a mistake or an attempt to reach outside the rails,
    and both should stop here.
    """
    problems = []
    for doc in docs:
        if doc.get("kind") == "Namespace":
            if doc["metadata"]["name"] != name:
                problems.append(
                    f"Namespace object is named {doc['metadata']['name']!r}, "
                    f"expected {name!r}"
                )
            continue
        ns = doc.get("metadata", {}).get("namespace")
        if ns is None:
            problems.append(
                f"{doc.get('kind')} {doc.get('metadata', {}).get('name')!r}: "
                "no namespace set"
            )
        elif ns != name:
            problems.append(
                f"{doc.get('kind')} {doc.get('metadata', {}).get('name')!r}: "
                f"namespace {ns!r} is not this service's namespace"
            )
    return problems


def _check_no_duplicate_env(docs: list[dict[str, Any]]) -> list[str]:
    """The failure that produced a manifest the API server would never accept.

    An override that appended instead of replacing left two entries with the
    same name. Kubernetes rejects that, no pod is created, and from outside it
    looks exactly like an image that will not pull.
    """
    problems = []
    for path, node in _walk(docs):
        if not (isinstance(node, list) and path.endswith(".env")):
            continue
        seen = set()
        for item in node:
            if not isinstance(item, dict):
                continue
            key = item.get("name")
            if key in seen:
                problems.append(f"{path}: duplicate environment variable {key!r}")
            seen.add(key)
    return problems


def _check_resources(docs: list[dict[str, Any]]) -> list[str]:
    """Every container asks for something.

    The scheduler bin-packs on requests. On a cluster of unequal machines,
    a container with no request is distributed by luck.
    """
    problems = []
    for path, node in _walk(docs):
        if not (isinstance(node, list) and path.endswith(".containers")):
            continue
        for item in node:
            if not isinstance(item, dict):
                continue
            requests = item.get("resources", {}).get("requests", {})
            if not requests.get("cpu") or not requests.get("memory"):
                problems.append(
                    f"container {item.get('name')!r}: no cpu/memory request. "
                    "A LimitRange default counts, but say so in the template."
                )
    return problems


def _check_no_poison(docs: list[dict[str, Any]]) -> list[str]:
    """Catch a fallback string that reached the manifest.

    Belt and braces: the fetch functions already raise rather than returning a
    default. This is here because that is exactly what we believed last time.
    """
    problems = []
    for path, node in _walk(docs):
        if not isinstance(node, str):
            continue
        for marker in POISON_MARKERS:
            if marker.lower() in node.lower():
                problems.append(
                    f"{path}: contains {marker!r} — this looks like a fallback "
                    "value that was written in place of a real one"
                )
    return problems


def _check_ca_bundle(docs: list[dict[str, Any]]) -> list[str]:
    """If a certificate is being distributed, it must actually be one."""
    problems = []
    for path, node in _walk(docs):
        if not isinstance(node, str) or not path.endswith(".crt"):
            continue
        if not node.lstrip().startswith("-----BEGIN CERTIFICATE-----"):
            problems.append(
                f"{path}: does not begin with a PEM header. Distributing a "
                "placeholder instead of a certificate is worse than doing "
                "nothing, because everything then looks configured."
            )
    return problems


def validate(docs: list[dict[str, Any]], entry: Entry) -> None:
    """Raise ValidationError listing everything wrong, not just the first thing.

    Reporting one problem per run turns a bad template into an afternoon of
    fix-rerun-fix-rerun.
    """
    problems: list[str] = []
    problems += _check_shape(docs)
    if problems:
        # The later checks assume kind/name/metadata exist.
        raise ValidationError(_format(entry.name, problems))

    kinds = {doc.get("kind") for doc in docs}
    for kind in REQUIRED_KINDS:
        if kind not in kinds:
            problems.append(f"no {kind} object — every service gets one")

    problems += _check_namespaces(docs, entry.name)
    problems += _check_no_duplicate_env(docs)
    problems += _check_resources(docs)
    problems += _check_no_poison(docs)
    problems += _check_ca_bundle(docs)

    if problems:
        raise ValidationError(_format(entry.name, problems))


def _format(name: str, problems: list[str]) -> str:
    lines = [f"{name}: rendered manifest is not deployable ({len(problems)} problem(s))"]
    lines += [f"  - {p}" for p in problems]
    lines.append("Nothing was committed.")
    return "\n".join(lines)
