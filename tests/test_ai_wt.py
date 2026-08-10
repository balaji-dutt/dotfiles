from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_WRAPPER = REPO_ROOT / "bin" / "executable_ai-wt.tmpl"


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


if __name__ == "__main__":
    unittest.main()
