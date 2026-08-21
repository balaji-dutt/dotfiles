from __future__ import annotations

import argparse
import contextlib
import csv
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
    "OpenCode": REPO_ROOT / "dot_local" / "executable_oc-commit.ps1",
    "Claude": REPO_ROOT / "dot_local" / "executable_cc-commit.ps1",
}
WINDOWS_COMMIT_STUBS = {
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


def make_config(
    *,
    opencode_command: str | None = "opencode-plannotator",
    opencode_profile: str | None = None,
):
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
        opencode_profile=opencode_profile,
        claude_command="claude-plannotator",
        submodule_init=False,
    )


class AutoArgumentTests(unittest.TestCase):
    def test_opencode_parser_accepts_profile_and_rejects_it_for_claude(self) -> None:
        parsed, branch, tool_args = ai_wt.parse_run_args(
            "opencode",
            ["--opencode-profile", "custom", "feat/example"],
        )

        self.assertEqual(parsed.opencode_profile, "custom")
        self.assertEqual(branch, "feat/example")
        self.assertEqual(tool_args, [])

        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ai_wt.parse_run_args(
                "claude",
                ["--opencode-profile", "custom", "feat/example"],
            )

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


class OpenCodeProfileConfigTests(unittest.TestCase):
    def load_profile(
        self,
        *,
        cli: str | None = None,
        env: str | None = None,
        git: str | None = None,
    ) -> str | None:
        root = Path("/tmp/ai-wt-profile-config")
        environ = {"AI_WT_OPENCODE_PROFILE": env} if env is not None else {}

        def git_value(_repo_root, key):
            return git if key == "ai-wt.opencodeProfile" else None

        with (
            mock.patch.object(ai_wt, "resolve_git_common_dir", return_value=root / ".git"),
            mock.patch.object(ai_wt, "git_config", side_effect=git_value),
            mock.patch.dict(ai_wt.os.environ, environ, clear=True),
        ):
            config = ai_wt.load_config(root, argparse.Namespace(opencode_profile=cli))
        return config.opencode_profile

    def test_profile_configuration_precedence(self) -> None:
        self.assertEqual(self.load_profile(cli="custom", env="build", git="build"), "custom")
        self.assertEqual(self.load_profile(env="custom", git="build"), "custom")
        self.assertEqual(self.load_profile(git="custom"), "custom")
        self.assertIsNone(self.load_profile())

    def test_invalid_profile_configuration_is_rejected(self) -> None:
        with self.assertRaisesRegex(ai_wt.AiWtError, "build.*custom"):
            self.load_profile(env="plain")

    def test_new_sessions_default_to_build(self) -> None:
        self.assertEqual(ai_wt.selected_opencode_profile(make_config()), "build")
        self.assertEqual(
            ai_wt.selected_opencode_profile(make_config(opencode_profile="custom")),
            "custom",
        )


class OpenCodeProfileCommandTests(unittest.TestCase):
    def test_profile_prefers_matching_posix_wrapper(self) -> None:
        wrappers = {
            "opencode-plannotator": "/usr/bin/opencode-plannotator",
            "opencode-plannotator-custom": "/usr/bin/opencode-plannotator-custom",
            "opencode": "/usr/bin/opencode",
        }
        with (
            mock.patch.object(ai_wt, "IS_WINDOWS", False),
            mock.patch.object(ai_wt, "command_path", side_effect=wrappers.get),
        ):
            build, _ = ai_wt.build_tool_command(
                make_config(opencode_command=None),
                "opencode",
                Path("/tmp/worktree"),
                [],
                opencode_profile="build",
            )
            custom, _ = ai_wt.build_tool_command(
                make_config(opencode_command=None),
                "opencode",
                Path("/tmp/worktree"),
                [],
                opencode_profile="custom",
            )

        self.assertEqual(build, ["/usr/bin/opencode-plannotator"])
        self.assertEqual(custom, ["/usr/bin/opencode-plannotator-custom"])

    def test_windows_uses_direct_opencode_for_each_profile(self) -> None:
        with (
            mock.patch.object(ai_wt, "IS_WINDOWS", True),
            mock.patch.object(ai_wt, "command_path", return_value=r"C:\Tools\opencode.exe") as command_path,
        ):
            for profile in ai_wt.OPENCODE_PROFILES:
                with self.subTest(profile=profile):
                    command, _ = ai_wt.build_tool_command(
                        make_config(opencode_command=None),
                        "opencode",
                        Path(r"C:\repo\worktree"),
                        [],
                        opencode_profile=profile,
                    )
                    self.assertEqual(command, [r"C:\Tools\opencode.exe"])

        self.assertEqual(
            command_path.call_args_list,
            [mock.call("opencode"), mock.call("opencode")],
        )

    def test_configured_command_keeps_profile_environment_separate(self) -> None:
        command, _ = ai_wt.build_tool_command(
            make_config(opencode_command="opencode --agent build"),
            "opencode",
            Path("/tmp/worktree"),
            ["--model", "provider/model"],
            opencode_profile="custom",
        )
        self.assertEqual(
            command,
            ["opencode", "--agent", "build", "--model", "provider/model"],
        )


