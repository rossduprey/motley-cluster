# SPDX-License-Identifier: Apache-2.0
"""Rendering, and the three failure modes it exists to make impossible."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deploy_engine.catalog import parse_catalog  # noqa: E402
from deploy_engine.errors import TemplateError  # noqa: E402
from deploy_engine.render import _find_unresolved, render, substitute  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CA_PEM = "-----BEGIN CERTIFICATE-----\nQUJD\nDEF\n-----END CERTIFICATE-----\n"

CATALOG = """\
services:
  - name: notepad
    description: "Shared scratch notes, and a description long enough to be truncated"
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
        value: "4"
    secretEnv:
      DB_PASSWORD: db-key
    initContainers:
      - name: fix-permissions
        image: registry.example.com/example/busybox:1.36
        command: ["true"]
"""

TEMPLATE_WITH_ENV = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: APP_NAME
  namespace: APP_NAMESPACE
spec:
  template:
    spec:
      initContainers:
        - name: wait-for-db
          image: registry.example.com/example/busybox:1.36
      containers:
        - name: APP_NAME
          image: APP_IMAGE
          env:
            - name: THUMBNAIL_WORKERS
              value: "1"
            - name: LOG_LEVEL
              value: "info"
          volumeMounts:
            - name: data
              mountPath: /data
"""


def entries():
    return parse_catalog(CATALOG)


def render_stateful(name: str):
    text = (REPO_ROOT / "templates" / "stateful.yaml").read_text(encoding="utf-8")
    return render(text, entries()[name], "home.arpa", CA_PEM, "node-class")


def by_kind(docs, kind, name=None):
    for doc in docs:
        if doc["kind"] == kind and (name is None or doc["metadata"]["name"] == name):
            return doc
    raise AssertionError(f"no {kind} named {name} in rendered output")


class TestSubstitution(unittest.TestCase):
    def test_a_lone_token_keeps_its_real_type(self):
        # `containerPort: APP_PORT` must yield an integer. A text-substituting
        # engine yields the string "8080", which the API server then rejects.
        out = substitute({"containerPort": "APP_PORT"}, {"APP_PORT": 8080})
        self.assertIs(type(out["containerPort"]), int)

    def test_a_token_inside_a_string_is_replaced_as_text(self):
        out = substitute("Host(`APP_DOMAIN`)", {"APP_DOMAIN": "a.home.arpa"})
        self.assertEqual(out, "Host(`a.home.arpa`)")

    def test_comments_cannot_be_corrupted_by_substitution(self):
        # The template header names every token INCLUDING the multi-line
        # CA_CERT. Under string replacement that header breaks out of the
        # comment and the file stops being YAML. Here it cannot: comments are
        # gone before substitution happens.
        header = (REPO_ROOT / "templates" / "stateful.yaml").read_text(encoding="utf-8")
        self.assertIn("CA_CERT", header.split("---", 1)[0], "fixture must exercise this")
        docs = render_stateful("notepad")
        rendered = str(docs)
        self.assertNotIn("# stateful", rendered)
        self.assertIn("BEGIN CERTIFICATE", by_kind(docs, "ConfigMap")["data"]["internal-ca.crt"])

    def test_unresolved_tokens_are_detected_rather_than_shipped(self):
        # The guard behind the "left tokens unresolved" error: better a loud
        # failure than the literal word APP_NAME landing in someone's cluster.
        leftovers = set()
        _find_unresolved({"image": "APP_IMAGE", "ok": "resolved"}, leftovers)
        self.assertEqual(leftovers, {"APP_IMAGE"})

        clean = set()
        _find_unresolved(render_stateful("notepad"), clean)
        self.assertEqual(clean, set(), "a full render leaves nothing behind")


