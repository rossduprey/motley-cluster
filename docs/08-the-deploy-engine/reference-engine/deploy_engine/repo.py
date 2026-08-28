# SPDX-License-Identifier: Apache-2.0
"""Git. The only thing this engine talks to.

There is no Kubernetes client in this package, and there is no import of one
anywhere. That is the design decision README.md section 2 is built on, and
section 6 explains what it cost us to learn: the engine held cluster
credentials for exactly one convenience — reading a certificate out of a Secret
in another namespace — and that one convenience is what put a placeholder where
every service's CA certificate should have been.

So: the certificate comes from the repository, like everything else.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from .errors import InputError, RepoError

#: How long a fetched copy of the catalog and templates is trusted before the
#: next read pulls again. Short, because a template change should ship on push;
#: non-zero, because a deploy should not be a network round-trip per file.
#:
#: The consequence is documented rather than fixed: for up to this long after a
#: push, a deploy uses the previous template.
DEFAULT_TTL_SECONDS = 60


def git(*args: str, cwd: Path | None = None) -> str:
    """Run one git command. Raise with the actual output on failure."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RepoError(
            f"git {' '.join(args)} failed ({proc.returncode}):\n"
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


class GitRepo:
    """A working copy of one repository, kept current on demand."""

    def __init__(
        self,
        url: str,
        path: Path,
        branch: str = "main",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.url = url
        self.path = Path(path)
        self.branch = branch
        self.ttl_seconds = ttl_seconds
        self._last_sync = 0.0

    # -- keeping the copy current -----------------------------------------

    def _has_remote_branch(self) -> bool:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"origin/{self.branch}"],
            cwd=str(self.path),
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0

    def sync(self, force: bool = False) -> None:
        """Clone if absent, otherwise fetch and hard-reset onto the branch.

        Hard reset rather than pull: this working copy is a cache, never a place
        anyone edits. Merge conflicts in a cache are a category error.

        The branch is allowed not to exist yet. A GitOps repository is usually
        created empty and the engine's first deploy is the first thing in it —
        an engine that only works against a repository somebody already
        populated is an engine you cannot start with.
        """
        if not (self.path / ".git").exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                shutil.rmtree(self.path)
            git("clone", self.url, str(self.path))
            if self._has_remote_branch():
                git("checkout", self.branch, cwd=self.path)
            else:
                git("checkout", "-B", self.branch, cwd=self.path)
            self._last_sync = time.monotonic()
            return

        if not force and (time.monotonic() - self._last_sync) < self.ttl_seconds:
            return

        git("fetch", "origin", cwd=self.path)
        if self._has_remote_branch():
            git("reset", "--hard", f"origin/{self.branch}", cwd=self.path)
        self._last_sync = time.monotonic()

    # -- reading -----------------------------------------------------------

    def read(self, relpath: str) -> str:
        self.sync()
        target = self.path / relpath
        if not target.is_file():
            raise InputError(f"{self.url}: no file at {relpath}")
        return target.read_text(encoding="utf-8")

    def exists(self, relpath: str) -> bool:
        self.sync()
        return (self.path / relpath).exists()

    def head(self) -> str:
        return git("rev-parse", "HEAD", cwd=self.path).strip()

    # -- writing -----------------------------------------------------------

    def write(self, relpath: str, text: str) -> None:
        target = self.path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def remove(self, relpath: str) -> None:
        target = self.path / relpath
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    def commit_and_push(self, message: str, author: str) -> str | None:
        """Commit whatever changed and push. Returns the sha, or None if clean.

        Returning None on an empty tree is what makes every operation in this
        engine idempotent: re-running a deploy that already landed is a no-op
        rather than an error or an empty commit.
        """
        git("add", "-A", cwd=self.path)
        status = git("status", "--porcelain", cwd=self.path).strip()
        if not status:
            return None
        name, _, email = author.partition(" <")
        git(
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email.rstrip('>')}",
            "commit",
            "-m",
            message,
            cwd=self.path,
        )
        git("push", "origin", f"HEAD:{self.branch}", cwd=self.path)
        return self.head()


def read_ca_certificate(repo: GitRepo, relpath: str) -> str:
    """Read the internal CA certificate, or raise.

    This function exists to raise. It has no fallback, no default, and no empty
    string return, because the version that had one wrote the literal words
    "CA cert not available" into every service this engine ever deployed, and
    nothing failed for weeks.
    """
    text = repo.read(relpath)  # raises InputError if missing
    if not text.lstrip().startswith("-----BEGIN CERTIFICATE-----"):
        raise InputError(
            f"{relpath} does not begin with '-----BEGIN CERTIFICATE-----'. "
            "Distributing a placeholder instead of a certificate is worse than "
            "doing nothing, because everything then looks configured."
        )
    return text
