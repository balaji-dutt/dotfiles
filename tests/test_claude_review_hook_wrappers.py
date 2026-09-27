"""Subprocess tests for Claude review-hook fallback behavior."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import unittest

from tests.support.fixtures import init_git_repository, isolated_environment, run_git, write_executable


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude/hooks"
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "requires bash")
class ReviewHookWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = isolated_environment(prefix="review-hook-wrapper-")
        self.isolated = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.repo = self.isolated.root / "repo"
        (self.repo / ".claude").mkdir(parents=True)
        shutil.copytree(HOOKS, self.repo / ".claude/hooks")
        self.env = self.isolated.env.copy()
        self.env["CLAUDE_PROJECT_DIR"] = str(self.repo)
        self.env["PATH"] = str(self.isolated.fake_bin)

    def fake_python(self, name: str, *, probe: int, helper: int | None = None) -> None:
        helper_code = probe if helper is None else helper
        write_executable(
            self.isolated.fake_bin / name,
            "#!/bin/sh\n"
            f"if [ \"${{1:-}}\" = \"-3\" ]; then shift; fi\n"
            f"if [ \"${{1:-}}\" = \"-c\" ]; then exit {probe}; fi\n"
            f"exit {helper_code}\n",
        )

    def install_fallback_commands(self) -> None:
        mkdir = shutil.which("mkdir")
        cat = shutil.which("cat")
        assert mkdir and cat
        write_executable(self.isolated.fake_bin / "mkdir", f'#!/bin/sh\nexec "{mkdir}" "$@"\n')
        write_executable(self.isolated.fake_bin / "date", "#!/bin/sh\nprintf '1234567890\\n'\n")
        write_executable(self.isolated.fake_bin / "cat", f'#!/bin/sh\nexec "{cat}" "$@"\n')

    def real_helper_env(self) -> dict[str, str]:
        env = self.env.copy()
        git = shutil.which("git")
        assert git
        env["PATH"] = os.pathsep.join((str(self.isolated.fake_bin), str(Path(git).parent)))
        env["CLAUDE_REVIEW_GATE_PYTHON"] = sys.executable
        env["CLAUDE_REVIEW_GATE_STATE_DIR"] = str(self.isolated.root / "state")
        return env

    def run_hook(self, name: str, payload: object | str = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        if payload is None:
            stdin = "{}"
        elif isinstance(payload, str):
            stdin = payload
        else:
            stdin = json.dumps(payload)
        return subprocess.run(
            [BASH, str(self.repo / ".claude/hooks" / name)],
            input=stdin,
            text=True,
            capture_output=True,
            cwd=self.repo,
            env=env or self.env,
            check=False,
        )

    def run_resolver(self) -> subprocess.CompletedProcess[str]:
        command = '. .claude/hooks/lib/resolve-python.sh; if resolve_python; then printf "%s\\n" "${PY_CMD[*]}"; else exit 9; fi'
        return subprocess.run([BASH, "-c", command], text=True, capture_output=True, cwd=self.repo, env=self.env, check=False)

    def test_resolver_rejects_failed_candidates(self) -> None:
        for name in ("override", "python3", "python", "py"):
            self.fake_python(name, probe=1)
        self.env["CLAUDE_REVIEW_GATE_PYTHON"] = "override"
        result = self.run_resolver()
        self.assertEqual(result.returncode, 9)
        self.assertEqual(result.stdout, "")

    def test_resolver_accepts_override_and_py_dash_three(self) -> None:
        self.fake_python("override", probe=0)
        self.env["CLAUDE_REVIEW_GATE_PYTHON"] = "override"
        result = self.run_resolver()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "override")

        (self.isolated.fake_bin / "override").unlink()
        self.env.pop("CLAUDE_REVIEW_GATE_PYTHON")
        for name in ("python3", "python"):
            self.fake_python(name, probe=1)
        self.fake_python("py", probe=0)
        result = self.run_resolver()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "py -3")

    def test_mark_fallback_writes_legacy_epoch(self) -> None:
        self.install_fallback_commands()
        for name in ("python3", "python", "py"):
            self.fake_python(name, probe=1)
        result = self.run_hook("mark-needs-review.sh", {"tool_input": {"file_path": "x"}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.repo / ".claude/.needs_dotfiles_review").read_text(encoding="utf-8"), "1234567890\n")

    def test_mark_fallback_runs_when_helper_exits_nonzero(self) -> None:
        self.install_fallback_commands()
        self.fake_python("python3", probe=0, helper=7)
        result = self.run_hook("mark-needs-review.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / ".claude/.needs_dotfiles_review").exists())

    def test_enforce_fallback_blocks_only_with_gate(self) -> None:
        self.install_fallback_commands()
        for name in ("python3", "python", "py"):
            self.fake_python(name, probe=1)
        result = self.run_hook("enforce-review-on-stop.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

        (self.repo / ".claude/.needs_dotfiles_review.session").write_text("1\n", encoding="utf-8")
        result = self.run_hook("enforce-review-on-stop.sh")
        self.assertEqual(json.loads(result.stdout)["decision"], "block")

        for value in ("0", "false", "off"):
            with self.subTest(value=value):
                disabled = self.env.copy()
                disabled["CLAUDE_ENFORCE_REVIEW"] = value
                self.assertEqual(self.run_hook("enforce-review-on-stop.sh", env=disabled).stdout, "")

    def test_clear_keeps_gate_when_python_or_helper_fails(self) -> None:
        gate = self.repo / ".claude/.needs_dotfiles_review.session"
        gate.write_text("1\n", encoding="utf-8")
        for name in ("python3", "python", "py"):
            self.fake_python(name, probe=1)
        result = self.run_hook("clear-needs-review-on-pass.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(gate.exists())

        self.fake_python("python3", probe=0, helper=8)
        result = self.run_hook("clear-needs-review-on-pass.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(gate.exists())

    def test_start_records_nothing_when_python_or_helper_fails(self) -> None:
        payload = {"session_id": "session", "agent_id": "rev1", "agent_type": "dotfiles-reviewer"}
        for name in ("python3", "python", "py"):
            self.fake_python(name, probe=1)
        result = self.run_hook("record-reviewer-start.sh", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

        self.fake_python("python3", probe=0, helper=8)
        result = self.run_hook("record-reviewer-start.sh", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(list((self.repo / ".claude").glob(".dotfiles_review_inflight*")), [])

    def test_wrappers_drive_real_helper_lifecycle(self) -> None:
        env = self.real_helper_env()
        init_git_repository(self.repo, env=env)
        tracked = self.repo / "tracked.txt"
        tracked.write_text("base\n", encoding="utf-8")
        run_git(self.repo, "add", "tracked.txt", env=env)
        run_git(self.repo, "commit", "-m", "baseline", env=env)
        tracked.write_text("changed\n", encoding="utf-8")
        session_id = "real-session"

        mark = self.run_hook(
            "mark-needs-review.sh",
            {"cwd": str(self.repo), "session_id": session_id, "tool_input": {"file_path": str(tracked)}},
            env=env,
        )
        self.assertEqual(mark.returncode, 0, mark.stderr)
        gate = self.repo / f".claude/.needs_dotfiles_review.{session_id}"
        self.assertTrue(gate.exists())
        self.assertEqual(json.loads(gate.read_text(encoding="utf-8"))["files"], ["tracked.txt"])

        enforce = self.run_hook("enforce-review-on-stop.sh", {"session_id": session_id}, env=env)
        self.assertEqual(enforce.returncode, 0, enforce.stderr)
        self.assertEqual(json.loads(enforce.stdout)["decision"], "block")

        reviewer = {"session_id": session_id, "agent_id": "rev1", "agent_type": "dotfiles-reviewer"}
        start = self.run_hook("record-reviewer-start.sh", reviewer, env=env)
        self.assertEqual(start.returncode, 0, start.stderr)
        self.assertEqual(start.stdout, "")
        record = self.repo / f".claude/.dotfiles_review_inflight.{session_id}.rev1"
        self.assertTrue(record.exists())

        enforce = self.run_hook("enforce-review-on-stop.sh", {"session_id": session_id}, env=env)
        self.assertEqual(enforce.returncode, 0, enforce.stderr)
        in_flight = json.loads(enforce.stdout)
        self.assertNotIn("decision", in_flight)
        self.assertIn("dotfiles-reviewer", in_flight["systemMessage"])

        transcript = self.isolated.root / "review.jsonl"
        handback = {
            "type": "tool_use",
            "name": "SubagentHandback",
            "input": {"message": "reviewed\n\nDOTFILES_REVIEWER_RESULT=PASS"},
        }
        transcript.write_text(
            json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "message": {"content": [handback]},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        clear = self.run_hook(
            "clear-needs-review-on-pass.sh",
            {**reviewer, "agent_transcript_path": str(transcript)},
            env=env,
        )
        self.assertEqual(clear.returncode, 0, clear.stderr)
        self.assertFalse(gate.exists())
        self.assertFalse(record.exists())

    def bash_payload(self, tool_use_id: str) -> dict[str, object]:
        return {"cwd": str(self.repo), "session_id": "bash-session", "tool_use_id": tool_use_id, "tool_input": {"command": "true"}}

    def test_bash_wrappers_drive_real_helper(self) -> None:
        env = self.real_helper_env()
        init_git_repository(self.repo, env=env)
        tracked = self.repo / "tracked.txt"
        tracked.write_text("base\n", encoding="utf-8")
        run_git(self.repo, "add", "tracked.txt", env=env)
        run_git(self.repo, "commit", "-m", "baseline", env=env)
        gate = self.repo / ".claude/.needs_dotfiles_review.bash-session"

        for name in ("snapshot-before-bash.sh", "mark-needs-review-bash.sh"):
            result = self.run_hook(name, self.bash_payload("read-only"), env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
        self.assertFalse(gate.exists())

        snapshot = self.run_hook("snapshot-before-bash.sh", self.bash_payload("edit"), env=env)
        self.assertEqual((snapshot.returncode, snapshot.stdout), (0, ""), snapshot.stderr)
        tracked.write_text("changed\n", encoding="utf-8")
        mark = self.run_hook("mark-needs-review-bash.sh", self.bash_payload("edit"), env=env)
        self.assertEqual((mark.returncode, mark.stdout), (0, ""), mark.stderr)
        self.assertEqual(json.loads(gate.read_text(encoding="utf-8"))["files"], ["tracked.txt"])

        enforce = self.run_hook("enforce-review-on-stop.sh", {"session_id": "bash-session"}, env=env)
        self.assertEqual(json.loads(enforce.stdout)["decision"], "block")

    def committed_hooks_without_python(self) -> dict[str, str]:
        for name in ("python3", "python", "py"):
            self.fake_python(name, probe=1)
        self.install_fallback_commands()
        git = shutil.which("git")
        assert git
        env = self.env.copy()
        env["PATH"] = os.pathsep.join((str(self.isolated.fake_bin), str(Path(git).parent)))
        init_git_repository(self.repo, env=env)
        run_git(self.repo, "add", ".claude/hooks", env=env)
        run_git(self.repo, "commit", "-q", "-m", "hooks", env=env)
        return env

    def test_wrappers_gate_a_native_worktree_from_the_project_dir(self) -> None:
        env = self.real_helper_env()
        init_git_repository(self.repo, env=env)
        (self.repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        run_git(self.repo, "add", "tracked.txt", env=env)
        run_git(self.repo, "commit", "-m", "baseline", env=env)
        worktree = self.repo / ".claude/worktrees/x"
        run_git(self.repo, "worktree", "add", "-q", "-b", "worktree-x", str(worktree), env=env)
        (worktree / "edited.txt").write_text("x\n", encoding="utf-8")
        session_id = "wt-session"

        mark = self.run_hook(
            "mark-needs-review.sh",
            {"cwd": str(worktree), "session_id": session_id, "tool_input": {"file_path": str(worktree / "edited.txt")}},
            env=env,
        )
        self.assertEqual(mark.returncode, 0, mark.stderr)
        gate = self.repo / f".claude/.needs_dotfiles_review.{session_id}"
        self.assertEqual(json.loads(gate.read_text(encoding="utf-8"))["roots"], {os.path.realpath(worktree): ["edited.txt"]})

        enforce = self.run_hook("enforce-review-on-stop.sh", {"session_id": session_id, "cwd": str(worktree)}, env=env)
        stop = json.loads(enforce.stdout)
        self.assertEqual(stop["decision"], "block")
        self.assertIn("git -C " + shlex.quote(os.path.realpath(worktree)) + " diff -- edited.txt", stop["reason"])

        reviewer = {"session_id": session_id, "agent_id": "rev1", "agent_type": "dotfiles-reviewer"}
        self.run_hook("record-reviewer-start.sh", reviewer, env=env)
        transcript = self.isolated.root / "review.jsonl"
        transcript.write_text(
            json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "message": {"content": "reviewed\nDOTFILES_REVIEWER_RESULT=PASS"}}) + "\n",
            encoding="utf-8",
        )
        clear = self.run_hook("clear-needs-review-on-pass.sh", {**reviewer, "agent_transcript_path": str(transcript)}, env=env)
        self.assertEqual(clear.returncode, 0, clear.stderr)
        self.assertFalse(gate.exists())

    def test_bash_wrappers_skip_without_python(self) -> None:
        env = self.committed_hooks_without_python()
        pre = self.run_hook("snapshot-before-bash.sh", self.bash_payload("t1"), env=env)
        post = self.run_hook("mark-needs-review-bash.sh", self.bash_payload("t1"), env=env)
        for result in (pre, post):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
        self.assertEqual(pre.stderr, "")
        self.assertIn("Bash edits were not checked", post.stderr)
        self.assertEqual(list((self.repo / ".claude").glob(".needs_dotfiles_review*")), [])

    def test_bash_mark_marks_when_a_modified_resolver_finds_no_python(self) -> None:
        env = self.committed_hooks_without_python()
        resolver = self.repo / ".claude/hooks/lib/resolve-python.sh"
        resolver.write_text(resolver.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        post = self.run_hook("mark-needs-review-bash.sh", self.bash_payload("t1"), env=env)
        self.assertEqual(post.returncode, 0, post.stderr)
        self.assertEqual((self.repo / ".claude/.needs_dotfiles_review").read_text(encoding="utf-8"), "1234567890\n")

    def test_bash_mark_falls_back_to_legacy_gate_when_helper_fails(self) -> None:
        self.install_fallback_commands()
        self.fake_python("python3", probe=0, helper=3)
        legacy = self.repo / ".claude/.needs_dotfiles_review"
        pre = self.run_hook("snapshot-before-bash.sh", self.bash_payload("t1"))
        self.assertEqual((pre.returncode, pre.stdout), (0, ""), pre.stderr)
        self.assertFalse(legacy.exists())
        post = self.run_hook("mark-needs-review-bash.sh", self.bash_payload("t1"))
        self.assertEqual(post.returncode, 0, post.stderr)
        self.assertEqual(legacy.read_text(encoding="utf-8"), "1234567890\n")

    def test_bash_mark_writes_legacy_gate_when_a_helper_file_is_missing(self) -> None:
        self.install_fallback_commands()
        self.fake_python("python3", probe=0)
        legacy = self.repo / ".claude/.needs_dotfiles_review"
        for name in ("review_gate.py", "resolve-python.sh"):
            with self.subTest(missing=name):
                shutil.rmtree(self.repo / ".claude/hooks")
                shutil.copytree(HOOKS, self.repo / ".claude/hooks")
                (self.repo / ".claude/hooks/lib" / name).unlink()
                legacy.unlink(missing_ok=True)
                result = self.run_hook("mark-needs-review-bash.sh", self.bash_payload("t1"))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(legacy.read_text(encoding="utf-8"), "1234567890\n")

    def test_broken_resolver_never_blocks_bash_and_still_marks(self) -> None:
        self.install_fallback_commands()
        self.fake_python("python3", probe=0)
        (self.repo / ".claude/hooks/lib/resolve-python.sh").write_text("if then\n", encoding="utf-8")
        pre = self.run_hook("snapshot-before-bash.sh", self.bash_payload("t1"))
        self.assertEqual((pre.returncode, pre.stdout), (0, ""), pre.stderr)
        post = self.run_hook("mark-needs-review-bash.sh", self.bash_payload("t1"))
        self.assertEqual(post.returncode, 0, post.stderr)
        self.assertEqual((self.repo / ".claude/.needs_dotfiles_review").read_text(encoding="utf-8"), "1234567890\n")

    def test_settings_wire_bash_hooks(self) -> None:
        hooks = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))["hooks"]

        def commands(event: str, matcher: str) -> list[tuple[str, object]]:
            return [
                (hook["command"], hook.get("timeout"))
                for group in hooks.get(event, [])
                if group.get("matcher") == matcher
                for hook in group["hooks"]
            ]

        pre = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/snapshot-before-bash.sh" || true'
        post = 'bash "$CLAUDE_PROJECT_DIR/.claude/hooks/mark-needs-review-bash.sh"'
        self.assertEqual(commands("PreToolUse", "Bash|PowerShell"), [(pre, 10)])
        self.assertEqual(commands("PostToolUse", "Bash|PowerShell"), [(post, 10)])
        self.assertEqual(commands("PostToolUseFailure", "Bash|PowerShell"), [(post, 10)])
        self.assertEqual(
            commands("PostToolUse", "Write|Edit"),
            [('bash "$CLAUDE_PROJECT_DIR/.claude/hooks/mark-needs-review.sh"', None)],
        )

    def test_subagent_hooks_without_project_context_exit_harmlessly(self) -> None:
        env = self.env.copy()
        env.pop("CLAUDE_PROJECT_DIR")
        for name in ("clear-needs-review-on-pass.sh", "record-reviewer-start.sh"):
            with self.subTest(name=name):
                result = self.run_hook(name, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
