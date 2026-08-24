from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / ".chezmoiscripts/run_onchange_after_host_ai_plugin_refresh.sh.tmpl"


class PosixHostAiPluginRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        config = self.root / "manifest.jsonc"
        self.config = config
        settings = self.root / "settings.json"
        config.write_text(
            json.dumps(
                {
                    "$schema": "./schemas/host-ai-plugin-refresh.v1.schema.json",
                    "schema_version": 1,
                    "claude": {"plugins": []},
                    "opencode": {"plugins": []},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        settings.write_text("{}\n", encoding="utf-8")

        source = TEMPLATE.read_text(encoding="utf-8")
        marker = "<<'PY'\n"
        start = source.index(marker) + len(marker)
        end = source.rindex("\nPY\n")
        python_source = source[start:end]
        namespace: dict[str, object] = {"__name__": "host_ai_plugin_refresh_test"}
        previous_argv = sys.argv
        try:
            sys.argv = [str(TEMPLATE), str(config), str(settings)]
            exec(compile(python_source, str(TEMPLATE), "exec"), namespace)
        finally:
            sys.argv = previous_argv
        self.module = namespace

    def call(self, name: str, *args, **kwargs):
        return self.module[name](*args, **kwargs)

    def catalog(self, marketplace: str, plugin_names: list[str]) -> Path:
        location = self.root / marketplace
        catalog = location / ".claude-plugin" / "marketplace.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(
            json.dumps({"plugins": [{"name": name} for name in plugin_names]}),
            encoding="utf-8",
        )
        return location

    def test_plugin_identity_and_marketplace_deduplication(self) -> None:
        self.assertEqual(
            self.call("claude_plugin_identity", "one@example"),
            ("one", "example"),
        )
        plugins = [
            {"marketplace": "shared"},
            {"marketplace": "shared"},
            {"marketplace": "other"},
        ]
        self.assertEqual(
            self.call("claude_marketplace_names", plugins), ["shared", "other"]
        )
        for candidate in ("missing", "@marketplace", "plugin@"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    self.call("claude_plugin_identity", candidate)

    def test_manifest_contract_marker_fails_closed(self) -> None:
        manifest = self.call("load_manifest")
        self.assertEqual(manifest["schema_version"], 1)

        payload = json.loads(self.config.read_text(encoding="utf-8"))
        payload["$schema"] = "./schemas/wrong.schema.json"
        self.config.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            self.call("load_manifest")
        self.assertIn("$schema must be", output.getvalue())

    def test_claude_preservation_environment_is_child_local(self) -> None:
        observed: list[dict[str, str]] = []

        def fake_run_checked(command, env=None):
            observed.append(dict(env))
            return SimpleNamespace(returncode=0, stdout="")

        self.module["run_checked"] = fake_run_checked
        variable = "CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE"
        previous = os.environ.get(variable)
        try:
            os.environ.pop(variable, None)
            self.call("run_claude_checked", ["plugin", "marketplace", "update", "one"])
            self.assertNotIn(variable, os.environ)
            os.environ[variable] = "caller-value"
            self.call("run_claude_checked", ["plugin", "update", "one@one"])
            self.assertEqual(os.environ[variable], "caller-value")
        finally:
            if previous is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = previous
        self.assertEqual([entry[variable] for entry in observed], ["1", "1"])

    def test_catalog_requires_expected_plugin_and_unique_record(self) -> None:
        location = self.catalog("example", ["expected-plugin"])
        plugins = [{"name": "expected-plugin"}]
        records = [{"name": "example", "installLocation": str(location)}]
        valid = self.call(
            "validate_claude_marketplace_catalog", "example", plugins, records
        )
        missing = self.call(
            "validate_claude_marketplace_catalog",
            "example",
            [{"name": "missing-plugin"}],
            records,
        )
        duplicate = self.call(
            "validate_claude_marketplace_catalog",
            "example",
            plugins,
            [records[0], records[0]],
        )
        self.assertTrue(valid["available"])
        self.assertFalse(missing["available"])
        self.assertIn("missing-plugin", missing["error"])
        self.assertFalse(duplicate["available"])
        self.assertIn("found 2", duplicate["error"])

    def test_named_refresh_skips_only_unavailable_catalog(self) -> None:
        healthy = self.catalog("healthy", ["healthy-plugin"])
        empty = self.root / "broken"
        empty.mkdir()
        plugins = [
            {
                "id": "healthy-plugin@healthy",
                "name": "healthy-plugin",
                "marketplace": "healthy",
                "scope": "user",
            },
            {
                "id": "broken-plugin@broken",
                "name": "broken-plugin",
                "marketplace": "broken",
                "scope": "user",
            },
        ]
        commands: list[list[str]] = []
        synced: list[str] = []
        self.module["claude_failures"].clear()
        self.module["command_exists"] = lambda command: True

        def fake_claude(arguments):
            commands.append(list(arguments))
            return SimpleNamespace(returncode=0, stdout="")

        self.module["run_claude_checked"] = fake_claude
        self.module["claude_marketplace_inventory"] = lambda: (
            [
                {"name": "healthy", "installLocation": str(healthy)},
                {"name": "broken", "installLocation": str(empty)},
            ],
            None,
        )
        self.module["installed_claude_plugin_ids"] = lambda: set()

        def fake_sync(plugin, actions):
            synced.append(plugin["id"])
            return True

        self.module["sync_claude_plugin"] = fake_sync
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.call("refresh_claude", plugins)
        self.assertEqual(
            commands,
            [
                ["plugin", "marketplace", "update", "healthy"],
                ["plugin", "marketplace", "update", "broken"],
            ],
        )
        self.assertEqual(synced, ["healthy-plugin@healthy"])
        self.assertEqual(self.module["claude_failures"], ["marketplace:broken"])
        self.assertIn("catalog is missing", output.getvalue())
        self.assertIn("Skipping Claude Code plugin 'broken-plugin@broken'", output.getvalue())

    def test_failed_named_update_uses_valid_preserved_catalog(self) -> None:
        location = self.catalog("preserved", ["preserved-plugin"])
        plugin = {
            "id": "preserved-plugin@preserved",
            "name": "preserved-plugin",
            "marketplace": "preserved",
            "scope": "user",
        }
        synced: list[str] = []
        self.module["claude_failures"].clear()
        self.module["command_exists"] = lambda command: True
        self.module["run_claude_checked"] = lambda arguments: SimpleNamespace(
            returncode=1, stdout="network unavailable"
        )
        self.module["claude_marketplace_inventory"] = lambda: (
            [{"name": "preserved", "installLocation": str(location)}],
            None,
        )
        self.module["installed_claude_plugin_ids"] = lambda: set()
        self.module["sync_claude_plugin"] = (
            lambda current, actions: synced.append(current["id"]) or True
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.call("refresh_claude", [plugin])
        self.assertEqual(synced, ["preserved-plugin@preserved"])
        self.assertEqual(
            self.module["claude_failures"], ["marketplace:preserved"]
        )

    def test_malformed_inventory_skips_plugins_and_records_marketplace(self) -> None:
        plugin = {
            "id": "one@broken",
            "name": "one",
            "marketplace": "broken",
            "scope": "user",
        }
        self.module["claude_failures"].clear()
        self.module["command_exists"] = lambda command: True
        self.module["run_claude_checked"] = lambda arguments: SimpleNamespace(
            returncode=0, stdout=""
        )
        self.module["claude_marketplace_inventory"] = lambda: (
            None,
            "marketplace list JSON must be an array",
        )
        self.module["installed_claude_plugin_ids"] = lambda: set()
        self.module["sync_claude_plugin"] = lambda plugin, actions: self.fail(
            "plugin sync must not run"
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.call("refresh_claude", [plugin])
        self.assertEqual(self.module["claude_failures"], ["marketplace:broken"])
        self.assertIn("marketplace list JSON must be an array", output.getvalue())

    def test_dry_run_previews_unique_named_update_without_mutation(self) -> None:
        plugins = [
            {"id": "one@shared", "name": "one", "marketplace": "shared", "scope": "user"},
            {"id": "two@shared", "name": "two", "marketplace": "shared", "scope": "user"},
        ]
        self.module["dry_run"] = True
        self.module["command_exists"] = lambda command: True
        self.module["run_claude_checked"] = lambda arguments: self.fail(
            "dry run must not mutate"
        )
        self.module["installed_claude_plugin_ids"] = lambda: set()
        self.module["sync_claude_plugin"] = lambda plugin, actions: self.fail(
            "dry run must not sync"
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.call("refresh_claude", plugins)
        self.assertEqual(
            output.getvalue().count("claude plugin marketplace update shared"), 1
        )


if __name__ == "__main__":
    unittest.main()
