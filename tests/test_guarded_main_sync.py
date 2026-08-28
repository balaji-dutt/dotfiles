from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support.fixtures import run_git, write_executable
from tests.test_agent_wt_merge import GitFixture


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_HELPER = REPO_ROOT / "assets" / "guarded-main-sync"
SOURCE_RUNTIME = REPO_ROOT / "assets" / "gitlab_pipeline_runtime.py"
SOURCE_ZSH_HELPERS = REPO_ROOT / "dot_local" / "share" / "git-helpers.zsh"
SOURCE_POWERSHELL_HELPERS = (
    REPO_ROOT / "private_dot_config" / "powershell" / "git.ps1.tmpl"
)
SOURCE_PYTHON_RESOLVER = REPO_ROOT / "assets" / "resolve-python3"


def render_windows_template(source: Path) -> str:
    lines = source.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[3:-1]) + "\n"


class GuardedMainSyncFixture(GitFixture):
    def __init__(self) -> None:
        super().__init__(feature_commit=False)
        self.set_override(False)
        self.upstream = self.root / "upstream clone"
        self.git(self.root, "clone", str(self.remote), str(self.upstream))
        self.git(self.upstream, "config", "user.name", "Upstream User")
        self.git(self.upstream, "config", "user.email", "upstream@example.com")
        self.helper = self.main / "assets" / "guarded-main-sync"
        shutil.copy2(SOURCE_HELPER, self.helper)
        shutil.copy2(SOURCE_RUNTIME, self.main / "assets" / SOURCE_RUNTIME.name)
        self.helper.chmod(0o755)

    @property
    def state_path(self) -> Path:
        common = Path(
            self.output(
                self.main,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        )
        return common / "guarded-main-sync" / "state.json"

    def diverge(self, *, conflict: bool = False) -> tuple[str, str]:
        name = "shared.txt" if conflict else "local.txt"
        (self.main / name).write_text("local\n", encoding="utf-8")
        local_sha = self.commit_all(self.main, "local main")
        upstream_name = "shared.txt" if conflict else "upstream.txt"
        (self.upstream / upstream_name).write_text("upstream\n", encoding="utf-8")
        upstream_sha = self.commit_all(self.upstream, "upstream main")
        self.git(self.upstream, "push", "origin", "main")
        return local_sha, upstream_sha

    def run_sync(
        self,
        *args: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(self.helper), *args],
            cwd=self.main,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )


class GuardedMainSyncCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = GuardedMainSyncFixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_sync_rebases_validates_ci_and_waits_for_finalize(self) -> None:
        local_sha, remote_sha = self.fixture.diverge()
        counter = self.fixture.root / "pipeline counter"
        result = self.fixture.run_sync(
            "sync",
            "--poll-interval",
            "1",
            "--poll-timeout",
            "2",
            extra_env={
                "FAKE_PIPELINE_SEQUENCE": "retryable,success",
                "FAKE_PIPELINE_COUNTER": str(counter),
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Main/tags pushed: no", result.stdout)
        state = json.loads(self.fixture.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "ready-to-finalize")
        self.assertNotEqual(state["rewritten_sha"], local_sha)
        self.assertEqual(state["remote_sha"], remote_sha)
        self.assertIsNone(
            self.fixture.remote_ref_sha(f"refs/heads/ci/main/{state['rewritten_sha']}")
        )
        self.assertEqual(self.fixture.remote_ref_sha("refs/heads/main"), remote_sha)
        self.assertEqual(
            self.fixture.output(self.fixture.main, "rev-list", "--count", "main..origin/main"),
            "0",
        )
        self.assertEqual(
            self.fixture.output(self.fixture.main, "rev-list", "--count", "origin/main..main"),
            "1",
        )

        finalized = self.fixture.run_sync("finalize")
        self.assertEqual(finalized.returncode, 0, finalized.stderr)
        self.assertIn("Next: git push", finalized.stdout)
        self.assertFalse(self.fixture.state_path.exists())

    def test_non_diverged_guarded_main_is_not_applicable(self) -> None:
        (self.fixture.main / "local-only.txt").write_text("local\n", encoding="utf-8")
        self.fixture.commit_all(self.fixture.main, "local only")
        result = self.fixture.run_sync("sync")
        self.assertEqual(result.returncode, 20, result.stderr)
        self.assertFalse(self.fixture.state_path.exists())

    def test_terminal_ci_retains_recovery_without_pushing_main(self) -> None:
        _local_sha, remote_sha = self.fixture.diverge()
        result = self.fixture.run_sync(
            "sync", extra_env={"FAKE_PIPELINE_OUTCOME": "terminal"}
        )
        self.assertEqual(result.returncode, 1)
        state = json.loads(self.fixture.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "ci-terminal")
        self.assertEqual(self.fixture.remote_ref_sha("refs/heads/main"), remote_sha)
        self.assertIsNone(state["ci_ref"])

    def test_timeout_retains_reserved_ref_and_state(self) -> None:
        self.fixture.diverge()
        result = self.fixture.run_sync(
            "sync",
            "--poll-interval",
            "1",
            "--poll-timeout",
            "1",
            extra_env={"FAKE_PIPELINE_OUTCOME": "retryable"},
        )
        self.assertEqual(result.returncode, 1)
        state = json.loads(self.fixture.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "ci-timeout")
        self.assertEqual(
            self.fixture.remote_ref_sha(state["ci_ref"]), state["rewritten_sha"]
        )

    def test_rebase_conflict_preserves_rebase_and_original_tip(self) -> None:
        local_sha, _remote_sha = self.fixture.diverge(conflict=True)
        result = self.fixture.run_sync("sync")
        self.assertEqual(result.returncode, 1)
        state = json.loads(self.fixture.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "rebase-conflict")
        self.assertEqual(state["original_local_sha"], local_sha)
        git_dir = Path(
            self.fixture.output(
                self.fixture.main,
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
            )
        )
        self.assertTrue((git_dir / "rebase-merge").exists())

        aborted = self.fixture.run_sync("abort")
        self.assertEqual(aborted.returncode, 0, aborted.stderr)
        self.assertEqual(self.fixture.output(self.fixture.main, "rev-parse", "HEAD"), local_sha)
        self.assertFalse(self.fixture.state_path.exists())

    def test_finalize_waits_until_recorded_stash_is_dropped(self) -> None:
        self.fixture.diverge()
        (self.fixture.main / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        self.fixture.git(self.fixture.main, "stash", "push", "-u", "-m", "gpls test")
        stash_oid = self.fixture.output(self.fixture.main, "rev-parse", "stash@{0}")
        result = self.fixture.run_sync(
            "sync", "--stash-oid", stash_oid, extra_env={"FAKE_PIPELINE_OUTCOME": "success"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        refused = self.fixture.run_sync("finalize")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("still exists", refused.stderr)
        self.fixture.git(self.fixture.main, "stash", "drop", "stash@{0}")
        finalized = self.fixture.run_sync("finalize")
        self.assertEqual(finalized.returncode, 0, finalized.stderr)

    def test_remote_movement_during_ci_retains_ref_and_state(self) -> None:
        local_sha, _remote_sha = self.fixture.diverge()
        result = self.fixture.run_sync(
            "sync",
            extra_env={
                "FAKE_PIPELINE_OUTCOME": "success",
                "FAKE_PIPELINE_MOVE_MAIN_TO": local_sha,
            },
        )
        self.assertEqual(result.returncode, 1)
        state = json.loads(self.fixture.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "ci-verified")
        self.assertIsNone(state["ci_ref"])

    def test_existing_state_is_not_overwritten(self) -> None:
        self.fixture.diverge()
        first = self.fixture.run_sync(
            "sync", extra_env={"FAKE_PIPELINE_OUTCOME": "terminal"}
        )
        self.assertEqual(first.returncode, 1)
        before = self.fixture.state_path.read_bytes()
        second = self.fixture.run_sync("sync")
        self.assertEqual(second.returncode, 1)
        self.assertIn("unresolved", second.stderr)
        self.assertEqual(self.fixture.state_path.read_bytes(), before)


@unittest.skipUnless(shutil.which("zsh"), "zsh is required")
class GuardedMainSyncZshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="gpls zsh ")
        self.root = Path(self.temporary.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"
        run_git(self.root, "init", "--bare", "-b", "main", str(self.remote))
        run_git(self.root, "clone", str(self.remote), str(self.repo))
        run_git(self.repo, "config", "user.name", "Test User")
        run_git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        run_git(self.repo, "add", "base.txt")
        run_git(self.repo, "commit", "-m", "initial")
        run_git(self.repo, "push", "-u", "origin", "main")
        (self.repo / "assets").mkdir()
        self.log = self.root / "helper.log"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def install_fake_helper(self, sync_exit: int) -> None:
        write_executable(
            self.repo / "assets" / "guarded-main-sync",
            f"""#!/bin/sh
printf '%s\\n' "$*" >> "$GPLS_TEST_LOG"
if [ "$1" = sync ]; then exit {sync_exit}; fi
exit 0
""",
        )
        run_git(self.repo, "add", "assets/guarded-main-sync")
        run_git(self.repo, "commit", "-m", "install test helper")
        run_git(self.repo, "push", "origin", "main")

    def run_gpls(self) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GPLS_TEST_LOG"] = str(self.log)
        return subprocess.run(
            [
                shutil.which("zsh") or "zsh",
                "-c",
                f"source {str(SOURCE_ZSH_HELPERS)!r}; git_pull_rebase_then_apply_stash",
            ],
            cwd=self.repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_dirty_success_passes_exact_stash_and_finalizes(self) -> None:
        self.install_fake_helper(0)
        (self.repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        result = self.run_gpls()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.repo / "dirty.txt").read_text(encoding="utf-8"), "dirty\n")
        self.assertEqual(run_git(self.repo, "stash", "list").stdout.strip(), "")
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertRegex(calls[0], r"^sync --stash-oid [0-9a-f]{40}$")
        self.assertEqual(calls[-1], "finalize")

    def test_dirty_failure_preserves_exact_stash(self) -> None:
        self.install_fake_helper(9)
        (self.repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        result = self.run_gpls()
        self.assertEqual(result.returncode, 9)
        self.assertFalse((self.repo / "dirty.txt").exists())
        self.assertIn("autostash-before-pull", run_git(self.repo, "stash", "list").stdout)
        self.assertRegex(self.log.read_text(encoding="utf-8"), r"--stash-oid [0-9a-f]{40}")

    def test_not_applicable_uses_normal_pull(self) -> None:
        self.install_fake_helper(20)
        result = self.run_gpls()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text(encoding="utf-8").strip(), "sync")


@unittest.skipUnless(shutil.which("pwsh"), "pwsh is required")
class GuardedMainSyncPowerShellTests(unittest.TestCase):
    def test_clean_dispatch_uses_probed_python_and_finalizes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gpls pwsh ") as temporary:
            root = Path(temporary)
            repo = root / "repo"
            run_git(root, "init", "-b", "main", str(repo))
            (repo / "assets").mkdir()
            log = root / "helper.log"
            write_executable(
                repo / "assets" / "guarded-main-sync",
                """#!/usr/bin/env python3
import os, sys
with open(os.environ["GPLS_TEST_LOG"], "a", encoding="utf-8") as stream:
    stream.write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:] == ["finalize"]:
    print("Next: git push")
raise SystemExit(0)
""",
            )
            script = root / "test.ps1"
            script.write_text(
                render_windows_template(SOURCE_POWERSHELL_HELPERS)
                + "Set-Location $env:GPLS_TEST_REPO\n"
                + "git_pull_rebase_then_apply_stash\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["GPLS_TEST_LOG"] = str(log)
            env["GPLS_TEST_REPO"] = str(repo)
            result = subprocess.run(
                [shutil.which("pwsh") or "pwsh", "-NoProfile", "-File", str(script)],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["sync", "finalize"])
            self.assertIn("Next: git push", result.stdout)


class PythonResolverTests(unittest.TestCase):
    def test_broken_python3_falls_through_to_py_launcher(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python resolver ") as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            log = root / "log"
            write_executable(fake_bin / "python3", "#!/bin/sh\nexit 49\n")
            write_executable(
                fake_bin / "py",
                """#!/bin/sh
if [ "$1" = -3 ] && [ "$2" = -c ]; then exit 0; fi
printf '%s\\n' "$*" > "$RESOLVER_TEST_LOG"
exit 0
""",
            )
            env = os.environ.copy()
            env["PATH"] = str(fake_bin)
            env["RESOLVER_TEST_LOG"] = str(log)
            result = subprocess.run(
                ["/bin/sh", str(SOURCE_PYTHON_RESOLVER), "--", "guard.py", "arg"],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text(encoding="utf-8").strip(), "-3 guard.py arg")

    def test_no_working_interpreter_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python resolver ") as temporary:
            fake_bin = Path(temporary)
            for name in ("python3", "py", "python"):
                write_executable(fake_bin / name, "#!/bin/sh\nexit 49\n")
            env = os.environ.copy()
            env["PATH"] = str(fake_bin)
            result = subprocess.run(
                ["/bin/sh", str(SOURCE_PYTHON_RESOLVER), "--", "guard.py"],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 127)
            self.assertIn("no working Python 3", result.stderr)


if __name__ == "__main__":
    unittest.main()
