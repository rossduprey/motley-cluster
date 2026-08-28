# SPDX-License-Identifier: Apache-2.0
"""The catalog: one file that is the whole definition of every service.

Schema and field meanings are in ../catalog.yaml.example. This module only
loads it and refuses the entries that would produce a broken deploy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from .errors import CatalogError

# name == namespace == hostname (README section 3), so the name has to be legal
# as all three at once. This is the intersection: a DNS label.
NAME_RE = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
MAX_NAME_LEN = 63

REQUIRED = ("name", "description", "image", "port", "template")

# Overrides that REPLACE what the template said. See README section 4, rule 1.
REPLACING_OVERRIDES = (
    "storageSize",
    "dataMount",
    "resourceOverrides",
    "env",
    "secretEnv",
    "securityContext",
    "nodeSelector",
    "nodeClass",
    "volumes",
    "volumeMounts",
    "configMap",
    "wave",
)

# The one field where appending is correct: init containers run in order and a
# template's own init step should still run before a service's extra one.
APPENDING_OVERRIDES = ("initContainers",)

KNOWN_FIELDS = frozenset(REQUIRED + REPLACING_OVERRIDES + APPENDING_OVERRIDES)


@dataclass(frozen=True)
class Entry:
    """One service, exactly as the catalog describes it."""

    name: str
    description: str
    image: str
    port: int
    template: str
    overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def wave(self) -> int:
        return int(self.overrides.get("wave", 20))


def _entry_from_mapping(raw: Any, index: int) -> Entry:
    where = f"services[{index}]"
    if not isinstance(raw, dict):
        raise CatalogError(f"{where}: expected a mapping, got {type(raw).__name__}")

    missing = [k for k in REQUIRED if k not in raw]
    if missing:
        raise CatalogError(f"{where}: missing required field(s): {', '.join(missing)}")

    name = raw["name"]
    if not isinstance(name, str) or not NAME_RE.match(name) or len(name) > MAX_NAME_LEN:
        raise CatalogError(
            f"{where}: name {name!r} is not usable as a namespace and a hostname. "
            "Lower-case letters, digits and hyphens; must start with a letter."
        )

    unknown = sorted(set(raw) - KNOWN_FIELDS)
    if unknown:
        # Not a warning. A typo'd override field silently does nothing, which is
        # the most expensive kind of quiet in this whole package.
        raise CatalogError(
            f"{where} ({name}): unknown field(s): {', '.join(unknown)}. "
            "If you need something the schema lacks, the answer is usually a "
            "template, not a field."
        )

    port = raw["port"]
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise CatalogError(f"{where} ({name}): port must be an integer 1-65535")

    for key in ("description", "image", "template"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise CatalogError(f"{where} ({name}): {key} must be a non-empty string")

    overrides = {k: v for k, v in raw.items() if k not in REQUIRED}
    return Entry(
        name=name,
        description=raw["description"],
        image=raw["image"],
        port=port,
        template=raw["template"],
        overrides=overrides,
    )


def parse_catalog(text: str) -> dict[str, Entry]:
    """Parse the catalog. Every entry is validated, not just the one wanted.

    Validating the whole file means a broken entry is found by the next deploy
    of any service, rather than lying in wait for the deploy of that one.
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError(f"catalog is not valid YAML: {exc}") from exc

    if not isinstance(doc, dict) or "services" not in doc:
        raise CatalogError("catalog must be a mapping with a top-level 'services' key")

    services = doc["services"]
    if not isinstance(services, list) or not services:
        raise CatalogError("catalog 'services' must be a non-empty list")

    entries: dict[str, Entry] = {}
    for index, raw in enumerate(services):
        entry = _entry_from_mapping(raw, index)
        if entry.name in entries:
            raise CatalogError(
                f"duplicate service name {entry.name!r}. Names are namespaces and "
                "hostnames; two entries would fight over the same cluster objects."
            )
        entries[entry.name] = entry
    return entries


def lookup(entries: dict[str, Entry], name: str) -> Entry:
    try:
        return entries[name]
    except KeyError:
        known = ", ".join(sorted(entries)) or "(none)"
        raise CatalogError(
            f"no catalog entry named {name!r}. Known services: {known}"
        ) from None
