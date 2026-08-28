# SPDX-License-Identifier: Apache-2.0
"""Test fixtures: two real git repositories on disk, no network, no cluster."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deploy_engine.engine import Engine, Settings  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

CA_PEM = """\
-----BEGIN CERTIFICATE-----
dGhpcyBpcyBub3QgYSByZWFsIGNlcnRpZmljYXRlLCBpdCBpcyBhIHRlc3QgZml4
dHVyZSB3aXRoIGVub3VnaCBiYXNlNjQtc2hhcGVkIHRleHQgdG8gbG9vayBsaWtl
IG9uZSB3aGlsZSBiZWluZyBjb21wbGV0ZWx5IGluZXJ0Lg==
-----END CERTIFICATE-----
"""

CATALOG = """\
services:
  - name: notepad
    description: "Shared scratch notes"
    image: registry.example.com/example/notepad:2.4.1
    port: 8080
    template: stateful

  - name: photos
    description: "Photo library"
    image: registry.example.com/example/photos:1.12.0
    port: 3000
    template: stateful
    storageSize: "50Gi"
    dataMount: /library
    nodeClass: heavy
    env:
      - name: THUMBNAIL_WORKERS
        value: "2"
    initContainers:
      - name: fix-permissions
        image: registry.example.com/example/busybox:1.36
        command: ["sh", "-c", "chown -R 1000:1000 /library"]
"""


def git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


class EngineTestCase(unittest.TestCase):
    """A source repo, a gitops repo, and an engine wired to both."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="deploy-engine-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.source_origin = self._bare("source")
        self.gitops_origin = self._bare("gitops")

        seed = self.tmp / "seed-source"
        git("clone", str(self.source_origin), str(seed), cwd=self.tmp)
        (seed / "catalog.yaml").write_text(CATALOG, encoding="utf-8")
        (seed / "templates").mkdir()
        shutil.copy(
            REPO_ROOT / "templates" / "stateful.yaml", seed / "templates" / "stateful.yaml"
        )
        (seed / "ca").mkdir()
        (seed / "ca" / "internal-ca.crt").write_text(CA_PEM, encoding="utf-8")
        git("add", "-A", cwd=seed)
        git("commit", "-m", "seed", cwd=seed)
        git("push", "origin", "HEAD:main", cwd=seed)

        self.engine = Engine(
            Settings(
                source_url=str(self.source_origin),
                gitops_url=str(self.gitops_origin),
                workdir=self.tmp / "work",
                cluster_domain="home.arpa",
                fallback_templates=REPO_ROOT / "templates",
                author="engine <engine@example.com>",
            )
        )

    def _bare(self, name: str) -> Path:
        path = self.tmp / f"{name}.git"
        subprocess.run(
            ["git", "init", "--bare", "--initial-branch=main", str(path)],
            check=True,
            capture_output=True,
        )
        return path

    # -- helpers -----------------------------------------------------------

    def gitops_file(self, relpath: str) -> str:
        self.engine.gitops.sync(force=True)
        return (self.engine.gitops.path / relpath).read_text(encoding="utf-8")

    def gitops_has(self, relpath: str) -> bool:
        self.engine.gitops.sync(force=True)
        return (self.engine.gitops.path / relpath).exists()

    def commit_subjects(self) -> list[str]:
        """Newest first — used to prove manifests are committed before records."""
        self.engine.gitops.sync(force=True)
        out = subprocess.run(
            ["git", "log", "--format=%s"],
            cwd=str(self.engine.gitops.path),
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip().splitlines()
