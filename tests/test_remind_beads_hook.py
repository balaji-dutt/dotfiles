"""Regression tests for the Beads plan-approval gate (dots-7iav).

Covers .claude/hooks/remind-beads-on-plan-approval.sh and its helper
.claude/hooks/lib/beads_state.py. The bug under test: the hook used to exit 0
on the mere existence of .beads/in-progress-claude.json, so a state file left
behind by an interrupted close disabled the gate silently and indefinitely.

Every blocking assertion checks *which class* of block was emitted, via the
[BEADS_GATE: ...] marker the hook puts at the head of its reason. A regression
that reclassifies a Beads tooling failure as staleness (or the reverse) is a
test failure, not a wording change.

Run: python3 tests/test_remind_beads_hook.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude/hooks/remind-beads-on-plan-approval.sh"
HELPER = ROOT / ".claude/hooks/lib/beads_state.py"

# POSIX-only by construction: the hook is bash, and the fake `bd` shims are
# /bin/sh scripts. Under Git Bash the suite runs; under native Windows Python
# there is no `sh` to point them at.
POSIX_ONLY = unittest.skipIf(
    os.name == "nt" and shutil.which("sh") is None,
    "hook and bd shims require a POSIX shell",
)

EXIT_LIVE = 0
EXIT_STALE = 10
EXIT_UNVERIFIED = 20

ISSUE = "dots-test1"

# The real not-found body, copied from `bd show dots-nope --json` (exit 1).
NOT_FOUND_BODY = json.dumps(
    {"error": 'no issues found matching the provided IDs', "schema_version": 1}
)


def write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def issue_body(status: str, issue_id: str = ISSUE) -> str:
    return json.dumps([{"id": issue_id, "title": "t", "status": status}])


@POSIX_ONLY
class GateTestCase(unittest.TestCase):
    """Builds a throwaway Beads repo plus a scriptable fake `bd` on PATH."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp = Path(self.temp_dir.name)

        self.repo = self.temp / "repo"
        (self.repo / ".beads").mkdir(parents=True)
        (self.repo / ".claude").mkdir()
        (self.repo / ".beads/metadata.json").write_text(
            json.dumps({"dolt_database": "dots"}), encoding="utf-8"
        )
        # The hook resolves its helper relative to CLAUDE_PROJECT_DIR, so the
        # throwaway repo needs the real hook tree. Symlink so the tests run
        # against the actual files rather than a snapshot of them.
        link = self.repo / ".claude/hooks"
        try:
            link.symlink_to(ROOT / ".claude/hooks", target_is_directory=True)
        except (OSError, NotImplementedError):  # e.g. Windows without privilege
            shutil.copytree(ROOT / ".claude/hooks", link)

        self.bin_dir = self.temp / "bin"
        self.bin_dir.mkdir()
        self.call_log = self.temp / "bd-calls.log"

    # --- fixture helpers -------------------------------------------------

    def drop_metadata(self) -> None:
        (self.repo / ".beads/metadata.json").unlink()

    def write_state(self, content: str) -> None:
        (self.repo / ".beads/in-progress-claude.json").write_text(
            content, encoding="utf-8"
        )

    def write_state_for(self, issue_id: str = ISSUE) -> None:
        self.write_state(json.dumps({"id": issue_id, "agent": "Claude"}))

    def fake_bd(self, *, stdout: str = "", stderr: str = "", code: int = 0,
                sleep: float = 0.0) -> None:
        """Install a `bd` shim that logs its argv and replays a canned result."""
        body = "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$BD_CALL_LOG\"\n"
        if sleep:
            body += "sleep %s\n" % sleep
        if stdout:
            body += "cat <<'BD_STDOUT'\n%s\nBD_STDOUT\n" % stdout
        if stderr:
            body += "cat >&2 <<'BD_STDERR'\n%s\nBD_STDERR\n" % stderr
        body += "exit %d\n" % code
        write_executable(self.bin_dir / "bd", body)

    def env(self, *, with_bd: bool = True, timeout: str | None = None) -> dict:
        env = dict(os.environ)
        env.pop("CLAUDE_BEADS_GATE_TIMEOUT", None)
        # A minimal PATH keeps a real `bd` from leaking in from the developer's
        # environment; the shim dir is prepended only when the test wants it.
        base = [str(Path(shutil.which("sh")).parent), "/usr/bin", "/bin"]
        interpreter = shutil.which("python3") or shutil.which("python")
        if interpreter:
            base.insert(0, str(Path(interpreter).parent))
        parts = ([str(self.bin_dir)] if with_bd else []) + base
        env["PATH"] = os.pathsep.join(parts)
        if not with_bd and shutil.which("bd", path=env["PATH"]):
            # The minimal PATH still carries the interpreter's directory, so on
            # a machine where python3 and bd share one (uv/mise/pyenv shim dir,
            # ~/.local/bin) the "missing bd" cases would quietly exercise the
            # real bd instead. Skip rather than assert something untrue.
            self.skipTest("a real bd is on the minimal PATH")
        env["BD_CALL_LOG"] = str(self.call_log)
        env["CLAUDE_PROJECT_DIR"] = str(self.repo)
        if timeout is not None:
            env["CLAUDE_BEADS_GATE_TIMEOUT"] = timeout
        return env

    # --- runners ---------------------------------------------------------

    def run_hook(self, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(HOOK)],
            input="{}",
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            env=self.env(**kwargs),
        )

    def run_helper(self, **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            [shutil.which("python3") or "python3", str(HELPER), "check",
             "--state-file", ".beads/in-progress-claude.json"],
            capture_output=True,
            text=True,
            cwd=str(self.repo),
            env=self.env(**kwargs),
        )

    def bd_calls(self) -> list[str]:
        if not self.call_log.exists():
            return []
        return [ln for ln in self.call_log.read_text(encoding="utf-8").splitlines() if ln]

    # --- assertions ------------------------------------------------------

    def assert_silent(self, result: subprocess.CompletedProcess) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "", "gate should emit nothing")

    def assert_block(self, result: subprocess.CompletedProcess, variant: str) -> str:
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "block")
        reason = payload["reason"]
        self.assertTrue(
            reason.startswith("[BEADS_GATE: %s]" % variant),
            "expected a %r block, got: %s" % (variant, reason[:120]),
        )
        return reason