class OpenCodeProfileEnvironmentTests(unittest.TestCase):
    def test_host_and_devcontainer_defaults_and_overrides(self) -> None:
        defaults = {
            "DEFAULT_HOST_BUILD_PORTS": "host-build",
            "DEFAULT_HOST_CUSTOM_PORTS": "host-custom",
            "DEFAULT_DEVCONTAINER_BUILD_PORTS": "container-build",
            "DEFAULT_DEVCONTAINER_CUSTOM_PORTS": "container-custom",
        }
        with (
            mock.patch.multiple(ai_wt, **defaults),
            mock.patch.dict(ai_wt.os.environ, {}, clear=True),
        ):
            self.assertEqual(ai_wt.opencode_port_range("build"), "host-build")
            self.assertEqual(ai_wt.opencode_port_range("custom"), "host-custom")
            ai_wt.os.environ["DEVCONTAINER"] = "1"
            self.assertEqual(ai_wt.opencode_port_range("build"), "container-build")
            self.assertEqual(ai_wt.opencode_port_range("custom"), "container-custom")
            ai_wt.os.environ["PLANNOTATOR_PORTS_CUSTOM"] = "override-custom"
            self.assertEqual(ai_wt.opencode_port_range("custom"), "override-custom")

    def test_opencode_child_gets_profile_and_sanitization(self) -> None:
        inherited = {
            "ANTHROPIC_SYSTEM_PROMPT_PATH": "   ",
            "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "0",
            "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "0",
            "PLANNOTATOR_PORTS_CUSTOM": "9004-9009",
        }
        with (
            mock.patch.object(ai_wt, "git_process_environment", return_value=inherited.copy()),
            mock.patch.dict(ai_wt.os.environ, inherited, clear=True),
        ):
            env = ai_wt.child_process_environment("opencode", "custom")

        self.assertIsNotNone(env)
        self.assertEqual(env["PLANNOTATOR_PORT"], "9004-9009")
        self.assertEqual(env["OPENCODE_PLANNOTATOR_POOL"], "custom")
        self.assertEqual(env["ANTHROPIC_SYSTEM_PROMPT_PATH"], os.devnull)
        self.assertEqual(env["OPENCODE_DISABLE_CLAUDE_CODE_PROMPT"], "1")
        self.assertEqual(env["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"], "1")

    def test_nonempty_anthropic_override_is_preserved(self) -> None:
        inherited = {
            "ANTHROPIC_SYSTEM_PROMPT_PATH": "/custom/prompt.json",
            "PLANNOTATOR_PORTS_BUILD": "8993-8998",
        }
        with (
            mock.patch.object(ai_wt, "git_process_environment", return_value=None),
            mock.patch.dict(ai_wt.os.environ, inherited, clear=True),
        ):
            env = ai_wt.child_process_environment("opencode", "build")

        self.assertIsNotNone(env)
        self.assertEqual(env["ANTHROPIC_SYSTEM_PROMPT_PATH"], "/custom/prompt.json")

    def test_claude_child_does_not_receive_opencode_environment(self) -> None:
        inherited = {"GIT_CONFIG_COUNT": "1"}
        with mock.patch.object(ai_wt, "git_process_environment", return_value=inherited):
            env = ai_wt.child_process_environment("claude", "custom")
        self.assertIs(env, inherited)
        self.assertNotIn("OPENCODE_PLANNOTATOR_POOL", env)
        self.assertNotIn("ANTHROPIC_SYSTEM_PROMPT_PATH", env)


class OpenCodeProfileResumeTests(unittest.TestCase):
    def test_stored_profile_is_preserved(self) -> None:
        metadata = {
            "opencode_profile": "custom",
            "tool_command": ["opencode-plannotator-custom"],
        }
        self.assertEqual(ai_wt.stored_opencode_profile(metadata), "custom")

        command, cwd = ai_wt.build_resume_tool_command(
            make_config(opencode_command=None, opencode_profile="build"),
            "opencode",
            Path("/tmp/worktree"),
            [],
            metadata,
            auto=False,
            opencode_profile=ai_wt.stored_opencode_profile(metadata),
        )
        self.assertEqual(command, ["opencode-plannotator-custom"])
        self.assertEqual(cwd, Path("/tmp/worktree"))

    def test_legacy_custom_wrapper_is_inferred(self) -> None:
        self.assertEqual(
            ai_wt.stored_opencode_profile(
                {"tool_command": ["/usr/local/bin/opencode-plannotator-custom"]}
            ),
            "custom",
        )
        self.assertEqual(
            ai_wt.stored_opencode_profile({"tool_command": ["opencode"]}),
            "build",
        )

    def test_legacy_invalid_command_defaults_to_build_profile(self) -> None:
        self.assertEqual(ai_wt.stored_opencode_profile({}), "build")
        self.assertEqual(
            ai_wt.stored_opencode_profile({"tool_command": "opencode"}),
            "build",
        )

    def test_rebuilt_resume_uses_stored_profile(self) -> None:
        config = make_config(opencode_command=None, opencode_profile="build")
        with mock.patch.object(
            ai_wt,
            "command_path",
            side_effect=lambda name: f"/usr/bin/{name}",
        ):
            command, _ = ai_wt.build_resume_tool_command(
                config,
                "opencode",
                Path("/tmp/worktree"),
                ["--model", "provider/model"],
                {"tool_command": ["opencode-plannotator-custom"]},
                auto=False,
                opencode_profile="custom",
            )
        self.assertEqual(
            command,
            [
                "/usr/bin/opencode-plannotator-custom",
                "--model",
                "provider/model",
            ],
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
            mock.patch.object(
                ai_wt,
                "child_process_environment",
                return_value=env,
            ) as child_environment,
            mock.patch.object(ai_wt, "validate_child_command"),
            mock.patch.object(ai_wt.subprocess, "Popen", return_value=child) as popen,
        ):
            result = ai_wt.launch_child(
                ["agent"],
                Path("/tmp/worktree"),
                tool="opencode",
                opencode_profile="build",
            )

        self.assertEqual(result, 7)
        self.assertIs(popen.call_args.kwargs["env"], env)
        child_environment.assert_called_once_with("opencode", "build")


