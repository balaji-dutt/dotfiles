from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support.fixtures import (
    append_json_line,
    init_git_repository,
    isolated_environment,
    loopback_listener,
    read_json,
    read_json_lines,
    run_git,
    write_fake_command,
    write_json,
)


class TestFixtureTests(unittest.TestCase):
    def test_isolated_environment_uses_synthetic_home_and_path(self) -> None:
        real_home = Path.home()
        leaked = {
            "BD_CONFIG": "real-beads",
            "BEADS_DB": "real-beads-db",
            "DOLT_REMOTE_PASSWORD": "secret",
            "GIT_DIR": "/real/repository",
            "GIT_INDEX_FILE": "/real/index",
            "GIT_AUTHOR_NAME": "Real User",
            "GIT_WORK_TREE": "/real/worktree",
            "GIT_SSH_COMMAND": "unsafe-ssh",
        }
        with mock.patch.dict(os.environ, leaked), isolated_environment() as fixture:
            self.assertNotEqual(fixture.home, real_home)
            self.assertEqual(fixture.env["HOME"], str(fixture.home))
            self.assertEqual(fixture.env["USERPROFILE"], str(fixture.home))
            self.assertEqual(fixture.env["PATH"].split(os.pathsep)[0], str(fixture.fake_bin))
            self.assertTrue(Path(fixture.env["GIT_CONFIG_GLOBAL"]).is_file())
            self.assertTrue(set(leaked).isdisjoint(fixture.env))
            bd_command = shutil.which("bd", path=fixture.env["PATH"])
            if bd_command is None:
                self.fail("bd guard command was not found")
            blocked = subprocess.run(
                [bd_command, "--version"],
                env=fixture.env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(blocked.returncode, 97)
            self.assertIn("blocked real bd invocation", blocked.stderr)
        self.assertFalse(fixture.root.exists())

    def test_fake_command_logs_argv_and_exit_status(self) -> None:
        with isolated_environment() as fixture:
            log = fixture.root / "calls.jsonl"
            write_fake_command(
                fixture.fake_bin,
                "fixture-command",
                log_path=log,
                exit_code=7,
                stdout="hello\n",
            )
            fixture_command = shutil.which("fixture-command", path=fixture.env["PATH"])
            if fixture_command is None:
                self.fail("fake command was not found")
            result = subprocess.run(
                [fixture_command, "two words", "literal;value"],
                cwd=fixture.root,
                env=fixture.env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout, "hello\n")
            self.assertEqual(
                read_json_lines(log),
                [
                    {
                        "argv": ["two words", "literal;value"],
                        "cwd": str(fixture.root.resolve()),
                    }
                ],
            )

    def test_json_helpers_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document = root / "nested" / "document.json"
            log = root / "calls.jsonl"
            write_json(document, {"z": 1, "a": [2]})
            append_json_line(log, {"second": 2})
            append_json_line(log, {"first": 1})
            self.assertEqual(read_json(document), {"a": [2], "z": 1})
            self.assertEqual(
                read_json_lines(log),
                [{"second": 2}, {"first": 1}],
            )
            self.assertTrue(document.read_text(encoding="utf-8").endswith("\n"))

    def test_git_repository_has_local_identity_and_isolated_config(self) -> None:
        with isolated_environment() as fixture:
            repo = init_git_repository(fixture.root / "repo", env=fixture.env)
            (repo / "tracked.txt").write_text("fixture\n", encoding="utf-8")
            run_git(repo, "add", "tracked.txt", env=fixture.env)
            run_git(repo, "commit", "-m", "fixture", env=fixture.env)
            self.assertEqual(run_git(repo, "log", "-1", "--format=%an", env=fixture.env).stdout.strip(), "Test User")
            self.assertEqual(run_git(repo, "status", "--short", env=fixture.env).stdout, "")

    def test_loopback_listener_uses_ephemeral_local_port_and_closes(self) -> None:
        with loopback_listener() as listener:
            address, port = listener.getsockname()
            self.assertEqual(address, "127.0.0.1")
            self.assertGreater(port, 0)
            listener.settimeout(1)
            client = socket.create_connection((address, port), timeout=1)
            connection, _ = listener.accept()
            connection.close()
            client.close()
            descriptor = listener.fileno()
            self.assertGreaterEqual(descriptor, 0)
        self.assertEqual(listener.fileno(), -1)


if __name__ == "__main__":
    unittest.main()