class SuppressTests(GateTestCase):
    def test_non_beads_repo_is_ignored(self) -> None:
        self.drop_metadata()
        self.write_state_for()
        self.fake_bd(stdout=issue_body("closed"))
        self.assert_silent(self.run_hook())

    def test_open_issue_still_suppresses(self) -> None:
        self.write_state_for()
        self.fake_bd(stdout=issue_body("open"))
        self.assert_silent(self.run_hook())
        self.assertEqual(len(self.bd_calls()), 1)

    def test_in_progress_issue_still_suppresses(self) -> None:
        self.write_state_for()
        self.fake_bd(stdout=issue_body("in_progress"))
        self.assert_silent(self.run_hook())

    def test_blocked_issue_still_suppresses(self) -> None:
        self.write_state_for()
        self.fake_bd(stdout=issue_body("blocked"))
        self.assert_silent(self.run_hook())


class AbsentStateTests(GateTestCase):
    def test_no_state_file_prompts_as_before(self) -> None:
        self.fake_bd(stdout=issue_body("open"))
        reason = self.assert_block(self.run_hook(), "absent")
        self.assertIn("Beads plan handoff", reason)
        self.assertEqual(self.bd_calls(), [], "bd must not be consulted")


class StaleStateTests(GateTestCase):
    """bd answered, and the answer disqualifies the state file."""

    def test_closed_issue_prompts_and_names_the_issue(self) -> None:
        self.write_state_for()
        self.fake_bd(stdout=issue_body("closed"))
        reason = self.assert_block(self.run_hook(), "stale")
        self.assertIn(ISSUE, reason)
        self.assertIn("closed", reason)

    def test_missing_issue_is_stale_not_a_tooling_failure(self) -> None:
        # bd exits 1 for a missing issue; the parsed body is what makes this a
        # real answer rather than a transport failure. Pins one side of the
        # rc=1 discrimination (see test_connection_error_* for the other).
        self.write_state_for()
        self.fake_bd(stdout=NOT_FOUND_BODY, code=1)
        reason = self.assert_block(self.run_hook(), "stale")
        self.assertIn(ISSUE, reason)

    def test_empty_result_list_is_stale(self) -> None:
        self.write_state_for()
        self.fake_bd(stdout="[]")
        self.assert_block(self.run_hook(), "stale")

    def test_malformed_state_file_is_stale_without_calling_bd(self) -> None:
        self.write_state("this is not json")
        self.fake_bd(stdout=issue_body("open"))
        self.assert_block(self.run_hook(), "stale")
        self.assertEqual(self.bd_calls(), [])

    def test_state_file_without_id_is_stale_without_calling_bd(self) -> None:
        self.write_state(json.dumps({"agent": "Claude"}))
        self.fake_bd(stdout=issue_body("open"))
        self.assert_block(self.run_hook(), "stale")
        self.assertEqual(self.bd_calls(), [])

    def test_state_file_with_blank_id_is_stale(self) -> None:
        self.write_state(json.dumps({"id": "   ", "agent": "Claude"}))
        self.fake_bd(stdout=issue_body("open"))
        self.assert_block(self.run_hook(), "stale")
        self.assertEqual(self.bd_calls(), [])