class FakeStderr(io.StringIO):
    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class TerminalTitleTests(unittest.TestCase):
    def test_title_joins_tool_and_context(self) -> None:
        self.assertEqual(ai_wt.terminal_title("claude", "fix/titles"), "claude · fix/titles")

    def test_title_omits_blank_context(self) -> None:
        for context in ("", "   "):
            with self.subTest(context=context):
                self.assertEqual(ai_wt.terminal_title("opencode", context), "opencode")

    def _emit(self, title: str, *, tty: bool = True, windows: bool = False, env=None):
        stream = FakeStderr(tty=tty)
        with (
            mock.patch.object(ai_wt, "IS_WINDOWS", windows),
            mock.patch.object(ai_wt.sys, "stderr", stream),
            mock.patch.dict(ai_wt.os.environ, env or {}, clear=True),
        ):
            emitted = ai_wt.emit_terminal_title(title)
        return emitted, stream.getvalue()

    def test_emits_osc_zero_on_a_posix_terminal(self) -> None:
        emitted, written = self._emit("claude · fix/titles")

        self.assertTrue(emitted)
        self.assertEqual(written, "\033]0;claude · fix/titles\007")

    def test_suppressed_when_stderr_is_not_a_terminal(self) -> None:
        emitted, written = self._emit("claude · main", tty=False)

        self.assertFalse(emitted)
        self.assertEqual(written, "")

    def test_suppressed_by_disable_auto_title(self) -> None:
        emitted, written = self._emit("claude · main", env={"DISABLE_AUTO_TITLE": "true"})

        self.assertFalse(emitted)
        self.assertEqual(written, "")

    def test_suppressed_on_native_windows_outside_windows_terminal(self) -> None:
        emitted, written = self._emit("claude · main", windows=True)

        self.assertFalse(emitted)
        self.assertEqual(written, "")

    def test_emitted_on_native_windows_inside_windows_terminal(self) -> None:
        emitted, written = self._emit(
            "claude · main",
            windows=True,
            env={"WT_SESSION": "d6d3a1e2-0000-0000-0000-000000000000"},
        )

        self.assertTrue(emitted)
        self.assertEqual(written, "\033]0;claude · main\007")

    def test_suppressed_for_an_empty_title(self) -> None:
        emitted, written = self._emit("")

        self.assertFalse(emitted)
        self.assertEqual(written, "")

    def test_launch_child_titles_the_tab_from_the_branch(self) -> None:
        child = mock.Mock()
        child.wait.return_value = 0
        with (
            mock.patch.object(ai_wt, "validate_child_command"),
            mock.patch.object(ai_wt, "child_process_environment", return_value=None),
            mock.patch.object(ai_wt.subprocess, "Popen", return_value=child),
            mock.patch.object(ai_wt, "emit_terminal_title") as emit,
        ):
            ai_wt.launch_child(
                ["agent"],
                Path("/tmp/worktree"),
                tool="claude",
                title_context="fix/titles",
            )

        emit.assert_called_once_with("claude · fix/titles")

    def test_launch_child_falls_back_to_the_bare_tool_name(self) -> None:
        child = mock.Mock()
        child.wait.return_value = 0
        with (
            mock.patch.object(ai_wt, "validate_child_command"),
            mock.patch.object(ai_wt, "child_process_environment", return_value=None),
            mock.patch.object(ai_wt.subprocess, "Popen", return_value=child),
            mock.patch.object(ai_wt, "emit_terminal_title") as emit,
        ):
            ai_wt.launch_child(["agent"], Path("/tmp/worktree"), tool="opencode")

        emit.assert_called_once_with("opencode")


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
            (root / "where.cmd").write_text("@exit /b 1\n", encoding="utf-8")
            env = os.environ.copy()
            env["PATH"] = str(root)
            env.pop("_AI_WT_PYTHON", None)
            env.pop("_AI_WT_PYTHON_ARGS", None)
            result = subprocess.run(
                [self.comspec, "/d", "/c", str(root / "ai-wt.cmd"), "--help"],
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(result.returncode, 2, f"stdout={result.stdout!r}\nstderr={result.stderr!r}")
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
                "        'plannotator_port': os.environ.get('PLANNOTATOR_PORT'),\n"
                "        'plannotator_pool': os.environ.get('OPENCODE_PLANNOTATOR_POOL'),\n"
                "        'system_prompt_path': os.environ.get('ANTHROPIC_SYSTEM_PROMPT_PATH'),\n"
                "        'disable_claude_prompt': os.environ.get('OPENCODE_DISABLE_CLAUDE_CODE_PROMPT'),\n"
                "        'disable_claude_skills': os.environ.get('OPENCODE_DISABLE_CLAUDE_CODE_SKILLS'),\n"
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
            env["PLANNOTATOR_PORTS_BUILD"] = "8993-8998"
            env.pop("ANTHROPIC_SYSTEM_PROMPT_PATH", None)
            env["OPENCODE_DISABLE_CLAUDE_CODE_PROMPT"] = "0"
            env["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"] = "0"
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
            self.assertEqual(launched["plannotator_port"], "8993-8998")
            self.assertEqual(launched["plannotator_pool"], "build")
            self.assertEqual(launched["system_prompt_path"].lower(), "nul")
            self.assertEqual(launched["disable_claude_prompt"], "1")
            self.assertEqual(launched["disable_claude_skills"], "1")
            sessions = repo / ".ai-wt" / "sessions"
            self.assertEqual(list(sessions.glob("*.json")), [])
            self.assertEqual(
                self.run_git(repo, "branch", "--list", "feat/windows-lifecycle").stdout.strip(),
                "feat/windows-lifecycle",
            )


@unittest.skipUnless(os.name == "nt", "requires native Windows")
class WindowsCommitWrapperTests(unittest.TestCase):
    def test_powershell_wrappers_preserve_multiline_args_and_scope_environment(self) -> None:
        git = shutil.which("git")
        pwsh = shutil.which("pwsh")
        if not git or not pwsh:
            self.skipTest("git and pwsh are required")
        expected = {
            "OpenCode": "OpenCode <noreply@opencode.ai>|OpenCode <noreply@opencode.ai>",
            "Claude": "Claude <noreply@anthropic.com>|Claude <noreply@anthropic.com>",
        }
        body = '- first bullet\n- second "quoted" & <angle> | pipe\n- third 100% ^ caret ! bang $dollar'
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
                    [pwsh, "-NoProfile", "-File", str(wrapper), "-m", message, "-m", body],
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
                commit_message = subprocess.run(
                    [git, "log", "-1", "--format=%B"],
                    cwd=repo,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                ).stdout.replace("\r\n", "\n").rstrip("\n")
                self.assertEqual(commit_message, f"{message}\n\n{body}")

                failed = subprocess.run(
                    [pwsh, "-NoProfile", "-File", str(wrapper), "-m", "No changes"],
                    cwd=repo,
                    env=env,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertNotEqual(failed.returncode, 0)

            self.assertEqual(env["GIT_AUTHOR_NAME"], "Parent Author")
            self.assertEqual(env["GIT_COMMITTER_NAME"], "Parent Committer")

    def test_bare_commands_resolve_to_powershell_wrappers(self) -> None:
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh is required")
        with tempfile.TemporaryDirectory() as temp_dir:
            wrapper_dir = Path(temp_dir)
            for identity, wrapper in WINDOWS_COMMIT_WRAPPERS.items():
                command_name = "oc-commit" if identity == "OpenCode" else "cc-commit"
                shutil.copy2(wrapper, wrapper_dir / f"{command_name}.ps1")
                shutil.copy2(WINDOWS_COMMIT_STUBS[identity], wrapper_dir / f"{command_name}.cmd")

            env = os.environ.copy()
            env["PATH"] = f"{wrapper_dir}{os.pathsep}{env['PATH']}"
            result = subprocess.run(
                [
                    pwsh,
                    "-NoProfile",
                    "-Command",
                    "$paths = foreach ($name in 'oc-commit', 'cc-commit') { "
                    "(Get-Command $name -ErrorAction Stop).Source }; "
                    "$paths | ConvertTo-Json -Compress",
                ],
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            resolved = [Path(item) for item in json.loads(result.stdout)]
            self.assertEqual(
                [item.name for item in resolved],
                ["oc-commit.ps1", "cc-commit.ps1"],
            )
            self.assertTrue(all(item.parent == wrapper_dir for item in resolved))

    def test_cmd_stubs_refuse_without_committing(self) -> None:
        comspec = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")
        git = shutil.which("git")
        if not git:
            self.skipTest("git is required")
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            subprocess.run([git, "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for index, wrapper in enumerate(WINDOWS_COMMIT_STUBS.values(), start=1):
                tracked = repo / f"refused-{index}.txt"
                tracked.write_text("must remain staged\n", encoding="utf-8")
                subprocess.run([git, "add", tracked.name], cwd=repo, check=True)
                result = subprocess.run(
                    [comspec, "/d", "/c", str(wrapper), "-m", "Must not commit"],
                    cwd=repo,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 64)
                self.assertIn("cannot safely forward", result.stderr.lower())
                self.assertIn("powershell", result.stderr.lower())
                self.assertIn(".ps1", result.stderr.lower())
                count = subprocess.run(
                    [git, "rev-list", "--count", "--all"],
                    cwd=repo,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                ).stdout.strip()
                self.assertEqual(count, "0")


class OperatorBackendTests(unittest.TestCase):
    def test_ui_backend_prefers_new_variable_and_supports_legacy_alias(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"AI_WT_UI_BACKEND": "plain", "AI_WT_PROMPT_BACKEND": "gum"},
            clear=True,
        ):
            self.assertEqual(ai_wt.ui_backend(), "plain")
        with mock.patch.dict(os.environ, {"AI_WT_PROMPT_BACKEND": "gum"}, clear=True):
            self.assertEqual(ai_wt.ui_backend(), "gum")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ai_wt.ui_backend(), "auto")

    def test_ui_backend_reports_the_invalid_source_variable(self) -> None:
        with mock.patch.dict(os.environ, {"AI_WT_UI_BACKEND": "fancy"}, clear=True):
            with self.assertRaisesRegex(ai_wt.AiWtError, "AI_WT_UI_BACKEND"):
                ai_wt.ui_backend()
        with mock.patch.dict(os.environ, {"AI_WT_PROMPT_BACKEND": "fancy"}, clear=True):
            with self.assertRaisesRegex(ai_wt.AiWtError, "AI_WT_PROMPT_BACKEND"):
                ai_wt.ui_backend()

    def test_auto_gum_falls_back_and_forced_gum_requires_ttys(self) -> None:
        with mock.patch.object(ai_wt.sys, "stdin", io.StringIO()):
            self.assertIsNone(ai_wt.interactive_gum_path("auto"))
            with self.assertRaisesRegex(ai_wt.AiWtError, "interactive stdin"):
                ai_wt.interactive_gum_path("gum")

    def test_auto_gum_falls_back_and_forced_gum_fails_when_missing(self) -> None:
        tty = mock.Mock()
        tty.isatty.return_value = True
        with (
            mock.patch.object(ai_wt.sys, "stdin", tty),
            mock.patch.object(ai_wt.sys, "stderr", tty),
            mock.patch.object(ai_wt.shutil, "which", return_value=None),
        ):
            self.assertIsNone(ai_wt.interactive_gum_path("auto"))
            with self.assertRaisesRegex(ai_wt.AiWtError, "gum not found"):
                ai_wt.interactive_gum_path("gum")

    def test_operator_gum_falls_back_when_stdout_is_redirected(self) -> None:
        tty = mock.Mock()
        tty.isatty.return_value = True
        with (
            mock.patch.object(ai_wt.sys, "stdin", tty),
            mock.patch.object(ai_wt.sys, "stderr", tty),
            mock.patch.object(ai_wt.sys, "stdout", io.StringIO()),
        ):
            self.assertIsNone(ai_wt.interactive_gum_path("auto", require_stdout=True))
            with self.assertRaisesRegex(ai_wt.AiWtError, "stdin, stdout, and stderr"):
                ai_wt.interactive_gum_path("gum", require_stdout=True)

    def test_session_table_uses_sanitized_csv_and_stable_id(self) -> None:
        metadata = {
            "session_id": "20260818-120000-abc123",
            "tool": "opencode",
            "branch": "feat/with,comma\r\nand-newline",
            "cleanup_status": "retained_dirty",
            "worktree_path": "/missing/worktree",
        }
        captured_path: Path | None = None

        def fake_run(command, **_kwargs):
            nonlocal captured_path
            captured_path = Path(command[command.index("--file") + 1])
            self.assertTrue(captured_path.exists())
            with captured_path.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0][0], metadata["session_id"])
            self.assertEqual(rows[0][2], "feat/with,comma and-newline")
            self.assertEqual(command[command.index("--return-column") + 1], "1")
            return SimpleNamespace(returncode=0, stdout=f"{metadata['session_id']}\n")

        with mock.patch.object(ai_wt.subprocess, "run", side_effect=fake_run):
            selected = ai_wt.select_session_gum([metadata], "/usr/bin/gum", header="Select")

        self.assertIs(selected, metadata)
        self.assertIsNotNone(captured_path)
        self.assertFalse(captured_path.exists())

    def test_session_table_treats_empty_success_as_cancel(self) -> None:
        metadata = {
            "session_id": "session-1",
            "tool": "opencode",
            "branch": "feat/example",
            "worktree_path": "/missing/worktree",
        }
        with mock.patch.object(
            ai_wt.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout="\n"),
        ):
            self.assertIsNone(
                ai_wt.select_session_gum([metadata], "/usr/bin/gum", header="Select")
            )


