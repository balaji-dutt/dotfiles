from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.support.fixtures import isolated_environment, run_git, write_json
from tests.support.github_fixtures import (
    REPO_ROOT, SHA, REF, install_fake_gh, job_data, policy_data, responses_for,
    run_data, runs_endpoint, write_state,
)

sys.path.insert(0, str(REPO_ROOT / "assets"))
try:
    import github_pipeline as github
    import pipeline_policy as policy
finally:
    sys.path.pop(0)


class GitHubPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = self.enterContext(isolated_environment(prefix="github pipeline "))
        self.repo = self.fixture.root / "repository with spaces"
        self.repo.mkdir()
        self.env = self.fixture.env.copy()
        for key in github.TOKEN_ENV:
            self.env.pop(key, None)
        self.env["FAKE_GH_STATE"] = str(self.fixture.root / "api.json")
        self.env["FAKE_GH_LOG"] = str(self.fixture.root / "calls.jsonl")
        self.enterContext(mock.patch.dict(os.environ, self.env, clear=True))
        run_git(self.repo, "init", "-b", "main", env=self.env)
        run_git(self.repo, "remote", "add", "origin", "https://github.com/sample/project.git", env=self.env)
        self.policy_path = write_json(self.repo / policy.POLICY_PATH, policy_data())
        self.state_path = Path(self.env["FAKE_GH_STATE"])
        self.log_path = Path(self.env["FAKE_GH_LOG"])
        install_fake_gh(self.fixture.fake_bin, self.state_path, self.log_path)
        self.responses = responses_for()
        self.extra = {}

    def check(self, **kwargs):
        write_state(self.state_path, self.responses, **self.extra)
        return github.check_workflows(self.repo, policy.load_github_policy(self.repo, "main"), SHA, REF, **kwargs)

    def calls(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()] if self.log_path.exists() else []

    def test_generic_workflow_success_and_dynamic_jobs(self):
        for count in (0, 1, 6, 9, 103):
            with self.subTest(jobs=count):
                run = run_data()
                jobs = [job_data(run, job_id=1000 + index, conclusion="failure" if index == 1 else "skipped" if index == 2 else "success") for index in range(count)]
                self.responses = responses_for(run, jobs)
                result = self.check()
                self.assertEqual(result["outcome"], "success")
                self.assertEqual(len(result["workflows"][0]["jobs"]), count)
        self.assertTrue(all(call["argv"][0] == "api" for call in self.calls()))
        self.assertFalse(any("/status" in str(call["argv"]) or "status=success" in str(call["argv"]) for call in self.calls()))

    def test_multiple_workflows_filenames_and_other_repository(self):
        run = run_data(repository="another-owner/entirely-different", workflow=871, path=".github/workflows/verify.yaml")
        second = run_data(repository="another-owner/entirely-different", workflow=872, run_id=42, path=".github/workflows/package.yml", conclusion="failure")
        run_git(self.repo, "remote", "set-url", "origin", "git@github.com:another-owner/entirely-different.git", env=self.env)
        write_json(self.policy_path, policy_data(repository=run["repository"]["full_name"], workflows=["verify.yaml", 872]))
        self.responses = {**responses_for(run), **responses_for(second)}
        result = self.check()
        self.assertEqual(result["outcome"], "terminal")
        self.assertEqual([item["workflow_id"] for item in result["workflows"]], [871, 872])

    def test_missing_active_and_terminal_results(self):
        for status, conclusion, expected in (("queued", None, "retryable"), ("in_progress", None, "retryable"), ("completed", "cancelled", "terminal"), ("completed", "failure", "terminal")):
            with self.subTest(status=status, conclusion=conclusion):
                self.responses = responses_for(run_data(status=status, conclusion=conclusion))
                self.assertEqual(self.check()["outcome"], expected)
        self.responses[runs_endpoint(run_data())] = {"total_count": 0, "workflow_runs": []}
        self.assertEqual(self.check()["outcome"], "retryable")

    def test_wrong_run_identity_never_substitutes(self):
        for key, value in (("head_sha", "b" * 40), ("head_branch", "main"), ("event", "pull_request"), ("workflow_id", 18), ("path", ".github/workflows/other.yml"), ("repository", {"id": 29, "full_name": "other/repository"}), ("head_repository", {"id": 30, "full_name": "sample/project"}), ("run_attempt", True)):
            with self.subTest(field=key):
                self.responses = responses_for()
                self.responses[runs_endpoint(run_data())]["workflow_runs"][0][key] = value
                with self.assertRaises(policy.PipelineError):
                    self.check()

    def test_newer_failed_run_beats_older_success_and_pinned_record(self):
        newest = run_data(run_id=42, created_at="2026-01-02T00:00:00Z", conclusion="failure")
        self.responses = responses_for(newest)
        self.responses[runs_endpoint(newest)] = {"total_count": 2, "workflow_runs": [run_data(), newest]}
        self.assertEqual(self.check()["outcome"], "terminal")
        with self.assertRaisesRegex(policy.PipelineError, "superseded"):
            self.check(expected_runs=[{"workflow_id": 17, "run_id": 41, "run_attempt": 1}])

    def test_attempt_and_run_movement_fail_closed(self):
        prefix = "repos/sample/project/actions/runs/41"
        for change in ("attempt", "new-run", "job", "status"):
            with self.subTest(change=change):
                self.responses = responses_for()
                if change == "attempt":
                    self.responses[prefix] = run_data(attempt=2)
                elif change == "new-run":
                    self.responses[runs_endpoint(run_data())] = [
                        {"total_count": 1, "workflow_runs": [run_data()]},
                        {"total_count": 1, "workflow_runs": [run_data(run_id=43)]},
                    ]
                elif change == "job":
                    self.responses[f"{prefix}/attempts/1/jobs?per_page=100&page=1"]["jobs"][0]["run_attempt"] = 2
                else:
                    self.responses[prefix] = run_data(conclusion="failure")
                self.log_path.unlink(missing_ok=True)
                with self.assertRaises(policy.PipelineError):
                    self.check()

    def test_active_state_movement_is_retryable(self):
        prefix = "repos/sample/project/actions/runs/41"
        for initial, later, conclusion in (("queued", "in_progress", None), ("in_progress", "completed", "success"), ("in_progress", "completed", "failure")):
            for endpoint in ("current", "latest", "both"):
                with self.subTest(initial=initial, later=later, conclusion=conclusion, endpoint=endpoint):
                    pinned = run_data(status=initial, conclusion=None)
                    advanced = run_data(status=later, conclusion=conclusion)
                    self.responses = responses_for(pinned)
                    if endpoint in {"current", "both"}:
                        self.responses[prefix] = advanced
                    if endpoint in {"latest", "both"}:
                        self.responses[runs_endpoint(pinned)] = [
                            {"total_count": 1, "workflow_runs": [pinned]},
                            {"total_count": 1, "workflow_runs": [advanced]},
                        ]
                    self.log_path.unlink(missing_ok=True)
                    result = self.check(expected_runs=[{"workflow_id": 17, "run_id": 41, "run_attempt": 1}])
                    self.assertEqual(result["outcome"], "retryable")
                    item = result["workflows"][0]
                    self.assertEqual(item["outcome"], "retryable")
                    self.assertEqual((item["status"], item["conclusion"]), (initial, None))
                    self.assertEqual((item["run_id"], item["run_attempt"]), (41, 1))
                    self.assertEqual(item["detail"], "run state changed during inspection")
                    self.assertEqual(item["jobs"][0]["id"], 51)
                    self.responses = responses_for(advanced)
                    self.log_path.unlink(missing_ok=True)
                    self.assertEqual(self.check()["outcome"], github.state(advanced))

    def test_state_movement_does_not_hide_invalid_evidence(self):
        prefix = "repos/sample/project/actions/runs/41"
        for initial, initial_conclusion in (("in_progress", None), ("completed", "success")):
            for endpoint in ("current", "latest"):
                cases = [{"run_id": 43}, {"attempt": 2}, {"status": "invalid"}, {"status": "completed", "conclusion": None}]
                if endpoint == "latest":
                    cases.append({"missing": True})
                if initial == "completed":
                    cases.extend([{"status": "completed", "conclusion": "failure"}, {"status": "queued", "conclusion": None}])
                for changes in cases:
                    with self.subTest(initial=initial, endpoint=endpoint, changes=changes):
                        pinned = run_data(status=initial, conclusion=initial_conclusion)
                        self.responses = responses_for(pinned)
                        advanced = run_data()
                        self.responses[prefix] = advanced
                        self.responses[runs_endpoint(pinned)] = [
                            {"total_count": 1, "workflow_runs": [pinned]},
                            {"total_count": 1, "workflow_runs": [advanced]},
                        ]
                        if endpoint == "current":
                            self.responses[prefix] = run_data(**changes)
                        else:
                            missing = changes == {"missing": True}
                            self.responses[runs_endpoint(pinned)][1] = {
                                "total_count": 0 if missing else 1,
                                "workflow_runs": [] if missing else [run_data(**changes)],
                            }
                        self.log_path.unlink(missing_ok=True)
                        with self.assertRaises(policy.PipelineError):
                            self.check()

    def test_cross_workflow_state_movement(self):
        write_json(self.policy_path, policy_data(workflows=[17, 18]))
        second = run_data(workflow=18, run_id=42, path=".github/workflows/package.yml")
        for initial, initial_conclusion in (("queued", None), ("in_progress", None), ("completed", "success")):
            for changes in ({"status": "in_progress", "conclusion": None}, {"conclusion": "success"}, {"conclusion": "failure"}, {"run_id": 43}, {"attempt": 2}, {"status": "invalid"}, {"missing": True}, {"appeared": True}):
                with self.subTest(initial=initial, changes=changes):
                    pinned = run_data(status=initial, conclusion=initial_conclusion)
                    self.responses = {**responses_for(pinned), **responses_for(second)}
                    missing = changes == {"missing": True}
                    appeared = changes == {"appeared": True}
                    advanced = run_data(**changes) if not (missing or appeared) else run_data()
                    listing = {"total_count": 1, "workflow_runs": [pinned]}
                    self.responses[runs_endpoint(pinned)] = [
                        {"total_count": 0, "workflow_runs": []} if appeared else listing,
                        {"total_count": 1, "workflow_runs": [advanced]} if appeared else listing,
                        {"total_count": 0 if missing else 1, "workflow_runs": [] if missing else [advanced]},
                    ]
                    self.log_path.unlink(missing_ok=True)
                    if missing or appeared or "run_id" in changes or "attempt" in changes or changes.get("status") == "invalid" or (initial == "completed" and advanced != pinned):
                        with self.assertRaises(policy.PipelineError):
                            self.check()
                    else:
                        result = self.check()
                        self.assertEqual(result["outcome"], "success" if initial == "completed" else "retryable")
                        item = result["workflows"][0]
                        self.assertEqual((item["status"], item["conclusion"]), (initial, initial_conclusion))
                        if advanced != pinned:
                            self.assertEqual(item["detail"], "run state changed across required workflows")

    def test_retryable_workflow_does_not_skip_later_validation(self):
        write_json(self.policy_path, policy_data(workflows=[17, 18]))
        pinned = run_data(status="in_progress", conclusion=None)
        second = run_data(workflow=18, run_id=42, path=".github/workflows/package.yml")
        self.responses = {**responses_for(pinned), **responses_for(second)}
        for run, advanced in ((pinned, run_data()), (second, dict(second, run_attempt=2))):
            listing = {"total_count": 1, "workflow_runs": [run]}
            self.responses[runs_endpoint(run)] = [listing, listing, {"total_count": 1, "workflow_runs": [advanced]}]
        with self.assertRaisesRegex(policy.PipelineError, "changed across required workflows"):
            self.check()
        self.responses[runs_endpoint(second)] = {"total_count": 1, "workflow_runs": [dict(second, conclusion="failure")]}
        self.responses["repos/sample/project/actions/runs/42"] = dict(second, conclusion="failure")
        self.responses["repos/sample/project/actions/runs/42/attempts/1"] = dict(second, conclusion="failure")
        self.log_path.unlink(missing_ok=True)
        self.assertEqual(self.check()["outcome"], "terminal")

    def test_disabled_duplicate_and_mismatched_workflows(self):
        for mode in ("disabled", "mismatch", "duplicate"):
            with self.subTest(mode=mode):
                self.responses = responses_for()
                write_json(self.policy_path, policy_data())
                workflow = self.responses["repos/sample/project/actions/workflows/17"]
                if mode == "disabled":
                    workflow["state"] = "disabled_manually"
                elif mode == "mismatch":
                    workflow["id"] = 18
                else:
                    write_json(self.policy_path, policy_data(workflows=[17, "build.yml"]))
                with self.assertRaises(policy.PipelineError):
                    self.check()

    def test_api_failures_are_bounded_and_do_not_echo_payloads(self):
        self.extra = {"error_text": "synthetic-private-token-do-not-log"}
        for response in ({"fake_exit": 4}, {"fake_raw": "not JSON"}, {"fake_raw": "[]"}, {"fake_raw": "x" * (github.MAX_RESPONSE + 1)}, {"fake_sleep": 2}):
            with self.subTest(response=list(response)):
                write_json(self.policy_path, policy_data(timeout_seconds=1))
                self.responses = {"repos/sample/project": response}
                with self.assertRaises(policy.PipelineError) as caught:
                    self.check()
                self.assertNotIn(self.extra["error_text"], str(caught.exception))

    def test_pagination_limits_duplicates_and_malformed_states(self):
        for response in ({"total_count": 301, "workflow_runs": []}, {"total_count": 1, "workflow_runs": []}, {"total_count": 2, "workflow_runs": [run_data(), run_data()]}, {"total_count": True, "workflow_runs": []}, {"total_count": 1, "workflow_runs": [run_data(status=[])]}):
            with self.subTest(response=response):
                self.responses = responses_for()
                self.responses[runs_endpoint(run_data())] = response
                with self.assertRaises(policy.PipelineError):
                    self.check()

    def test_account_pin_is_child_scoped_and_unpinned_keeps_precedence(self):
        os.environ.update({"GH_TOKEN": "synthetic-foo", "GITHUB_TOKEN": "synthetic-conflict", "GH_DEBUG": "api"})
        self.extra = {"accounts": {"bar": "synthetic-bar"}, "expected_env": {"GH_TOKEN": "synthetic-foo", "GITHUB_TOKEN": "synthetic-conflict"}}
        self.assertEqual(self.check()["outcome"], "success")
        self.assertTrue(all(all(call["token_matches"].values()) for call in self.calls()))
        self.assertFalse(any(call["argv"][0] == "auth" for call in self.calls()))
        self.log_path.unlink()
        run_git(self.repo, "config", "--local", "pipeline-guard.githubAccount", "bar", env=self.env)
        self.extra["expected_env"] = {"GH_TOKEN": "synthetic-bar", "GITHUB_TOKEN": None}
        self.assertEqual(self.check()["outcome"], "success")
        calls = self.calls()
        self.assertEqual(calls[0]["argv"], ["auth", "token", "--hostname", "github.com", "--user", "bar"])
        self.assertTrue(all(all(call["token_matches"].values()) for call in calls[1:]))
        self.assertTrue(all(not call["debug"] and call["prompt_disabled"] for call in calls))
        self.assertEqual(os.environ["GH_TOKEN"], "synthetic-foo")
        self.assertEqual(os.environ["GH_DEBUG"], "api")
        self.assertNotIn("synthetic-bar", self.log_path.read_text())

    def test_missing_or_unauthorized_pinned_account_does_not_fall_back(self):
        run_git(self.repo, "config", "--local", "pipeline-guard.githubAccount", "bar", env=self.env)
        os.environ["GH_TOKEN"] = "synthetic-working-other-account"
        with self.assertRaisesRegex(policy.PipelineError, "stored GitHub account lookup failed"):
            self.check()
        self.assertEqual(len(self.calls()), 1)
        self.log_path.unlink()
        self.extra = {"accounts": {"bar": "synthetic-expired"}, "error_text": "synthetic-expired"}
        self.responses = {"repos/sample/project": {"fake_exit": 1}}
        with self.assertRaises(policy.PipelineError) as caught:
            self.check()
        self.assertNotIn("synthetic-expired", str(caught.exception))
        self.assertEqual(len(self.calls()), 2)

    def test_enterprise_account_uses_only_enterprise_child_token(self):
        write_json(self.policy_path, policy_data(host="github.corp.example"))
        run_git(self.repo, "remote", "set-url", "origin", "ssh://git@github.corp.example/sample/project.git", env=self.env)
        run_git(self.repo, "config", "pipeline-guard.githubAccount", "bar", env=self.env)
        self.extra = {"accounts": {"bar": "synthetic-enterprise"}, "expected_env": {"GH_ENTERPRISE_TOKEN": "synthetic-enterprise", "GH_TOKEN": None, "GITHUB_ENTERPRISE_TOKEN": None}}
        self.assertEqual(self.check()["outcome"], "success")
        self.assertTrue(all(all(call["token_matches"].values()) for call in self.calls()[1:]))
        self.assertTrue(all("github.corp.example" in call["argv"] for call in self.calls()))

    def test_policy_opt_in_is_strict_and_cwd_independent(self):
        self.policy_path.unlink()
        self.assertIsNone(policy.policy_kind(self.repo))
        write_json(self.repo / ".github/workflows/ci.yml", {"arbitrary": "not a policy"})
        self.assertIsNone(policy.policy_kind(self.repo))
        write_json(self.repo / policy.LEGACY_POLICY_PATH, {})
        self.assertEqual(policy.policy_kind(self.repo), "gitlab")
        write_json(self.policy_path, policy_data())
        with self.assertRaisesRegex(policy.PipelineError, "conflicting"):
            policy.policy_kind(self.repo)
        (self.repo / policy.LEGACY_POLICY_PATH).unlink()
        for changes in ({"provider": "unknown"}, {"workflows": []}, {"workflows": [True]}, {"workflows": [17, 17]}, {"workflows": ["Test Suite"]}, {"schema_version": True}, {"timeout_seconds": 0}, {"max_pages": 21}, {"extra": "rejected"}, {"host": "github.com/x"}, {"guarded_ref": "refs/heads/../main"}):
            with self.subTest(changes=changes):
                write_json(self.policy_path, policy_data(**changes))
                with self.assertRaises(policy.PipelineError):
                    policy.load_github_policy(self.repo)
        self.assertEqual(self.calls(), [])

    def test_remote_url_identity_and_expanded_rewrites(self):
        loaded = policy.load_github_policy(self.repo)
        for url in ("git@github.com:sample/project.git", "ssh://git@github.com/sample/project", "https://github.com/sample/project.git"):
            self.assertEqual(policy.remote_identity(url), ("github.com", "sample/project"))
        for url in ("git@alias:sample/project", "https://github.com/other/project", "file:///tmp/local", "https://token@github.com/sample/project", "ssh://git@github.com:2222/sample/project", "https://github.com:bad/sample/project"):
            with self.subTest(url=url):
                run_git(self.repo, "remote", "set-url", "origin", url, env=self.env)
                with self.assertRaises(policy.PipelineError):
                    policy.validate_remote(self.repo, loaded)
        run_git(self.repo, "remote", "set-url", "origin", "gh:sample/project.git", env=self.env)
        run_git(self.repo, "config", "url.https://github.com/.insteadOf", "gh:", env=self.env)
        policy.validate_remote(self.repo, loaded)
        run_git(self.repo, "remote", "set-url", "--add", "--push", "origin", "https://github.com/sample/project", env=self.env)
        run_git(self.repo, "remote", "set-url", "--add", "--push", "origin", "https://github.com/other/repo", env=self.env)
        with self.assertRaises(policy.PipelineError):
            policy.validate_remote(self.repo, loaded)


if __name__ == "__main__":
    unittest.main()