class UnverifiedTests(GateTestCase):
    """bd could not answer. Must be reported as a failure, never as silence."""

    def test_missing_bd_blocks_and_names_bd(self) -> None:
        self.write_state_for()
        reason = self.assert_block(self.run_hook(with_bd=False), "unverified")
        self.assertIn("bd", reason)

    def test_connection_error_is_unverified_not_stale(self) -> None:
        # Same rc=1 as a missing issue, but nothing parseable on stdout. Pins
        # the other side of the discrimination against the not-found case.
        self.write_state_for()
        self.fake_bd(stderr="dial tcp 127.0.0.1:3306: connection refused", code=1)
        reason = self.assert_block(self.run_hook(), "unverified")
        self.assertIn(ISSUE, reason)

    def test_unparseable_success_output_is_unverified(self) -> None:
        self.write_state_for()
        self.fake_bd(stdout="Dolt server endpoint changed: port 35950 -> 53210")
        self.assert_block(self.run_hook(), "unverified")

    def test_unknown_status_is_unverified(self) -> None:
        self.write_state_for()
        self.fake_bd(stdout=issue_body("hibernating"))
        self.assert_block(self.run_hook(), "unverified")

    def test_bare_not_found_body_is_unverified_not_stale(self) -> None:
        # "not found" alone must not read as staleness: a transport-layer body
        # can carry the same words about the database rather than the issue.
        # Misfiling this as stale would tell the user their issue is gone when
        # the server is merely down.
        self.write_state_for()
        self.fake_bd(
            stdout=json.dumps({"error": "database dots not found", "schema_version": 1}),
            code=1,
        )
        self.assert_block(self.run_hook(), "unverified")

    def test_helper_crash_still_names_a_reason(self) -> None:
        # A helper that dies without printing must not leave the message
        # trailing an empty reason.
        self.write_state_for()
        self.fake_bd(stdout=issue_body("open"))
        broken = self.repo / ".claude/hooks-broken"
        shutil.copytree(ROOT / ".claude/hooks", broken)
        (broken / "lib/beads_state.py").write_text(
            "import sys\nsys.exit(3)\n", encoding="utf-8"
        )
        (self.repo / ".claude/hooks").unlink()
        broken.rename(self.repo / ".claude/hooks")
        reason = self.assert_block(self.run_hook(), "unverified")
        self.assertNotIn("json: .\n", reason)
        self.assertIn("exited 3", reason)

    def test_timeout_blocks_and_is_bounded(self) -> None:
        self.write_state_for()
        self.fake_bd(stdout=issue_body("open"), sleep=10)
        started = time.monotonic()
        result = self.run_hook(timeout="0.5")
        elapsed = time.monotonic() - started
        reason = self.assert_block(result, "unverified")
        self.assertIn("timed out", reason)
        self.assertLess(elapsed, 8, "gate must bound the bd call, not wait it out")

    def test_unverified_block_is_loud_on_stderr(self) -> None:
        # The "fail loudly" half of the contract: the tooling failure has to
        # reach the transcript, not just the model's context.
        self.write_state_for()
        result = self.run_hook(with_bd=False)
        self.assert_block(result, "unverified")
        self.assertIn("remind-beads-on-plan-approval", result.stderr)


