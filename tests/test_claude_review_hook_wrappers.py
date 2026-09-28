"""Subprocess tests for Claude review-hook fallback behavior."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.support.fixtures import init_git_repository, isolated_environment, run_git


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude/hooks"


def resolve_hook_bash() -> str | None:
    candidates = [shutil.which("bash")]
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidates.insert(0, str(Path(git).parent.parent / "bin" / "bash.exe"))
    for candidate in dict.fromkeys(candidates):
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            probe = subprocess.run(
                [
                    candidate,
                    "-c",
                    'test -f "$1" && test -d "$2"',
                    "bash",
                    str(HOOKS / "mark-needs-review-bash.sh"),
                    tempfile.gettempdir(),
                ],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return candidate
    return None


BASH = resolve_hook_bash()
BROKEN_RESOLVERS = {
    "syntax error": "if then\n",
    "set -u": "x=$UNSET_VAR\n",
    "exit": "exit 0\n",
    "no function": ":\n",
}


def hook_shells() -> list[str]:
    """The PATH bash, plus /bin/bash when it is the 3.x that macOS ships."""
    shells = [BASH] if BASH else []
    system = "/bin/bash"
    if os.path.exists(system) and not any(os.path.samefile(system, shell) for shell in shells):
        major = subprocess.run([system, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, check=False)
        if major.stdout.strip() == "3":
            shells.append(system)
    return shells


def write_shell_executable(path: Path, body: str) -> None:
    path.write_bytes(body.encode("utf-8"))
    path.chmod(0o755)


def shell_command(name: str) -> str | None:
    candidate = shutil.which(name)
    if candidate:
        return candidate
    if os.name == "nt" and BASH:
        bundled = Path(BASH).parent.parent / "usr" / "bin" / f"{name}.exe"
        if bundled.is_file():
            return str(bundled)
    return None


@unittest.skipUnless(BASH, "requires bash that can access the checkout and temporary files")
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
        if os.name == "nt":
            bash_env = self.isolated.root / "bash-env"
            bash_env.write_bytes(b'PATH="$(cygpath -u "$REVIEW_FAKE_BIN"):$PATH"\n')
            self.env["BASH_ENV"] = str(bash_env)
            self.env["REVIEW_FAKE_BIN"] = str(self.isolated.fake_bin)

    def fake_python(self, name: str, *, probe: int, helper: int | None = None) -> None:
        helper_code = probe if helper is None else helper
        write_shell_executable(
            self.isolated.fake_bin / name,
            "#!/bin/sh\n"
            f"if [ \"${{1:-}}\" = \"-3\" ]; then shift; fi\n"
            f"if [ \"${{1:-}}\" = \"-c\" ]; then exit {probe}; fi\n"
            f"exit {helper_code}\n",
        )

    def install_fallback_commands(self) -> None:
        mkdir = shell_command("mkdir")
        cat = shell_command("cat")
        assert mkdir and cat
        write_shell_executable(self.isolated.fake_bin / "mkdir", f'#!/bin/sh\nexec "{mkdir}" "$@"\n')
        write_shell_executable(self.isolated.fake_bin / "date", "#!/bin/sh\nprintf '1234567890\\n'\n")
        write_shell_executable(self.isolated.fake_bin / "cat", f'#!/bin/sh\nexec "{cat}" "$@"\n')

    def real_helper_env(self) -> dict[str, str]:
        env = self.env.copy()
        git = shutil.which("git")
        assert git
        env["PATH"] = os.pathsep.join((str(self.isolated.fake_bin), str(Path(git).parent)))
        env["CLAUDE_REVIEW_GATE_PYTHON"] = sys.executable
        env["CLAUDE_REVIEW_GATE_STATE_DIR"] = str(self.isolated.root / "state")
        return env

    def run_hook(
        self,
        name: str,
        payload: object | str = None,
        env: dict[str, str] | None = None,
        shell: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if payload is None:
            stdin = "{}"
        elif isinstance(payload, str):
            stdin = payload
        else:
            stdin = json.dumps(payload)
        return subprocess.run(
            [shell or BASH, str(self.repo / ".claude/hooks" / name)],
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

    def test_repairing_the_helper_keeps_the_legacy_marks_whole_repo_scope(self) -> None:
        env = self.real_helper_env()
        self.install_fallback_commands()
        init_git_repository(self.repo, env=env)
        run_git(self.repo, "add", ".claude/hooks", env=env)
        run_git(self.repo, "commit", "-q", "-m", "hooks", env=env)
        helper = self.repo / ".claude/hooks/lib/review_gate.py"
        working = helper.read_text(encoding="utf-8")
        legacy = self.repo / ".claude/.needs_dotfiles_review"

        helper.write_text("raise SystemExit(3)\n", encoding="utf-8")
        snapshot = self.run_hook("snapshot-before-bash.sh", self.bash_payload("edit-y"), env=env)
        self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
        (self.repo / "y.txt").write_text("y\n", encoding="utf-8")
        mark = self.run_hook("mark-needs-review-bash.sh", self.bash_payload("edit-y"), env=env)
        self.assertEqual(mark.returncode, 0, mark.stderr)
        self.assertTrue(legacy.exists())

        helper.write_text(working, encoding="utf-8")
        repair = {"cwd": str(self.repo), "session_id": "bash-session", "tool_input": {"file_path": str(helper)}}
        mark = self.run_hook("mark-needs-review.sh", repair, env=env)
        self.assertEqual(mark.returncode, 0, mark.stderr)
        self.assertFalse(legacy.exists())

        enforce = self.run_hook("enforce-review-on-stop.sh", {"session_id": "bash-session"}, env=env)
        self.assertEqual(enforce.returncode, 0, enforce.stderr)
        self.assertIn("y.txt", json.loads(enforce.stdout)["reason"])

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
        resolver.write_bytes((resolver.read_text(encoding="utf-8") + "\n# edited\n").encode("utf-8"))
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
        (self.repo / ".claude/hooks/lib/resolve-python.sh").write_bytes(b"if then\n")
        pre = self.run_hook("snapshot-before-bash.sh", self.bash_payload("t1"))
        self.assertEqual((pre.returncode, pre.stdout), (0, ""), pre.stderr)
        post = self.run_hook("mark-needs-review-bash.sh", self.bash_payload("t1"))
        self.assertEqual(post.returncode, 0, post.stderr)
        self.assertEqual((self.repo / ".claude/.needs_dotfiles_review").read_text(encoding="utf-8"), "1234567890\n")

    def test_aborting_resolver_takes_every_hooks_fallback(self) -> None:
        self.install_fallback_commands()
        self.fake_python("python3", probe=0)
        resolver = self.repo / ".claude/hooks/lib/resolve-python.sh"
        legacy = self.repo / ".claude/.needs_dotfiles_review"
        edit = {"cwd": str(self.repo), "session_id": "s", "tool_input": {"file_path": str(self.repo / "a.txt")}}
        subagent = {"session_id": "s", "agent_id": "a1", "agent_type": "dotfiles-reviewer"}
        for shell in hook_shells():
            for label, body in BROKEN_RESOLVERS.items():
                with self.subTest(shell=shell, resolver=label):
                    resolver.write_bytes(body.encode("utf-8"))
                    legacy.unlink(missing_ok=True)
                    stop = self.run_hook("enforce-review-on-stop.sh", {"session_id": "s"}, shell=shell)
                    self.assertEqual((stop.returncode, stop.stdout), (0, ""), stop.stderr)
                    pre = self.run_hook("snapshot-before-bash.sh", self.bash_payload("t1"), shell=shell)
                    self.assertEqual((pre.returncode, pre.stdout), (0, ""), pre.stderr)
                    for name, payload in (("mark-needs-review.sh", edit), ("mark-needs-review-bash.sh", self.bash_payload("t1"))):
                        legacy.unlink(missing_ok=True)
                        mark = self.run_hook(name, payload, shell=shell)
                        self.assertEqual(mark.returncode, 0, mark.stderr)
                        self.assertEqual(legacy.read_text(encoding="utf-8"), "1234567890\n")
                        if label == "no function" and name == "mark-needs-review-bash.sh":
                            self.assertNotIn("resolve_python: command not found", mark.stderr)
                    for name in ("record-reviewer-start.sh", "clear-needs-review-on-pass.sh"):
                        result = self.run_hook(name, subagent, shell=shell)
                        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
                        self.assertTrue(legacy.exists())
                    stop = self.run_hook("enforce-review-on-stop.sh", {"session_id": "s"}, shell=shell)
                    self.assertEqual(stop.returncode, 0, stop.stderr)
                    self.assertEqual(json.loads(stop.stdout)["decision"], "block")

    def test_bash_mark_writes_legacy_mark_for_a_committed_broken_resolver(self) -> None:
        self.install_fallback_commands()
        self.fake_python("python3", probe=0)
        git = shutil.which("git")
        assert git
        env = self.env.copy()
        env["PATH"] = os.pathsep.join((str(self.isolated.fake_bin), str(Path(git).parent)))
        init_git_repository(self.repo, env=env)
        (self.repo / ".claude/hooks/lib/resolve-python.sh").write_bytes(b"x=$UNSET_VAR\n")
        run_git(self.repo, "add", ".claude/hooks", env=env)
        run_git(self.repo, "commit", "-q", "-m", "hooks", env=env)
        post = self.run_hook("mark-needs-review-bash.sh", self.bash_payload("t1"), env=env)
        self.assertEqual(post.returncode, 0, post.stderr)
        self.assertEqual((self.repo / ".claude/.needs_dotfiles_review").read_text(encoding="utf-8"), "1234567890\n")

    def test_committed_no_function_resolver_requires_guard(self) -> None:
        env = self.committed_hooks_without_python()
        resolver = self.repo / ".claude/hooks/lib/resolve-python.sh"
        resolver.write_bytes(b":\n")
        run_git(self.repo, "add", str(resolver), env=env)
        run_git(self.repo, "commit", "-q", "-m", "no-function resolver", env=env)
        mark = self.run_hook("mark-needs-review-bash.sh", self.bash_payload("t1"), env=env)
        self.assertEqual(mark.returncode, 0, mark.stderr)
        self.assertNotIn("resolve_python: command not found", mark.stderr)
        self.assertEqual((self.repo / ".claude/.needs_dotfiles_review").read_text(encoding="utf-8"), "1234567890\n")

        hook = self.repo / ".claude/hooks/mark-needs-review-bash.sh"
        guard = b"declare -F resolve_python >/dev/null || exit 90\n"
        original = hook.read_bytes()
        self.assertEqual(original.count(guard), 1)
        hook.write_bytes(original.replace(guard, b""))
        without_guard = self.run_hook("mark-needs-review-bash.sh", self.bash_payload("t1"), env=env)
        self.assertIn("resolve_python: command not found", without_guard.stderr)

    def test_no_hook_sources_the_resolver_in_process(self) -> None:
        in_process = re.compile(r'(?:^|\s)(?:\.|source)\s+"?\$\{?RESOLVER\b', re.MULTILINE)
        hooks = [hook for hook in sorted(HOOKS.glob("*.sh")) if "resolve-python.sh" in hook.read_text(encoding="utf-8")]
        self.assertEqual(len(hooks), 7)
        for hook in hooks:
            with self.subTest(hook=hook.name):
                text = hook.read_text(encoding="utf-8")
                self.assertNotRegex(text, in_process)
                self.assertIn('"$BASH" -euo pipefail -c', text)

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
