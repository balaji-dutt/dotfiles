from __future__ import annotations

import argparse
import contextlib
import errno
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_WRAPPER = REPO_ROOT / "bin" / "executable_ai-wt.tmpl"
WINDOWS_LAUNCHER = REPO_ROOT / "dot_local" / "executable_ai-wt.cmd"
WINDOWS_COMMIT_WRAPPERS = {
    "OpenCode": REPO_ROOT / "dot_local" / "executable_oc-commit.cmd",
    "Claude": REPO_ROOT / "dot_local" / "executable_cc-commit.cmd",
}


def load_ai_wt():
    loader = importlib.machinery.SourceFileLoader("ai_wt_under_test", str(SOURCE_WRAPPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("could not create ai-wt import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


ai_wt = load_ai_wt()


def make_config(*, opencode_command: str = "opencode-plannotator"):
    root = Path("/tmp/ai-wt-test")
    return ai_wt.Config(
        repo_root=root,
        git_common_dir=root / ".git",
        worktree_parent=None,
        path_template=ai_wt.DEFAULT_PATH_TEMPLATE,
        state_dir=root / ai_wt.DEFAULT_STATE_DIR,
        base_ref="HEAD",
        delete_branch_on_cleanup=False,
        cleanup_dirty="keep",
        update_exclude=False,
        opencode_command=opencode_command,
        claude_command="claude-plannotator",
        submodule_init=False,
    )


class AutoArgumentTests(unittest.TestCase):
    def test_opencode_parser_accepts_auto_and_preserves_passthrough(self) -> None:
        parsed, branch, tool_args = ai_wt.parse_run_args(
            "opencode",
            ["--auto", "feat/example", "--", "--agent", "build"],
        )

        self.assertTrue(parsed.auto)
        self.assertEqual(branch, "feat/example")
        self.assertEqual(tool_args, ["--agent", "build"])

    def test_run_dispatch_forwards_opencode_auto(self) -> None:
        with mock.patch.object(ai_wt, "cmd_run", return_value=0) as cmd_run:
            result = ai_wt.cmd_run_dispatch(["opencode", "--auto", "feat/example"])

        self.assertEqual(result, 0)
        cmd_run.assert_called_once_with(
            "opencode",
            ["--auto", "feat/example"],
            prog=f"{ai_wt.SCRIPT_NAME} run opencode",
        )

    def test_claude_parser_rejects_first_class_auto(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ai_wt.parse_run_args("claude", ["--auto", "fix/example"])

    def test_claude_passthrough_auto_remains_a_tool_argument(self) -> None:
        parsed, branch, tool_args = ai_wt.parse_run_args(
            "claude",
            ["fix/example", "--", "--auto"],
        )

        self.assertFalse(hasattr(parsed, "auto"))
        self.assertEqual(branch, "fix/example")
        self.assertEqual(tool_args, ["--auto"])
        self.assertFalse(
            ai_wt.resolve_auto_choice(
                "claude",
                explicit=False,
                branch_prompted=False,
                tool_args=tool_args,
            )
        )


class AutoPromptTests(unittest.TestCase):
    def test_plain_prompt_defaults_to_no(self) -> None:
        with mock.patch("builtins.input", return_value=""):
            self.assertFalse(ai_wt.prompt_for_auto_plain())

    def test_plain_prompt_accepts_yes_and_no(self) -> None:
        for answer, expected in (("y", True), ("YES", True), ("n", False), ("No", False)):
            with self.subTest(answer=answer), mock.patch("builtins.input", return_value=answer):
                self.assertEqual(ai_wt.prompt_for_auto_plain(), expected)

    def test_plain_prompt_retries_invalid_input(self) -> None:
        with (
            mock.patch("builtins.input", side_effect=["maybe", "yes"]),
            mock.patch.object(ai_wt, "eprint") as eprint,
        ):
            self.assertTrue(ai_wt.prompt_for_auto_plain())

        eprint.assert_called_once_with("please answer yes or no")

    def test_gum_prompt_distinguishes_yes_no_and_cancel(self) -> None:
        with mock.patch.object(ai_wt, "gum_path", return_value="/usr/bin/gum"):
            for returncode, expected in ((0, True), (1, False)):
                with self.subTest(returncode=returncode), mock.patch.object(
                    ai_wt.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=returncode),
                ):
                    self.assertEqual(ai_wt.prompt_for_auto_gum(backend="gum"), expected)

            with mock.patch.object(
                ai_wt.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=130),
            ), self.assertRaisesRegex(ai_wt.AiWtError, "auto prompt cancelled"):
                ai_wt.prompt_for_auto_gum(backend="gum")

    def test_gum_prompt_falls_back_when_gum_is_unavailable(self) -> None:
        with mock.patch.object(ai_wt, "gum_path", return_value=None):
            self.assertIsNone(ai_wt.prompt_for_auto_gum(backend="auto"))

    def test_interactive_opencode_launch_prompts_for_auto(self) -> None:
        with mock.patch.object(ai_wt, "prompt_for_auto", return_value=True) as prompt:
            selected = ai_wt.resolve_auto_choice(
                "opencode",
                explicit=False,
                branch_prompted=True,
                tool_args=[],
            )

        self.assertTrue(selected)
        prompt.assert_called_once_with()

    def test_auto_prompt_is_skipped_when_choice_is_already_known(self) -> None:
        cases = (
            ("opencode", True, True, [], True),
            ("opencode", False, True, ["--auto"], True),
            ("opencode", False, False, [], False),
            ("claude", False, True, [], False),
        )
        with mock.patch.object(ai_wt, "prompt_for_auto") as prompt:
            for tool, explicit, branch_prompted, tool_args, expected in cases:
                with self.subTest(tool=tool, explicit=explicit, tool_args=tool_args):
                    self.assertEqual(
                        ai_wt.resolve_auto_choice(
                            tool,
                            explicit=explicit,
                            branch_prompted=branch_prompted,
                            tool_args=tool_args,
                        ),
                        expected,
                    )

        prompt.assert_not_called()


class AutoCommandTests(unittest.TestCase):
    def test_append_auto_preserves_arguments_and_deduplicates(self) -> None:
        command = ["opencode-plannotator", "--agent", "build"]
        self.assertEqual(
            ai_wt.append_opencode_auto(command, "opencode", True),
            [*command, "--auto"],
        )
        existing = [*command, "--auto"]
        self.assertIs(ai_wt.append_opencode_auto(existing, "opencode", True), existing)

    def test_append_auto_rejects_claude(self) -> None:
        with self.assertRaisesRegex(ai_wt.AiWtError, "only supported for OpenCode"):
            ai_wt.append_opencode_auto(["claude-plannotator"], "claude", True)

    def test_resume_auto_appends_to_stored_command_without_mutating_metadata(self) -> None:
        config = make_config()
        worktree = Path("/tmp/ai-wt-test/worktree")
        stored = ["opencode-plannotator", "--agent", "build"]
        metadata = {"tool_command": stored.copy()}

        command, cwd = ai_wt.build_resume_tool_command(
            config,
            "opencode",
            worktree,
            [],
            metadata,
            auto=True,
        )

        self.assertEqual(command, [*stored, "--auto"])
        self.assertEqual(cwd, worktree)
        self.assertEqual(metadata["tool_command"], stored)

    def test_resume_auto_appends_to_rebuilt_explicit_command(self) -> None:
        config = make_config(opencode_command="opencode-plannotator --agent plan")
        worktree = Path("/tmp/ai-wt-test/worktree")

        command, cwd = ai_wt.build_resume_tool_command(
            config,
            "opencode",
            worktree,
            ["--model", "provider/model"],
            {"tool_command": ["ignored"]},
            auto=True,
        )

        self.assertEqual(
            command,
            [
                "opencode-plannotator",
                "--agent",
                "plan",
                "--model",
                "provider/model",
                "--auto",
            ],
        )
        self.assertEqual(cwd, worktree)

    def test_resume_auto_rejects_retained_claude_session(self) -> None:
        with self.assertRaisesRegex(ai_wt.AiWtError, "only supported for OpenCode"):
            ai_wt.build_resume_tool_command(
                make_config(),
                "claude",
                Path("/tmp/ai-wt-test/worktree"),
                [],
                {"tool_command": ["claude-plannotator"]},
                auto=True,
            )


class GitProcessEnvironmentTests(unittest.TestCase):
    def test_windows_adds_longpaths_without_existing_process_config(self) -> None:
        with (
            mock.patch.object(ai_wt, "IS_WINDOWS", True),
            mock.patch.dict(ai_wt.os.environ, {}, clear=True),
        ):
            env = ai_wt.git_process_environment()

        self.assertIsNotNone(env)
        self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "core.longpaths")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "true")

    def test_windows_preserves_and_appends_existing_process_config(self) -> None:
        inherited = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "test.existing",
            "GIT_CONFIG_VALUE_0": "kept",
        }
        with (
            mock.patch.object(ai_wt, "IS_WINDOWS", True),
            mock.patch.dict(ai_wt.os.environ, inherited, clear=True),
        ):
            env = ai_wt.git_process_environment()

        self.assertIsNotNone(env)
        self.assertEqual(env["GIT_CONFIG_COUNT"], "2")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "test.existing")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "kept")
        self.assertEqual(env["GIT_CONFIG_KEY_1"], "core.longpaths")
        self.assertEqual(env["GIT_CONFIG_VALUE_1"], "true")

    def test_windows_rejects_malformed_or_negative_config_count(self) -> None:
        for raw_count in ("not-a-number", "-1"):
            with (
                self.subTest(raw_count=raw_count),
                mock.patch.object(ai_wt, "IS_WINDOWS", True),
                mock.patch.dict(
                    ai_wt.os.environ,
                    {"GIT_CONFIG_COUNT": raw_count},
                    clear=True,
                ),
                self.assertRaisesRegex(
                    ai_wt.AiWtError,
                    "unset it or set it to a non-negative integer",
                ),
            ):
                ai_wt.git_process_environment()

    def test_posix_uses_normal_inherited_environment(self) -> None:
        with mock.patch.object(ai_wt, "IS_WINDOWS", False):
            self.assertIsNone(ai_wt.git_process_environment())

    def test_run_git_passes_process_environment(self) -> None:
        env = {"GIT_CONFIG_COUNT": "1"}
        completed = subprocess.CompletedProcess(["git"], 0, stdout="", stderr="")
        with (
            mock.patch.object(ai_wt, "git_process_environment", return_value=env),
            mock.patch.object(ai_wt.subprocess, "run", return_value=completed) as run,
        ):
            result = ai_wt.run_git(None, ["status"])

        self.assertIs(result, completed)
        self.assertIs(run.call_args.kwargs["env"], env)

    def test_launch_child_passes_process_environment(self) -> None:
        env = {"GIT_CONFIG_COUNT": "1"}
        child = mock.Mock()
        child.wait.return_value = 7
        with (
            mock.patch.object(ai_wt, "git_process_environment", return_value=env),
            mock.patch.object(ai_wt, "validate_child_command"),
            mock.patch.object(ai_wt.subprocess, "Popen", return_value=child) as popen,
        ):
            result = ai_wt.launch_child(["agent"], Path("/tmp/worktree"))

        self.assertEqual(result, 7)
        self.assertIs(popen.call_args.kwargs["env"], env)


