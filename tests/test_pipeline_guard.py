from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.test_agent_wt_merge import GitFixture
from tests.support.fixtures import read_json, write_json
from tests.support.github_fixtures import REPO_ROOT, configure_github_fixture, responses_for, run_data, runs_endpoint, write_state

sys.path.insert(0, str(REPO_ROOT / "assets"))
try:
    import pipeline_guard
    from pipeline_evidence import EvidenceStore
    from pipeline_policy import load_github_policy
finally:
    sys.path.pop(0)


class PipelineGuardTests(unittest.TestCase):
    def fixture(self):
        fixture = GitFixture(ci_gated=False, feature_commit=False)
        self.addCleanup(fixture.cleanup)
        api, log = configure_github_fixture(fixture)
        return fixture, api, log

    def env(self, fixture):
        return dict(fixture.env, PATH=str(fixture.fake_bin) + os.pathsep + fixture.env.get("PATH", ""))

    def helper(self, fixture, cwd, *args):
        result = fixture.run_helper(fixture.main_helper, cwd, *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def land(self, fixture, api):
        before = fixture.output(fixture.main, "rev-parse", "HEAD")
        sha = fixture.commit_feature("feature.txt", "feature\n", "feature")
        run = run_data(sha=sha, ref="refs/heads/feature")
        write_state(api, responses_for(run))
        self.helper(fixture, fixture.feature, "prepare-ci", "--poll-interval", "1", "--poll-timeout", "1")
        self.helper(fixture, fixture.feature, "ff", "--actor", "opencode")
        return before, sha, run

    def guard(self, fixture, before, sha, *, cwd=None, hook=False):
        command = [sys.executable, str(fixture.main / "assets/check-pipeline.py"), "--repo-root", str(cwd or fixture.main)]
        if hook:
            hook_path = fixture.root / "pre-push"
            shutil.copy2(REPO_ROOT / "private_dot_config/git/template/hooks/executable_pre-push", hook_path)
            command = ["sh", str(hook_path)]
        return subprocess.run(command + ["origin", "git@github.com:sample/project.git"],
                              input=f"refs/heads/main {sha} refs/heads/main {before}\n",
                              cwd=cwd or fixture.main, env=self.env(fixture), text=True, capture_output=True, check=False)

    def store(self, fixture):
        with mock.patch.dict(os.environ, self.env(fixture), clear=True):
            return EvidenceStore(fixture.main)

    def prune(self, fixture, *, apply=False):
        return fixture.run_helper(fixture.main_helper, fixture.main, "prune-evidence", "--json", *(["--apply"] if apply else []))

    def test_feature_receipt_survives_worktree_removal_and_guard_never_prunes(self):
        fixture, api, _ = self.fixture()
        before, sha, _ = self.land(fixture, api)
        store = self.store(fixture)
        receipt = next(store.path.glob("*.json"))
        original = receipt.read_bytes()
        fixture.git(fixture.main, "worktree", "remove", str(fixture.feature))
        for hook in (False, True):
            checked = self.guard(fixture, before, sha, hook=hook)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn("refs/heads/feature", checked.stdout)
        self.assertEqual(receipt.read_bytes(), original)
        self.assertEqual(fixture.remote_ref_sha("refs/heads/main"), before)
        preview = self.prune(fixture)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(preview.stdout)["records"][0]["action"], "retained")
        self.assertTrue(receipt.exists())
        fixture.git(fixture.main, "push", "origin", "main")
        preview = self.prune(fixture)
        self.assertEqual(json.loads(preview.stdout)["records"][0]["action"], "eligible")
        self.assertTrue(receipt.exists())
        applied = self.prune(fixture, apply=True)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(json.loads(applied.stdout)["records"][0]["action"], "pruned")
        self.assertFalse(receipt.exists())
        self.assertEqual(fixture.remote_ref_sha("refs/heads/main"), sha)

    def test_deleted_remote_runs_reruns_failed_api_and_corruption_block_push(self):
        fixture, api, _ = self.fixture()
        before, sha, run = self.land(fixture, api)
        store = self.store(fixture)
        receipt = next(store.path.glob("*.json"))
        original = receipt.read_bytes()
        for mode in ("deleted", "rerun", "failed", "unauthorized", "corrupt", "wrong-repository"):
            with self.subTest(mode=mode):
                responses = responses_for(run)
                if mode == "deleted":
                    responses[runs_endpoint(run)] = {"total_count": 0, "workflow_runs": []}
                elif mode == "rerun":
                    responses[runs_endpoint(run)]["workflow_runs"][0]["run_attempt"] = 2
                elif mode == "failed":
                    responses = responses_for(dict(run, conclusion="failure"))
                elif mode == "unauthorized":
                    responses["repos/sample/project"] = {"fake_exit": 1, "error_text": "secret-sentinel"}
                elif mode == "wrong-repository":
                    record = read_json(receipt)
                    record["repository"] = "other/project"
                    write_json(receipt, record)
                else:
                    receipt.write_text("{")
                write_state(api, responses)
                checked = self.guard(fixture, before, sha)
                self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
                self.assertNotIn("secret-sentinel", checked.stderr)
                self.assertTrue(receipt.exists())
                receipt.write_bytes(original)
        self.assertEqual(fixture.remote_ref_sha("refs/heads/main"), before)

    def test_missing_receipt_requires_reserved_main_identity_and_new_tip_needs_new_ci(self):
        fixture, api, _ = self.fixture()
        before, sha, run = self.land(fixture, api)
        next(self.store(fixture).path.glob("*.json")).unlink()
        responses = responses_for(run)
        reserved = run_data(sha=sha, ref=f"refs/heads/ci/main/{sha}", run_id=72)
        responses[runs_endpoint(reserved)] = {"total_count": 0, "workflow_runs": []}
        write_state(api, responses)
        blocked = self.guard(fixture, before, sha)
        self.assertEqual(blocked.returncode, 1, blocked.stderr)
        self.assertIn("prepare-main-ci", blocked.stderr)
        write_state(api, responses_for(reserved))
        checked = self.guard(fixture, before, sha)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(list(self.store(fixture).path.glob("*.json")), [])
        newer = fixture.commit_main("newer.txt", "new\n", "new main")
        blocked = self.guard(fixture, before, newer)
        self.assertEqual(blocked.returncode, 1)
        self.assertIn(newer, blocked.stderr)

    def test_independent_batch_requires_exact_final_main_and_reuses_preparation(self):
        fixture, api, _ = self.fixture()
        second = fixture.root / "independent worktree"
        fixture.git(fixture.main, "worktree", "add", "-b", "second", str(second))
        before, first, _ = self.land(fixture, api)
        (second / "second.txt").write_text("second\n")
        second_sha = fixture.commit_all(second, "independent feature")
        run = run_data(sha=second_sha, ref="refs/heads/second", run_id=52)
        write_state(api, responses_for(run))
        self.helper(fixture, second, "prepare-ci", "--poll-interval", "1", "--poll-timeout", "1")
        self.helper(fixture, second, "no-ff", "--actor", "opencode", "-m", "Land independent feature")
        final = fixture.output(fixture.main, "rev-parse", "HEAD")
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD^1"), first)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD^2"), second_sha)
        self.assertEqual(self.guard(fixture, before, final).returncode, 1)
        prepared_run = run_data(sha=final, ref=f"refs/heads/ci/main/{final}", run_id=63)
        responses = responses_for(prepared_run)
        responses[runs_endpoint(prepared_run)] = [{"total_count": 0, "workflow_runs": []}, {"total_count": 1, "workflow_runs": [prepared_run]}]
        write_state(api, responses)
        self.helper(fixture, fixture.main, "prepare-main-ci", "--poll-interval", "1", "--poll-timeout", "1")
        self.assertIsNone(fixture.remote_ref_sha("refs/heads/" + prepared_run["head_branch"]))
        checked = self.guard(fixture, before, final, cwd=second, hook=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn(final, checked.stdout)
        reused = self.helper(fixture, fixture.main, "prepare-main-ci")
        self.assertIn("no temporary ref was published", reused.stdout)
        self.assertEqual(fixture.remote_ref_sha("refs/heads/main"), before)

    def test_hook_uses_authoritative_main_when_feature_policy_is_old_or_missing(self):
        fixture, api, _ = self.fixture()
        before, sha, _ = self.land(fixture, api)
        for policy_text in (None, "{}"):
            with self.subTest(policy_text=policy_text):
                path = fixture.feature / "configs/pipeline-guard.json"
                if policy_text is None:
                    path.unlink()
                else:
                    path.write_text(policy_text)
                checked = self.guard(fixture, before, sha, cwd=fixture.feature, hook=True)
                self.assertEqual(checked.returncode, 0, checked.stderr)
        (fixture.main / "configs/pipeline-guard.json").unlink()
        blocked = self.guard(fixture, before, sha, cwd=fixture.feature, hook=True)
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("tracked pipeline policy is missing", blocked.stderr)

    def test_prune_retains_active_abandoned_offline_and_changed_records(self):
        fixture, api, _ = self.fixture()
        _, sha, _ = self.land(fixture, api)
        fixture.git(fixture.main, "push", "origin", "main")
        store = self.store(fixture)
        env = self.env(fixture)
        with mock.patch.dict(os.environ, env, clear=True):
            token = store.start_operation()
            active = self.prune(fixture, apply=True)
            self.assertEqual(active.returncode, 0, active.stderr)
            self.assertEqual(json.loads(active.stdout)["records"][0]["action"], "retained")
            store.finish_operation(token)
            policy = load_github_policy(fixture.main)
            original_remote = pipeline_guard.remote_main
            calls = []

            def concurrent(repo, selected):
                value = original_remote(repo, selected)
                calls.append(value)
                if len(calls) == 2:
                    calls.append(store.start_operation())
                return value

            with mock.patch.object(pipeline_guard, "remote_main", side_effect=concurrent):
                changed = store.prune(policy, apply=True)
            self.assertIn("changed", changed["error"])
            self.assertEqual(changed["records"][0]["action"], "retained")
            store.finish_operation(calls[-1])
        fixture.git(fixture.main, "config", "core.sshCommand", '"' + sys.executable + '" -c "raise SystemExit(17)"')
        offline = self.prune(fixture, apply=True)
        self.assertEqual(offline.returncode, 1)
        self.assertEqual(json.loads(offline.stdout)["records"][0]["action"], "retained")
        self.assertEqual(len(list(store.path.glob("*.json"))), 1)

    def test_stale_policy_requires_fresh_main_preparation(self):
        fixture, api, _ = self.fixture()
        before, sha, _ = self.land(fixture, api)
        policy_path = fixture.main / "configs/pipeline-guard.json"
        policy = read_json(policy_path)
        policy["timeout_seconds"] = 6
        write_json(policy_path, policy)
        blocked = self.guard(fixture, before, sha)
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("older policy", blocked.stderr)
        self.assertIn("prepare-main-ci", blocked.stderr)

    def test_unassociated_preparation_is_retained_after_main_push(self):
        fixture, api, _ = self.fixture()
        sha = fixture.commit_feature("feature.txt", "feature\n", "feature")
        write_state(api, responses_for(run_data(sha=sha, ref="refs/heads/feature")))
        self.helper(fixture, fixture.feature, "prepare-ci", "--poll-interval", "1", "--poll-timeout", "1")
        store = self.store(fixture)
        receipt = next(store.path.glob("*.json"))
        original = receipt.read_bytes()
        fixture.git(fixture.main, "merge", "--ff-only", sha)
        fixture.git(fixture.main, "push", "origin", "main")
        result = self.prune(fixture, apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)["records"][0]
        self.assertEqual(row["action"], "retained")
        self.assertIn("associated", row["reason"])
        self.assertEqual(receipt.read_bytes(), original)

    def test_missing_runtime_blocks_hook_without_traceback(self):
        fixture, api, _ = self.fixture()
        before, sha, _ = self.land(fixture, api)
        (fixture.main / "assets/github_pipeline.py").unlink()
        blocked = self.guard(fixture, before, sha, hook=True)
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("restore the documented", blocked.stderr)
        self.assertNotIn("Traceback", blocked.stderr)


if __name__ == "__main__":
    unittest.main()