class TestRenderedObjects(unittest.TestCase):
    def test_name_becomes_namespace_and_hostname(self):
        docs = render_stateful("notepad")
        self.assertEqual(by_kind(docs, "Namespace")["metadata"]["name"], "notepad")
        cert = by_kind(docs, "Certificate")
        self.assertEqual(cert["spec"]["dnsNames"], ["notepad.home.arpa"])
        self.assertTrue(all(
            d["metadata"].get("namespace") == "notepad"
            for d in docs if d["kind"] != "Namespace"
        ))

    def test_description_is_truncated_before_it_becomes_a_label(self):
        deployment = by_kind(render_stateful("notepad"), "Deployment")
        title = deployment["metadata"]["labels"]["app.kubernetes.io/description"]
        self.assertEqual(len(title), 40)


class TestOverrides(unittest.TestCase):
    def test_env_replaces_and_never_duplicates(self):
        # The failure this prevents: an override that appended produced two
        # entries with the same name, the API server refused the Deployment,
        # and no pod was ever created.
        docs = render(TEMPLATE_WITH_ENV, entries()["photos"], "home.arpa", CA_PEM, "nc")
        env = by_kind(docs, "Deployment")["spec"]["template"]["spec"]["containers"][0]["env"]
        names = [e["name"] for e in env]
        self.assertEqual(len(names), len(set(names)), f"duplicate env: {names}")
        values = {e["name"]: e.get("value") for e in env}
        self.assertEqual(values["THUMBNAIL_WORKERS"], "4", "catalog wins outright")
        self.assertEqual(values["LOG_LEVEL"], "info", "untouched template values survive")

    def test_secret_env_becomes_a_reference_never_a_value(self):
        docs = render(TEMPLATE_WITH_ENV, entries()["photos"], "home.arpa", CA_PEM, "nc")
        env = by_kind(docs, "Deployment")["spec"]["template"]["spec"]["containers"][0]["env"]
        entry = next(e for e in env if e["name"] == "DB_PASSWORD")
        self.assertEqual(
            entry["valueFrom"]["secretKeyRef"], {"name": "photos-secret", "key": "db-key"}
        )
        self.assertNotIn("value", entry, "the engine never sees or writes the secret")

    def test_init_containers_append_because_order_matters(self):
        docs = render(TEMPLATE_WITH_ENV, entries()["photos"], "home.arpa", CA_PEM, "nc")
        inits = by_kind(docs, "Deployment")["spec"]["template"]["spec"]["initContainers"]
        self.assertEqual([c["name"] for c in inits], ["wait-for-db", "fix-permissions"])

    def test_storage_size_and_data_mount(self):
        docs = render_stateful("photos")
        pvc = by_kind(docs, "PersistentVolumeClaim", "photos-data")
        self.assertEqual(pvc["spec"]["resources"]["requests"]["storage"], "50Gi")
        mounts = by_kind(docs, "Deployment")["spec"]["template"]["spec"]["containers"][0][
            "volumeMounts"
        ]
        self.assertEqual(
            next(m for m in mounts if m["name"] == "data")["mountPath"], "/library"
        )

    def test_node_class_becomes_a_label_not_a_hostname(self):
        pod = by_kind(render_stateful("photos"), "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(pod["nodeSelector"], {"node-class": "heavy"})

    def test_overrides_touch_only_the_main_workload(self):
        template = TEMPLATE_WITH_ENV + """\
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: photos-postgres
  namespace: APP_NAMESPACE
spec:
  template:
    spec:
      containers:
        - name: postgres
          image: registry.example.com/example/postgres:16
          env:
            - name: THUMBNAIL_WORKERS
              value: "untouched"
"""
        docs = render(template, entries()["photos"], "home.arpa", CA_PEM, "nc")
        sidecar = by_kind(docs, "Deployment", "photos-postgres")
        env = sidecar["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertEqual(env[0]["value"], "untouched")
        self.assertNotIn(
            "initContainers", sidecar["spec"]["template"]["spec"],
            "a template that ships a sidecar owns that sidecar entirely",
        )

    def test_a_workload_the_overrides_cannot_find_is_an_error(self):
        with self.assertRaises(TemplateError):
            render(
                "kind: ConfigMap\napiVersion: v1\nmetadata: {name: APP_NAME}\n",
                entries()["photos"],
                "home.arpa",
                CA_PEM,
                "nc",
            )


if __name__ == "__main__":
    unittest.main()
