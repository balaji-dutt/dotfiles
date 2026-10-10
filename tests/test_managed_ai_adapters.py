from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support.fixtures import compatible_bash, isolated_environment, write_executable, write_python_command


REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = REPO_ROOT / "tests" / "support" / "test_managed_ai_adapters.mjs"
BD_GATE = REPO_ROOT / "dot_claude" / "hooks" / "executable_gate-bd-destructive.sh"
EXITPLAN_GUARD = REPO_ROOT / "dot_claude" / "hooks" / "executable_exitplan-freshness-guard.py"
BASH = compatible_bash(BD_GATE, Path(tempfile.gettempdir()))


class ManagedNodeAdapterTests(unittest.TestCase):
    def test_node_contracts(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is required")
        with isolated_environment(prefix="managed-ai-node-") as isolated:
            env = {
                name: value
                for name, value in isolated.env.items()
                if not name.startswith(("OPENCODE_", "ANTHROPIC_", "OPENAI_", "GOOGLE_", "GEMINI_"))
            }
            env["DOTFILES_TEST_REPO"] = str(REPO_ROOT)
            result = subprocess.run(
                [node, "--test", "--test-reporter=tap", str(NODE_TEST)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=45,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pass 4", result.stdout)
        self.assertIn("fail 0", result.stdout)
        self.assertIn("skipped 0", result.stdout)


@unittest.skipUnless(BASH, "bash is required")
class DestructiveBeadsGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = isolated_environment(prefix="bd-destructive-gate-")
        self.isolated = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        jq = f"""#!{sys.executable}
import json, sys
raw = sys.stdin.read()
if '-R' in sys.argv:
    print(json.dumps(raw))
    raise SystemExit(0)
try:
    payload = json.loads(raw)
except Exception:
    raise SystemExit(2)
query = sys.argv[-1]
if 'agent_type' in query:
    print(payload.get('agent_type') or '')
elif 'tool_input.command' in query:
    print((payload.get('tool_input') or {{}}).get('command') or '')
"""
        write_python_command(self.isolated.fake_bin, "jq", jq)

    def run_gate(self, agent: str | None, command: str | None) -> subprocess.CompletedProcess[str]:
        payload: dict[str, object] = {}
        if agent is not None:
            payload["agent_type"] = agent
        if command is not None:
            payload["tool_input"] = {"command": command}
        return subprocess.run(
            [BASH, str(BD_GATE)],
            env=self.isolated.env,
            input=json.dumps(payload),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def decision(self, result: subprocess.CompletedProcess[str]) -> tuple[str, str]:
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        return output["permissionDecision"], output["permissionDecisionReason"]

    def test_issue_author_denies_all_destructive_subcommands(self) -> None:
        for subcommand in ("delete", "reopen", "close"):
            with self.subTest(subcommand=subcommand):
                decision, reason = self.decision(
                    self.run_gate("beads-issue-author", f"source env; command bd {subcommand} dots-test")
                )
                self.assertEqual(decision, "deny")
                self.assertIn(f"bd {subcommand}", reason)

    def test_backlog_manager_denies_or_asks_with_deny_precedence(self) -> None:
        for command, expected in (
            ("bd close dots-test", "ask"),
            ("bd reopen dots-test", "deny"),
            ("bd delete dots-test", "deny"),
            ("bd close dots-test && bd delete dots-other", "deny"),
        ):
            with self.subTest(command=command):
                decision, _ = self.decision(self.run_gate("beads-backlog-manager", command))
                self.assertEqual(decision, expected)

    def test_unrelated_and_malformed_requests_fail_open(self) -> None:
        cases = (
            (None, "bd close dots-test"),
            ("other-agent", "bd close dots-test"),
            ("beads-issue-author", "printf 'bd close dots-test'"),
            ("beads-issue-author", "bdelete dots-test"),
            ("beads-issue-author", None),
        )
        for agent, command in cases:
            with self.subTest(agent=agent, command=command):
                result = self.run_gate(agent, command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

        write_executable(self.isolated.fake_bin / "jq", "#!/bin/sh\nexit 2\n")
        result = self.run_gate("beads-issue-author", "bd delete dots-test")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


class ExitPlanFreshnessGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="exitplan guard ")
        self.addCleanup(self.temporary.cleanup)
        self.plan_file = Path(self.temporary.name) / "plan.md"
        self.plan_file.write_text("# Plan v2 — greet\n\n- add --shout\n", encoding="utf-8")

    def run_guard(self, payload: object) -> subprocess.CompletedProcess[str]:
        stdin = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return subprocess.run(
            [sys.executable, str(EXITPLAN_GUARD)],
            env={**os.environ, "PYTHONIOENCODING": "cp1252"},
            input=stdin,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )

    def exit_plan(self, tool_input: object) -> dict[str, object]:
        return {"hook_event_name": "PreToolUse", "tool_name": "ExitPlanMode", "tool_input": tool_input}

    def assert_denied(self, result: subprocess.CompletedProcess[str], expected: str) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(expected, output["permissionDecisionReason"])
        self.assertIn("on its own in a new reply", output["permissionDecisionReason"])
        self.assertIn("If this call was already on its own, stop", output["permissionDecisionReason"])

    def assert_silent(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_denies_plan_older_than_the_plan_file(self) -> None:
        result = self.run_guard(
            self.exit_plan({"plan": "# Plan v1\n", "planFilePath": str(self.plan_file)})
        )
        self.assert_denied(result, str(self.plan_file))

    def test_allows_current_plan_including_whitespace_and_line_ending_drift(self) -> None:
        current = self.plan_file.read_text(encoding="utf-8")
        for plan in (current, current.strip(), "\n" + current.replace("\n", "\r\n") + "\n\n"):
            with self.subTest(plan=plan):
                result = self.run_guard(self.exit_plan({"plan": plan, "planFilePath": str(self.plan_file)}))
                self.assert_silent(result)

    def test_unverifiable_and_unrelated_requests_fail_open(self) -> None:
        missing = Path(self.temporary.name) / "missing.md"
        undecodable = Path(self.temporary.name) / "binary.md"
        undecodable.write_bytes(b"\xff\xfe\x00plan")
        cases = {
            "no plan or path": self.exit_plan({}),
            "no plan": self.exit_plan({"planFilePath": str(self.plan_file)}),
            "no plan path": self.exit_plan({"plan": "# Plan v1\n"}),
            "empty plan path": self.exit_plan({"plan": "# Plan v1\n", "planFilePath": ""}),
            "missing file": self.exit_plan({"plan": "# Plan v1\n", "planFilePath": str(missing)}),
            "directory path": self.exit_plan({"plan": "# Plan v1\n", "planFilePath": self.temporary.name}),
            "undecodable file": self.exit_plan({"plan": "# Plan v1\n", "planFilePath": str(undecodable)}),
            "non-string plan": self.exit_plan({"plan": 7, "planFilePath": str(self.plan_file)}),
            "non-object input": self.exit_plan(["plan"]),
            "other tool": {"tool_name": "Write", "tool_input": {}},
            "non-object payload": [],
            "invalid json": "{not json",
            "empty stdin": "",
        }
        for label, payload in cases.items():
            with self.subTest(case=label):
                self.assert_silent(self.run_guard(payload))


if __name__ == "__main__":
    unittest.main()
