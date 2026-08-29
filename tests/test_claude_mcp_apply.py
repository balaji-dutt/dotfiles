from __future__ import annotations

import platform
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, read_json_lines, write_executable, write_json


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "assets" / "claude-mcp-apply.py"
SCHEMA = "./schemas/claude-mcp.v1.schema.json"


def current_platform() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        release = platform.uname().release.lower()
        if "microsoft" in release and "wsl2" in release:
            return "wsl2"
        if "microsoft" in release:
            return "wsl"
        return "linux"
    return sys.platform


class ClaudeMcpApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = isolated_environment(prefix="claude-mcp-apply-")
        self.isolated = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.config_path = self.isolated.root / "claude-mcp.json"
        self.log_path = self.isolated.root / "claude-calls.jsonl"
        fake = f"""#!{sys.executable}
import json, os, pathlib, sys
log = pathlib.Path(os.environ['CLAUDE_TEST_LOG'])
with log.open('a', encoding='utf-8') as handle:
    handle.write(json.dumps({{'argv': sys.argv[1:]}}) + '\\n')
action = sys.argv[2] if len(sys.argv) > 2 else ''
name = sys.argv[-1] if action in ('get', 'remove') else ''
if action == 'get':
    raise SystemExit(0 if name in os.environ.get('CLAUDE_TEST_EXISTING', '').split(',') else 1)
if action == 'remove':
    sys.stdout.write(os.environ.get('CLAUDE_TEST_REMOVE_OUTPUT', ''))
    raise SystemExit(int(os.environ.get('CLAUDE_TEST_REMOVE_RC', '0')))
if action == 'add':
    sys.stdout.write(os.environ.get('CLAUDE_TEST_ADD_OUTPUT', ''))
    raise SystemExit(int(os.environ.get('CLAUDE_TEST_ADD_RC', '0')))
"""
        write_executable(self.isolated.fake_bin / "claude", fake)

    def config(self, servers: object) -> dict[str, object]:
        return {"$schema": SCHEMA, "schema_version": 1, "servers": servers}

    def run_script(
        self,
        payload: object | None = None,
        *,
        args: list[str] | None = None,
        env_updates: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if payload is not None:
            write_json(self.config_path, payload)
        env = dict(self.isolated.env)
        env["CLAUDE_TEST_LOG"] = str(self.log_path)
        if env_updates:
            env.update(env_updates)
        command = [sys.executable, str(SCRIPT), *(args if args is not None else [str(self.config_path)])]
        return subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def calls(self) -> list[list[str]]:
        return [record["argv"] for record in read_json_lines(self.log_path)]

    def test_rejects_usage_and_invalid_config_before_claude_calls(self) -> None:
        usage = self.run_script(args=[])
        self.assertEqual(usage.returncode, 2)
        self.assertIn("usage", usage.stdout)

        invalid_payloads = (
            ([], "root must be an object"),
            ({"$schema": "wrong", "schema_version": 1, "servers": {}}, "$schema"),
            ({"$schema": SCHEMA, "schema_version": 2, "servers": {}}, "schema_version"),
            ({"$schema": SCHEMA, "schema_version": 1, "servers": []}, 'key "servers"'),
            (self.config({"bad name": {}}), "Invalid Claude MCP server name"),
            (self.config({"bad": {"enabled": "yes"}}), "enabled must be a boolean"),
            (self.config({"bad": {"scope": "project"}}), "invalid scope"),
            (self.config({"bad": {"transport": "grpc"}}), "invalid transport"),
            (self.config({"bad": {"transport": "http"}}), "requires a non-empty url"),
            (self.config({"bad": {"transport": "stdio", "command": "x", "args": [1]}}), "args must be a string list"),
        )
        for payload, message in invalid_payloads:
            with self.subTest(message=message):
                result = self.run_script(payload)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stdout)
        self.assertEqual(self.calls(), [])

        self.config_path.write_text("{", encoding="utf-8")
        malformed = self.run_script()
        self.assertEqual(malformed.returncode, 1)
        self.assertIn("Invalid Claude MCP JSON", malformed.stdout)

    def test_dry_run_sorts_servers_and_honors_skip_controls(self) -> None:
        payload = self.config(
            {
                "zeta": {"transport": "http", "url": "https://zeta.test"},
                "alpha": {"transport": "sse", "url": "https://alpha.test"},
                "disabled": {"enabled": False},
                "platform-skip": {"platforms": ["never"]},
                "container-skip": {"skipDevcontainer": True, "platforms": [current_platform()]},
            }
        )
        result = self.run_script(payload, env_updates={"CLAUDE_MCP_DRY_RUN": "1", "DEVCONTAINER": "1"})
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertLess(result.stdout.index("alpha"), result.stdout.index("zeta"))
        self.assertIn("not enabled for platform", result.stdout)
        self.assertIn("skipped in devcontainers", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_registers_http_sse_and_stdio_with_resolved_executable(self) -> None:
        executable = self.isolated.home / "tool binary"
        executable.write_text("fixture", encoding="utf-8")
        payload = self.config(
            {
                "http": {"transport": "http", "url": "https://http.test"},
                "sse": {"transport": "sse", "url": "https://sse.test"},
                "stdio": {
                    "transport": "stdio",
                    "command": "server-command",
                    "args": ["--flag"],
                    "executablePaths": {current_platform(): ["$HOME/missing", "$HOME/tool binary"]},
                },
            }
        )
        result = self.run_script(payload)
        self.assertEqual(result.returncode, 0, result.stdout)
        calls = self.calls()
        self.assertEqual(calls[0], ["mcp", "get", "http"])
        self.assertEqual(
            calls[1],
            ["mcp", "add", "--transport", "http", "--scope", "user", "http", "https://http.test"],
        )
        self.assertEqual(calls[2], ["mcp", "get", "sse"])
        self.assertEqual(
            calls[3],
            ["mcp", "add", "--transport", "sse", "--scope", "user", "sse", "https://sse.test"],
        )
        self.assertEqual(calls[4], ["mcp", "get", "stdio"])
        self.assertEqual(
            calls[5],
            [
                "mcp",
                "add",
                "--transport",
                "stdio",
                "--scope",
                "user",
                "stdio",
                "--",
                "server-command",
                "--flag",
                f"--executablePath={executable}",
            ],
        )

    def test_existing_and_replace_flows_are_safe(self) -> None:
        payload = self.config(
            {
                "keep": {"transport": "http", "url": "https://keep.test"},
                "replace": {"transport": "http", "url": "https://replace.test", "replace": True},
            }
        )
        result = self.run_script(payload, env_updates={"CLAUDE_TEST_EXISTING": "keep,replace"})
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            self.calls(),
            [
                ["mcp", "get", "keep"],
                ["mcp", "get", "replace"],
                ["mcp", "remove", "--scope", "user", "replace"],
                ["mcp", "add", "--transport", "http", "--scope", "user", "replace", "https://replace.test"],
            ],
        )
        self.assertIn("already exists", result.stdout)

    def test_remove_and_add_failures_stop_with_diagnostics(self) -> None:
        replace = self.config(
            {"one": {"transport": "http", "url": "https://one.test", "replace": True}}
        )
        remove = self.run_script(
            replace,
            env_updates={
                "CLAUDE_TEST_EXISTING": "one",
                "CLAUDE_TEST_REMOVE_RC": "7",
                "CLAUDE_TEST_REMOVE_OUTPUT": "remove detail",
            },
        )
        self.assertEqual(remove.returncode, 7)
        self.assertIn("remove detail", remove.stdout)
        self.assertEqual(len(self.calls()), 2)

        self.log_path.unlink()
        add = self.run_script(
            self.config({"one": {"transport": "http", "url": "https://one.test"}}),
            env_updates={"CLAUDE_TEST_ADD_RC": "9", "CLAUDE_TEST_ADD_OUTPUT": "add detail"},
        )
        self.assertEqual(add.returncode, 9)
        self.assertIn("add detail", add.stdout)
        self.assertEqual(len(self.calls()), 2)

    def test_missing_executable_candidate_skips_without_claude(self) -> None:
        payload = self.config(
            {
                "stdio": {
                    "transport": "stdio",
                    "command": "server-command",
                    "executablePaths": {current_platform(): ["$HOME/not-there"]},
                }
            }
        )
        result = self.run_script(payload)
        self.assertEqual(result.returncode, 0)
        self.assertIn("no existing executablePath candidates", result.stdout)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
