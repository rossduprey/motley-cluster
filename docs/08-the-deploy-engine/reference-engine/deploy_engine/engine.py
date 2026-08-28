# SPDX-License-Identifier: Apache-2.0
"""The engine: resolve, render, validate, commit. In that order, and no further.

It writes files to a git repository and stops. The GitOps controller from
docs/05-gitops is what changes the cluster. That boundary is the most important
decision in this package — an engine that applied manifests directly would be a
second source of truth, and docs/05 §5 is an entire section about what that
costs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import catalog, records, render, validate
from .errors import EngineError, TemplateError
from .repo import GitRepo, read_ca_certificate

GENERATED_HEADER = """\
# GENERATED FILE — DO NOT EDIT.
#
# Written by the deploy engine from:
#   catalog entry : {name}
#   template      : {template}
#   engine source : {engine}
#
# An edit here works, and is silently reverted the next time this service is
# deployed. To change this service, change its catalog entry or its template.
"""

ENGINE_SOURCE = "docs/08-the-deploy-engine/reference-engine"


@dataclass
class Settings:
    """Everything the engine needs to know, and nothing about any cluster."""

    source_url: str  # repo holding the catalog, the templates and the CA cert
    gitops_url: str  # repo the GitOps controller watches
    workdir: Path
    cluster_domain: str
    catalog_path: str = "catalog.yaml"
    templates_dir: str = "templates"
    ca_cert_path: str = "ca/internal-ca.crt"
    gitops_repo_url_for_argocd: str = ""
    node_class_label: str = "node-class"
    author: str = "deploy-engine <deploy-engine@localhost>"
    branch: str = "main"
    fallback_templates: Path | None = None  # baked-in copies, for a git outage

    @classmethod
    def from_file(cls, path: Path) -> "Settings":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        raw["workdir"] = Path(raw["workdir"])
        if raw.get("fallback_templates"):
            raw["fallback_templates"] = Path(raw["fallback_templates"])
        return cls(**raw)


class Engine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.source = GitRepo(
            settings.source_url, settings.workdir / "source", settings.branch
        )
        self.gitops = GitRepo(
            settings.gitops_url, settings.workdir / "gitops", settings.branch
        )

    # -- inputs ------------------------------------------------------------

    def catalog(self) -> dict[str, catalog.Entry]:
        """Read live from git, with the repo's own short cache in front of it."""
        return catalog.parse_catalog(self.source.read(self.settings.catalog_path))

    def template(self, name: str) -> str:
        """Templates come from git; the baked-in copies are an offline fallback.

        Reading them live is what decouples a template edit from an engine
        rebuild: a template ships on push, and the engine's image only needs
        rebuilding when the engine's code changes. Keeping the baked copies is
        what stops a git outage from meaning no deploys at all.
        """
        relpath = f"{self.settings.templates_dir}/{name}.yaml"
        try:
            return self.source.read(relpath)
        except EngineError as exc:
            fallback = self.settings.fallback_templates
            if fallback and (fallback / f"{name}.yaml").is_file():
                return (fallback / f"{name}.yaml").read_text(encoding="utf-8")
            raise TemplateError(
                f"template {name!r} is not in the repository at {relpath}, and "
                f"there is no baked-in fallback for it ({exc})"
            ) from exc

    # -- outputs -----------------------------------------------------------

    def _application(self, entry: catalog.Entry) -> dict[str, Any]:
        """The object that tells the GitOps controller to watch this service.

        Shaped for Argo CD; the same three facts — where the manifests are, what
        namespace they go in, sync automatically — exist in every controller.
        """
        return {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {
                "name": entry.name,
                "namespace": "argocd",
                "annotations": {"argocd.argoproj.io/sync-wave": str(entry.wave)},
            },
            "spec": {
                "project": "default",
                "source": {
                    "repoURL": self.settings.gitops_repo_url_for_argocd
                    or self.settings.gitops_url,
                    "targetRevision": "HEAD",
                    "path": f"apps/{entry.name}",
                },
                "destination": {
                    "server": "https://kubernetes.default.svc",
                    "namespace": entry.name,
                },
                "syncPolicy": {
                    "automated": {"prune": True, "selfHeal": True},
                    "syncOptions": ["CreateNamespace=true", "ServerSideApply=true"],
                },
            },
        }

    def _serialise(self, docs: list[dict[str, Any]], entry: catalog.Entry) -> str:
        header = GENERATED_HEADER.format(
            name=entry.name, template=entry.template, engine=ENGINE_SOURCE
        )
        body = yaml.safe_dump_all(docs, sort_keys=False, default_flow_style=False)
        return header + "---\n" + body

    # -- operations --------------------------------------------------------

    def deploy(self, name: str) -> dict[str, Any]:
        """Render a service and commit it. Returns what was actually written.

        The order is deliberate and it is the reason an interrupted run is
        recoverable: manifests first, lifecycle record last. Stopping in the
        middle leaves manifests with no record, which reconciliation adopts.
        The reverse would leave a record claiming a service that does not exist.
        """
        corrections = records.reconcile(self.gitops)

        entry = catalog.lookup(self.catalog(), name)
        template_text = self.template(entry.template)
        ca_cert = read_ca_certificate(self.source, self.settings.ca_cert_path)

        docs = render.render(
            template_text,
            entry,
            self.settings.cluster_domain,
            ca_cert,
            self.settings.node_class_label,
        )

        # Nothing has been written yet. This is the last moment it is free to
        # fail, so it is where every check happens.
        validate.validate(docs, entry)

        self.gitops.write(
            f"apps/{entry.name}/service.yaml", self._serialise(docs, entry)
        )
        self.gitops.write(
            f"apps/{entry.name}.yaml", self._serialise([self._application(entry)], entry)
        )
        manifest_sha = self.gitops.commit_and_push(
            f"deploy: {entry.name} ({entry.template})", self.settings.author
        )

        existing = records.load(self.gitops, entry.name)
        unchanged = (
            manifest_sha is None
            and existing is not None
            and (existing.state, existing.template, existing.image, existing.description)
            == (records.STATE_DEPLOYED, entry.template, entry.image, entry.description)
        )
        if unchanged:
            # A redeploy that changed nothing writes nothing. Bumping a
            # timestamp would make every no-op a commit, and a history full of
            # commits that changed nothing is a history nobody reads.
            record_sha = None
        else:
            records.write(
                self.gitops,
                records.Record(
                    name=entry.name,
                    state=records.STATE_DEPLOYED,
                    template=entry.template,
                    image=entry.image,
                    description=entry.description,
                    committed_sha=manifest_sha or self.gitops.head(),
                    updated=records.now(),
                ),
            )
            record_sha = self.gitops.commit_and_push(
                f"record: {entry.name} deployed", self.settings.author
            )

        return {
            "service": entry.name,
            "manifest_commit": manifest_sha,
            "record_commit": record_sha,
            "reconciled": corrections,
            "cluster_state": (
                "not tracked by this engine — ask the cluster. Committing is not "
                "deploying; the GitOps controller has not necessarily reconciled "
                "yet, and until the root app-of-apps does, this service's "
                "Application object does not exist. That is normal timing."
            ),
        }

    def _set_replicas(self, name: str, replicas: int) -> str | None:
        path = f"apps/{name}/service.yaml"
        if not self.gitops.exists(path):
            raise EngineError(f"{name}: no manifests at {path}")
        text = self.gitops.read(path)
        docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
        for doc in docs:
            if doc.get("kind") in ("Deployment", "StatefulSet"):
                doc.setdefault("spec", {})["replicas"] = replicas
        header = text.split("---\n", 1)[0]
        self.gitops.write(
            path, header + "---\n" + yaml.safe_dump_all(docs, sort_keys=False)
        )
        verb = "suspend" if replicas == 0 else "resume"
        return self.gitops.commit_and_push(f"{verb}: {name}", self.settings.author)

    def suspend(self, name: str) -> dict[str, Any]:
        """Scale to zero, keep the volume. Manifests first, record last."""
        records.reconcile(self.gitops)
        sha = self._set_replicas(name, 0)
        return self._finish_lifecycle(name, records.STATE_SUSPENDED, sha)

    def resume(self, name: str) -> dict[str, Any]:
        records.reconcile(self.gitops)
        sha = self._set_replicas(name, 1)
        return self._finish_lifecycle(name, records.STATE_DEPLOYED, sha)

    def _finish_lifecycle(self, name: str, state: str, sha: str | None) -> dict[str, Any]:
        record = records.load(self.gitops, name)
        if record is None:
            raise EngineError(f"{name}: no lifecycle record; run reconcile first")
        record.state = state
        record.committed_sha = sha or self.gitops.head()
        record.updated = records.now()
        records.write(self.gitops, record)
        record_sha = self.gitops.commit_and_push(
            f"record: {name} {state}", self.settings.author
        )
        return {
            "service": name,
            "state": state,
            "manifest_commit": sha,
            "record_commit": record_sha,
        }

    def remove(self, name: str) -> dict[str, Any]:
        """Delete the manifests and the record. The controller prunes the rest.

        Prune is on (docs/05-gitops §2), so removing the files is what removes
        the objects. If prune is off, this quietly orphans a namespace — which
        is one of several reasons §2 argues for turning it on from day one.
        """
        records.reconcile(self.gitops)
        self.gitops.remove(f"apps/{name}")
        self.gitops.remove(f"apps/{name}.yaml")
        manifest_sha = self.gitops.commit_and_push(
            f"remove: {name}", self.settings.author
        )
        self.gitops.remove(records.record_path(name))
        record_sha = self.gitops.commit_and_push(
            f"record: {name} removed", self.settings.author
        )
        return {
            "service": name,
            "manifest_commit": manifest_sha,
            "record_commit": record_sha,
        }

    def status(self) -> dict[str, Any]:
        corrections = records.reconcile(self.gitops)
        return {
            "services": {
                name: record.state for name, record in records.load_all(self.gitops).items()
            },
            "reconciled": corrections,
            "advisory": (
                "This is what the engine committed, not what the cluster is "
                "running. For that, ask the cluster."
            ),
        }
