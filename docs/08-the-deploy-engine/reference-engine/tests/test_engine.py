# SPDX-License-Identifier: Apache-2.0
"""End to end, against two real git repositories. No cluster, no network."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from deploy_engine import records  # noqa: E402
from deploy_engine.errors import InputError, TemplateError  # noqa: E402
from support import CA_PEM, EngineTestCase, git  # noqa: E402


class TestDeploy(EngineTestCase):
    def test_deploy_writes_manifests_and_an_application(self):
        result = self.engine.deploy("notepad")
        self.assertIsNotNone(result["manifest_commit"])

        docs = list(yaml.safe_load_all(self.gitops_file("apps/notepad/service.yaml")))
        kinds = {d["kind"] for d in docs if d}
        self.assertTrue({"Namespace", "Deployment", "Service", "Certificate"} <= kinds)

        app = yaml.safe_load(self.gitops_file("apps/notepad.yaml"))
        self.assertEqual(app["kind"], "Application")
        self.assertEqual(app["spec"]["source"]["path"], "apps/notepad")
        self.assertTrue(app["spec"]["syncPolicy"]["automated"]["prune"])

    def test_every_generated_file_says_where_it_came_from(self):
        self.engine.deploy("notepad")
        for path in ("apps/notepad/service.yaml", "apps/notepad.yaml"):
            with self.subTest(path=path):
                text = self.gitops_file(path)
                self.assertIn("GENERATED FILE — DO NOT EDIT", text)
                self.assertIn("catalog entry : notepad", text)
                self.assertIn("template      : stateful", text)

    def test_manifests_are_committed_before_the_record(self):
        # The ordering that makes an interrupted run recoverable: stopping in
        # the middle leaves manifests with no record, which reconciliation
        # adopts. The reverse leaves a record for a service that never landed.
        self.engine.deploy("notepad")
        subjects = self.commit_subjects()
        self.assertEqual(subjects[0], "record: notepad deployed")
        self.assertEqual(subjects[1], "deploy: notepad (stateful)")

    def test_redeploying_an_unchanged_service_is_a_no_op(self):
        self.engine.deploy("notepad")
        before = self.commit_subjects()
        result = self.engine.deploy("notepad")
        self.assertIsNone(result["manifest_commit"], "no empty commit")
        self.assertEqual(self.commit_subjects(), before)

    def test_the_result_refuses_to_claim_the_cluster_is_running_anything(self):
        result = self.engine.deploy("notepad")
        self.assertIn("not tracked by this engine", result["cluster_state"])

    def test_overrides_reach_the_committed_manifest(self):
        self.engine.deploy("photos")
        docs = [d for d in yaml.safe_load_all(self.gitops_file("apps/photos/service.yaml")) if d]
        pvc = next(d for d in docs if d["kind"] == "PersistentVolumeClaim")
        self.assertEqual(pvc["spec"]["resources"]["requests"]["storage"], "50Gi")
        pod = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]
        self.assertEqual(pod["nodeSelector"], {"node-class": "heavy"})
        self.assertEqual([c["name"] for c in pod["initContainers"]], ["fix-permissions"])


class TestInputs(EngineTestCase):
    def test_a_missing_certificate_stops_the_deploy(self):
        self._edit_source(lambda root: (root / "ca" / "internal-ca.crt").unlink())
        with self.assertRaises(InputError):
            self.engine.deploy("notepad")
        self.assertFalse(self.gitops_has("apps/notepad/service.yaml"))

    def test_a_placeholder_where_a_certificate_belongs_stops_the_deploy(self):
        # The exact failure from README section 6: a fetch that returned
        # something useless, written into every service the engine deployed.
        self._edit_source(
            lambda root: (root / "ca" / "internal-ca.crt").write_text(
                "# CA cert not available\n", encoding="utf-8"
            )
        )
        with self.assertRaises(InputError) as caught:
            self.engine.deploy("notepad")
        self.assertIn("BEGIN CERTIFICATE", str(caught.exception))
        self.assertFalse(self.gitops_has("apps/notepad/service.yaml"))

    def test_a_missing_template_falls_back_to_the_baked_in_copy(self):
        self._edit_source(lambda root: (root / "templates" / "stateful.yaml").unlink())
        self.engine.deploy("notepad")  # the offline fallback carries it
        self.assertTrue(self.gitops_has("apps/notepad/service.yaml"))

    def test_a_template_that_exists_nowhere_is_an_error(self):
        self.engine.settings.fallback_templates = None
        self._edit_source(lambda root: (root / "templates" / "stateful.yaml").unlink())
        with self.assertRaises(TemplateError):
            self.engine.deploy("notepad")

    def test_a_template_change_ships_on_push_with_no_engine_rebuild(self):
        self.engine.deploy("notepad")

        def bump(root):
            path = root / "templates" / "stateful.yaml"
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    'memory: "128Mi"', 'memory: "192Mi"'
                ),
                encoding="utf-8",
            )

        self._edit_source(bump)
        self.engine.source.ttl_seconds = 0  # the documented cache window, elapsed
        self.engine.deploy("notepad")
        self.assertIn("192Mi", self.gitops_file("apps/notepad/service.yaml"))

    def _edit_source(self, mutate):
        clone = self.tmp / "edit-source"
        git("clone", str(self.source_origin), str(clone), cwd=self.tmp)
        mutate(clone)
        git("add", "-A", cwd=clone)
        git("commit", "-m", "edit", cwd=clone)
        git("push", "origin", "HEAD:main", cwd=clone)
        self.engine.source.ttl_seconds = 0


class TestLifecycle(EngineTestCase):
    def test_suspend_and_resume(self):
        self.engine.deploy("notepad")
        self.engine.suspend("notepad")
        docs = [d for d in yaml.safe_load_all(self.gitops_file("apps/notepad/service.yaml")) if d]
        deployment = next(d for d in docs if d["kind"] == "Deployment")
        self.assertEqual(deployment["spec"]["replicas"], 0)
        self.assertEqual(
            yaml.safe_load(self.gitops_file("records/notepad.yaml"))["state"], "suspended"
        )

        self.engine.resume("notepad")
        docs = [d for d in yaml.safe_load_all(self.gitops_file("apps/notepad/service.yaml")) if d]
        self.assertEqual(next(d for d in docs if d["kind"] == "Deployment")["spec"]["replicas"], 1)

    def test_suspend_keeps_the_volume(self):
        self.engine.deploy("notepad")
        self.engine.suspend("notepad")
        docs = [d for d in yaml.safe_load_all(self.gitops_file("apps/notepad/service.yaml")) if d]
        self.assertTrue(any(d["kind"] == "PersistentVolumeClaim" for d in docs))

    def test_the_generated_header_survives_a_lifecycle_edit(self):
        self.engine.deploy("notepad")
        self.engine.suspend("notepad")
        self.assertIn("GENERATED FILE", self.gitops_file("apps/notepad/service.yaml"))

    def test_remove_deletes_the_manifests_and_the_record(self):
        self.engine.deploy("notepad")
        self.engine.remove("notepad")
        self.assertFalse(self.gitops_has("apps/notepad/service.yaml"))
        self.assertFalse(self.gitops_has("apps/notepad.yaml"))
        self.assertFalse(self.gitops_has("records/notepad.yaml"))

    def test_delete_and_redeploy_produces_an_identical_manifest(self):
        # The gate at the end of README: if what comes back is not identical,
        # something was in someone's memory rather than in the template.
        self.engine.deploy("notepad")
        first = self.gitops_file("apps/notepad/service.yaml")
        self.engine.remove("notepad")
        self.engine.deploy("notepad")
        self.assertEqual(self.gitops_file("apps/notepad/service.yaml"), first)


class TestReconciliation(EngineTestCase):
    def test_a_record_that_disagrees_with_the_manifests_is_corrected(self):
        # The split state that wedged a service for weeks: the record said
        # suspended, the cluster was serving it, and neither suspend nor resume
        # would touch it again because each expected the other's state.
        self.engine.deploy("notepad")
        self._corrupt_record(state="suspended")

        corrections = records.reconcile(self.engine.gitops)
        self.assertTrue(any("corrected" in c for c in corrections), corrections)
        self.assertEqual(records.load(self.engine.gitops, "notepad").state, "deployed")

    def test_a_half_finished_suspend_can_be_re_run_to_completion(self):
        self.engine.deploy("notepad")
        self._corrupt_record(state="suspended")  # record flipped, manifests not
        self.engine.suspend("notepad")  # re-running FINISHES it
        docs = [d for d in yaml.safe_load_all(self.gitops_file("apps/notepad/service.yaml")) if d]
        self.assertEqual(next(d for d in docs if d["kind"] == "Deployment")["spec"]["replicas"], 0)
        self.assertEqual(records.load(self.engine.gitops, "notepad").state, "suspended")

    def test_manifests_with_no_record_are_adopted(self):
        # What an interrupted deploy leaves behind, by design.
        self.engine.deploy("notepad")
        self._drop_record()
        corrections = records.reconcile(self.engine.gitops)
        self.engine.gitops.commit_and_push("reconcile", self.engine.settings.author)
        self.assertTrue(any("adopted" in c for c in corrections), corrections)
        self.assertEqual(records.load(self.engine.gitops, "notepad").state, "deployed")

    def test_a_record_for_a_service_that_does_not_exist_is_removed(self):
        self.engine.deploy("notepad")
        self._edit_gitops(lambda root: __import__("shutil").rmtree(root / "apps" / "notepad"))
        corrections = records.reconcile(self.engine.gitops)
        self.assertTrue(any("record removed" in c for c in corrections), corrections)

    def test_status_says_out_loud_that_it_is_not_the_cluster(self):
        self.engine.deploy("notepad")
        status = self.engine.status()
        self.assertEqual(status["services"], {"notepad": "deployed"})
        self.assertIn("not what the cluster is running", status["advisory"])

    # -- helpers -----------------------------------------------------------

    def _corrupt_record(self, state):
        def mutate(root):
            path = root / "records" / "notepad.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            data["state"] = state
            path.write_text(yaml.safe_dump(data), encoding="utf-8")

        self._edit_gitops(mutate)

    def _drop_record(self):
        self._edit_gitops(lambda root: (root / "records" / "notepad.yaml").unlink())

    def _edit_gitops(self, mutate):
        clone = self.tmp / f"edit-gitops-{id(mutate)}"
        git("clone", str(self.gitops_origin), str(clone), cwd=self.tmp)
        mutate(clone)
        git("add", "-A", cwd=clone)
        git("commit", "-m", "out-of-band edit", cwd=clone)
        git("push", "origin", "HEAD:main", cwd=clone)


class TestItNeverTouchesACluster(unittest.TestCase):
    """The design decision the whole package rests on, asserted mechanically."""

    FORBIDDEN = ("kubernetes", "kubectl", "kubeconfig", "openshift")

    def test_no_module_imports_a_kubernetes_client(self):
        package = Path(__file__).resolve().parent.parent / "deploy_engine"
        offenders = []
        for path in sorted(package.glob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if not stripped.startswith(("import ", "from ")):
                    continue
                if any(name in stripped for name in self.FORBIDDEN):
                    offenders.append(f"{path.name}:{number}: {stripped}")
        self.assertEqual(
            offenders, [], "the engine writes to git and touches nothing else"
        )

    def test_the_settings_have_nowhere_to_put_a_cluster_credential(self):
        from deploy_engine.engine import Settings

        fields = set(Settings.__dataclass_fields__)
        self.assertEqual(
            fields & {"kubeconfig", "api_server", "service_account", "token"}, set()
        )


if __name__ == "__main__":
    unittest.main()
