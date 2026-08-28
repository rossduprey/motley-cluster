# SPDX-License-Identifier: Apache-2.0
"""Lifecycle records: which services exist, and what state they were left in.

Three decisions worth stating, all of them reactions to README.md section 6:

1. **The records live in the git repository**, beside the manifests they
   describe. Ours lived on a volume inside the engine's own pod, which made the
   list of deployed services a piece of state with no history and no backup.

2. **There is no background thread and no in-flight state.** Our engine polled
   the GitOps controller in a daemon thread and advanced a job record through
   states; when its pod restarted, every thread died and every record froze at
   whatever it last said. Records here hold only what is durably true: the
   engine committed this, at this sha, at this time. *Whether the cluster has
   caught up is a question for the cluster* — see docs/07-observability §6.

3. **Reconciliation derives state from the manifests, not from a status field.**
   An interrupted suspend once left the record saying "suspended" while the
   cluster happily served the service, and neither the suspend nor the resume
   command would touch it again because each expected the other's state.
   `reconcile()` reads what is actually committed and corrects the record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import yaml

from .repo import GitRepo

RECORDS_DIR = "records"

STATE_DEPLOYED = "deployed"
STATE_SUSPENDED = "suspended"

HEADER = """\
# Lifecycle record, written by the deploy engine. Do not edit by hand.
#
# This file records what the ENGINE did: the catalog entry it deployed, the
# template it used, and the commit it wrote. It deliberately says nothing about
# whether the service is currently healthy — the cluster is authoritative for
# that, and a cached opinion about the cluster is how you end up debugging the
# display instead of the system.
"""


@dataclass
class Record:
    name: str
    state: str
    template: str
    image: str
    description: str
    committed_sha: str
    updated: str

    def to_yaml(self) -> str:
        return HEADER + yaml.safe_dump(asdict(self), sort_keys=True)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def record_path(name: str) -> str:
    return f"{RECORDS_DIR}/{name}.yaml"


def load(repo: GitRepo, name: str) -> Record | None:
    path = record_path(name)
    if not repo.exists(path):
        return None
    raw: dict[str, Any] = yaml.safe_load(repo.read(path)) or {}
    fields = {k: raw.get(k, "") for k in Record.__dataclass_fields__}
    fields["name"] = name
    return Record(**fields)


def load_all(repo: GitRepo) -> dict[str, Record]:
    repo.sync()
    directory = repo.path / RECORDS_DIR
    if not directory.is_dir():
        return {}
    records = {}
    for path in sorted(directory.glob("*.yaml")):
        record = load(repo, path.stem)
        if record is not None:
            records[record.name] = record
    return records


def write(repo: GitRepo, record: Record) -> None:
    """Stage the record. The caller commits it — LAST, after the manifests.

    That ordering is the whole reason an interrupted run is recoverable: it
    leaves manifests with no record, which reconcile() adopts, rather than a
    record claiming something that was never committed.
    """
    repo.write(record_path(record.name), record.to_yaml())


# --------------------------------------------------------------------------
# Reconciliation — run this at startup, before doing anything else
# --------------------------------------------------------------------------


def observed_state(repo: GitRepo, name: str) -> str | None:
    """What the committed manifests actually say. None if there are none.

    Suspension here means every workload is scaled to zero, which is a fact
    visible in the manifest — not a flag somewhere else that has to agree.
    """
    path = f"apps/{name}/service.yaml"
    if not repo.exists(path):
        return None
    docs = [d for d in yaml.safe_load_all(repo.read(path)) if isinstance(d, dict)]
    replica_counts = [
        doc.get("spec", {}).get("replicas")
        for doc in docs
        if doc.get("kind") in ("Deployment", "StatefulSet")
    ]
    if replica_counts and all(count == 0 for count in replica_counts):
        return STATE_SUSPENDED
    return STATE_DEPLOYED


def reconcile(repo: GitRepo) -> list[str]:
    """Make every record agree with the manifests. Returns what was corrected.

    Call this on startup and before any lifecycle operation. It is cheap, it is
    idempotent, and it is the difference between a half-finished operation being
    resumable and being wedged.
    """
    repo.sync(force=True)
    corrections: list[str] = []
    records = load_all(repo)

    for name, record in records.items():
        actual = observed_state(repo, name)
        if actual is None:
            repo.remove(record_path(name))
            corrections.append(
                f"{name}: record said {record.state!r} but no manifests exist; "
                "record removed"
            )
        elif actual != record.state:
            claimed = record.state
            record.state = actual
            record.updated = now()
            write(repo, record)
            corrections.append(
                f"{name}: record said {claimed!r}, manifests say {actual!r}; "
                "record corrected to match the manifests"
            )

    apps = repo.path / "apps"
    if apps.is_dir():
        for child in sorted(apps.iterdir()):
            name = child.name
            if not child.is_dir() or name in records:
                continue
            actual = observed_state(repo, name)
            if actual is None:
                continue
            # Manifests with no record: either an interrupted deploy, or a
            # hand-authored service that was never ours. Adopt the record so the
            # service is at least visible; adoption changes no manifest.
            write(
                repo,
                Record(
                    name=name,
                    state=actual,
                    template="unknown",
                    image="unknown",
                    description="adopted during reconciliation",
                    committed_sha=repo.head(),
                    updated=now(),
                ),
            )
            corrections.append(
                f"{name}: manifests exist with no record; adopted as {actual!r}"
            )

    return corrections
