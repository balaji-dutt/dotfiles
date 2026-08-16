from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "assets/check-ai-tooling.py"
DEPENDENCIES = (
    ("claude-code", "Claude Code", ["claude"], []),
    ("opencode", "OpenCode", ["opencode"], []),
    ("beads", "Beads (bd)", ["bd"], []),
    ("dolt", "Dolt", [], []),
    ("codebase-memory-mcp", "codebase-memory-mcp", ["codebase-memory-mcp"], ["cbm"]),
    ("plannotator", "Plannotator CLI", ["plannotator"], []),
    ("jq", "jq", ["jq"], []),
    ("deepwiki", "DeepWiki MCP", [], ["deepwiki"]),
)


class DriftFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "configs").mkdir()
        (root / "docs/inventory").mkdir(parents=True)
        (root / "sources/agents").mkdir(parents=True)
        self.write_policy()
        self.write_matrix()
        self.write_opencode()
        self.write_claude()
        self.write_agent()

    @property
    def policy_path(self) -> Path:
        return self.root / "configs/ai-tooling-support.json"

    @property
    def matrix_path(self) -> Path:
        return self.root / "docs/inventory/ai-tooling.md"

    @property
    def opencode_path(self) -> Path:
        return self.root / "sources/opencode.jsonc"

    def write_policy(self, dependencies: tuple = DEPENDENCIES) -> None:
        payload = {
            "schema_version": 1,
            "matrix": "docs/inventory/ai-tooling.md",
            "sources": [
                {"format": "opencode-jsonc", "patterns": ["sources/opencode.jsonc"]},
                {"format": "claude-mcp-json", "patterns": ["sources/claude.json"]},
                {
                    "format": "claude-agent-frontmatter",
                    "patterns": ["sources/agents/*.md"],
                },
            ],
            "dependencies": [
                {
                    "id": dependency_id,
                    "matrix_row": row,
                    "command_aliases": commands,
                    "mcp_server_aliases": servers,
                }
                for dependency_id, row, commands, servers in dependencies
            ],
        }
        self.policy_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def write_matrix(self) -> None:
        decision = "**Supported** — fixture owner"
        rows = [
            "| Tool | Native Windows | WSL2 | macOS | Devcontainer |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for _, row, _, _ in DEPENDENCIES:
            rendered_row = "Beads (`bd`)" if row == "Beads (bd)" else row
            rows.append(
                f"| {rendered_row} | {decision} | {decision} | {decision} | {decision} |"
            )
        self.matrix_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def write_opencode(self, mcp: str | None = None) -> None:
        body = mcp or (
            '"cbm": {"type": "local", "command": ["codebase-memory-mcp"]},\n'
            '    "deepwiki": {"type": "remote", "url": "https://mcp.deepwiki.com/mcp"}'
        )
        self.opencode_path.write_text(
            "{\n"
            "  // A URL containing // must survive comment removal.\n"
            '  "documentation": "https://opencode.ai/config.json",\n'
            f'  "mcp": {{\n    {body},\n  }},\n'
            "}\n",
            encoding="utf-8",
        )

    def write_claude(self) -> None:
        payload = {
            "servers": {
                "cbm": {
                    "enabled": True,
                    "transport": "stdio",
                    "command": "codebase-memory-mcp",
                },
                "deepwiki": {
                    "enabled": True,
                    "transport": "http",
                    "url": "https://mcp.deepwiki.com/mcp",
                },
            }
        }
        (self.root / "sources/claude.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def write_agent(self) -> None:
        (self.root / "sources/agents/builder.md").write_text(
            "---\n"
            "name: builder\n"
            "mcpServers:\n"
            "  cbm:\n"
            "    type: stdio\n"
            "    command: codebase-memory-mcp\n"
            "---\n",
            encoding="utf-8",
        )

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), "--repo-root", str(self.root)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


class AiToolingDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = DriftFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_current_repository_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CHECKER), "--repo-root", str(REPO_ROOT)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_jsonc_comments_urls_trailing_commas_and_duplicates_pass(self) -> None:
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("5 declarations, 2 runtime identities", result.stdout)

    def test_unknown_local_command_fails_with_source(self) -> None:
        self.fixture.write_opencode(
            '"mystery": {"type": "local", "command": ["mystery-server"]}'
        )
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unclassified local MCP command 'mystery-server'", result.stderr)
        self.assertIn("sources/opencode.jsonc", result.stderr)

    def test_unknown_remote_server_fails_with_source(self) -> None:
        self.fixture.write_opencode(
            '"mystery": {"type": "remote", "url": "https://example.test/mcp"}'
        )
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unclassified remote MCP server 'mystery'", result.stderr)
        self.assertIn("sources/opencode.jsonc", result.stderr)

    def test_missing_required_dependency_fails(self) -> None:
        self.fixture.write_policy(DEPENDENCIES[:-1])
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing required dependency classification 'deepwiki'", result.stderr)

    def test_missing_and_malformed_matrix_rows_fail_closed(self) -> None:
        original = self.fixture.matrix_path.read_text(encoding="utf-8")
        self.fixture.matrix_path.write_text(
            original.replace("| DeepWiki MCP |", "| Missing DeepWiki |"),
            encoding="utf-8",
        )
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("references missing matrix row 'DeepWiki MCP'", result.stderr)

        malformed = original.replace(
            "| jq | **Supported** — fixture owner | **Supported** — fixture owner | **Supported** — fixture owner | **Supported** — fixture owner |",
            "| jq | supported somehow | **Supported** — fixture owner | **Supported** — fixture owner |",
        )
        self.fixture.matrix_path.write_text(malformed, encoding="utf-8")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must contain one name and four platform cells", result.stderr)

        malformed = original.replace(
            "| jq | **Supported** — fixture owner |",
            "| jq | available manually |",
        )
        self.fixture.matrix_path.write_text(malformed, encoding="utf-8")
        result = self.fixture.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must start with an explicit Supported/Unsupported decision", result.stderr)


if __name__ == "__main__":
    unittest.main()