class PlatformCommandTests(unittest.TestCase):
    def test_display_name_hides_windows_python_suffix(self) -> None:
        self.assertEqual(ai_wt.display_script_name(r"C:\Users\Example\.local\ai-wt.py"), "ai-wt")
        self.assertEqual(ai_wt.display_script_name("/home/example/bin/ai-wt"), "ai-wt")

    def test_posix_command_parsing_and_formatting(self) -> None:
        with mock.patch.object(ai_wt, "IS_WINDOWS", False):
            self.assertEqual(
                ai_wt.parse_command("opencode --model 'provider/model name'"),
                ["opencode", "--model", "provider/model name"],
            )
            self.assertEqual(ai_wt.format_command(["opencode", "two words"]), "opencode 'two words'")

    def test_windows_command_parsing_uses_native_parser(self) -> None:
        expected = [r"C:\Program Files\OpenCode\opencode.exe", "two words"]
        with (
            mock.patch.object(ai_wt, "IS_WINDOWS", True),
            mock.patch.object(ai_wt, "split_windows_command_line", return_value=expected) as split,
        ):
            self.assertEqual(ai_wt.parse_command("configured command"), expected)
            self.assertEqual(ai_wt.format_command(expected), subprocess.list2cmdline(expected))
        split.assert_called_once_with("configured command")

    @unittest.skipUnless(os.name == "nt", "requires CommandLineToArgvW")
    def test_windows_native_parser_preserves_paths_and_quotes(self) -> None:
        command = subprocess.list2cmdline(
            [r"C:\Program Files\OpenCode\opencode.exe", "--model", "provider/model name"]
        )
        self.assertEqual(
            ai_wt.split_windows_command_line(command),
            [r"C:\Program Files\OpenCode\opencode.exe", "--model", "provider/model name"],
        )

    def test_command_path_recognizes_alternate_separator(self) -> None:
        with (
            mock.patch.object(ai_wt.os, "sep", "\\"),
            mock.patch.object(ai_wt.os, "altsep", "/"),
            mock.patch.object(ai_wt.os, "access", return_value=True) as access,
            mock.patch.object(ai_wt.shutil, "which") as which,
        ):
            self.assertEqual(ai_wt.command_path("C:/Tools/opencode.exe"), "C:/Tools/opencode.exe")
        access.assert_called_once_with("C:/Tools/opencode.exe", os.X_OK)
        which.assert_not_called()

    def test_windows_rejects_batch_backed_agents(self) -> None:
        with mock.patch.object(ai_wt, "IS_WINDOWS", True):
            for command in ([r"C:\Tools\opencode.cmd"], [r"C:\Tools\claude.BAT", "--flag"]):
                with self.subTest(command=command), self.assertRaisesRegex(
                    ai_wt.AiWtError, "native .exe"
                ):
                    ai_wt.validate_child_command(command)
            ai_wt.validate_child_command([r"C:\Tools\opencode.exe", "--flag"])