class HelperExitCodeTests(GateTestCase):
    """The decision table asserted directly, without driving bash."""

    def test_live_statuses_exit_zero(self) -> None:
        for status in ("open", "in_progress", "blocked"):
            with self.subTest(status=status):
                self.write_state_for()
                self.fake_bd(stdout=issue_body(status))
                self.assertEqual(self.run_helper().returncode, EXIT_LIVE)

    def test_closed_exits_stale(self) -> None:
        self.write_state_for()
        self.fake_bd(stdout=issue_body("closed"))
        self.assertEqual(self.run_helper().returncode, EXIT_STALE)

    def test_not_found_exits_stale(self) -> None:
        self.write_state_for()
        self.fake_bd(stdout=NOT_FOUND_BODY, code=1)
        self.assertEqual(self.run_helper().returncode, EXIT_STALE)

    def test_issue_not_found_phrasing_exits_stale(self) -> None:
        self.write_state_for()
        self.fake_bd(stdout=json.dumps({"error": "issue not found"}), code=1)
        self.assertEqual(self.run_helper().returncode, EXIT_STALE)

    def test_bare_not_found_bodies_exit_unverified(self) -> None:
        # Transport failures that merely contain "not found" must not be read
        # as "your issue is gone" — that would invite deleting a valid state
        # file while the server is simply down.
        for body in (
            "database dots not found",
            "dial tcp 127.0.0.1:3306: host not found",
            "config file not found",
        ):
            with self.subTest(body=body):
                self.write_state_for()
                self.fake_bd(stdout=json.dumps({"error": body}), code=1)
                self.assertEqual(self.run_helper().returncode, EXIT_UNVERIFIED)

    def test_absent_state_file_exits_stale(self) -> None:
        self.fake_bd(stdout=issue_body("open"))
        self.assertEqual(self.run_helper().returncode, EXIT_STALE)

    def test_missing_bd_exits_unverified(self) -> None:
        self.write_state_for()
        self.assertEqual(self.run_helper(with_bd=False).returncode, EXIT_UNVERIFIED)

    def test_transport_failure_exits_unverified(self) -> None:
        self.write_state_for()
        self.fake_bd(stderr="connection refused", code=1)
        result = self.run_helper()
        self.assertEqual(result.returncode, EXIT_UNVERIFIED)
        self.assertIn("connection refused", result.stderr)

    def test_bad_invocation_exits_unverified(self) -> None:
        result = subprocess.run(
            [shutil.which("python3") or "python3", str(HELPER), "wat"],
            capture_output=True, text=True, cwd=str(self.repo), env=self.env(),
        )
        self.assertEqual(result.returncode, EXIT_UNVERIFIED)


if __name__ == "__main__":
    unittest.main()