class OperatorCommandTests(unittest.TestCase):
    def test_targetless_resume_keeps_loaded_config(self) -> None:
        config = make_config()
        metadata = {"session_id": "session-1"}
        with (
            mock.patch.object(ai_wt, "resolve_repo_root", return_value=config.repo_root),
            mock.patch.object(ai_wt, "load_config", return_value=config),
            mock.patch.object(ai_wt, "ensure_state"),
            mock.patch.object(ai_wt, "load_sessions", return_value=[metadata]),
            mock.patch.object(ai_wt, "session_resumable", return_value=True),
            mock.patch.object(ai_wt, "choose_session", return_value=metadata),
            mock.patch.object(ai_wt, "resume_session", return_value=7) as resume,
        ):
            result = ai_wt.cmd_resume(["--state-dir", "custom-state"])

        self.assertEqual(result, 7)
        resume.assert_called_once_with(config, metadata, [], auto=False)

    def test_resume_list_selects_and_resumes_with_gum(self) -> None:
        config = make_config()
        metadata = {"session_id": "session-1"}
        with (
            mock.patch.object(ai_wt, "resolve_repo_root", return_value=config.repo_root),
            mock.patch.object(ai_wt, "load_config", return_value=config),
            mock.patch.object(ai_wt, "load_sessions", return_value=[metadata]),
            mock.patch.object(ai_wt, "interactive_gum_path", return_value="gum"),
            mock.patch.object(ai_wt, "session_resumable", return_value=True),
            mock.patch.object(ai_wt, "select_session_gum", return_value=metadata),
            mock.patch.object(ai_wt, "resume_session", return_value=3) as resume,
        ):
            result = ai_wt.cmd_resume_list(["--state-dir", "custom-state"])

        self.assertEqual(result, 3)
        resume.assert_called_once_with(config, metadata, [], auto=False)

    def test_list_safe_cleanup_preserves_loaded_config(self) -> None:
        config = make_config()
        metadata = {"session_id": "session-1"}
        with (
            mock.patch.object(ai_wt, "resolve_repo_root", return_value=config.repo_root),
            mock.patch.object(ai_wt, "load_config", return_value=config),
            mock.patch.object(ai_wt, "load_sessions", return_value=[metadata]),
            mock.patch.object(ai_wt, "interactive_gum_path", return_value="gum"),
            mock.patch.object(ai_wt, "select_session_gum", return_value=metadata),
            mock.patch.object(ai_wt, "session_resumable", return_value=True),
            mock.patch.object(
                ai_wt,
                "choose_gum_action",
                return_value="Cleanup (safe: clean worktrees only)",
            ),
            mock.patch.object(ai_wt, "manual_cleanup_session", return_value=True) as cleanup,
        ):
            result = ai_wt.cmd_list(["--state-dir", "custom-state"])

        self.assertEqual(result, 0)
        cleanup.assert_called_once_with(
            config,
            metadata,
            delete=False,
            force=False,
            dry_run=False,
            yes=False,
        )

    def test_cleanup_dry_run_never_confirms(self) -> None:
        config = make_config()
        metadata = {"session_id": "session-1", "worktree_path": "/missing"}
        with (
            mock.patch.object(ai_wt, "cleanup_session", return_value=True) as cleanup,
            mock.patch.object(ai_wt, "confirm_plain") as confirm,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = ai_wt.manual_cleanup_session(
                config,
                metadata,
                delete=True,
                force=False,
                dry_run=True,
                yes=False,
            )

        self.assertTrue(result)
        confirm.assert_not_called()
        cleanup.assert_called_once_with(
            config,
            metadata,
            delete=True,
            force=False,
            dry_run=True,
            auto=False,
        )

    def test_cleanup_requires_yes_without_a_tty(self) -> None:
        config = make_config()
        metadata = {"session_id": "session-1", "worktree_path": "/missing"}
        with (
            mock.patch.object(ai_wt.sys, "stdin", io.StringIO()),
            mock.patch.object(ai_wt, "cleanup_session") as cleanup,
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(ai_wt.AiWtError, "requires --yes"),
        ):
            ai_wt.manual_cleanup_session(
                config,
                metadata,
                delete=False,
                force=False,
                dry_run=False,
                yes=False,
            )
        cleanup.assert_not_called()

    def test_cleanup_cancel_is_a_no_op(self) -> None:
        config = make_config()
        metadata = {"session_id": "session-1", "worktree_path": "/missing"}
        tty = mock.Mock()
        tty.isatty.return_value = True
        with (
            mock.patch.object(ai_wt.sys, "stdin", tty),
            mock.patch.object(ai_wt, "ui_backend", return_value="plain"),
            mock.patch("builtins.input", return_value=""),
            mock.patch.object(ai_wt, "cleanup_session") as cleanup,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = ai_wt.manual_cleanup_session(
                config,
                metadata,
                delete=False,
                force=False,
                dry_run=False,
                yes=False,
            )

        self.assertTrue(result)
        cleanup.assert_not_called()

    def test_safe_cleanup_does_not_turn_yes_into_force(self) -> None:
        config = make_config()
        metadata = {"session_id": "session-1", "worktree_path": "/worktree"}
        with (
            mock.patch.object(ai_wt, "session_worktree_state", return_value="dirty"),
            mock.patch.object(ai_wt, "cleanup_session") as cleanup,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = ai_wt.manual_cleanup_session(
                config,
                metadata,
                delete=False,
                force=False,
                dry_run=False,
                yes=True,
            )

        self.assertFalse(result)
        cleanup.assert_not_called()

    def test_prune_plans_once_and_skips_dirty_without_force(self) -> None:
        config = make_config()
        stale = {"session_id": "stale", "_metadata_path": "/tmp/stale.json"}
        clean = {"session_id": "clean", "worktree_path": "/tmp/clean"}
        dirty = {"session_id": "dirty", "worktree_path": "/tmp/dirty"}
        states = {"stale": "missing", "clean": "clean", "dirty": "dirty"}
        with (
            mock.patch.object(ai_wt, "resolve_repo_root", return_value=config.repo_root),
            mock.patch.object(ai_wt, "load_config", return_value=config),
            mock.patch.object(ai_wt, "load_sessions", return_value=[stale, clean, dirty]),
            mock.patch.object(
                ai_wt,
                "session_worktree_state",
                side_effect=lambda item: states[item["session_id"]],
            ),
            mock.patch.object(ai_wt, "remove_metadata", return_value=True) as remove,
            mock.patch.object(ai_wt, "cleanup_session", return_value=True) as cleanup,
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            result = ai_wt.cmd_prune(["--yes"])

        self.assertEqual(result, 0)
        remove.assert_called_once_with(config, stale)
        cleanup.assert_called_once_with(
            config,
            clean,
            delete=False,
            force=False,
            dry_run=False,
            auto=False,
        )
        self.assertIn("2 removed, 1 skipped, 0 failed", stdout.getvalue())

    def test_prune_requires_yes_before_mutation_without_a_tty(self) -> None:
        config = make_config()
        clean = {"session_id": "clean", "worktree_path": "/tmp/clean"}
        with (
            mock.patch.object(ai_wt, "resolve_repo_root", return_value=config.repo_root),
            mock.patch.object(ai_wt, "load_config", return_value=config),
            mock.patch.object(ai_wt, "load_sessions", return_value=[clean]),
            mock.patch.object(ai_wt, "session_worktree_state", return_value="clean"),
            mock.patch.object(ai_wt.sys, "stdin", io.StringIO()),
            mock.patch.object(ai_wt, "cleanup_session") as cleanup,
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(ai_wt.AiWtError, "requires --yes"),
        ):
            ai_wt.cmd_prune([])
        cleanup.assert_not_called()

    def test_prune_force_is_explicitly_forwarded_for_dirty_worktrees(self) -> None:
        config = make_config()
        dirty = {"session_id": "dirty", "worktree_path": "/tmp/dirty"}
        with (
            mock.patch.object(ai_wt, "resolve_repo_root", return_value=config.repo_root),
            mock.patch.object(ai_wt, "load_config", return_value=config),
            mock.patch.object(ai_wt, "load_sessions", return_value=[dirty]),
            mock.patch.object(ai_wt, "session_worktree_state", return_value="dirty"),
            mock.patch.object(ai_wt, "cleanup_session", return_value=True) as cleanup,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = ai_wt.cmd_prune(["--force", "--yes"])

        self.assertEqual(result, 0)
        cleanup.assert_called_once_with(
            config,
            dirty,
            delete=False,
            force=True,
            dry_run=False,
            auto=False,
        )

    def test_prune_skips_invalid_worktree_paths_even_with_force(self) -> None:
        config = make_config()
        invalid = {"session_id": "invalid", "worktree_path": "/tmp/not-a-directory"}
        with (
            mock.patch.object(ai_wt, "resolve_repo_root", return_value=config.repo_root),
            mock.patch.object(ai_wt, "load_config", return_value=config),
            mock.patch.object(ai_wt, "load_sessions", return_value=[invalid]),
            mock.patch.object(ai_wt, "session_worktree_state", return_value="invalid"),
            mock.patch.object(ai_wt, "cleanup_session") as cleanup,
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            result = ai_wt.cmd_prune(["--force", "--yes"])

        self.assertEqual(result, 0)
        cleanup.assert_not_called()
        self.assertIn("Invalid worktree paths to skip: 1", stdout.getvalue())
        self.assertIn("No valid sessions can be pruned", stdout.getvalue())


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ai-wt-doctor-")
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "AI WT Tests")
        self.git("config", "user.email", "ai-wt@example.invalid")
        (self.repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "-qm", "initial")
        self.git("config", "ai-wt.opencodeCommand", sys.executable)
        self.git("config", "ai-wt.claudeCommand", sys.executable)
        self.common_dir = Path(
            self.git("rev-parse", "--path-format=absolute", "--git-common-dir", capture=True)
        ).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *args: str, capture: bool = False) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return result.stdout.strip() if capture else ""

    def args(self, **overrides):
        values = {
            "autofix": False,
            "yes": False,
            "state_dir": None,
            "worktree_parent": None,
            "path_template": None,
            "base_ref": None,
            "cleanup_dirty": None,
            "delete_branch_on_cleanup": None,
            "update_exclude": None,
            "submodule_init": None,
            "opencode_profile": None,
            "opencode_command": None,
            "claude_command": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def exclude_file(self) -> Path:
        return self.common_dir / "info" / "exclude"

    def set_healthy_excludes(self) -> None:
        path = self.exclude_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("/.ai-wt/\n/worktrees/\n", encoding="utf-8")

    def clear_excludes(self) -> None:
        path = self.exclude_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def collect(self, **overrides):
        return ai_wt.collect_doctor_report(
            self.repo,
            self.common_dir,
            self.args(**overrides),
        )

    def write_metadata(self, session_id: str, worktree: Path) -> Path:
        sessions = self.repo / ".ai-wt" / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        path = sessions / f"{session_id}.json"
        path.write_text(
            json.dumps(
                {
                    "version": ai_wt.VERSION,
                    "session_id": session_id,
                    "repo_root": str(self.repo),
                    "tool": "opencode",
                    "branch": "feat/doctor-test",
                    "worktree_path": str(worktree),
                    "created_at": "2026-08-21T00:00:00Z",
                    "updated_at": "2026-08-21T00:00:00Z",
                    "cleanup_status": "retained_dirty",
                }
            ),
            encoding="utf-8",
        )
        return path

    def codes(self, report) -> set[str]:
        return {item.code for item in report.findings}

    def test_healthy_report_is_read_only_and_has_all_sections(self) -> None:
        self.set_healthy_excludes()
        state_dir = self.repo / ".ai-wt"
        before = self.exclude_file().read_bytes()

        report, _ = self.collect()

        self.assertFalse(report.actionable)
        self.assertEqual({item.section for item in report.findings}, set(ai_wt.DOCTOR_SECTIONS))
        self.assertFalse(state_dir.exists())
        self.assertFalse((self.repo / "worktrees").exists())
        self.assertEqual(self.exclude_file().read_bytes(), before)

    def test_missing_excludes_autofix_is_idempotent(self) -> None:
        self.clear_excludes()
        report, config = self.collect()
        self.assertEqual([fix.kind for fix in report.fixes], ["exclude", "exclude"])

        results = ai_wt.apply_doctor_fixes(config, report.fixes)
        self.assertTrue(all(item.success for item in results))
        first = self.exclude_file().read_text(encoding="utf-8")
        self.assertEqual(first.count("/.ai-wt/"), 1)
        self.assertEqual(first.count("/worktrees/"), 1)

        final, _ = self.collect()
        self.assertNotIn("ignore.state", {item.code for item in final.actionable})
        self.assertEqual(final.fixes, [])
        self.assertEqual(self.exclude_file().read_text(encoding="utf-8"), first)

    def test_update_exclude_false_reports_without_fixing(self) -> None:
        self.git("config", "ai-wt.updateExclude", "false")
        self.clear_excludes()
        before = self.exclude_file().read_bytes()

        report, _ = self.collect()

        self.assertIn("config.update-exclude-disabled", self.codes(report))
        self.assertIn("ignore.state", {item.code for item in report.actionable})
        self.assertEqual(report.fixes, [])
        self.assertEqual(self.exclude_file().read_bytes(), before)

    def test_valid_stale_metadata_is_the_only_metadata_autofix(self) -> None:
        self.set_healthy_excludes()
        stale = self.write_metadata("stale-session", self.repo / "missing-worktree")
        malformed = stale.parent / "broken.json"
        malformed.write_text("{not json", encoding="utf-8")

        report, config = self.collect()
        metadata_fixes = [fix for fix in report.fixes if fix.kind == "metadata"]
        self.assertEqual([fix.target for fix in metadata_fixes], [stale])
        self.assertIn("metadata.malformed", self.codes(report))

        with mock.patch.object(ai_wt, "cleanup_session") as cleanup:
            results = ai_wt.apply_doctor_fixes(config, metadata_fixes)

        self.assertTrue(all(item.success for item in results))
        self.assertFalse(stale.exists())
        self.assertTrue(malformed.exists())
        cleanup.assert_not_called()

    def test_stale_metadata_revalidation_retains_changed_or_revived_session(self) -> None:
        self.set_healthy_excludes()
        stale = self.write_metadata("stale-session", self.repo / "missing-worktree")
        report, config = self.collect()
        fix = next(item for item in report.fixes if item.kind == "metadata")
        stale.write_bytes(stale.read_bytes() + b"\n")
        changed = ai_wt.apply_doctor_metadata_fix(config, fix)
        self.assertFalse(changed.success)
        self.assertTrue(stale.exists())

        revived_path = self.repo / "revived-worktree"
        revived = self.write_metadata("revived-session", revived_path)
        report, config = self.collect()
        fix = next(item for item in report.fixes if item.target == revived)
        revived_path.mkdir()
        result = ai_wt.apply_doctor_metadata_fix(config, fix)
        self.assertFalse(result.success)
        self.assertTrue(revived.exists())

    def test_dirty_retained_worktree_and_registry_mismatch_are_reported(self) -> None:
        self.set_healthy_excludes()
        self.write_metadata("root-session", self.repo)
        (self.repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        report, _ = self.collect()
        self.assertIn("worktree.dirty", self.codes(report))

        other = self.repo.parent / "unregistered-worktree"
        other.mkdir()
        self.write_metadata("unregistered-session", other)
        report, _ = self.collect()
        self.assertIn("registry.missing", self.codes(report))
        self.assertIn("worktree.status", self.codes(report))

    def test_registered_managed_orphan_is_reported(self) -> None:
        self.set_healthy_excludes()
        orphan = self.repo / "worktrees" / "orphan"
        orphan.parent.mkdir()
        self.git("worktree", "add", "-q", "-b", "feat/orphan", str(orphan), "HEAD")

        report, _ = self.collect()

        self.assertIn("registry.orphan", self.codes(report))

    def test_unsafe_template_does_not_expand_orphan_scope(self) -> None:
        self.set_healthy_excludes()
        sibling = self.repo.parent / "unrelated-worktree"
        with mock.patch.object(ai_wt, "doctor_worktree_paths", return_value=({sibling}, 0)):
            report, _ = self.collect(path_template="{repo_root}")

        self.assertIn("config.path-template-safety", self.codes(report))
        self.assertNotIn("registry.orphan", self.codes(report))

    def test_invalid_configuration_tools_and_risky_cleanup_are_findings(self) -> None:
        self.set_healthy_excludes()
        self.git("config", "ai-wt.updateExclude", "maybe")
        self.git("config", "ai-wt.cleanupDirty", "delete")
        self.git("config", "ai-wt.deleteBranchOnCleanup", "true")
        self.git("config", "ai-wt.opencodeCommand", "/definitely/missing-opencode")
        self.git("config", "ai-wt.claudeCommand", "/definitely/missing-claude")

        report, _ = self.collect(path_template="{unknown}", base_ref="missing-ref")

        codes = self.codes(report)
        self.assertIn("config.updateExclude", codes)
        self.assertIn("config.path-template", codes)
        self.assertIn("config.base-ref", codes)
        self.assertIn("tool.opencode", codes)
        self.assertIn("tool.claude", codes)
        self.assertIn("risk.cleanup-dirty", codes)
        self.assertIn("risk.delete-branch", codes)
        self.assertNotIn("config.update-exclude", codes)

    def test_plain_and_gum_reports_use_the_selected_backend(self) -> None:
        self.set_healthy_excludes()
        report, _ = self.collect()
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            ai_wt.render_doctor_report(report, gum=None)
        self.assertIn("ai-wt doctor", stdout.getvalue())
        self.assertIn("Summary:", stdout.getvalue())

        with (
            mock.patch.object(ai_wt, "doctor_gum_table") as table,
            mock.patch.object(ai_wt, "present_summary") as summary,
        ):
            ai_wt.render_doctor_report(report, gum="/usr/bin/gum")
        table.assert_called_once_with("/usr/bin/gum", report.findings)
        self.assertGreaterEqual(summary.call_count, 2)

    def test_doctor_gum_table_sanitizes_csv_and_removes_it(self) -> None:
        finding = ai_wt.DoctorFinding("Repository", "WARN", "test.code", "line,one\r\nline two")
        captured: Path | None = None

        def fake_run(_gum, command, **_kwargs):
            nonlocal captured
            captured = Path(command[command.index("--file") + 1])
            with captured.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows, [["Repository", "WARN", "test.code", "line,one line two"]])
            return ""

        with mock.patch.object(ai_wt, "run_gum_selection", side_effect=fake_run):
            ai_wt.doctor_gum_table("/usr/bin/gum", [finding])
        self.assertIsNotNone(captured)
        self.assertFalse(captured.exists())

    def test_noninteractive_autofix_requires_yes_and_yes_applies(self) -> None:
        self.clear_excludes()
        patches = (
            mock.patch.object(ai_wt, "resolve_repo_root", return_value=self.repo),
            mock.patch.object(ai_wt, "resolve_git_common_dir", return_value=self.common_dir),
            mock.patch.object(ai_wt, "ui_backend", return_value="plain"),
            mock.patch.object(ai_wt.sys, "stdin", io.StringIO()),
        )
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(ai_wt.AiWtError, "requires --yes"),
        ):
            ai_wt.cmd_doctor(["--autofix"])

        with (
            mock.patch.object(ai_wt, "resolve_repo_root", return_value=self.repo),
            mock.patch.object(ai_wt, "resolve_git_common_dir", return_value=self.common_dir),
            mock.patch.object(ai_wt, "ui_backend", return_value="plain"),
            mock.patch.object(ai_wt.sys, "stdin", io.StringIO()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = ai_wt.cmd_doctor(["--autofix", "--yes"])
        self.assertEqual(result, 0)
        self.assertIn("/.ai-wt/", self.exclude_file().read_text(encoding="utf-8"))

    def test_dispatch_usage_and_exit_statuses(self) -> None:
        self.assertIn("doctor", ai_wt.usage())
        with mock.patch.object(ai_wt, "cmd_doctor", return_value=1) as doctor:
            self.assertEqual(ai_wt.main(["doctor", "--base-ref", "HEAD"]), 1)
        doctor.assert_called_once_with(["--base-ref", "HEAD"])

        with mock.patch.object(ai_wt, "cmd_doctor", side_effect=ai_wt.AiWtError("fatal")):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(ai_wt.main(["doctor"]), 2)


if __name__ == "__main__":
    unittest.main()