class RepoLockTests(unittest.TestCase):
    def test_windows_lock_retries_contention_and_releases(self) -> None:
        calls: list[int] = []
        fake_msvcrt = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2)

        def locking(_fd, mode, _length):
            calls.append(mode)
            if mode == fake_msvcrt.LK_NBLCK and calls.count(mode) == 1:
                raise OSError(errno.EACCES, "locked")

        fake_msvcrt.locking = locking
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            mock.patch.object(ai_wt, "IS_WINDOWS", True),
            mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
            mock.patch.object(ai_wt.time, "sleep") as sleep,
        ):
            lock = ai_wt.RepoLock(Path(temp_dir) / "lock")
            with lock:
                self.assertIsNotNone(lock.handle)
            self.assertIsNone(lock.handle)

        self.assertEqual(calls, [fake_msvcrt.LK_NBLCK, fake_msvcrt.LK_NBLCK, fake_msvcrt.LK_UNLCK])
        sleep.assert_called_once_with(0.05)

    def test_windows_lock_does_not_retry_unexpected_errors(self) -> None:
        fake_msvcrt = SimpleNamespace(
            LK_NBLCK=1,
            LK_UNLCK=2,
            locking=mock.Mock(side_effect=OSError(errno.EINVAL, "invalid")),
        )
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            mock.patch.object(ai_wt, "IS_WINDOWS", True),
            mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
            mock.patch.object(ai_wt.time, "sleep") as sleep,
        ):
            lock = ai_wt.RepoLock(Path(temp_dir) / "lock")
            with self.assertRaises(OSError):
                lock.__enter__()
            self.assertIsNone(lock.handle)
        sleep.assert_not_called()


