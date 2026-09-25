from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

from tests.support.fixtures import read_json, run_git, write_executable, write_json


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_HELPER = REPO_ROOT / "assets" / "agent-wt-merge"
SOURCE_RUNTIME = REPO_ROOT / "assets" / "gitlab_pipeline_runtime.py"
OPENCODE_BOT = "Co-authored-by: opencode-agent[bot] <opencode-agent[bot]@users.noreply.github.com>"
EXEC_ENV_KEYS = (
    "AGENT_WT_MERGE_EXEC_CHAIN",
    "AGENT_WT_MERGE_DELEGATED_FROM",
    "AGENT_WT_MERGE_REEXEC_AFTER_UPDATE",
)


def load_helper_module():
    name = "agent_wt_merge_test_module"
    loader = SourceFileLoader(name, str(SOURCE_HELPER))
    spec = spec_from_loader(name, loader)
    if spec is None:
        raise RuntimeError("could not create helper module spec")
    module = module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(SOURCE_HELPER.parent))
    try:
        loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class GitFixture:
    def __init__(self, *, feature_commit: bool = True, ci_gated: bool = True) -> None:
        self.ci_gated = ci_gated
        self._temporary = tempfile.TemporaryDirectory(prefix="agent wt merge ")
        self.root = Path(self._temporary.name).resolve()
        self.main = self.root / "main worktree"
        self.feature = self.root / "feature worktree"
        self.remote = self.root / "origin remote.git"
        self.fake_bin = self.root / "fake bin"
        self.bd_log = self.root / "bd log.json"
        self.git_config = self.root / "isolated gitconfig"
        self.git_config.write_text("", encoding="utf-8")
        self.env = os.environ.copy()
        self.env["GIT_CONFIG_GLOBAL"] = str(self.git_config)
        self.env["GIT_CONFIG_SYSTEM"] = os.devnull
        self.env["GIT_TERMINAL_PROMPT"] = "0"

        self.main.mkdir()
        self.git(self.main, "init", "-b", "main")
        self.git(self.main, "config", "user.name", "Test User")
        self.git(self.main, "config", "user.email", "test@example.com")
        self.install_helper(self.main)
        if ci_gated:
            self.install_pipeline_files(self.main)
        (self.main / "base.txt").write_text("base\n", encoding="utf-8")
        self.commit_all(self.main, "initial")

        self.git(self.root, "init", "--bare", "-b", "main", str(self.remote))
        self.git(self.main, "remote", "add", "origin", str(self.remote))
        self.git(self.main, "push", "-u", "origin", "main")
        self.git(self.main, "worktree", "add", "-b", "feature", str(self.feature))
        self.git(self.feature, "config", "user.name", "Test User")
        self.git(self.feature, "config", "user.email", "test@example.com")
        if feature_commit:
            self.commit_feature("feature.txt", "feature\n", "feature")
        elif ci_gated:
            self.publish_feature()
        self.set_override(ci_gated)

        self.fake_bin.mkdir()
        write_executable(
            self.fake_bin / "bd",
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

log_path = os.environ.get("FAKE_BD_LOG")
if log_path:
    Path(log_path).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
if os.environ.get("FAKE_BD_REMOVE_STATE"):
    Path(os.environ["FAKE_BD_REMOVE_STATE"]).unlink(missing_ok=True)
if os.environ.get("FAKE_BD_FAIL"):
    print("simulated bd failure", file=sys.stderr)
    raise SystemExit(17)
""",
        )

    @property
    def main_helper(self) -> Path:
        return self.main / "assets" / "agent-wt-merge"

    @property
    def feature_helper(self) -> Path:
        return self.feature / "assets" / "agent-wt-merge"

    def cleanup(self) -> None:
        self._temporary.cleanup()

    def git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run_git(cwd, *args, env=self.env, check=check)

    def output(self, cwd: Path, *args: str) -> str:
        return self.git(cwd, *args).stdout.strip()

    def install_helper(self, worktree: Path, text: str | None = None) -> Path:
        helper = worktree / "assets" / "agent-wt-merge"
        helper.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            shutil.copy2(SOURCE_HELPER, helper)
            if self.ci_gated:
                shutil.copy2(SOURCE_RUNTIME, helper.parent / SOURCE_RUNTIME.name)
        else:
            write_executable(helper, text)
        return helper

    def install_pipeline_files(self, worktree: Path) -> None:
        write_json(
            worktree / "configs" / "gitlab-pipeline-guard.json",
            {
                "$schema": "./schemas/gitlab-pipeline-guard.v1.schema.json",
                "schema_version": 1,
                "api_url": "http://127.0.0.1:1/api/v4",
                "project_id": 44618209,
                "guarded_remote": "origin",
                "guarded_ref": "refs/heads/main",
                "required_job": "linux-fast",
                "timeout_seconds": 1,
            },
        )
        write_executable(
            worktree / "assets" / "check-gitlab-pipeline.py",
            """#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

if "--check-sha" not in sys.argv:
    raise SystemExit(0)
sha = sys.argv[sys.argv.index("--check-sha") + 1]
repo = Path(sys.argv[sys.argv.index("--repo-root") + 1])
common = subprocess.run(
    ["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
    check=True,
    text=True,
    stdout=subprocess.PIPE,
).stdout.strip()
index = -1
if (Path(common) / "pipeline-guard.override").is_file():
    outcome = "bypass"
else:
    sequence = [item for item in os.environ.get("FAKE_PIPELINE_SEQUENCE", "").split(",") if item]
    if sequence:
        counter_path = Path(os.environ["FAKE_PIPELINE_COUNTER"])
        try:
            index = int(counter_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            index = 0
        counter_path.write_text(str(index + 1), encoding="utf-8")
        outcome = sequence[min(index, len(sequence) - 1)]
    else:
        outcome = os.environ.get("FAKE_PIPELINE_OUTCOME", "success")
move_to = os.environ.get("FAKE_PIPELINE_MOVE_FEATURE_TO")
if move_to:
    subprocess.run(
        ["git", "-C", str(repo), "push", "--force", "origin", f"{move_to}:refs/heads/feature"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
move_main_to = os.environ.get("FAKE_PIPELINE_MOVE_MAIN_TO")
if move_main_to:
    subprocess.run(
        ["git", "-C", str(repo), "push", "--force", "origin", f"{move_main_to}:refs/heads/main"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
move_main_ci_to = os.environ.get("FAKE_PIPELINE_MOVE_MAIN_CI_TO")
move_main_ci_on_index = os.environ.get("FAKE_PIPELINE_MOVE_MAIN_CI_ON_INDEX")
if move_main_ci_to and (not move_main_ci_on_index or move_main_ci_on_index == str(index)):
    subprocess.run(
        [
            "git", "-C", str(repo), "push", "--force", "origin",
            f"{move_main_ci_to}:refs/heads/ci/main/{sha}",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
payload = {
    "schema_version": 1,
    "outcome": outcome,
    "sha": sha,
    "required_job": None if outcome in {"bypass", "error"} else "linux-fast",
    "detail": f"fake {outcome}",
    "pipeline_url": "https://gitlab.example/pipeline/1" if outcome == "success" else None,
}
print(json.dumps(payload, sort_keys=True))
raise SystemExit(0 if outcome in {"success", "bypass"} else 1)
""",
        )

    def commit_all(self, cwd: Path, message: str) -> str:
        self.git(cwd, "add", "-A")
        self.git(cwd, "commit", "-m", message)
        return self.output(cwd, "rev-parse", "HEAD")

    def commit_feature(self, name: str, content: str, message: str) -> str:
        (self.feature / name).write_text(content, encoding="utf-8")
        sha = self.commit_all(self.feature, message)
        if self.ci_gated:
            self.publish_feature()
        return sha

    def commit_main(self, name: str, content: str, message: str) -> str:
        (self.main / name).write_text(content, encoding="utf-8")
        return self.commit_all(self.main, message)

    def publish_feature(self) -> None:
        self.git(self.feature, "push", "origin", "HEAD:refs/heads/feature")

    def remote_feature_sha(self) -> str | None:
        return self.remote_ref_sha("refs/heads/feature")

    def remote_ref_sha(self, remote_ref: str) -> str | None:
        result = self.git(
            self.main,
            "ls-remote",
            "--heads",
            "origin",
            remote_ref,
        )
        line = result.stdout.strip()
        return line.split()[0] if line else None

    def set_override(self, enabled: bool) -> Path:
        common_dir = Path(
            self.output(
                self.feature,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        )
        override = common_dir / "pipeline-guard.override"
        if enabled:
            override.touch()
        else:
            override.unlink(missing_ok=True)
        return override

    def run_helper(
        self,
        helper: Path,
        cwd: Path,
        *args: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = self.env.copy()
        for key in EXEC_ENV_KEYS:
            env.pop(key, None)
        env["PATH"] = str(self.fake_bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_BD_LOG"] = str(self.bd_log)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(helper), *args],
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def write_state(self, *, started_sha: str, issue_id: str = "dots-test") -> Path:
        state_path = self.feature / ".beads" / "in-progress-opencode.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            state_path,
            {
                "id": issue_id,
                "branch": "feature",
                "worktree_path": str(self.feature),
                "started_sha": started_sha,
            },
        )
        return state_path

    def write_ai_wt_session(self, session_id: str = "test-session") -> Path:
        metadata_path = self.main / ".ai-wt" / "sessions" / f"{session_id}.json"
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            metadata_path,
            {
                "session_id": session_id,
                "worktree_path": str(self.feature),
                "branch_created": True,
            },
        )
        return metadata_path

    def upstream_clone(self) -> Path:
        upstream = self.root / "upstream clone"
        self.git(self.root, "clone", str(self.remote), str(upstream))
        self.git(upstream, "config", "user.name", "Upstream User")
        self.git(upstream, "config", "user.email", "upstream@example.com")
        return upstream


class AgentWtMergeTests(unittest.TestCase):
    def fixture(self, *, feature_commit: bool = True, ci_gated: bool = True) -> GitFixture:
        fixture = GitFixture(feature_commit=feature_commit, ci_gated=ci_gated)
        self.addCleanup(fixture.cleanup)
        return fixture

    def assert_ok(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_feature_remote_ref_rejects_unsafe_branch(self) -> None:
        helper = load_helper_module()
        with self.assertRaisesRegex(helper.AgentWtMergeError, "unsafe for publication"):
            helper.feature_remote_ref("feature;ignore")

    def test_local_help_is_standalone_and_ignores_github_workflows_and_cwd(self) -> None:
        fixture = self.fixture(ci_gated=False)
        workflows = fixture.main / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "test.yml").write_text(
            "name: Test\non:\n  push:\n    branches: [main]\n"
            "  pull_request:\n    branches: [main]\n  workflow_dispatch:\n",
            encoding="utf-8",
        )
        fixture.install_pipeline_files(fixture.feature)
        secondary = fixture.main / ".opencode" / "bin" / "agent-wt-merge"
        secondary.parent.mkdir(parents=True)
        shutil.copy2(SOURCE_HELPER, secondary)
        for helper in (fixture.main_helper, secondary):
            for cwd in (fixture.feature, fixture.root):
                with self.subTest(helper=helper, cwd=cwd):
                    result = fixture.run_helper(helper, cwd, "--help")
                    self.assert_ok(result)
                    self.assertEqual(result.stdout.count("Commands:"), 1)
                    self.assertNotIn("prepare-ci", result.stdout)
                    self.assertNotIn("prepare-main-ci", result.stdout)
                    rows = result.stdout.split("Commands:\n")[1].splitlines()
                    self.assertEqual([row.split()[0] for row in rows], ["inspect", "ff", "no-ff"])

    def test_ci_help_matches_original_text_without_loading_runtime(self) -> None:
        fixture = self.fixture()
        (fixture.main / "assets" / SOURCE_RUNTIME.name).unlink()
        result = fixture.run_helper(fixture.main_helper, fixture.root, "--help")
        self.assert_ok(result)
        self.assertEqual(result.stdout, """Usage:
  ./assets/agent-wt-merge inspect [--fetch] [--json] [--use-local-helper]
  ./assets/agent-wt-merge prepare-ci [--poll-interval <seconds>] [--poll-timeout <seconds>] [--use-local-helper]
  ./assets/agent-wt-merge prepare-main-ci [--poll-interval <seconds>] [--poll-timeout <seconds>] [--use-local-helper]
  ./assets/agent-wt-merge ff --actor <opencode|claude> [--update-main] [--close-beads <issue-id>] [--use-local-helper]
  ./assets/agent-wt-merge no-ff --actor <opencode|claude> -m <subject> [-m <body>] [--update-main] [--close-beads <issue-id>] [--use-local-helper]

Commands:
  inspect  Report merge target facts and cleanup suggestions.
  prepare-ci  Publish the exact feature tip and wait for required CI.
  prepare-main-ci  Publish an unpushed main tip to a temporary ref and wait for CI.
  ff       Fast-forward main/master to the current feature branch.
  no-ff    Create a no-ff merge commit on main/master.
""")

    def test_broken_ci_configuration_never_falls_back_to_local(self) -> None:
        for broken in ("json", "runtime", "checker", "symlink", "directory", "configs-file", "configs-symlink"):
            with self.subTest(broken=broken):
                fixture = self.fixture()
                policy = fixture.main / "configs" / "gitlab-pipeline-guard.json"
                if broken == "json":
                    policy.write_text("{", encoding="utf-8")
                elif broken in {"runtime", "checker"}:
                    name = SOURCE_RUNTIME.name if broken == "runtime" else "check-gitlab-pipeline.py"
                    (fixture.main / "assets" / name).unlink()
                else:
                    policy.unlink()
                    if broken == "symlink":
                        policy.symlink_to("missing-policy")
                    elif broken == "directory":
                        policy.mkdir()
                    else:
                        policy.parent.rmdir()
                        if broken == "configs-file":
                            policy.parent.write_text("invalid", encoding="utf-8")
                        else:
                            policy.parent.symlink_to("missing-configs", target_is_directory=True)
                help_result = fixture.run_helper(fixture.main_helper, fixture.root, "--help")
                if broken.startswith("configs-"):
                    self.assertNotEqual(help_result.returncode, 0)
                else:
                    self.assert_ok(help_result)
                    self.assertIn("  prepare-ci  ", help_result.stdout)
                fixture.commit_all(fixture.main, "configure invalid CI")
                before = fixture.output(fixture.main, "rev-parse", "HEAD")
                refs = fixture.output(fixture.remote, "show-ref")
                result = fixture.run_helper(fixture.main_helper, fixture.feature, "no-ff", "--actor", "opencode", "-m", "blocked")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), before)
                self.assertEqual(fixture.output(fixture.remote, "show-ref"), refs)

    def test_local_ff_and_no_ff_allow_best_effort_fetch_without_remote_mutation(self) -> None:
        for merge_type in ("ff", "no-ff"):
            for remote in ("online", "offline", "absent"):
                with self.subTest(merge_type=merge_type, remote=remote):
                    fixture = self.fixture(ci_gated=False)
                    self.assertIsNone(fixture.remote_feature_sha())
                    feature_sha = fixture.output(fixture.feature, "rev-parse", "HEAD")
                    if merge_type == "no-ff":
                        fixture.commit_main("main-only", "main\n", "local main")
                    if remote == "offline":
                        fixture.git(fixture.main, "remote", "set-url", "origin", str(fixture.root / "missing-remote"))
                    elif remote == "absent":
                        fixture.git(fixture.main, "remote", "remove", "origin")
                    refs = fixture.output(fixture.remote, "show-ref")
                    inspected = fixture.run_helper(fixture.main_helper, fixture.feature, "inspect", "--fetch", "--json")
                    self.assert_ok(inspected)
                    facts = json.loads(inspected.stdout)
                    self.assertFalse(facts["main_dirty"])
                    self.assertGreater(facts["feature"]["commits_ahead"], 0)
                    self.assertEqual(facts["feature"]["fast_forward_possible"], merge_type == "ff")
                    args = [merge_type, "--actor", "opencode"]
                    if merge_type == "no-ff":
                        args.extend(["-m", "land local feature"])
                    result = fixture.run_helper(fixture.main_helper, fixture.feature, *args)
                    self.assert_ok(result)
                    self.assertIn("Merge mode: local-only", result.stdout)
                    self.assertIn("Feature CI: not performed", result.stdout)
                    self.assertIn("Feature publication: not performed", result.stdout)
                    self.assertIn("Remote feature cleanup: not performed", result.stdout)
                    self.assertNotIn("prepare-main-ci", result.stdout)
                    if remote != "online":
                        self.assertIn("Fetch warning:", result.stdout)
                    self.assertEqual(fixture.output(fixture.remote, "show-ref"), refs)
                    tip = "HEAD" if merge_type == "ff" else "HEAD^2"
                    self.assertEqual(fixture.output(fixture.main, "rev-parse", tip), feature_sha)
                    if merge_type == "no-ff":
                        self.assertEqual(fixture.output(fixture.main, "log", "-1", "--format=%an <%ae>"), "OpenCode <noreply@opencode.ai>")
                        self.assertIn(OPENCODE_BOT, fixture.output(fixture.main, "log", "-1", "--format=%B"))
                    else:
                        self.assertNotIn(OPENCODE_BOT, fixture.output(fixture.main, "log", "-1", "--format=%B"))

    def test_no_ff_opencode_coauthor_preserves_message_and_existing_trailers(self) -> None:
        for actor in ("opencode", "claude"):
            for preexisting in (False, True):
                with self.subTest(actor=actor, preexisting=preexisting):
                    fixture = self.fixture(ci_gated=False)
                    fixture.commit_main("main-only", "main\n", "local main")
                    fixture.git(fixture.main, "config", "trailer.Co-authored-by.ifexists", "replace")
                    fixture.git(fixture.main, "config", "trailer.Co-authored-by.where", "end")
                    fixture.git(fixture.main, "config", "trailer.Co-authored-by.cmd", "printf hostile")
                    body = "Body paragraph\n\nRefs: dots-test\nCo-authored-by: Teammate <teammate@example.com>"
                    if preexisting:
                        body += "\n" + OPENCODE_BOT
                    body += "\nAI-Participant: tool=editor\nSource-Digest: sha256:abc"
                    result = fixture.run_helper(
                        fixture.main_helper, fixture.feature, "no-ff", "--actor", actor,
                        "-m", "land feature", "-m", body,
                    )
                    self.assert_ok(result)
                    message = fixture.output(fixture.main, "log", "-1", "--format=%B")
                    self.assertTrue(message.startswith("land feature\n\nBody paragraph\n\n"))
                    self.assertEqual(message.count(OPENCODE_BOT), 1 if actor == "opencode" or preexisting else 0)
                    parsed = subprocess.run(
                        ["git", "interpret-trailers", "--parse"],
                        cwd=fixture.main, env=fixture.env, input=message,
                        check=True, text=True, stdout=subprocess.PIPE,
                    ).stdout.splitlines()
                    self.assertIn("Co-authored-by: Teammate <teammate@example.com>", parsed)
                    self.assertEqual(parsed[-2:], ["AI-Participant: tool=editor", "Source-Digest: sha256:abc"])
                    if actor == "opencode" and not preexisting:
                        self.assertEqual(parsed[0], OPENCODE_BOT)
                    expected = "OpenCode <noreply@opencode.ai>" if actor == "opencode" else "Claude <noreply@anthropic.com>"
                    self.assertEqual(fixture.output(fixture.main, "log", "-1", "--format=%an <%ae>|%cn <%ce>"),
                                     f"{expected}|{expected}")

    def test_no_ff_trailer_format_failure_does_not_merge(self) -> None:
        fixture = self.fixture(ci_gated=False)
        fixture.commit_main("main-only", "main\n", "local main")
        before = fixture.output(fixture.main, "rev-parse", "HEAD")
        real_git = shutil.which("git")
        self.assertIsNotNone(real_git)
        write_executable(
            fixture.fake_bin / "git",
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "if 'interpret-trailers' in sys.argv:\n"
            "    print('simulated formatter failure', file=sys.stderr)\n"
            "    raise SystemExit(23)\n"
            f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n",
        )
        result = fixture.run_helper(
            fixture.main_helper, fixture.feature, "no-ff", "--actor", "opencode", "-m", "land feature",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("simulated formatter failure", result.stderr)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), before)

    def test_local_mode_retains_published_feature_and_closes_only_matching_beads(self) -> None:
        for state_kind in ("matching", "mismatched", "failure", "unlink-failure"):
            with self.subTest(state_kind=state_kind):
                fixture = self.fixture(ci_gated=False)
                fixture.publish_feature()
                refs = fixture.output(fixture.remote, "show-ref")
                started = fixture.output(fixture.main, "rev-parse", "HEAD")
                state = fixture.write_state(started_sha=started)
                fixture.git(fixture.main, "worktree", "lock", "--reason", "AoE-managed", str(fixture.feature))
                env = {}
                if state_kind == "mismatched":
                    payload = read_json(state)
                    payload["branch"] = "another-feature"
                    write_json(state, payload)
                elif state_kind == "failure":
                    env["FAKE_BD_FAIL"] = "1"
                elif state_kind == "unlink-failure":
                    env["FAKE_BD_REMOVE_STATE"] = str(state)
                result = fixture.run_helper(fixture.main_helper, fixture.feature, "ff", "--actor", "opencode", "--close-beads", "dots-test", extra_env=env)
                self.assertEqual(result.returncode, 0 if state_kind == "matching" else 1, result.stderr)
                self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), fixture.output(fixture.feature, "rev-parse", "HEAD"))
                self.assertEqual(fixture.output(fixture.remote, "show-ref"), refs)
                self.assertEqual(state.exists(), state_kind in {"mismatched", "failure"})
                self.assertIn("aoe", result.stdout.lower())
                if state_kind == "mismatched":
                    self.assertFalse(fixture.bd_log.exists())

    def test_local_ci_commands_fail_before_mutation(self) -> None:
        fixture = self.fixture(ci_gated=False)
        refs = fixture.output(fixture.remote, "show-ref")
        before = fixture.output(fixture.main, "rev-parse", "HEAD")
        for command, cwd in (("prepare-ci", fixture.feature), ("prepare-main-ci", fixture.main)):
            result = fixture.run_helper(fixture.main_helper, cwd, command)
            self.assertEqual(result.returncode, 2)
            self.assertIn("unsupported", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), before)
        self.assertEqual(fixture.output(fixture.remote, "show-ref"), refs)

    def test_local_merges_retain_preconditions(self) -> None:
        for condition in ("dirty", "detached", "no-commits", "wrong-type", "behind", "diverged"):
            with self.subTest(condition=condition):
                fixture = self.fixture(ci_gated=False, feature_commit=condition != "no-commits")
                args = ["ff", "--actor", "opencode"]
                if condition == "dirty":
                    (fixture.main / "base.txt").write_text("dirty\n", encoding="utf-8")
                elif condition == "detached":
                    fixture.git(fixture.feature, "switch", "--detach")
                elif condition == "wrong-type":
                    args = ["no-ff", "--actor", "opencode", "-m", "wrong"]
                elif condition in {"behind", "diverged"}:
                    upstream = fixture.upstream_clone()
                    (upstream / "upstream.txt").write_text("upstream\n", encoding="utf-8")
                    fixture.commit_all(upstream, "upstream")
                    fixture.git(upstream, "push", "origin", "main")
                    if condition == "diverged":
                        fixture.commit_main("local.txt", "local\n", "local main")
                        args.append("--update-main")
                before = fixture.output(fixture.main, "rev-parse", "HEAD")
                refs = fixture.output(fixture.remote, "show-ref")
                result = fixture.run_helper(fixture.main_helper, fixture.feature, *args)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), before)
                self.assertEqual(fixture.output(fixture.remote, "show-ref"), refs)

    def test_local_update_reexecutes_and_mode_changes_stop_before_feature_merge(self) -> None:
        for initial_ci, change_mode in ((False, False), (False, True), (True, True)):
            with self.subTest(initial_ci=initial_ci, change_mode=change_mode):
                fixture = self.fixture(ci_gated=initial_ci)
                upstream = fixture.upstream_clone()
                if change_mode:
                    if initial_ci:
                        fixture.git(upstream, "rm", "configs/gitlab-pipeline-guard.json")
                    else:
                        fixture.install_pipeline_files(upstream)
                        shutil.copy2(SOURCE_RUNTIME, upstream / "assets" / SOURCE_RUNTIME.name)
                (upstream / "upstream.txt").write_text("upstream\n", encoding="utf-8")
                upstream_sha = fixture.commit_all(upstream, "update main contract")
                fixture.git(upstream, "push", "origin", "main")
                result = fixture.run_helper(fixture.main_helper, fixture.feature, "no-ff", "--actor", "opencode", "-m", "land feature", "--update-main")
                if change_mode:
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("helper mode changed", result.stderr)
                    self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), upstream_sha)
                else:
                    self.assert_ok(result)
                    self.assertIn("Re-executed after main update: yes", result.stdout)
                    self.assertIn("Merge mode: local-only", result.stdout)

    def test_delegation_override_and_fallback_select_helper_source_policy(self) -> None:
        for selection in ("delegated", "override", "fallback"):
            with self.subTest(selection=selection):
                fixture = self.fixture(ci_gated=False)
                fixture.install_pipeline_files(fixture.feature)
                args = ["prepare-ci"]
                if selection == "override":
                    args.append("--use-local-helper")
                elif selection == "fallback":
                    fixture.git(fixture.main, "rm", "assets/agent-wt-merge")
                    fixture.commit_all(fixture.main, "remove main helper")
                result = fixture.run_helper(fixture.feature_helper, fixture.feature, *args)
                self.assertEqual(result.returncode, 2)
                expected = "unsupported" if selection == "delegated" else "cannot load GitLab pipeline runtime"
                self.assertIn(expected, result.stderr)

    def test_main_helper_uses_feature_cwd_with_spaces(self) -> None:
        fixture = self.fixture()
        result = fixture.run_helper(fixture.main_helper, fixture.feature, "inspect", "--json")
        self.assert_ok(result)
        report = json.loads(result.stdout)
        self.assertEqual(report["original_worktree"], str(fixture.feature))
        self.assertEqual(report["feature_branch"], "feature")
        self.assertEqual(report["main_worktree"], str(fixture.main))
        self.assertEqual(report["helper"]["path"], str(fixture.main_helper))
        self.assertEqual(report["helper"]["state"], "canonical")
        self.assertEqual(
            report["feature_worktree"],
            {"locked": False, "lock_reason": None},
        )
        self.assertEqual(report["cleanup"]["action"], "suggest")
        self.assertEqual(report["cleanup"]["manager"], "git")

    def test_aoe_lock_defers_cleanup_and_preserves_reason(self) -> None:
        fixture = self.fixture()
        reason = "aoe-managed worktree (prevents cross-boundary prune)"
        fixture.git(fixture.main, "worktree", "lock", "--reason", reason, str(fixture.feature))

        result = fixture.run_helper(fixture.main_helper, fixture.feature, "inspect", "--json")
        self.assert_ok(result)
        report = json.loads(result.stdout)
        self.assertEqual(
            report["feature_worktree"],
            {"locked": True, "lock_reason": reason},
        )
        self.assertEqual(report["cleanup"]["action"], "defer")
        self.assertEqual(report["cleanup"]["manager"], "aoe")
        self.assertEqual(report["cleanup"]["commands"], [])

    def test_non_aoe_lock_remains_visible_without_deferral(self) -> None:
        fixture = self.fixture()
        reason = "maintenance lock"
        fixture.git(fixture.main, "worktree", "lock", "--reason", reason, str(fixture.feature))

        result = fixture.run_helper(fixture.main_helper, fixture.feature, "inspect", "--json")
        self.assert_ok(result)
        report = json.loads(result.stdout)
        self.assertEqual(report["feature_worktree"]["lock_reason"], reason)
        self.assertEqual(report["cleanup"]["action"], "suggest")
        self.assertEqual(report["cleanup"]["manager"], "git")

    def test_ai_wt_cleanup_differs_by_platform(self) -> None:
        helper = load_helper_module()
        worktree = helper.WorktreeEntry(path=Path("/tmp/feature"))
        session = {"session_id": "test-session"}
        arguments = {
            "main_worktree": Path("/tmp/main"),
            "original_worktree": worktree.path,
            "feature_branch": "feature",
            "feature_worktree": worktree,
            "ai_wt_session": session,
        }

        windows = helper.cleanup_policy(**arguments, is_windows=True)
        self.assertEqual(windows["action"], "defer")
        self.assertEqual(windows["manager"], "ai-wt")
        self.assertEqual(windows["commands"], [])

        posix = helper.cleanup_policy(**arguments, is_windows=False)
        self.assertEqual(posix["action"], "suggest")
        self.assertEqual(posix["manager"], "ai-wt")
        self.assertEqual(posix["commands"], ["ai-wt cleanup test-session --delete --yes"])

        unmanaged_windows = helper.cleanup_policy(
            **{**arguments, "ai_wt_session": None},
            is_windows=True,
        )
        self.assertEqual(unmanaged_windows["action"], "defer")
        self.assertEqual(unmanaged_windows["manager"], "user")
        self.assertEqual(unmanaged_windows["commands"], [])

    def test_feature_helper_delegates_to_main(self) -> None:
        fixture = self.fixture()
        result = fixture.run_helper(fixture.feature_helper, fixture.feature, "inspect", "--json")
        self.assert_ok(result)
        report = json.loads(result.stdout)
        self.assertEqual(report["helper"]["state"], "delegated")
        self.assertEqual(report["helper"]["path"], str(fixture.main_helper))
        self.assertEqual(report["helper"]["delegated_from"], str(fixture.feature_helper))

    def test_main_wrapper_is_secondary_canonical_candidate(self) -> None:
        fixture = self.fixture()
        wrapper = fixture.main / ".opencode" / "bin" / "agent-wt-merge"
        wrapper.parent.mkdir(parents=True)
        fixture.main_helper.replace(wrapper)
        fixture.commit_all(fixture.main, "move helper to wrapper")

        result = fixture.run_helper(fixture.feature_helper, fixture.feature, "inspect", "--json")
        self.assert_ok(result)
        report = json.loads(result.stdout)
        self.assertEqual(report["helper"]["state"], "delegated")
        self.assertEqual(report["helper"]["path"], str(wrapper))
        self.assertEqual(report["helper"]["canonical_path"], str(wrapper))

    def test_non_executable_main_helper_does_not_fallback(self) -> None:
        fixture = self.fixture()
        fixture.main_helper.chmod(0o644)

        result = fixture.run_helper(fixture.feature_helper, fixture.feature, "inspect", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("main helper exists but is not an executable file", result.stderr)

    def test_missing_main_helper_is_visible_fallback(self) -> None:
        fixture = self.fixture()
        fixture.git(fixture.main, "rm", "assets/agent-wt-merge")
        fixture.git(fixture.main, "commit", "-m", "remove helper")
        result = fixture.run_helper(fixture.feature_helper, fixture.feature, "inspect", "--json")
        self.assert_ok(result)
        report = json.loads(result.stdout)
        self.assertEqual(report["helper"]["state"], "fallback")
        self.assertIsNone(report["helper"]["canonical_path"])
        self.assertEqual(report["helper"]["path"], str(fixture.feature_helper))

    def test_explicit_override_stays_local(self) -> None:
        fixture = self.fixture()
        result = fixture.run_helper(
            fixture.feature_helper,
            fixture.feature,
            "inspect",
            "--json",
            "--use-local-helper",
        )
        self.assert_ok(result)
        report = json.loads(result.stdout)
        self.assertEqual(report["helper"]["state"], "override")
        self.assertEqual(report["helper"]["path"], str(fixture.feature_helper))
        self.assertEqual(report["helper"]["canonical_path"], str(fixture.main_helper))

    def test_explicit_override_bypasses_non_executable_main_helper(self) -> None:
        fixture = self.fixture()
        fixture.main_helper.chmod(0o644)

        result = fixture.run_helper(
            fixture.feature_helper,
            fixture.feature,
            "inspect",
            "--json",
            "--use-local-helper",
        )
        self.assert_ok(result)
        report = json.loads(result.stdout)
        self.assertEqual(report["helper"]["state"], "override")
        self.assertEqual(report["helper"]["path"], str(fixture.feature_helper))
        self.assertIsNone(report["helper"]["canonical_path"])

    def test_dirty_main_helper_stops_before_delegation(self) -> None:
        fixture = self.fixture()
        with fixture.main_helper.open("a", encoding="utf-8") as stream:
            stream.write("\n# dirty\n")
        result = fixture.run_helper(fixture.feature_helper, fixture.feature, "inspect", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("main helper path is dirty", result.stderr)

    def test_delegation_loop_is_rejected(self) -> None:
        fixture = self.fixture()
        result = fixture.run_helper(
            fixture.feature_helper,
            fixture.feature,
            "inspect",
            "--json",
            extra_env={"AGENT_WT_MERGE_EXEC_CHAIN": json.dumps([str(fixture.main_helper)])},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("delegation loop", result.stderr)

    def test_prepare_ci_publishes_exact_tip_and_polls_until_success(self) -> None:
        fixture = self.fixture()
        fixture.set_override(False)
        fixture.git(fixture.feature, "push", "origin", ":refs/heads/feature")
        feature_sha = fixture.output(fixture.feature, "rev-parse", "HEAD")
        main_sha = fixture.output(fixture.main, "rev-parse", "HEAD")
        counter = fixture.root / "pipeline counter"

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "prepare-ci",
            "--poll-interval",
            "1",
            "--poll-timeout",
            "3",
            extra_env={
                "FAKE_PIPELINE_SEQUENCE": "retryable,success",
                "FAKE_PIPELINE_COUNTER": str(counter),
            },
        )

        self.assert_ok(result)
        self.assertEqual(fixture.remote_feature_sha(), feature_sha)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), main_sha)
        self.assertEqual(counter.read_text(encoding="utf-8"), "2")
        self.assertIn("Waiting for linux-fast", result.stdout)
        self.assertIn(f"PASS feature CI: linux-fast succeeded for {feature_sha}", result.stdout)

    def test_prepare_ci_terminal_and_timeout_leave_main_untouched(self) -> None:
        for outcome, timeout, expected in (
            ("terminal", "3", "feature CI blocked"),
            ("error", "3", "fake error"),
            ("retryable", "1", "timed out after 1s"),
        ):
            with self.subTest(outcome=outcome):
                fixture = self.fixture()
                fixture.set_override(False)
                fixture.git(fixture.feature, "push", "origin", ":refs/heads/feature")
                feature_sha = fixture.output(fixture.feature, "rev-parse", "HEAD")
                main_sha = fixture.output(fixture.main, "rev-parse", "HEAD")
                result = fixture.run_helper(
                    fixture.main_helper,
                    fixture.feature,
                    "prepare-ci",
                    "--poll-interval",
                    "1",
                    "--poll-timeout",
                    timeout,
                    extra_env={"FAKE_PIPELINE_OUTCOME": outcome},
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn(expected, result.stderr)
                self.assertEqual(fixture.remote_feature_sha(), feature_sha)
                self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), main_sha)

    def test_prepare_ci_rejects_more_than_sixty_checks_before_publication(self) -> None:
        fixture = self.fixture()
        fixture.git(fixture.feature, "push", "origin", ":refs/heads/feature")
        main_sha = fixture.output(fixture.main, "rev-parse", "HEAD")

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "prepare-ci",
            "--poll-interval",
            "1",
            "--poll-timeout",
            "61",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("more than 60 checks", result.stderr)
        self.assertIsNone(fixture.remote_feature_sha())
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), main_sha)

    def test_prepare_main_ci_reuses_existing_success_without_publication(self) -> None:
        fixture = self.fixture()
        fixture.set_override(False)
        main_sha = fixture.commit_main("main.txt", "main\n", "local main")
        remote_main = fixture.remote_ref_sha("refs/heads/main")
        remote_ref = f"refs/heads/ci/main/{main_sha}"

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.main,
            "prepare-main-ci",
        )

        self.assert_ok(result)
        self.assertEqual(fixture.remote_ref_sha("refs/heads/main"), remote_main)
        self.assertIsNone(fixture.remote_ref_sha(remote_ref))
        self.assertIn("already succeeded", result.stdout)
        self.assertIn("no temporary ref was published", result.stdout)

    def test_prepare_main_ci_publishes_polls_and_deletes_exact_temp_ref(self) -> None:
        fixture = self.fixture()
        fixture.set_override(False)
        main_sha = fixture.commit_main("main.txt", "main\n", "local main")
        remote_main = fixture.remote_ref_sha("refs/heads/main")
        remote_ref = f"refs/heads/ci/main/{main_sha}"
        counter = fixture.root / "main pipeline counter"

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.main,
            "prepare-main-ci",
            "--poll-interval",
            "1",
            "--poll-timeout",
            "3",
            extra_env={
                "FAKE_PIPELINE_SEQUENCE": "retryable,success",
                "FAKE_PIPELINE_COUNTER": str(counter),
            },
        )

        self.assert_ok(result)
        self.assertEqual(counter.read_text(encoding="utf-8"), "2")
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), main_sha)
        self.assertEqual(fixture.remote_ref_sha("refs/heads/main"), remote_main)
        self.assertIsNone(fixture.remote_ref_sha(remote_ref))
        self.assertIn("Published rewritten main", result.stdout)
        self.assertIn("Temporary CI ref: deleted", result.stdout)
        self.assertIn("Main/tags pushed: no", result.stdout)

    def test_prepare_main_ci_failure_and_bypass_retain_temp_ref(self) -> None:
        for override, outcome, expected in (
            (False, "terminal", "main CI blocked"),
            (False, "error", "fake error"),
            (False, "retryable", "timed out after 1s"),
            (True, "success", "BYPASS main CI"),
        ):
            with self.subTest(override=override, outcome=outcome):
                fixture = self.fixture()
                fixture.set_override(override)
                main_sha = fixture.commit_main("main.txt", "main\n", "local main")
                remote_main = fixture.remote_ref_sha("refs/heads/main")
                remote_ref = f"refs/heads/ci/main/{main_sha}"
                result = fixture.run_helper(
                    fixture.main_helper,
                    fixture.main,
                    "prepare-main-ci",
                    "--poll-interval",
                    "1",
                    "--poll-timeout",
                    "1",
                    extra_env={"FAKE_PIPELINE_OUTCOME": outcome},
                )

                if override:
                    self.assert_ok(result)
                    self.assertIn(expected, result.stdout)
                else:
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(expected, result.stderr)
                self.assertEqual(fixture.remote_ref_sha(remote_ref), main_sha)
                self.assertEqual(fixture.remote_ref_sha("refs/heads/main"), remote_main)

    def test_prepare_main_ci_rejects_invalid_main_history_before_publication(self) -> None:
        cases = ("no-ahead", "diverged")
        for case in cases:
            with self.subTest(case=case):
                fixture = self.fixture()
                fixture.set_override(False)
                if case == "diverged":
                    main_sha = fixture.commit_main("main.txt", "local\n", "local main")
                    upstream = fixture.upstream_clone()
                    (upstream / "upstream.txt").write_text("upstream\n", encoding="utf-8")
                    fixture.commit_all(upstream, "upstream main")
                    fixture.git(upstream, "push", "origin", "main")
                    expected = "behind origin/main"
                else:
                    main_sha = fixture.output(fixture.main, "rev-parse", "HEAD")
                    expected = "no unpushed commits"
                remote_ref = f"refs/heads/ci/main/{main_sha}"

                result = fixture.run_helper(
                    fixture.main_helper,
                    fixture.main,
                    "prepare-main-ci",
                )

                self.assertEqual(result.returncode, 2)
                self.assertIn(expected, result.stderr)
                self.assertIsNone(fixture.remote_ref_sha(remote_ref))

    def test_prepare_main_ci_retains_temp_ref_when_remote_main_moves(self) -> None:
        fixture = self.fixture()
        fixture.set_override(False)
        main_sha = fixture.commit_main("main.txt", "local\n", "local main")
        moved_sha = fixture.output(fixture.feature, "rev-parse", "HEAD")
        remote_ref = f"refs/heads/ci/main/{main_sha}"
        counter = fixture.root / "move pipeline counter"

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.main,
            "prepare-main-ci",
            "--poll-interval",
            "1",
            "--poll-timeout",
            "3",
            extra_env={
                "FAKE_PIPELINE_SEQUENCE": "retryable,success",
                "FAKE_PIPELINE_COUNTER": str(counter),
                "FAKE_PIPELINE_MOVE_MAIN_TO": moved_sha,
            },
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("origin/main moved while CI was checked", result.stderr)
        self.assertEqual(fixture.remote_ref_sha(remote_ref), main_sha)
        self.assertEqual(fixture.remote_ref_sha("refs/heads/main"), moved_sha)

    def test_prepare_main_ci_does_not_delete_moved_temp_ref_after_success(self) -> None:
        fixture = self.fixture()
        fixture.set_override(False)
        main_sha = fixture.commit_main("main.txt", "local\n", "local main")
        moved_sha = fixture.output(fixture.feature, "rev-parse", "HEAD")
        remote_ref = f"refs/heads/ci/main/{main_sha}"
        counter = fixture.root / "move temp pipeline counter"

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.main,
            "prepare-main-ci",
            "--poll-interval",
            "1",
            "--poll-timeout",
            "3",
            extra_env={
                "FAKE_PIPELINE_SEQUENCE": "retryable,success",
                "FAKE_PIPELINE_COUNTER": str(counter),
                "FAKE_PIPELINE_MOVE_MAIN_CI_TO": moved_sha,
                "FAKE_PIPELINE_MOVE_MAIN_CI_ON_INDEX": "1",
            },
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("PARTIAL main CI", result.stdout)
        self.assertIn("temporary CI ref moved", result.stdout)
        self.assertEqual(fixture.remote_ref_sha(remote_ref), moved_sha)

    def test_prepare_main_ci_rejects_excessive_checks_before_publication(self) -> None:
        fixture = self.fixture()
        main_sha = fixture.commit_main("main.txt", "main\n", "local main")
        remote_ref = f"refs/heads/ci/main/{main_sha}"

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.main,
            "prepare-main-ci",
            "--poll-interval",
            "1",
            "--poll-timeout",
            "61",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("more than 60 checks", result.stderr)
        self.assertIsNone(fixture.remote_ref_sha(remote_ref))

    def test_merge_requires_exact_remote_feature_before_main_mutation(self) -> None:
        fixture = self.fixture()
        fixture.set_override(False)
        fixture.git(fixture.feature, "push", "origin", ":refs/heads/feature")
        main_sha = fixture.output(fixture.main, "rev-parse", "HEAD")

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "ff",
            "--actor",
            "opencode",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("origin/feature is absent", result.stderr)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), main_sha)

    def test_successful_ff_deletes_exact_remote_feature(self) -> None:
        fixture = self.fixture()
        fixture.set_override(False)
        feature_sha = fixture.output(fixture.feature, "rev-parse", "HEAD")

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "ff",
            "--actor",
            "opencode",
        )

        self.assert_ok(result)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), feature_sha)
        self.assertIsNone(fixture.remote_feature_sha())
        self.assertIn("Feature CI: success", result.stdout)
        self.assertIn("Remote feature cleanup: deleted", result.stdout)
        self.assertIn("Main/tags pushed: no", result.stdout)

    def test_bypass_merge_retains_remote_feature(self) -> None:
        fixture = self.fixture()
        feature_sha = fixture.output(fixture.feature, "rev-parse", "HEAD")

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "ff",
            "--actor",
            "opencode",
        )

        self.assert_ok(result)
        self.assertEqual(fixture.remote_feature_sha(), feature_sha)
        self.assertIn("Feature CI: bypass", result.stdout)
        self.assertIn("Remote feature cleanup: retained", result.stdout)

    def test_remote_feature_move_is_post_merge_partial_failure(self) -> None:
        fixture = self.fixture()
        fixture.set_override(False)
        feature_sha = fixture.output(fixture.feature, "rev-parse", "HEAD")
        main_sha = fixture.output(fixture.main, "rev-parse", "HEAD")

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "ff",
            "--actor",
            "opencode",
            extra_env={"FAKE_PIPELINE_MOVE_FEATURE_TO": main_sha},
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), feature_sha)
        self.assertEqual(fixture.remote_feature_sha(), main_sha)
        self.assertIn("exact-lease deletion was not attempted", result.stdout)
        self.assertIn("lease-protected remote feature cleanup did not complete", result.stdout)

    def test_no_ff_allows_local_main_ahead_of_advertised_main(self) -> None:
        fixture = self.fixture()
        fixture.set_override(False)
        (fixture.main / "main-only.txt").write_text("main only\n", encoding="utf-8")
        main_sha = fixture.commit_all(fixture.main, "main only")

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "no-ff",
            "--actor",
            "opencode",
            "-m",
            "merge feature",
        )

        self.assert_ok(result)
        self.assertIn("Main push requires successful exact-SHA CI", result.stdout)
        self.assertIn("At batch end, run prepare-main-ci", result.stdout)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD^1"), main_sha)
        self.assertIsNone(fixture.remote_feature_sha())

    def test_ff_allows_local_main_ahead_when_feature_contains_it(self) -> None:
        fixture = self.fixture(feature_commit=False)
        fixture.set_override(False)
        (fixture.main / "main-only.txt").write_text("main only\n", encoding="utf-8")
        fixture.commit_all(fixture.main, "main only")
        fixture.git(fixture.feature, "merge", "--ff-only", "main")
        feature_sha = fixture.commit_feature("feature.txt", "feature\n", "feature")

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "ff",
            "--actor",
            "opencode",
        )

        self.assert_ok(result)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), feature_sha)
        self.assertIsNone(fixture.remote_feature_sha())

    def test_successful_no_ff_deletes_remote_feature(self) -> None:
        fixture = self.fixture()
        fixture.set_override(False)
        (fixture.main / "main-only.txt").write_text("main only\n", encoding="utf-8")
        fixture.commit_all(fixture.main, "main only")
        fixture.git(fixture.main, "push", "origin", "main")

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "no-ff",
            "--actor",
            "opencode",
            "-m",
            "merge feature",
        )

        self.assert_ok(result)
        self.assertEqual(fixture.output(fixture.main, "log", "-1", "--format=%s"), "merge feature")
        self.assertIsNone(fixture.remote_feature_sha())
        self.assertIn("Merge type: no-ff", result.stdout)

    def test_update_main_reexecutes_updated_helper_before_merge(self) -> None:
        fixture = self.fixture()
        upstream = fixture.upstream_clone()
        marker = fixture.root / "updated helper ran"
        updated = SOURCE_HELPER.read_text(encoding="utf-8").replace(
            "from __future__ import annotations\n",
            "from __future__ import annotations\n\n"
            "import os as _marker_os\n"
            "_marker_path = _marker_os.environ.get('AGENT_WT_MERGE_TEST_MARKER')\n"
            "if _marker_path:\n"
            "    with open(_marker_path, 'a', encoding='utf-8') as _marker_stream:\n"
            "        _marker_stream.write('updated\\n')\n",
            1,
        )
        fixture.install_helper(upstream, updated)
        fixture.commit_all(upstream, "update helper")
        fixture.git(upstream, "push", "origin", "main")
        fixture.git(fixture.main, "fetch")

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "no-ff",
            "--actor",
            "opencode",
            "-m",
            "merge feature",
            "--update-main",
            extra_env={"AGENT_WT_MERGE_TEST_MARKER": str(marker)},
        )
        self.assert_ok(result)
        self.assertEqual(marker.read_text(encoding="utf-8"), "updated\n")
        self.assertIn("Re-executed after main update: yes", result.stdout)
        self.assertEqual(
            fixture.output(fixture.main, "rev-parse", "--abbrev-ref", "HEAD"),
            "main",
        )
        self.assertEqual(
            fixture.output(fixture.main, "log", "-1", "--format=%s"),
            "merge feature",
        )

    def test_missing_updated_helper_stops_before_feature_merge(self) -> None:
        fixture = self.fixture()
        feature_sha = fixture.output(fixture.feature, "rev-parse", "HEAD")
        upstream = fixture.upstream_clone()
        fixture.git(upstream, "rm", "assets/agent-wt-merge")
        fixture.git(upstream, "commit", "-m", "remove helper")
        fixture.git(upstream, "push", "origin", "main")
        fixture.git(fixture.main, "fetch")

        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "no-ff",
            "--actor",
            "opencode",
            "-m",
            "must not happen",
            "--update-main",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("no longer provides an executable merge helper", result.stderr)
        ancestor = fixture.git(
            fixture.main,
            "merge-base",
            "--is-ancestor",
            feature_sha,
            "main",
            check=False,
        )
        self.assertNotEqual(ancestor.returncode, 0)

    def test_beads_reason_excludes_main_only_and_pre_session_commits(self) -> None:
        fixture = self.fixture(feature_commit=False)
        pre_session_sha = fixture.commit_feature("before.txt", "before\n", "before session")
        feature_sha = fixture.commit_feature("during.txt", "during\n", "during session")
        (fixture.main / "main-only.txt").write_text("main only\n", encoding="utf-8")
        main_only_sha = fixture.commit_all(fixture.main, "main only")
        fixture.git(fixture.main, "push", "origin", "main")
        fixture.write_state(started_sha=pre_session_sha)

        before_sha = fixture.output(fixture.main, "rev-parse", "HEAD")
        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "no-ff",
            "--actor",
            "opencode",
            "-m",
            "merge feature",
            "--close-beads",
            "dots-test",
        )
        self.assert_ok(result)
        after_sha = fixture.output(fixture.main, "rev-parse", "HEAD")
        expected_shas = fixture.output(
            fixture.main,
            "log",
            f"{pre_session_sha}..{after_sha}",
            f"^{before_sha}",
            "--format=%h",
        ).splitlines()
        reason = "Fixed with commit(s) " + " ".join(expected_shas)
        bd_args = read_json(fixture.bd_log)
        recorded_reason = bd_args[bd_args.index("--reason") + 1]
        self.assertEqual(recorded_reason, reason)
        self.assertIn(f"Close reason: {reason}", result.stdout)
        self.assertIn(fixture.output(fixture.main, "rev-parse", "--short", feature_sha), expected_shas)
        self.assertNotIn(
            fixture.output(fixture.main, "rev-parse", "--short", main_only_sha),
            expected_shas,
        )
        self.assertNotIn(
            fixture.output(fixture.main, "rev-parse", "--short", pre_session_sha),
            expected_shas,
        )

    def test_bd_close_failure_is_nonzero_after_successful_merge(self) -> None:
        fixture = self.fixture()
        started_sha = fixture.output(fixture.main, "rev-parse", "HEAD")
        state_path = fixture.write_state(started_sha=started_sha)
        feature_sha = fixture.output(fixture.feature, "rev-parse", "HEAD")
        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "ff",
            "--actor",
            "opencode",
            "--close-beads",
            "dots-test",
            extra_env={"FAKE_BD_FAIL": "1"},
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), feature_sha)
        self.assertTrue(state_path.exists())
        self.assertIn("Git merge succeeded", result.stdout)
        self.assertIn("requested Beads closure did not complete", result.stdout)

    def test_state_cleanup_failure_is_nonzero_after_close(self) -> None:
        fixture = self.fixture()
        started_sha = fixture.output(fixture.main, "rev-parse", "HEAD")
        state_path = fixture.write_state(started_sha=started_sha)
        feature_sha = fixture.output(fixture.feature, "rev-parse", "HEAD")
        result = fixture.run_helper(
            fixture.main_helper,
            fixture.feature,
            "ff",
            "--actor",
            "opencode",
            "--close-beads",
            "dots-test",
            extra_env={"FAKE_BD_REMOVE_STATE": str(state_path)},
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), feature_sha)
        self.assertFalse(state_path.exists())
        self.assertIn("Beads issue closed: yes", result.stdout)
        self.assertIn("state cleanup did not complete", result.stdout)

    def test_direct_invocation_on_main_fails_without_mutation(self) -> None:
        fixture = self.fixture()
        before_sha = fixture.output(fixture.main, "rev-parse", "HEAD")
        result = fixture.run_helper(
            fixture.main_helper,
            fixture.main,
            "ff",
            "--actor",
            "opencode",
            "--close-beads",
            "dots-test",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("already on main; there is nothing to merge", result.stderr)
        self.assertEqual(fixture.output(fixture.main, "rev-parse", "HEAD"), before_sha)
        self.assertFalse(fixture.bd_log.exists())

    def test_dirty_main_detached_head_absent_main_and_no_commits_fail_safely(self) -> None:
        with self.subTest("dirty main"):
            fixture = self.fixture()
            (fixture.main / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            result = fixture.run_helper(
                fixture.main_helper,
                fixture.feature,
                "ff",
                "--actor",
                "opencode",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("main worktree is dirty", result.stderr)

        with self.subTest("detached HEAD"):
            fixture = self.fixture()
            fixture.git(fixture.feature, "checkout", "--detach")
            result = fixture.run_helper(
                fixture.feature_helper,
                fixture.feature,
                "inspect",
                "--use-local-helper",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("detached HEAD", result.stderr)

        with self.subTest("main not checked out"):
            fixture = self.fixture()
            fixture.git(fixture.main, "switch", "-c", "parking")
            result = fixture.run_helper(fixture.feature_helper, fixture.feature, "inspect")
            self.assertEqual(result.returncode, 2)
            self.assertIn("main is not checked out", result.stderr)

        with self.subTest("no commits"):
            fixture = self.fixture(feature_commit=False)
            result = fixture.run_helper(
                fixture.main_helper,
                fixture.feature,
                "ff",
                "--actor",
                "opencode",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("has no commits ahead", result.stderr)


if __name__ == "__main__":
    unittest.main()
