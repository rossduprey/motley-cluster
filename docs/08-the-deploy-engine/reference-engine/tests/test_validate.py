# SPDX-License-Identifier: Apache-2.0
"""Validation runs before anything is committed. These are the things it catches."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from deploy_engine.catalog import parse_catalog  # noqa: E402
from deploy_engine.errors import ValidationError  # noqa: E402
from deploy_engine.render import render  # noqa: E402
from deploy_engine.validate import validate  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CA_PEM = "-----BEGIN CERTIFICATE-----\nQUJD\n-----END CERTIFICATE-----\n"

CATALOG = """\
services:
  - name: notepad
    description: "Shared scratch notes"
    image: registry.example.com/example/notepad:2.4.1
    port: 8080
    template: stateful
"""


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.entry = parse_catalog(CATALOG)["notepad"]
        text = (REPO_ROOT / "templates" / "stateful.yaml").read_text(encoding="utf-8")
        self.docs = render(text, self.entry, "home.arpa", CA_PEM, "node-class")

    def check_fails(self, docs, needle):
        with self.assertRaises(ValidationError) as caught:
            validate(docs, self.entry)
        message = str(caught.exception)
        self.assertIn(needle, message)
        self.assertIn("Nothing was committed.", message)

    def deployment(self, docs):
        return next(d for d in docs if d["kind"] == "Deployment")

    def test_the_shipped_template_passes(self):
        validate(self.docs, self.entry)  # must not raise

    def test_duplicate_env_is_caught_before_the_api_server_sees_it(self):
        docs = copy.deepcopy(self.docs)
        container = self.deployment(docs)["spec"]["template"]["spec"]["containers"][0]
        container["env"] = [
            {"name": "LOG_LEVEL", "value": "info"},
            {"name": "LOG_LEVEL", "value": "debug"},
        ]
        self.check_fails(docs, "duplicate environment variable")

    def test_a_container_with_no_request_is_caught(self):
        docs = copy.deepcopy(self.docs)
        self.deployment(docs)["spec"]["template"]["spec"]["containers"][0].pop("resources")
        self.check_fails(docs, "no cpu/memory request")

    def test_a_fallback_string_that_reached_the_manifest_is_caught(self):
        # Belt and braces: the fetch already raises. This is here because that
        # is exactly what we believed last time.
        docs = copy.deepcopy(self.docs)
        cm = next(d for d in docs if d["kind"] == "ConfigMap")
        cm["data"]["internal-ca.crt"] = "# CA cert not available"
        self.check_fails(docs, "not available")

    def test_a_certificate_that_is_not_a_certificate_is_caught(self):
        docs = copy.deepcopy(self.docs)
        cm = next(d for d in docs if d["kind"] == "ConfigMap")
        cm["data"]["internal-ca.crt"] = "just some text\n"
        self.check_fails(docs, "PEM header")

    def test_an_object_in_another_namespace_is_caught(self):
        docs = copy.deepcopy(self.docs)
        self.deployment(docs)["metadata"]["namespace"] = "kube-system"
        self.check_fails(docs, "not this service's namespace")

    def test_a_missing_required_object_is_caught(self):
        docs = [d for d in copy.deepcopy(self.docs) if d["kind"] != "Service"]
        self.check_fails(docs, "no Service object")

    def test_every_problem_is_reported_at_once(self):
        # One problem per run turns a bad template into an afternoon of
        # fix-rerun-fix-rerun.
        docs = copy.deepcopy(self.docs)
        docs = [d for d in docs if d["kind"] != "Service"]
        self.deployment(docs)["metadata"]["namespace"] = "kube-system"
        with self.assertRaises(ValidationError) as caught:
            validate(docs, self.entry)
        self.assertIn("2 problem(s)", str(caught.exception))

    def test_output_of_validation_failure_is_readable_yaml_free(self):
        docs = yaml.safe_load_all("kind: Deployment\n")
        with self.assertRaises(ValidationError):
            validate([d for d in docs if d], self.entry)


if __name__ == "__main__":
    unittest.main()
