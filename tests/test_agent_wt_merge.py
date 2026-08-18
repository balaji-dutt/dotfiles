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


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_HELPER = REPO_ROOT / "assets" / "agent-wt-merge"
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
    loader.exec_module(module)
    return module


class GitFixture:
    def __init__(self, *, feature_commit: bool = True) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="agent wt merge ")
        self.root = Path(self._temporary.name).resolve()
        self.main = self.root / "main worktree"
        self.feature = self.root / "feature worktree"
        self.remote = self.root / "origin remote.git"
        self.fake_bin = self.root / "fake bin"
        self.bd_log = self.root / "bd log.json"

        self.main.mkdir()
        self.git(self.main, "init", "-b", "main")
        self.git(self.main, "config", "user.name", "Test User")
        self.git(self.main, "config", "user.email", "test@example.com")
        self.install_helper(self.main)
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

        self.fake_bin.mkdir()
        fake_bd = self.fake_bin / "bd"
        fake_bd.write_text(
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
            encoding="utf-8",
        )
        fake_bd.chmod(0o755)

    @property
    def main_helper(self) -> Path:
        return self.main / "assets" / "agent-wt-merge"

    @property
    def feature_helper(self) -> Path:
        return self.feature / "assets" / "agent-wt-merge"

    def cleanup(self) -> None:
        self._temporary.cleanup()

    def git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if check and result.returncode != 0:
            self.fail_command(["git", *args], cwd, result)
        return result

    @staticmethod
    def fail_command(
        command: list[str],
        cwd: Path,
        result: subprocess.CompletedProcess[str],
    ) -> None:
        raise AssertionError(
            f"command failed in {cwd}: {command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def output(self, cwd: Path, *args: str) -> str:
        return self.git(cwd, *args).stdout.strip()

    def install_helper(self, worktree: Path, text: str | None = None) -> Path:
        helper = worktree / "assets" / "agent-wt-merge"
        helper.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            shutil.copy2(SOURCE_HELPER, helper)
        else:
            helper.write_text(text, encoding="utf-8")
        helper.chmod(0o755)
        return helper

    def commit_all(self, cwd: Path, message: str) -> str:
        self.git(cwd, "add", "-A")
        self.git(cwd, "commit", "-m", message)
        return self.output(cwd, "rev-parse", "HEAD")

    def commit_feature(self, name: str, content: str, message: str) -> str:
        (self.feature / name).write_text(content, encoding="utf-8")
        return self.commit_all(self.feature, message)

    def run_helper(
        self,
        helper: Path,
        cwd: Path,
        *args: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
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
        state_path.write_text(
            json.dumps(
                {
                    "id": issue_id,
                    "branch": "feature",
                    "worktree_path": str(self.feature),
                    "started_sha": started_sha,
                }
            ),
            encoding="utf-8",
        )
        return state_path

    def write_ai_wt_session(self, session_id: str = "test-session") -> Path:
        metadata_path = self.main / ".ai-wt" / "sessions" / f"{session_id}.json"
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "worktree_path": str(self.feature),
                    "branch_created": True,
                }
            ),
            encoding="utf-8",
        )
        return metadata_path

    def upstream_clone(self) -> Path:
        upstream = self.root / "upstream clone"
        self.git(self.root, "clone", str(self.remote), str(upstream))
        self.git(upstream, "config", "user.name", "Upstream User")
        self.git(upstream, "config", "user.email", "upstream@example.com")
        return upstream


class AgentWtMergeTests(unittest.TestCase):
    def fixture(self, *, feature_commit: bool = True) -> GitFixture:
        fixture = GitFixture(feature_commit=feature_commit)
        self.addCleanup(fixture.cleanup)
        return fixture

    def assert_ok(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

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
        bd_args = json.loads(fixture.bd_log.read_text(encoding="utf-8"))
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
