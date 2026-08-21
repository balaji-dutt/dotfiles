from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = REPO_ROOT / "assets" / "git-template-hook-sync.py"
SPEC = importlib.util.spec_from_file_location("git_template_hook_sync", HELPER_PATH)
assert SPEC and SPEC.loader
hook_sync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hook_sync
SPEC.loader.exec_module(hook_sync)


class GitTemplateHookSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.environment = mock.patch.dict(
            os.environ,
            {
                "HOME": str(self.home),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.template_hooks = self.base / "template" / "hooks"
        self.template_hooks.mkdir(parents=True)
        self.write_source("pre-commit", "#!/bin/sh\nexit 0\n")
        self.repos = self.base / "repos"
        self.repos.mkdir()

    def git(self, repo: Path, *arguments: str) -> str:
        process = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return process.stdout.strip()

    def init_repo(self, name: str = "project") -> Path:
        repo = self.repos / name
        repo.mkdir()
        self.git(repo, "init", "--quiet")
        return repo

    def write_source(self, name: str, content: str) -> Path:
        source = self.template_hooks / name
        source.write_text(content, encoding="utf-8")
        source.chmod(0o755)
        return source

    def run_sync(self, *roots: Path) -> tuple[int, str, str]:
        arguments = ["--template-hooks-dir", str(self.template_hooks)]
        for root in roots or (self.repos,):
            arguments.extend(("--repo-root", str(root)))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            return_code = hook_sync.main(arguments)
        return return_code, stdout.getvalue(), stderr.getvalue()

    def hooks_dir(self, repo: Path) -> Path:
        return Path(
            self.git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        ) / "hooks"

    def manifest(self, repo: Path) -> dict[str, str]:
        manifest_path = self.hooks_dir(repo) / hook_sync.MANIFEST_NAME
        return json.loads(manifest_path.read_text(encoding="utf-8"))["hooks"]

    def test_initial_seed_preserves_unrelated_hook_and_executable_mode(self) -> None:
        repo = self.init_repo()
        hooks_dir = self.hooks_dir(repo)
        unrelated = hooks_dir / "commit-msg"
        unrelated.write_text("custom\n", encoding="utf-8")

        return_code, output, errors = self.run_sync()

        installed = hooks_dir / "pre-commit"
        self.assertEqual(return_code, 0)
        self.assertIn("installed managed hook", output)
        self.assertEqual(errors, "")
        self.assertEqual(installed.read_text(encoding="utf-8"), "#!/bin/sh\nexit 0\n")
        self.assertTrue(installed.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "custom\n")
        self.assertIn("pre-commit", self.manifest(repo))

    def test_identical_hook_is_adopted_and_second_run_is_idempotent(self) -> None:
        repo = self.init_repo()
        destination = self.hooks_dir(repo) / "pre-commit"
        destination.write_bytes((self.template_hooks / "pre-commit").read_bytes())
        destination.chmod(0o644)

        first_code, first_output, _ = self.run_sync()
        manifest_path = self.hooks_dir(repo) / hook_sync.MANIFEST_NAME
        first_mtime = manifest_path.stat().st_mtime_ns
        second_code, second_output, second_errors = self.run_sync()

        self.assertEqual(first_code, 0)
        self.assertIn("adopted existing managed hook", first_output)
        self.assertTrue(destination.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(second_code, 0)
        self.assertNotIn("installed managed hook", second_output)
        self.assertNotIn("adopted existing managed hook", second_output)
        self.assertEqual(second_errors, "")
        self.assertEqual(manifest_path.stat().st_mtime_ns, first_mtime)

    def test_owned_hooks_update_and_retire(self) -> None:
        repo = self.init_repo()
        self.write_source("prepare-commit-msg", "#!/bin/sh\nexit 0\n")
        self.assertEqual(self.run_sync()[0], 0)
        destination = self.hooks_dir(repo) / "pre-commit"

        self.write_source("pre-commit", "#!/bin/sh\nexit 1\n")
        (self.template_hooks / "prepare-commit-msg").unlink()
        return_code, output, errors = self.run_sync()

        self.assertEqual(return_code, 0)
        self.assertEqual(errors, "")
        self.assertIn("updated managed hook", output)
        self.assertIn("removed retired managed hook", output)
        self.assertEqual(destination.read_text(encoding="utf-8"), "#!/bin/sh\nexit 1\n")
        self.assertFalse((self.hooks_dir(repo) / "prepare-commit-msg").exists())

    def test_modified_owned_hook_is_preserved_and_relinquished(self) -> None:
        repo = self.init_repo()
        self.assertEqual(self.run_sync()[0], 0)
        destination = self.hooks_dir(repo) / "pre-commit"
        destination.write_text("local edit\n", encoding="utf-8")
        (self.template_hooks / "pre-commit").unlink()
        self.write_source("commit-msg", "#!/bin/sh\nexit 0\n")

        return_code, _, errors = self.run_sync()

        self.assertEqual(return_code, 0)
        self.assertIn("preserving modified retired hook", errors)
        self.assertEqual(destination.read_text(encoding="utf-8"), "local edit\n")
        self.assertNotIn("pre-commit", self.manifest(repo))

    def test_unknown_same_name_hook_is_preserved(self) -> None:
        repo = self.init_repo()
        destination = self.hooks_dir(repo) / "pre-commit"
        destination.write_text("custom hook\n", encoding="utf-8")

        return_code, output, errors = self.run_sync()

        self.assertEqual(return_code, 0)
        self.assertIn("conflicts=1", output)
        self.assertIn("preserving unknown same-name hook", errors)
        self.assertEqual(destination.read_text(encoding="utf-8"), "custom hook\n")
        self.assertFalse((self.hooks_dir(repo) / hook_sync.MANIFEST_NAME).exists())

    def test_worktrees_sharing_a_common_hooks_dir_are_reconciled_once(self) -> None:
        repo = self.init_repo()
        (repo / "tracked").write_text("content\n", encoding="utf-8")
        self.git(repo, "add", "tracked")
        self.git(
            repo,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "initial",
        )
        worktree = self.repos / "linked"
        self.git(repo, "worktree", "add", "--quiet", "-b", "linked", str(worktree))

        with mock.patch.object(
            hook_sync, "sync_hooks", wraps=hook_sync.sync_hooks
        ) as sync_mock:
            return_code, _, _ = self.run_sync()

        self.assertEqual(return_code, 0)
        self.assertEqual(sync_mock.call_count, 1)

    def test_managed_template_hooks_path_is_migrated_after_seeding(self) -> None:
        repo = self.init_repo()
        self.git(repo, "config", "--local", "core.hooksPath", str(self.template_hooks))

        return_code, output, errors = self.run_sync()

        self.assertEqual(return_code, 0)
        self.assertEqual(errors, "")
        self.assertIn("cleared managed template hooks path", output)
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(repo), "config", "--local", "--get", "core.hooksPath"],
                check=False,
            ).returncode,
            1,
        )
        self.assertTrue((self.hooks_dir(repo) / "pre-commit").exists())

    def test_missing_beads_hooks_path_is_migrated(self) -> None:
        repo = self.init_repo()
        self.git(repo, "config", "--local", "core.hooksPath", ".beads/hooks")

        return_code, output, _ = self.run_sync()

        self.assertEqual(return_code, 0)
        self.assertIn("cleared missing .beads/hooks path", output)
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(repo), "config", "--local", "--get", "core.hooksPath"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode,
            1,
        )

    def test_valid_custom_hooks_path_is_preserved(self) -> None:
        repo = self.init_repo()
        custom_hooks = repo / ".custom-hooks"
        custom_hooks.mkdir()
        custom = custom_hooks / "pre-commit"
        custom.write_text("custom\n", encoding="utf-8")
        self.git(repo, "config", "--local", "core.hooksPath", str(custom_hooks))

        return_code, output, errors = self.run_sync()

        self.assertEqual(return_code, 0)
        self.assertIn("preserving custom core.hooksPath", errors)
        self.assertIn(str(custom_hooks), errors)
        self.assertEqual(custom.read_text(encoding="utf-8"), "custom\n")
        self.assertFalse((self.hooks_dir(repo) / "pre-commit").exists())
        self.assertIn("installed=0", output)

    def test_symlinked_default_hooks_directory_is_preserved(self) -> None:
        repo = self.init_repo()
        hooks_dir = self.hooks_dir(repo)
        actual_hooks = hooks_dir.with_name("external-hooks")
        hooks_dir.rename(actual_hooks)
        try:
            hooks_dir.symlink_to(actual_hooks, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks are unavailable: {exc}")

        return_code, output, errors = self.run_sync()

        self.assertEqual(return_code, 0)
        self.assertIn("preserving symlinked Git hooks directory", errors)
        self.assertIn("failures=0", output)
        self.assertFalse((actual_hooks / "pre-commit").exists())

    def test_non_object_manifest_is_ignored_safely(self) -> None:
        repo = self.init_repo()
        manifest_path = self.hooks_dir(repo) / hook_sync.MANIFEST_NAME
        manifest_path.write_text("[]\n", encoding="utf-8")

        return_code, output, errors = self.run_sync()

        self.assertEqual(return_code, 0)
        self.assertIn("ignoring invalid hook ownership manifest", errors)
        self.assertIn("installed managed hook", output)
        self.assertIn("pre-commit", self.manifest(repo))

    def test_dubious_or_invalid_repo_warns_without_failing_other_repos(self) -> None:
        self.init_repo("valid")
        invalid = self.repos / "invalid"
        (invalid / ".git").mkdir(parents=True)

        return_code, output, errors = self.run_sync()

        self.assertEqual(return_code, 0)
        self.assertIn("resolving --git-common-dir", errors)
        self.assertIn("failures=0", output)

    def test_real_sync_error_fails_the_run(self) -> None:
        self.init_repo()
        with mock.patch.object(
            hook_sync, "sync_hooks", side_effect=OSError("disk full")
        ):
            return_code, output, errors = self.run_sync()

        self.assertEqual(return_code, 1)
        self.assertIn("ERROR: synchronizing hooks", errors)
        self.assertIn("failures=1", output)


if __name__ == "__main__":
    unittest.main()
