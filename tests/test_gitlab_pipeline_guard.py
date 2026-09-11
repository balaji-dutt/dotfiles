from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

from tests.support.fixtures import init_git_repository, run_git, write_executable, write_json


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "assets" / "check-gitlab-pipeline.py"
PRE_PUSH_HOOK = REPO_ROOT / "private_dot_config" / "git" / "template" / "hooks" / "executable_pre-push"
PRE_REBASE_HOOK = REPO_ROOT / "private_dot_config" / "git" / "template" / "hooks" / "executable_pre-rebase"
PYTHON_RESOLVER = REPO_ROOT / "assets" / "resolve-python3"
ZERO_SHA = "0" * 40
ZERO_SHA256 = "0" * 64


class ApiState:
    def __init__(self, *, job_name: str = "linux-fast", job_status: str = "success") -> None:
        self.job_name = job_name
        self.job_status = job_status
        self.pipeline_mode = "normal"
        self.requests: list[str] = []
        self.available_shas: set[str] | None = None
        self.ci_remote: Path | None = None


@contextmanager
def gitlab_api(state: ApiState) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            state.requests.append(self.path)
            parsed = urllib.parse.urlparse(self.path)
            if state.pipeline_mode == "http-error":
                self.send_error(503)
                return
            if parsed.path.endswith("/pipelines"):
                if state.pipeline_mode == "malformed":
                    body = b"not json"
                elif state.pipeline_mode == "empty":
                    body = b"[]"
                else:
                    sha = urllib.parse.parse_qs(parsed.query)["sha"][0]
                    if state.ci_remote is not None and state.available_shas is not None:
                        published = run_git(state.ci_remote, "rev-parse", "--verify", f"refs/heads/ci/main/{sha}", check=False)
                        if published.returncode == 0 and published.stdout.strip() == sha:
                            state.available_shas.add(sha)
                    pipeline_ids = [42, 41] if state.pipeline_mode == "multiple" else [41]
                    if state.available_shas is not None and sha not in state.available_shas:
                        pipeline_ids = []
                    body = json.dumps(
                        [
                            {
                                "id": pipeline_id,
                                "sha": sha,
                                "web_url": f"https://gitlab.example/pipelines/{pipeline_id}",
                            }
                            for pipeline_id in pipeline_ids
                        ]
                    ).encode()
            elif re.fullmatch(r".*/pipelines/(41|42)/jobs", parsed.path):
                pipeline_id = int(parsed.path.split("/")[-2])
                status = (
                    "pending"
                    if state.pipeline_mode == "multiple" and pipeline_id == 42
                    else state.job_status
                )
                body = json.dumps(
                    [{"name": state.job_name, "status": status}]
                ).encode()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/api/v4"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


class PipelineGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        init_git_repository(self.root)
        run_git(self.root, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.root / "tracked").write_text("base\n", encoding="utf-8")
        run_git(self.root, "add", "tracked")
        run_git(self.root, "commit", "--quiet", "-m", "base")
        self.base_sha = self.rev_parse("HEAD")
        self.main_branch = run_git(
            self.root, "symbolic-ref", "--short", "HEAD"
        ).stdout.strip()

    def rev_parse(self, value: str) -> str:
        return run_git(self.root, "rev-parse", value).stdout.strip()

    def commit(self, content: str, message: str) -> str:
        (self.root / "tracked").write_text(content, encoding="utf-8")
        run_git(self.root, "add", "tracked")
        run_git(self.root, "commit", "--quiet", "-m", message)
        return self.rev_parse("HEAD")

    def policy(self, api_url: str, **updates: object) -> Path:
        payload: dict[str, object] = {
            "$schema": "./schemas/gitlab-pipeline-guard.v1.schema.json",
            "schema_version": 1,
            "api_url": api_url,
            "project_id": 44618209,
            "guarded_remote": "origin",
            "guarded_ref": "refs/heads/main",
            "required_job": "linux-fast",
            "timeout_seconds": 2,
        }
        payload.update(updates)
        return write_json(self.root / "configs" / "gitlab-pipeline-guard.json", payload)

    def run_guard(
        self,
        record: str,
        policy: Path,
        *,
        remote_name: str = "origin",
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--repo-root",
                str(self.root),
                "--policy",
                str(policy),
                remote_name,
                "git@gitlab.com:balaji-personal-files/dotfiles.git",
            ],
            input=record,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def run_direct(
        self,
        sha: str,
        policy: Path,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--repo-root",
                str(self.root),
                "--policy",
                str(policy),
                "--check-sha",
                sha,
                "--json",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result, json.loads(result.stdout)

    def run_rebase_check(
        self,
        policy: Path,
        *,
        branch: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        arguments = [
            sys.executable,
            str(HELPER),
            "--repo-root",
            str(self.root),
            "--policy",
            str(policy),
            "--check-rebase",
        ]
        if branch is not None:
            arguments.extend(["--rebase-branch", branch])
        return subprocess.run(
            arguments,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @staticmethod
    def record(local_sha: str, remote_sha: str, remote_ref: str = "refs/heads/main") -> str:
        return f"refs/heads/main {local_sha} {remote_ref} {remote_sha}\n"

    def test_fast_forward_requires_successful_exact_job(self) -> None:
        tip = self.commit("feature\n", "feature")
        state = ApiState()
        with gitlab_api(state) as api_url:
            result = self.run_guard(self.record(tip, self.base_sha), self.policy(api_url))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"linux-fast succeeded for {tip}", result.stdout)
        self.assertTrue(any(f"sha={tip}" in request for request in state.requests))
        self.assertTrue(any("/pipelines/41/jobs" in request for request in state.requests))

    def test_policy_strings_are_normalized(self) -> None:
        tip = self.commit("feature\n", "feature")
        state = ApiState()
        with gitlab_api(state) as api_url:
            policy = self.policy(
                f"  {api_url}/  ",
                guarded_remote=" origin ",
                guarded_ref=" refs/heads/main ",
                required_job=" linux-fast ",
            )
            result = self.run_guard(self.record(tip, self.base_sha), policy)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"linux-fast succeeded for {tip}", result.stdout)

    def test_any_successful_job_for_exact_sha_is_accepted(self) -> None:
        tip = self.commit("feature\n", "feature")
        state = ApiState(job_status="success")
        state.pipeline_mode = "multiple"
        with gitlab_api(state) as api_url:
            result = self.run_guard(self.record(tip, self.base_sha), self.policy(api_url))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("/pipelines/42/jobs" in request for request in state.requests))
        self.assertTrue(any("/pipelines/41/jobs" in request for request in state.requests))

    def test_no_ff_merge_checks_feature_parent(self) -> None:
        run_git(self.root, "switch", "--quiet", "-c", "feature")
        feature_sha = self.commit("feature\n", "feature")
        run_git(self.root, "switch", "--quiet", self.main_branch)
        run_git(self.root, "merge", "--quiet", "--no-ff", "feature", "-m", "merge feature")
        merge_sha = self.rev_parse("HEAD")
        state = ApiState()
        with gitlab_api(state) as api_url:
            result = self.run_guard(
                self.record(merge_sha, self.base_sha), self.policy(api_url)
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(feature_sha, result.stdout)
        self.assertNotIn(merge_sha, result.stdout)
        self.assertTrue(any(f"sha={feature_sha}" in request for request in state.requests))

    def test_missing_pending_failed_and_wrong_job_block(self) -> None:
        tip = self.commit("feature\n", "feature")
        cases = (
            ("empty", "linux-fast", "success", "job not found"),
            ("normal", "linux-fast", "pending", "pending"),
            ("normal", "linux-fast", "failed", "failed"),
            ("normal", "different-job", "success", "job not found"),
        )
        for mode, name, status, expected in cases:
            with self.subTest(mode=mode, name=name, status=status):
                state = ApiState(job_name=name, job_status=status)
                state.pipeline_mode = mode
                with gitlab_api(state) as api_url:
                    result = self.run_guard(
                        self.record(tip, self.base_sha), self.policy(api_url)
                    )
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)
                self.assertIn("prepare-main-ci", result.stderr)
                self.assertIn("retry the original push separately", result.stderr)
                self.assertNotIn("documented override", result.stderr)

    def test_direct_mode_classifies_exact_sha_job_states(self) -> None:
        tip = self.commit("feature\n", "feature")
        cases = (
            ("normal", "success", "success", 0),
            ("empty", "success", "retryable", 1),
            ("normal", "pending", "retryable", 1),
            ("normal", "failed", "terminal", 1),
        )
        for mode, status, outcome, returncode in cases:
            with self.subTest(mode=mode, status=status):
                state = ApiState(job_status=status)
                state.pipeline_mode = mode
                with gitlab_api(state) as api_url:
                    result, payload = self.run_direct(tip, self.policy(api_url))
                self.assertEqual(result.returncode, returncode, result.stderr)
                self.assertEqual(payload["schema_version"], 1)
                self.assertEqual(payload["outcome"], outcome)
                self.assertEqual(payload["sha"], tip)
                self.assertEqual(payload["required_job"], "linux-fast")

    def test_direct_mode_reports_errors_and_override_bypass_as_json(self) -> None:
        tip = self.commit("feature\n", "feature")
        state = ApiState()
        state.pipeline_mode = "malformed"
        with gitlab_api(state) as api_url:
            error, error_payload = self.run_direct(tip, self.policy(api_url))
        self.assertEqual(error.returncode, 1)
        self.assertEqual(error_payload["outcome"], "error")
        self.assertIn("malformed JSON", str(error_payload["detail"]))

        common_dir = Path(
            run_git(
                self.root,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ).stdout.strip()
        )
        (common_dir / "pipeline-guard.override").touch()
        bypass, bypass_payload = self.run_direct(
            tip, self.policy("http://127.0.0.1:1/api/v4")
        )
        self.assertEqual(bypass.returncode, 0, bypass.stderr)
        self.assertEqual(bypass_payload["outcome"], "bypass")
        self.assertIsNone(bypass_payload["required_job"])
        self.assertIn("WARNING: bypassing", bypass.stderr)

    def test_api_and_policy_errors_block(self) -> None:
        tip = self.commit("feature\n", "feature")
        for mode, expected in (("malformed", "malformed JSON"), ("http-error", "API request failed")):
            with self.subTest(mode=mode):
                state = ApiState()
                state.pipeline_mode = mode
                with gitlab_api(state) as api_url:
                    result = self.run_guard(
                        self.record(tip, self.base_sha), self.policy(api_url)
                    )
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)

        invalid = self.policy("not-a-url")
        malformed = self.run_guard(self.record(tip, self.base_sha), invalid)
        self.assertEqual(malformed.returncode, 1)
        self.assertIn("absolute HTTP(S) URL", malformed.stderr)

        unreachable = self.run_guard(
            self.record(tip, self.base_sha),
            self.policy("http://127.0.0.1:1/api/v4", timeout_seconds=1),
        )
        self.assertEqual(unreachable.returncode, 1)
        self.assertIn("API request failed", unreachable.stderr)

    def test_batch_merge_checks_final_main_and_non_ancestor_still_blocks(self) -> None:
        run_git(self.root, "switch", "--quiet", "-c", "feature")
        self.commit("feature\n", "feature")
        run_git(self.root, "switch", "--quiet", self.main_branch)
        (self.root / "main-only").write_text("main\n", encoding="utf-8")
        run_git(self.root, "add", "main-only")
        run_git(self.root, "commit", "--quiet", "-m", "local main")
        run_git(self.root, "merge", "--quiet", "--no-ff", "feature", "-m", "merge feature")
        batch_tip = self.rev_parse("HEAD")
        state = ApiState()
        with gitlab_api(state) as api_url:
            batch = self.run_guard(
                self.record(batch_tip, self.base_sha), self.policy(api_url)
            )
        self.assertEqual(batch.returncode, 0, batch.stderr)
        self.assertIn(batch_tip, batch.stdout)
        self.assertTrue(any(f"sha={batch_tip}" in request for request in state.requests))

        run_git(self.root, "switch", "--quiet", "--detach", self.base_sha)
        unrelated = self.commit("unrelated\n", "unrelated")
        run_git(self.root, "switch", "--quiet", self.main_branch)
        state = ApiState()
        with gitlab_api(state) as api_url:
            non_ancestor = self.run_guard(
                self.record(unrelated, batch_tip), self.policy(api_url)
            )
        self.assertEqual(non_ancestor.returncode, 1)
        self.assertIn("not a fast-forward", non_ancestor.stderr)
        self.assertEqual(state.requests, [])

    def test_non_target_and_deletion_records_do_not_call_api(self) -> None:
        tip = self.commit("feature\n", "feature")
        state = ApiState()
        with gitlab_api(state) as api_url:
            policy = self.policy(api_url)
            wrong_remote = self.run_guard(
                self.record(tip, self.base_sha), policy, remote_name="upstream"
            )
            wrong_ref = self.run_guard(
                self.record(tip, self.base_sha, "refs/heads/other"), policy
            )
            deletion = self.run_guard(self.record(ZERO_SHA, self.base_sha), policy)
            sha256_deletion = self.run_guard(
                self.record(ZERO_SHA256, self.base_sha), policy
            )
            sha256_new_ref = self.run_guard(
                self.record(tip, ZERO_SHA256, "refs/heads/main"), policy
            )
        self.assertEqual(wrong_remote.returncode, 0, wrong_remote.stderr)
        self.assertEqual(wrong_ref.returncode, 0, wrong_ref.stderr)
        self.assertEqual(deletion.returncode, 0, deletion.stderr)
        self.assertEqual(sha256_deletion.returncode, 0, sha256_deletion.stderr)
        self.assertEqual(sha256_new_ref.returncode, 0, sha256_new_ref.stderr)
        self.assertEqual(len(state.requests), 2)

    def test_independent_branches_land_before_push_and_batch_needs_prepared_main(self) -> None:
        from tests.test_agent_wt_merge import GitFixture

        for ci_gated in (False, True):
            with self.subTest(ci_gated=ci_gated):
                fixture = GitFixture(feature_commit=False, ci_gated=ci_gated)
                self.addCleanup(fixture.cleanup)
                fixture.set_override(False)
                state = ApiState()
                state.available_shas = set()
                with gitlab_api(state) as api_url:
                    policy_path = fixture.main / "configs" / "gitlab-pipeline-guard.json"
                    if ci_gated:
                        policy = json.loads(policy_path.read_text(encoding="utf-8"))
                        policy["api_url"] = api_url
                        write_json(policy_path, policy)
                        shutil.copy2(HELPER, fixture.main / "assets" / HELPER.name)
                        fixture.commit_all(fixture.main, "configure isolated CI service")
                        fixture.git(fixture.main, "push", "origin", "main")
                        fixture.git(fixture.feature, "merge", "--ff-only", "main")
                    remote_main = fixture.remote_ref_sha("refs/heads/main")
                    second = fixture.root / "second feature"
                    fixture.git(fixture.main, "worktree", "add", "-b", "second", str(second), "main")
                    first_sha = fixture.commit_feature("first.txt", "first\n", "first change")
                    (second / "second.txt").write_text("second\n", encoding="utf-8")
                    second_sha = fixture.commit_all(second, "second change")
                    if ci_gated:
                        fixture.git(second, "push", "origin", "second")
                    state.available_shas.update({first_sha, second_sha})
                    refs_before = fixture.output(fixture.remote, "show-ref")
                    first = fixture.run_helper(fixture.main_helper, fixture.feature, "ff", "--actor", "opencode")
                    self.assertEqual(first.returncode, 0, first.stderr)
                    landed = fixture.run_helper(fixture.main_helper, second, "no-ff", "--actor", "opencode", "-m", "land second change")
                    self.assertEqual(landed.returncode, 0, landed.stderr)
                    main_sha = fixture.output(fixture.main, "rev-parse", "HEAD")
                    self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD^1"), first_sha)
                    self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD^2"), second_sha)
                    self.assertEqual(fixture.remote_ref_sha("refs/heads/main"), remote_main)
                    if not ci_gated:
                        self.assertEqual(fixture.output(fixture.remote, "show-ref"), refs_before)
                        self.assertEqual(state.requests, [])
                        continue

                    def check_push(sha: str) -> subprocess.CompletedProcess[str]:
                        return subprocess.run(
                            [sys.executable, str(HELPER), "--repo-root", str(fixture.main), "--policy", str(policy_path), "origin", str(fixture.remote)],
                            input=self.record(sha, str(remote_main)), env=fixture.env,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                        )

                    refs_before_check = fixture.output(fixture.remote, "show-ref")
                    blocked = check_push(main_sha)
                    self.assertEqual(blocked.returncode, 1)
                    self.assertIn(main_sha, blocked.stderr)
                    self.assertIn("prepare-main-ci", blocked.stderr)
                    self.assertEqual(fixture.output(fixture.remote, "show-ref"), refs_before_check)
                    for job_status in ("pending", "failed"):
                        state.available_shas.add(main_sha)
                        state.job_status = job_status
                        self.assertEqual(check_push(main_sha).returncode, 1)
                    state.available_shas.discard(main_sha)
                    state.job_status = "success"
                    state.ci_remote = fixture.remote
                    prepared = fixture.run_helper(fixture.main_helper, fixture.main, "prepare-main-ci", "--poll-interval", "1", "--poll-timeout", "1")
                    self.assertEqual(prepared.returncode, 0, prepared.stderr)
                    self.assertIn("Temporary CI ref: deleted", prepared.stdout)
                    self.assertIn(main_sha, state.available_shas)
                    self.assertEqual(check_push(main_sha).returncode, 0)
                    self.assertIsNone(fixture.remote_ref_sha(f"refs/heads/ci/main/{main_sha}"))
                    self.assertEqual(fixture.remote_ref_sha("refs/heads/main"), remote_main)
                    reused = fixture.run_helper(fixture.main_helper, fixture.main, "prepare-main-ci")
                    self.assertEqual(reused.returncode, 0, reused.stderr)
                    self.assertIn("no temporary ref was published", reused.stdout)
                    newer = fixture.commit_main("later.txt", "later\n", "later change")
                    self.assertEqual(check_push(newer).returncode, 1)
                    self.assertEqual(fixture.output(fixture.remote, "show-ref"), refs_before_check)

    def test_octopus_and_missing_object_histories_block_before_api(self) -> None:
        parents = []
        for number in range(3):
            run_git(self.root, "switch", "--quiet", "-c", f"side-{number}", self.base_sha)
            item = self.root / f"item-{number}"
            item.write_text(str(number), encoding="utf-8")
            run_git(self.root, "add", item.name)
            run_git(self.root, "commit", "--quiet", "-m", f"side {number}")
            parents.append(self.rev_parse("HEAD"))
        tree = self.rev_parse("HEAD^{tree}")
        parent_args = [argument for sha in parents for argument in ("-p", sha)]
        octopus = run_git(self.root, "commit-tree", tree, *parent_args, "-m", "octopus").stdout.strip()
        for tip, remote, expected in (
            (octopus, self.base_sha, "octopus"),
            ("f" * 40, self.base_sha, "git"),
            (parents[0], "f" * 40, "git"),
        ):
            state = ApiState()
            with gitlab_api(state) as api_url:
                result = self.run_guard(self.record(tip, remote), self.policy(api_url))
            self.assertEqual(result.returncode, 1)
            self.assertIn(expected, result.stderr)
            self.assertEqual(state.requests, [])

    def test_common_dir_override_bypasses_policy_and_network(self) -> None:
        tip = self.commit("feature\n", "feature")
        common_dir = Path(
            run_git(
                self.root,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ).stdout.strip()
        )
        override = common_dir / "pipeline-guard.override"
        override.touch()
        result = self.run_guard(
            self.record(tip, self.base_sha), self.policy("http://127.0.0.1:1/api/v4")
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: bypassing", result.stderr)
        self.assertIn(str(override), result.stderr)

    def test_rebase_guard_blocks_only_unpushed_guarded_main(self) -> None:
        policy = self.policy("http://127.0.0.1:1/api/v4")
        run_git(
            self.root,
            "update-ref",
            f"refs/remotes/origin/{self.main_branch}",
            self.base_sha,
        )

        clean_main = self.run_rebase_check(policy)
        self.assertEqual(clean_main.returncode, 0, clean_main.stderr)

        run_git(self.root, "branch", "feature", self.base_sha)
        feature = self.run_rebase_check(policy, branch="feature")
        self.assertEqual(feature.returncode, 0, feature.stderr)

        self.commit("local main\n", "local main")
        override = self.root / ".git" / "pipeline-guard.override"
        override.touch()
        blocked = self.run_rebase_check(policy)
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("refusing to rebase guarded main", blocked.stderr)
        self.assertIn("exact-SHA CI evidence", blocked.stderr)
        self.assertIn("--no-verify", blocked.stderr)

    def test_rebase_guard_fails_closed_when_tracking_ref_is_missing(self) -> None:
        blocked = self.run_rebase_check(self.policy("http://127.0.0.1:1/api/v4"))
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("refs/remotes/origin/main is unavailable", blocked.stderr)

    def test_pre_rebase_hook_is_opt_in_and_uses_repo_checker(self) -> None:
        hook = self.root / ".git" / "hooks" / "pre-rebase"
        hook.write_bytes(PRE_REBASE_HOOK.read_bytes())
        hook.chmod(0o755)
        without_policy = subprocess.run(
            ["sh", str(hook), "origin/main"],
            cwd=self.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(without_policy.returncode, 0, without_policy.stderr)

        helper = self.root / "assets" / "check-gitlab-pipeline.py"
        helper.parent.mkdir(parents=True)
        shutil.copy2(HELPER, helper)
        helper.chmod(0o755)
        resolver = self.root / "assets" / "resolve-python3"
        shutil.copy2(PYTHON_RESOLVER, resolver)
        resolver.chmod(0o755)
        self.policy("http://127.0.0.1:1/api/v4")
        run_git(
            self.root,
            "update-ref",
            f"refs/remotes/origin/{self.main_branch}",
            self.base_sha,
        )
        self.commit("local main\n", "local main")
        (self.root / ".git" / "pipeline-guard.override").touch()

        blocked = subprocess.run(
            ["sh", str(hook), "origin/main"],
            cwd=self.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("refusing to rebase guarded main", blocked.stderr)

    def test_override_in_common_dir_applies_to_linked_worktree(self) -> None:
        policy = self.policy("http://127.0.0.1:1/api/v4")
        run_git(self.root, "add", "configs/gitlab-pipeline-guard.json")
        run_git(self.root, "commit", "--quiet", "-m", "add policy")
        linked = self.root.parent / "linked"
        run_git(
            self.root,
            "worktree",
            "add",
            "--quiet",
            "-b",
            "linked",
            str(linked),
        )
        self.addCleanup(
            lambda: run_git(
                self.root, "worktree", "remove", "--force", str(linked), check=False
            )
        )
        common_dir = Path(
            run_git(
                self.root,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ).stdout.strip()
        )
        (common_dir / "pipeline-guard.override").touch()

        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--repo-root",
                str(linked),
                "--policy",
                str(linked / policy.relative_to(self.root)),
                "origin",
                "unused",
            ],
            input="",
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"GIT_DIR": str(linked / ".git")},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(common_dir / "pipeline-guard.override"), result.stderr)

    def test_hook_is_opt_in_and_override_precedes_missing_helper(self) -> None:
        hook = self.root / ".git" / "hooks" / "pre-push"
        hook.write_bytes(PRE_PUSH_HOOK.read_bytes())
        hook.chmod(0o755)
        no_policy = subprocess.run(
            ["sh", str(hook), "origin", "unused"],
            cwd=self.root,
            input="",
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(no_policy.returncode, 0, no_policy.stderr)

        self.policy("http://127.0.0.1:1/api/v4")
        missing_helper = subprocess.run(
            ["sh", str(hook), "origin", "unused"],
            cwd=self.root,
            input="",
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(missing_helper.returncode, 1)
        self.assertIn("helper is missing", missing_helper.stderr)

        (self.root / ".git" / "pipeline-guard.override").touch()
        bypassed = subprocess.run(
            ["sh", str(hook), "origin", "unused"],
            cwd=self.root,
            input="",
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(bypassed.returncode, 0, bypassed.stderr)
        self.assertIn("WARNING: bypassing", bypassed.stderr)

    def test_pre_push_hook_rejects_broken_python3_and_uses_py(self) -> None:
        hook = self.root / ".git" / "hooks" / "pre-push"
        hook.write_bytes(PRE_PUSH_HOOK.read_bytes())
        hook.chmod(0o755)
        self.policy("http://127.0.0.1:1/api/v4")
        assets = self.root / "assets"
        assets.mkdir(exist_ok=True)
        write_executable(
            assets / "check-gitlab-pipeline.py",
            "#!/usr/bin/env python3\nraise SystemExit(0)\n",
        )
        resolver = assets / "resolve-python3"
        shutil.copy2(PYTHON_RESOLVER, resolver)
        resolver.chmod(0o755)

        fake_bin = self.root.parent / "fake-bin"
        fake_bin.mkdir()
        log = self.root.parent / "py.log"
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        write_executable(fake_bin / "git", f'#!/bin/sh\nexec "{real_git}" "$@"\n')
        write_executable(fake_bin / "python3", "#!/bin/sh\nexit 49\n")
        write_executable(
            fake_bin / "py",
            """#!/bin/sh
if [ "$1" = -3 ] && [ "$2" = -c ]; then exit 0; fi
printf '%s\n' "$*" > "$PIPELINE_GUARD_PY_LOG"
exit 0
""",
        )
        env = os.environ.copy()
        env["PATH"] = str(fake_bin)
        env["PIPELINE_GUARD_PY_LOG"] = str(log)
        result = subprocess.run(
            [shutil.which("sh") or "/bin/sh", str(hook), "origin", "unused"],
            cwd=self.root,
            input="",
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        invocation = log.read_text(encoding="utf-8")
        self.assertTrue(invocation.startswith("-3 "), invocation)
        self.assertIn("check-gitlab-pipeline.py", invocation)


if __name__ == "__main__":
    unittest.main()
