# SPDX-License-Identifier: Apache-2.0
"""Command line. Deliberately not an HTTP API.

Ours was a web service, and a web service is the right eventual shape — but it
is also the shape that invites a background thread, a job id, and a status
field that lies after a restart. Start with a command that either finishes or
fails, and add the API once you know what it should say.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import Engine, Settings
from .errors import EngineError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deploy-engine",
        description="Render a catalog entry into a GitOps repository.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="engine settings file (see engine.yaml.example)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("deploy", "render and commit a service"),
        ("suspend", "scale a service to zero, keeping its volume"),
        ("resume", "scale a suspended service back up"),
        ("remove", "delete a service's manifests and record"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("service")

    sub.add_parser("status", help="what the engine has committed")
    sub.add_parser("reconcile", help="make every record agree with the manifests")
    sub.add_parser("list", help="what the catalog offers")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        engine = Engine(Settings.from_file(args.config))
        if args.command == "list":
            result = {
                name: entry.description for name, entry in sorted(engine.catalog().items())
            }
        elif args.command == "status":
            result = engine.status()
        elif args.command == "reconcile":
            from . import records

            result = {"corrections": records.reconcile(engine.gitops)}
        else:
            result = getattr(engine, args.command)(args.service)
    except EngineError as exc:
        # Every failure in this package is one of these, and every one of them
        # means nothing was committed. Print it and stop; do not carry on with a
        # default value, which is the mistake this whole engine is shaped around.
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
