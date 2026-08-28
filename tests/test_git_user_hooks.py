from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, run_git


REPO_ROOT = Path(__file__).resolve().parents[1]
PRE_COMMIT = REPO_ROOT / "private_dot_config/git/template/hooks/executable_pre-commit"
PREPARE_COMMIT_MSG = (
    REPO_ROOT / "private_dot_config/git/template/hooks/executable_prepare-commit-msg"
)
SCISSORS = "# ------------------------ >8 ------------------------"
DEFAULT_TRAILER = "Co-authored-by: GPT-5.5 (High Reasoning mode) <noreply@openai.com>"


class GitHookFixture:
    def __init__(self) -> None:
        self.context = isolated_environment(prefix="git user hooks ")
        self.isolated = self.context.__enter__()
        self.repo = self.isolated.root / "repository with spaces"
        self.repo.mkdir()
        self.env = self.isolated.env.copy()
        run_git(self.repo, "init", "-b", "main", env=self.env)
        run_git(self.repo, "config", "user.name", "Hook Test", env=self.env)
        run_git(self.repo, "config", "user.email", "hook@example.com", env=self.env)

    def cleanup(self) -> None:
        self.context.__exit__(None, None, None)

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run_git(self.repo, *args, env=self.env, check=check)

    def run_hook(
        self, hook: Path, *args: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", str(hook), *args],
            cwd=self.repo,
            env=self.env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def write_config(self, content: bytes) -> Path:
        path = self.repo / ".beads" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def commit_safe_baseline(self) -> None:
        self.write_config(b"database: dots\n")
        (self.repo / "tracked.txt").write_text("tracked base\n", encoding="utf-8")
        self.git("add", ".beads/config.yaml", "tracked.txt")
        self.git("commit", "-m", "safe baseline")

    def global_config(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self.git("config", "--global", *args, check=check)

    def git_temp_files(self) -> list[Path]:
        git_dir = Path(self.git("rev-parse", "--git-dir").stdout.strip())
        if not git_dir.is_absolute():
            git_dir = self.repo / git_dir
        return list(git_dir.glob("beads-config-clean.*.tmp")) + list(
            git_dir.glob("gpt-coauthor-tmp.*")
        )


class PreCommitHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = GitHookFixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_absent_and_safe_configs_pass(self) -> None:
        absent = self.fixture.run_hook(PRE_COMMIT)
        self.assertEqual(absent.returncode, 0, absent.stderr)

        self.fixture.write_config(b"database: dots\n# sync.remote: ignored\n")
        self.fixture.git("add", ".beads/config.yaml")
        safe = self.fixture.run_hook(PRE_COMMIT)
        self.assertEqual(safe.returncode, 0, safe.stderr)

    def test_forbidden_forms_restore_safe_baseline_without_touching_other_state(self) -> None:
        self.fixture.commit_safe_baseline()
        forbidden_forms = (
            b"sync.remote: git@example.invalid/dots\n",
            b"sync:\n  remote: git@example.invalid/dots\n",
            b"sync:\r\n\tremote: git@example.invalid/dots\r\n",
        )
        for content in forbidden_forms:
            with self.subTest(content=content):
                self.fixture.git("reset", "--hard", "HEAD")
                config_path = self.fixture.write_config(content)
                (self.fixture.repo / "tracked.txt").write_text(
                    "staged unrelated\n", encoding="utf-8"
                )
                (self.fixture.repo / "working-only.txt").write_bytes(b"working bytes\x00\n")
                self.fixture.git("add", ".beads/config.yaml", "tracked.txt")
                staged_unrelated = self.fixture.git("show", ":tracked.txt").stdout

                result = self.fixture.run_hook(PRE_COMMIT)

                self.assertEqual(result.returncode, 1)
                self.assertIn("refusing commit", result.stderr)
                self.assertIn("restored .beads/config.yaml from HEAD", result.stderr)
                self.assertEqual(config_path.read_bytes(), b"database: dots\n")
                self.assertEqual(
                    self.fixture.git("show", ":.beads/config.yaml").stdout,
                    "database: dots\n",
                )
                self.assertEqual(
                    self.fixture.git("show", ":tracked.txt").stdout,
                    staged_unrelated,
                )
                self.assertEqual(
                    (self.fixture.repo / "working-only.txt").read_bytes(),
                    b"working bytes\x00\n",
                )
                self.assertEqual(self.fixture.git_temp_files(), [])

    def test_forbidden_new_config_is_blocked_without_destructive_restore(self) -> None:
        config_path = self.fixture.write_config(b"sync:\n  remote: unsafe\n")
        self.fixture.git("add", ".beads/config.yaml")

        result = self.fixture.run_hook(PRE_COMMIT)

        self.assertEqual(result.returncode, 1)
        self.assertIn("no safe tracked baseline", result.stderr)
        self.assertEqual(config_path.read_bytes(), b"sync:\n  remote: unsafe\n")
        self.assertEqual(
            self.fixture.git("show", ":.beads/config.yaml").stdout,
            "sync:\n  remote: unsafe\n",
        )
        self.assertEqual(self.fixture.git_temp_files(), [])

    def test_restored_config_passes_on_retry(self) -> None:
        self.fixture.commit_safe_baseline()
        self.fixture.write_config(b"sync.remote: unsafe\n")
        self.fixture.git("add", ".beads/config.yaml")
        first = self.fixture.run_hook(PRE_COMMIT)

        second = self.fixture.run_hook(PRE_COMMIT)

        self.assertEqual(first.returncode, 1)
        self.assertEqual(second.returncode, 0, second.stderr)


class PrepareCommitMessageHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = GitHookFixture()
        self.message = self.fixture.repo / "commit message.txt"

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def arm(self) -> None:
        self.fixture.global_config("coauthor.gptNext", "true")

    def assert_disarmed(self) -> None:
        result = self.fixture.global_config(
            "--get", "coauthor.gptNext", check=False
        )
        self.assertNotEqual(result.returncode, 0)

    def test_unarmed_message_is_byte_preserving_noop(self) -> None:
        original = b"subject\r\nbody without final newline"
        self.message.write_bytes(original)

        result = self.fixture.run_hook(PREPARE_COMMIT_MSG, str(self.message))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.message.read_bytes(), original)

    def test_local_trailer_precedes_global_and_is_inserted_before_scissors(self) -> None:
        global_trailer = "Co-authored-by: Global Person <global@example.com>"
        local_trailer = "Co-authored-by: Local Person <local@example.com>"
        self.fixture.global_config("coauthor.gptTrailer", global_trailer)
        self.fixture.git("config", "--local", "coauthor.gptTrailer", local_trailer)
        self.arm()
        self.message.write_bytes(f"subject\r\n\r\n{SCISSORS}\r\nignored\r\n".encode())

        result = self.fixture.run_hook(PREPARE_COMMIT_MSG, str(self.message))

        self.assertEqual(result.returncode, 0, result.stderr)
        content = self.message.read_text(encoding="utf-8")
        self.assertIn(f"{local_trailer}\n\n{SCISSORS}", content)
        self.assertNotIn(global_trailer, content)
        self.assertNotIn("\r", content)
        self.assert_disarmed()
        self.assertEqual(self.fixture.git_temp_files(), [])

    def test_default_trailer_is_appended_to_message_without_final_newline(self) -> None:
        self.arm()
        self.message.write_bytes(b"subject without newline")

        result = self.fixture.run_hook(PREPARE_COMMIT_MSG, str(self.message))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.message.read_text(encoding="utf-8"),
            f"subject without newline\n\n\n{DEFAULT_TRAILER}\n",
        )
        self.assert_disarmed()

    def test_crlf_duplicate_is_unchanged_but_one_shot_is_consumed(self) -> None:
        trailer = "Co-authored-by: Existing Person <existing@example.com>"
        self.fixture.global_config("coauthor.gptTrailer", trailer)
        self.arm()
        original = f"subject\r\n\r\n{trailer}\r\n".encode()
        self.message.write_bytes(original)

        result = self.fixture.run_hook(PREPARE_COMMIT_MSG, str(self.message))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.message.read_bytes(), original)
        self.assert_disarmed()

    def test_missing_message_fails_after_consuming_one_shot(self) -> None:
        self.arm()

        result = self.fixture.run_hook(PREPARE_COMMIT_MSG, str(self.message))

        self.assertNotEqual(result.returncode, 0)
        self.assert_disarmed()
        self.assertEqual(self.fixture.git_temp_files(), [])

    def test_second_invocation_is_idempotent(self) -> None:
        self.arm()
        self.message.write_text("subject\n", encoding="utf-8")
        first = self.fixture.run_hook(PREPARE_COMMIT_MSG, str(self.message))
        after_first = self.message.read_bytes()

        second = self.fixture.run_hook(PREPARE_COMMIT_MSG, str(self.message))

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.message.read_bytes(), after_first)


if __name__ == "__main__":
    unittest.main()
