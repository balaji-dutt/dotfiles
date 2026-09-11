from __future__ import annotations

import json
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.test_agent_wt_merge import GitFixture, load_helper_module
from tests.support.fixtures import read_json, write_json
from tests.support.github_fixtures import (
    REPO_ROOT, configure_github_fixture, responses_for, run_data, runs_endpoint, write_state,
)


class GitHubWorktreeMergeTests(unittest.TestCase):
    def fixture(self):
        fixture = GitFixture(ci_gated=False, feature_commit=False)
        self.addCleanup(fixture.cleanup)
        api, log = configure_github_fixture(fixture)
        return fixture, api, log

    def test_feature_prepare_merge_and_shared_receipt(self):
        fixture, api, _ = self.fixture()
        before = fixture.output(fixture.main, "rev-parse", "HEAD")
        sha = fixture.commit_feature("feature.txt", "feature\n", "feature")
        run = run_data(sha=sha, ref="refs/heads/feature")
        write_state(api, responses_for(run))
        prepared = fixture.run_helper(fixture.main_helper, fixture.feature, "prepare-ci", "--poll-interval", "1", "--poll-timeout", "1")
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        self.assertIn("run 41, attempt 1", prepared.stdout)
        self.assertEqual(fixture.remote_feature_sha(), sha)
        inspection = fixture.run_helper(fixture.main_helper, fixture.feature, "inspect", "--json")
        self.assertEqual(inspection.returncode, 0, inspection.stderr)
        store = Path(json.loads(inspection.stdout)["pipeline"]["evidence_store"])
        self.assertIsNone(read_json(next(store.glob("*.json")))["landing_sha"])
        merged = fixture.run_helper(fixture.main_helper, fixture.feature, "ff", "--actor", "opencode")
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), sha)
        self.assertIsNone(fixture.remote_feature_sha())
        self.assertEqual(fixture.remote_ref_sha("refs/heads/main"), before)
        receipt = read_json(next(store.glob("*.json")))
        self.assertEqual((receipt["before_sha"], receipt["landing_sha"]), (before, sha))
        self.assertIn("Workflow evidence record", merged.stdout)
        self.assertEqual(list(store.glob(".active-*")), [])

    def test_main_preparation_reuses_only_reserved_ref_identity(self):
        fixture, api, _ = self.fixture()
        sha = fixture.commit_main("main-change.txt", "batch\n", "batch tip")
        main_ref = f"refs/heads/ci/main/{sha}"
        run = run_data(sha=sha, ref=main_ref)
        responses = responses_for(run)
        responses[runs_endpoint(run)] = [{"total_count": 0, "workflow_runs": []}, {"total_count": 1, "workflow_runs": [run]}]
        write_state(api, responses)
        prepared = fixture.run_helper(fixture.main_helper, fixture.main, "prepare-main-ci", "--poll-interval", "1", "--poll-timeout", "1")
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        self.assertIn("Temporary CI ref: deleted", prepared.stdout)
        self.assertIsNone(fixture.remote_ref_sha(main_ref))

        write_state(api, responses_for(run))
        reused = fixture.run_helper(fixture.main_helper, fixture.main, "prepare-main-ci")
        self.assertEqual(reused.returncode, 0, reused.stdout + reused.stderr)
        self.assertIn("no temporary ref was published", reused.stdout)
        bad = responses_for(run)
        bad[runs_endpoint(run)]["workflow_runs"][0]["head_branch"] = "main"
        write_state(api, bad)
        refused = fixture.run_helper(fixture.main_helper, fixture.main, "prepare-main-ci")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIsNone(fixture.remote_ref_sha(main_ref))

    def test_feature_publication_distinguishes_same_sha_from_new_commit(self):
        fixture, api, _ = self.fixture()
        for label in ("first", "same", "next"):
            if label != "same":
                sha = fixture.commit_feature("feature.txt", label + "\n", label)
            write_state(api, responses_for(run_data(sha=sha, ref="refs/heads/feature")))
            result = fixture.run_helper(fixture.main_helper, fixture.feature, "prepare-ci", "--poll-interval", "1", "--poll-timeout", "1")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            expected = "Reusing already published" if label == "same" else "Published"
            self.assertIn(f"{expected} feature at {sha}", result.stdout)
            self.assertEqual(fixture.remote_feature_sha(), sha)

    def test_failed_feature_ci_and_record_store_block_without_merge(self):
        fixture, api, _ = self.fixture()
        before = fixture.output(fixture.main, "rev-parse", "HEAD")
        sha = fixture.commit_feature("feature.txt", "feature\n", "feature")
        run = run_data(sha=sha, ref="refs/heads/feature", conclusion="failure")
        write_state(api, responses_for(run))
        prepared = fixture.run_helper(fixture.main_helper, fixture.feature, "prepare-ci", "--poll-interval", "1", "--poll-timeout", "1")
        self.assertEqual(prepared.returncode, 2, prepared.stdout + prepared.stderr)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), before)
        self.assertEqual(fixture.remote_feature_sha(), sha)
        common = Path(fixture.output(fixture.main, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        record = common / "agent-wt-merge/evidence/v1/bad.json"
        record.write_text("{")
        record.chmod(0o600)
        write_state(api, responses_for(run_data(sha=sha, ref="refs/heads/feature")))
        merged = fixture.run_helper(fixture.main_helper, fixture.feature, "ff", "--actor", "opencode")
        self.assertEqual(merged.returncode, 2)
        self.assertIn("corrupt", merged.stderr)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), before)

    def test_main_update_reexec_preserves_operation_and_contract_changes_stop(self):
        for transition in ("same", "workflow", "provider"):
            with self.subTest(transition=transition):
                fixture, api, _ = self.fixture()
                before = fixture.output(fixture.main, "rev-parse", "HEAD")
                sha = fixture.commit_feature("feature.txt", "feature\n", "feature")
                write_state(api, responses_for(run_data(sha=sha, ref="refs/heads/feature")))
                prepared = fixture.run_helper(fixture.main_helper, fixture.feature, "prepare-ci")
                self.assertEqual(prepared.returncode, 0, prepared.stderr)
                policy_path = fixture.main / "configs/pipeline-guard.json"
                if transition == "workflow":
                    policy = read_json(policy_path)
                    policy["workflows"] = [18]
                    write_json(policy_path, policy)
                elif transition == "provider":
                    policy_path.unlink()
                    fixture.install_pipeline_files(fixture.main)
                else:
                    (fixture.main / "upstream.txt").write_text("upstream\n")
                upstream = fixture.commit_all(fixture.main, "upstream contract")
                fixture.git(fixture.main, "push", "origin", "main")
                fixture.git(fixture.main, "reset", "--hard", before)
                merged = fixture.run_helper(fixture.main_helper, fixture.feature, "no-ff", "--actor", "opencode", "-m", "Land feature", "--update-main")
                common = Path(fixture.output(fixture.main, "rev-parse", "--path-format=absolute", "--git-common-dir"))
                self.assertEqual(list((common / "agent-wt-merge/evidence/v1").glob(".active-*")), [])
                if transition == "same":
                    self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
                    self.assertIn("Re-executed after main update: yes", merged.stdout)
                    self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD^2"), sha)
                else:
                    self.assertEqual(merged.returncode, 2, merged.stdout + merged.stderr)
                    self.assertIn("evidence contract changed", merged.stderr)
                    self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), upstream)
                    self.assertEqual(fixture.remote_feature_sha(), sha)

    def test_record_write_failure_after_merge_retains_feature_and_attempts_beads(self):
        fixture, api, _ = self.fixture()
        sha = fixture.commit_feature("feature.txt", "feature\n", "feature")
        write_state(api, responses_for(run_data(sha=sha, ref="refs/heads/feature")))
        prepared = fixture.run_helper(fixture.main_helper, fixture.feature, "prepare-ci")
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        module = load_helper_module()
        output = io.StringIO()
        original_cwd = Path.cwd()
        env = dict(fixture.env, PATH=str(fixture.fake_bin) + os.pathsep + fixture.env.get("PATH", ""))
        try:
            os.chdir(fixture.feature)
            with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output), \
                    mock.patch.object(sys, "path", [str(REPO_ROOT / "assets"), *sys.path]), \
                    mock.patch.object(module, "running_helper_path", return_value=fixture.main_helper), \
                    mock.patch.object(module, "persist_evidence", side_effect=module.AgentWtMergeError("synthetic record write failure")), \
                    mock.patch.object(module, "close_beads_issue", return_value={"requested": True, "closed": False, "reason": "synthetic Beads failure"}) as beads:
                helper = module.prepare_helper_execution(["ff", "--actor", "opencode"])
                result = module.perform_merge(["--actor", "opencode", "--close-beads", "dots-fixture"], merge_type="ff", helper=helper)
            self.assertEqual(result, 1)
            beads.assert_called_once()
        finally:
            os.chdir(original_cwd)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), sha)
        self.assertEqual(fixture.remote_feature_sha(), sha)
        self.assertIn("synthetic record write failure", output.getvalue())
        self.assertIn("synthetic Beads failure", output.getvalue())
        self.assertIn(sha, output.getvalue())


if __name__ == "__main__":
    unittest.main()