@unittest.skipUnless(os.name == "nt", "requires cmd.exe")
class WindowsLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.comspec = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
        self.git = shutil.which("git")
        if not self.git:
            self.skipTest("git is required")

    def run_git(self, cwd: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.git, *args],
            cwd=cwd,
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def isolated_path(self) -> str:
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        return os.pathsep.join([str(Path(sys.executable).parent), str(system32), str(Path(self.git).parent)])

    def nested_path_with_length(self, total_length: int) -> Path:
        filename = "long-path.txt"
        directory_count = max(1, (total_length - len(filename) + 32) // 33)
        directory_characters = total_length - len(filename) - directory_count
        if directory_characters < directory_count or directory_characters > 32 * directory_count:
            raise ValueError(f"cannot construct nested path with length {total_length}")
        directory_lengths: list[int] = []
        remaining = directory_characters
        for index in range(directory_count):
            remaining_directories = directory_count - index - 1
            length = min(32, remaining - remaining_directories)
            directory_lengths.append(length)
            remaining -= length
        return Path(*("d" * length for length in directory_lengths), filename)

    def test_ai_wt_cmd_reports_missing_python_without_installing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shutil.copy2(WINDOWS_LAUNCHER, root / "ai-wt.cmd")
            shutil.copy2(SOURCE_WRAPPER, root / "ai-wt.py")
            env = os.environ.copy()
            env["PATH"] = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
            result = subprocess.run(
                [self.comspec, "/d", "/c", str(root / "ai-wt.cmd"), "--help"],
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Python 3.10 or newer was not found", result.stderr)

    def test_ai_wt_cmd_runs_windows_lifecycle_and_preserves_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            launcher_dir = root / "launcher"
            launcher_dir.mkdir()
            shutil.copy2(WINDOWS_LAUNCHER, launcher_dir / "ai-wt.cmd")
            shutil.copy2(SOURCE_WRAPPER, launcher_dir / "ai-wt.py")
            fake_agent = root / "fake agent.py"
            output = root / "agent-output.json"
            fake_agent.write_text(
                "import json, os, pathlib, subprocess, sys\n"
                "cwd = pathlib.Path.cwd()\n"
                "long_path = cwd / os.environ['AI_WT_TEST_LONG_PATH']\n"
                "long_path_io = pathlib.Path(chr(92) * 2 + '?' + chr(92) + str(long_path))\n"
                "git_config = subprocess.run(\n"
                "    ['git', 'config', '--get', 'core.longpaths'],\n"
                "    check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
                "git_status = subprocess.run(\n"
                "    ['git', 'status', '--short'],\n"
                "    check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
                "pathlib.Path(os.environ['AI_WT_TEST_OUTPUT']).write_text(\n"
                "    json.dumps({\n"
                "        'argv': sys.argv[1:],\n"
                "        'cwd': os.getcwd(),\n"
                "        'long_path': str(long_path),\n"
                "        'long_path_exists': long_path_io.is_file(),\n"
                "        'git_longpaths': git_config.stdout.strip(),\n"
                "        'git_config_returncode': git_config.returncode,\n"
                "        'git_status_returncode': git_status.returncode,\n"
                "        'git_status_stdout': git_status.stdout,\n"
                "        'git_status_stderr': git_status.stderr,\n"
                "    }), encoding='utf-8')\n"
                "raise SystemExit(7)\n",
                encoding="utf-8",
            )
            repo = root / "repo with spaces"
            repo.mkdir()
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.name", "Test User")
            self.run_git(repo, "config", "user.email", "test@example.com")
            (repo / "README.md").write_text("test\n", encoding="utf-8")
            # Stay below legacy MAX_PATH in the repo but cross it in the worktree.
            relative_length = 245 - len(str(repo)) - 1
            long_relative_path = self.nested_path_with_length(relative_length)
            long_source_path = repo / long_relative_path
            long_source_path.parent.mkdir(parents=True)
            long_source_path.write_text("tracked long path\n", encoding="utf-8")
            self.assertEqual(len(str(long_source_path)), 245)
            self.run_git(repo, "add", "README.md", long_relative_path.as_posix())
            self.run_git(repo, "commit", "-m", "Initial")

            env = os.environ.copy()
            env["PATH"] = self.isolated_path()
            env["AI_WT_PROMPT_BACKEND"] = "plain"
            env["AI_WT_TEST_OUTPUT"] = str(output)
            env["AI_WT_TEST_LONG_PATH"] = long_relative_path.as_posix()
            env["AI_WT_OPENCODE_COMMAND"] = subprocess.list2cmdline([sys.executable, str(fake_agent)])
            result = subprocess.run(
                [
                    self.comspec,
                    "/d",
                    "/c",
                    str(launcher_dir / "ai-wt.cmd"),
                    "opencode",
                    "--auto",
                    "feat/windows-lifecycle",
                    "--",
                    "--agent",
                    "build",
                ],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 7, result.stderr)
            launched = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(launched["argv"], ["--agent", "build", "--auto"])
            self.assertIn("worktrees", launched["cwd"])
            self.assertGreater(len(launched["long_path"]), 260)
            self.assertTrue(launched["long_path_exists"])
            self.assertEqual(launched["git_longpaths"], "true")
            self.assertEqual(launched["git_config_returncode"], 0)
            self.assertEqual(launched["git_status_returncode"], 0, launched["git_status_stderr"])
            self.assertEqual(launched["git_status_stdout"], "")
            sessions = repo / ".ai-wt" / "sessions"
            self.assertEqual(list(sessions.glob("*.json")), [])
            self.assertEqual(
                self.run_git(repo, "branch", "--list", "feat/windows-lifecycle").stdout.strip(),
                "feat/windows-lifecycle",
            )


@unittest.skipUnless(os.name == "nt", "requires cmd.exe")
class WindowsCommitWrapperTests(unittest.TestCase):
    def test_wrappers_set_identity_preserve_args_and_scope_environment(self) -> None:
        comspec = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
        git = shutil.which("git")
        if not git:
            self.skipTest("git is required")
        expected = {
            "OpenCode": "OpenCode <noreply@opencode.ai>|OpenCode <noreply@opencode.ai>",
            "Claude": "Claude <noreply@anthropic.com>|Claude <noreply@anthropic.com>",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            subprocess.run([git, "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            env = os.environ.copy()
            env.update(
                {
                    "GIT_AUTHOR_NAME": "Parent Author",
                    "GIT_AUTHOR_EMAIL": "parent-author@example.com",
                    "GIT_COMMITTER_NAME": "Parent Committer",
                    "GIT_COMMITTER_EMAIL": "parent-committer@example.com",
                }
            )
            for index, (identity, wrapper) in enumerate(WINDOWS_COMMIT_WRAPPERS.items(), start=1):
                tracked = repo / f"file-{index}.txt"
                tracked.write_text(f"{identity}\n", encoding="utf-8")
                subprocess.run([git, "add", tracked.name], cwd=repo, check=True, env=env)
                message = f"Commit as {identity}!"
                result = subprocess.run(
                    [comspec, "/d", "/v:on", "/c", str(wrapper), "-m", message],
                    cwd=repo,
                    env=env,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = subprocess.run(
                    [git, "log", "-1", "--format=%an <%ae>|%cn <%ce>"],
                    cwd=repo,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                ).stdout.strip()
                self.assertEqual(actual, expected[identity])
                subject = subprocess.run(
                    [git, "log", "-1", "--format=%s"],
                    cwd=repo,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                ).stdout.strip()
                self.assertEqual(subject, message)

            self.assertEqual(env["GIT_AUTHOR_NAME"], "Parent Author")
            failed = subprocess.run(
                [comspec, "/d", "/c", str(WINDOWS_COMMIT_WRAPPERS["OpenCode"]), "-m", "No changes"],
                cwd=repo,
                env=env,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(failed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
