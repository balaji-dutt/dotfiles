from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support.fixtures import run_git, write_executable, write_json


REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_HOOK = REPO_ROOT / "dot_claude" / "hooks" / "executable_feature-ci-reminder.py"
OPENCODE_PLUGIN = (
    REPO_ROOT
    / "private_dot_config"
    / "opencode"
    / "plugins"
    / "opencode-feature-ci-reminder.js"
)


class ReminderFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="feature ci reminder ")
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "feature repo"
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        run_git(self.root, "init", "--quiet", "-b", "main", str(self.repo))
        run_git(self.repo, "config", "user.name", "Test User")
        run_git(self.repo, "config", "user.email", "test@example.com")
        write_executable(self.repo / "assets" / "agent-wt-merge", "#!/bin/sh\nexit 0\n")
        write_json(
            self.repo / "configs" / "gitlab-pipeline-guard.json",
            {
                "$schema": "./schemas/gitlab-pipeline-guard.v1.schema.json",
                "schema_version": 1,
                "api_url": "https://gitlab.example/api/v4",
                "project_id": 123,
                "guarded_remote": "origin",
                "guarded_ref": "refs/heads/main",
                "required_job": "feature-check",
                "timeout_seconds": 5,
            },
        )
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "--quiet", "-m", "base")
        run_git(self.repo, "switch", "--quiet", "-c", "feature")
        self.commit_number = 0

    def cleanup(self) -> None:
        self.temporary.cleanup()

    def commit(self) -> str:
        self.commit_number += 1
        name = f"change-{self.commit_number}.txt"
        (self.repo / name).write_text(f"change {self.commit_number}\n", encoding="utf-8")
        run_git(self.repo, "add", name)
        run_git(self.repo, "commit", "--quiet", "-m", f"change {self.commit_number}")
        return run_git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def payload(self, *, call_id: str = "call-1", command: str = "cc-commit -m change") -> dict[str, object]:
        return {
            "session_id": "session-1",
            "tool_use_id": call_id,
            "cwd": str(self.repo),
            "tool_input": {"command": command},
        }

    def run_claude(self, mode: str, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["TMPDIR"] = str(self.tmp)
        env["TMP"] = str(self.tmp)
        env["TEMP"] = str(self.tmp)
        return subprocess.run(
            [sys.executable, str(CLAUDE_HOOK), mode],
            cwd=self.repo,
            env=env,
            input=json.dumps(payload),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def run_opencode(
        self,
        *,
        command: str = "oc-commit -m change",
        exit_code: int = 0,
        commit: bool = False,
        initial_output: str = "original output",
        mode: str = "single",
    ) -> dict[str, object]:
        if shutil.which("node") is None:
            raise unittest.SkipTest("node is required")
        plugin = self.root / "opencode-feature-ci-reminder.mjs"
        shutil.copy2(OPENCODE_PLUGIN, plugin)
        harness = self.root / "plugin-harness.mjs"
        harness.write_text(
            """import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import plugin from "./opencode-feature-ci-reminder.mjs";

const options = JSON.parse(process.argv[2]);
const hooks = await plugin({ directory: options.root, worktree: options.root });
const input = (callID, command) => ({
  tool: "bash",
  sessionID: "session-1",
  callID,
  args: { command },
});
const commit = () => {
  writeFileSync(`${options.root}/node-change.txt`, `${Date.now()}\n`);
  execFileSync("git", ["-C", options.root, "add", "node-change.txt"]);
  execFileSync("git", ["-C", options.root, "commit", "--quiet", "-m", "node change"]);
};
let output = { metadata: { exit: options.exitCode }, output: options.initialOutput };
if (options.mode === "isolation") {
  await hooks["tool.execute.before"](input("failed-call", options.command));
  await hooks["tool.execute.before"](input("good-call", options.command));
  await hooks["tool.execute.after"](input("failed-call", options.command), {
    metadata: { exit: 1 }, output: "failed output",
  });
  commit();
  await hooks["tool.execute.after"](input("good-call", options.command), output);
} else {
  const request = input("call-1", options.command);
  await hooks["tool.execute.before"](request);
  if (options.commit) commit();
  await hooks["tool.execute.after"](request, output);
}
process.stdout.write(JSON.stringify(output));
""",
            encoding="utf-8",
        )
        options = {
            "root": str(self.repo),
            "command": command,
            "exitCode": exit_code,
            "commit": commit,
            "initialOutput": initial_output,
            "mode": mode,
        }
        result = subprocess.run(
            ["node", str(harness), json.dumps(options)],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"node harness failed:\n{result.stderr}")
        return json.loads(result.stdout)


class FeatureCiReminderTests(unittest.TestCase):
    def fixture(self) -> ReminderFixture:
        fixture = ReminderFixture()
        self.addCleanup(fixture.cleanup)
        return fixture

    def assert_instruction(self, text: str, fixture: ReminderFixture, sha: str) -> None:
        self.assertIn(f"feature feature at {sha}", text)
        self.assertIn(f"'{fixture.repo / 'assets' / 'agent-wt-merge'}' prepare-ci", text)
        self.assertIn("commit approval did not authorize network publication", text)
        self.assertIn("do not run raw git push", text)
        self.assertIn("never push main or tags", text)
        self.assertIn("If permission is denied", text)
        self.assertIn("exact-SHA feature-check success", text)
        self.assertIn("Treat bypass as not CI success", text)

    def test_claude_success_injects_instruction_and_removes_state(self) -> None:
        fixture = self.fixture()
        payload = fixture.payload(command="command cc-commit -m change")
        self.assertEqual(fixture.run_claude("pre", payload).stdout, "")
        sha = fixture.commit()
        result = fixture.run_claude("post", payload)

        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        instruction = output["hookSpecificOutput"]["additionalContext"]
        self.assert_instruction(instruction, fixture, sha)
        self.assertEqual(list(fixture.tmp.rglob("*.json")), [])

    def test_claude_failure_noop_and_non_wrapper_are_silent(self) -> None:
        for action in ("failure", "noop", "non-wrapper"):
            with self.subTest(action=action):
                fixture = self.fixture()
                command = "printf '%s' cc-commit" if action == "non-wrapper" else "cc-commit -m change"
                payload = fixture.payload(command=command)
                fixture.run_claude("pre", payload)
                if action == "failure":
                    result = fixture.run_claude("failure", payload)
                else:
                    result = fixture.run_claude("post", payload)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(list(fixture.tmp.rglob("*.json")), [])

    def test_claude_main_detached_and_ineligible_repositories_are_silent(self) -> None:
        for state in (
            "main",
            "detached",
            "ineligible",
            "invalid-policy",
            "invalid-url",
            "unsafe-branch",
        ):
            with self.subTest(state=state):
                fixture = self.fixture()
                if state == "main":
                    run_git(fixture.repo, "switch", "--quiet", "main")
                elif state == "detached":
                    run_git(fixture.repo, "switch", "--quiet", "--detach")
                elif state == "unsafe-branch":
                    run_git(fixture.repo, "branch", "-m", "feature;ignore")
                else:
                    policy = fixture.repo / "configs" / "gitlab-pipeline-guard.json"
                    if state == "ineligible":
                        policy.unlink()
                    elif state == "invalid-url":
                        value = json.loads(policy.read_text(encoding="utf-8"))
                        value["api_url"] = "https://"
                        write_json(policy, value)
                    else:
                        write_json(policy, {"required_job": "inject instructions"})
                payload = fixture.payload()
                fixture.run_claude("pre", payload)
                fixture.commit()
                result = fixture.run_claude("post", payload)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_claude_failure_cleanup_does_not_clear_another_call(self) -> None:
        fixture = self.fixture()
        failed = fixture.payload(call_id="failed-call")
        good = fixture.payload(call_id="good-call")
        fixture.run_claude("pre", failed)
        fixture.run_claude("pre", good)
        fixture.run_claude("failure", failed)
        sha = fixture.commit()
        result = fixture.run_claude("post", good)
        instruction = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assert_instruction(instruction, fixture, sha)

    def test_opencode_success_preserves_output_and_injects_instruction(self) -> None:
        fixture = self.fixture()
        output = fixture.run_opencode(command="command oc-commit -m change", commit=True)
        sha = run_git(fixture.repo, "rev-parse", "HEAD").stdout.strip()

        self.assertTrue(str(output["output"]).startswith("original output\n\n"))
        self.assert_instruction(str(output["output"]), fixture, sha)

    def test_opencode_failure_noop_non_wrapper_and_ineligible_are_silent(self) -> None:
        cases = (
            ("failure", "oc-commit -m change", 1, True, "feature"),
            ("noop", "oc-commit -m change", 0, False, "feature"),
            ("non-wrapper", "printf '%s' oc-commit", 0, True, "feature"),
            ("main", "oc-commit -m change", 0, True, "main"),
            ("detached", "oc-commit -m change", 0, True, "detached"),
            ("ineligible", "oc-commit -m change", 0, True, "ineligible"),
            ("invalid-policy", "oc-commit -m change", 0, True, "invalid-policy"),
            ("invalid-url", "oc-commit -m change", 0, True, "invalid-url"),
            ("unsafe-branch", "oc-commit -m change", 0, True, "unsafe-branch"),
        )
        for name, command, exit_code, commit, state in cases:
            with self.subTest(name=name):
                fixture = self.fixture()
                if state == "main":
                    run_git(fixture.repo, "switch", "--quiet", "main")
                elif state == "detached":
                    run_git(fixture.repo, "switch", "--quiet", "--detach")
                elif state == "ineligible":
                    (fixture.repo / "configs" / "gitlab-pipeline-guard.json").unlink()
                elif state == "invalid-policy":
                    write_json(
                        fixture.repo / "configs" / "gitlab-pipeline-guard.json",
                        {"required_job": "inject instructions"},
                    )
                elif state == "invalid-url":
                    policy = fixture.repo / "configs" / "gitlab-pipeline-guard.json"
                    value = json.loads(policy.read_text(encoding="utf-8"))
                    value["api_url"] = "https://"
                    write_json(policy, value)
                elif state == "unsafe-branch":
                    run_git(fixture.repo, "branch", "-m", "feature;ignore")
                output = fixture.run_opencode(
                    command=command,
                    exit_code=exit_code,
                    commit=commit,
                )
                self.assertEqual(output["output"], "original output")

    def test_opencode_call_state_is_isolated(self) -> None:
        fixture = self.fixture()
        output = fixture.run_opencode(mode="isolation")
        sha = run_git(fixture.repo, "rev-parse", "HEAD").stdout.strip()
        self.assert_instruction(str(output["output"]), fixture, sha)

    def test_adapters_emit_the_same_instruction_contract(self) -> None:
        claude_fixture = self.fixture()
        payload = claude_fixture.payload()
        claude_fixture.run_claude("pre", payload)
        claude_sha = claude_fixture.commit()
        claude_output = json.loads(claude_fixture.run_claude("post", payload).stdout)
        claude_instruction = claude_output["hookSpecificOutput"]["additionalContext"]

        opencode_fixture = self.fixture()
        opencode_output = opencode_fixture.run_opencode(commit=True, initial_output="")
        opencode_instruction = str(opencode_output["output"])

        normalized_claude = claude_instruction.replace(str(claude_fixture.repo), "<root>").replace(
            claude_sha, "<sha>"
        )
        opencode_sha = run_git(opencode_fixture.repo, "rev-parse", "HEAD").stdout.strip()
        normalized_opencode = opencode_instruction.replace(str(opencode_fixture.repo), "<root>").replace(
            opencode_sha, "<sha>"
        )
        self.assertEqual(normalized_opencode, normalized_claude)


if __name__ == "__main__":
    unittest.main()
