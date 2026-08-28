# SPDX-License-Identifier: Apache-2.0
"""The catalog refuses entries that would produce a broken deploy."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deploy_engine.catalog import lookup, parse_catalog  # noqa: E402
from deploy_engine.errors import CatalogError  # noqa: E402

MINIMAL = """\
services:
  - name: notepad
    description: "Shared scratch notes"
    image: registry.example.com/example/notepad:2.4.1
    port: 8080
    template: stateful
"""


def with_entry(extra: str) -> str:
    return MINIMAL + extra


class TestCatalog(unittest.TestCase):
    def test_minimal_entry_parses(self):
        entries = parse_catalog(MINIMAL)
        entry = lookup(entries, "notepad")
        self.assertEqual(entry.port, 8080)
        self.assertEqual(entry.template, "stateful")
        self.assertEqual(entry.wave, 20, "wave defaults rather than being required")
        self.assertEqual(entry.overrides, {})

    def test_unknown_service_names_the_alternatives(self):
        with self.assertRaises(CatalogError) as caught:
            lookup(parse_catalog(MINIMAL), "photos")
        self.assertIn("notepad", str(caught.exception))

    def test_missing_required_field_is_refused(self):
        broken = MINIMAL.replace("    port: 8080\n", "")
        with self.assertRaises(CatalogError) as caught:
            parse_catalog(broken)
        self.assertIn("port", str(caught.exception))

    def test_name_must_work_as_namespace_and_hostname(self):
        for bad in ("Notepad", "note_pad", "9notes", "note.pad", "notepad-"):
            with self.subTest(name=bad):
                with self.assertRaises(CatalogError):
                    parse_catalog(MINIMAL.replace("notepad", bad))

    def test_unknown_field_is_an_error_not_a_shrug(self):
        # A typo'd override silently does nothing, which is the most expensive
        # kind of quiet in the whole package.
        with self.assertRaises(CatalogError) as caught:
            parse_catalog(with_entry("    storagesize: 10Gi\n"))
        self.assertIn("storagesize", str(caught.exception))

    def test_duplicate_names_are_refused(self):
        with self.assertRaises(CatalogError) as caught:
            parse_catalog(MINIMAL + MINIMAL.split("services:\n", 1)[1])
        self.assertIn("duplicate", str(caught.exception))

    def test_port_must_be_a_port(self):
        for bad in ("0", "70000", '"8080"', "true"):
            with self.subTest(port=bad):
                with self.assertRaises(CatalogError):
                    parse_catalog(MINIMAL.replace("port: 8080", f"port: {bad}"))

    def test_every_entry_is_validated_not_just_the_one_wanted(self):
        # A broken entry is found by the next deploy of ANY service, rather than
        # lying in wait for the deploy of that one.
        two = MINIMAL + """\
  - name: photos
    description: "Photo library"
    image: registry.example.com/example/photos:1.0
    port: not-a-port
    template: stateful
"""
        with self.assertRaises(CatalogError):
            parse_catalog(two)


if __name__ == "__main__":
    unittest.main()
