from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from tests.support.fixtures import isolated_environment, write_executable
from tests.test_chezmoi_lifecycle_render import render_template


@unittest.skipIf(os.name == "nt" or not shutil.which("chezmoi"), "POSIX and chezmoi required")
class PageantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = types.ModuleType("pageant_fixture")
        source = render_template("bin/executable_wsl2-pageant.py.tmpl", "wsl2")
        exec(compile(source, "wsl2-pageant.py", "exec"), cls.module.__dict__)

    def setUp(self) -> None:
        context = isolated_environment(prefix="pageant-test-")
        self.iso = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.env = {**self.iso.env, "HOME": str(self.iso.home.resolve())}
        sockets = tempfile.TemporaryDirectory(prefix="pg-", dir=Path("/tmp").resolve())
        self.addCleanup(sockets.cleanup)
        self.socket_path = Path(sockets.name) / "agent.sock"
        self.executable = write_executable(self.iso.fake_bin / "npiperelay.exe", "#!/bin/sh\nexit 0\n")
        write_executable(self.iso.fake_bin / "socat", "#!/bin/sh\nexit 0\n")
        self.add = self.iso.fake_bin / "ssh-add"
        write_executable(self.add, "#!/bin/sh\nexit 2\n")
        self.relay = self.module.Relay(self.socket_path, self.executable, self.env)

    def bind(self) -> socket.socket:
        listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(listener.close)
        listener.bind(str(self.socket_path))
        return listener

    def record(self, pid: int = 12345, start: str = "10") -> dict:
        return {"pid": pid, "start": start, "socket": str(self.socket_path), "argv": self.relay.command(), "boot": self.relay.boot}

    def test_healthy_and_empty_agents_do_not_restart(self) -> None:
        self.bind()
        for code in (0, 1):
            with self.subTest(code=code):
                write_executable(self.add, f"#!/bin/sh\nexit {code}\n")
                self.assertTrue(self.relay.ensure(False))
                self.assertFalse(self.relay.state.exists())
                self.assertFalse(self.relay.repaired)

    def test_dead_absent_and_hung_health_are_bounded(self) -> None:
        self.assertFalse(self.relay.healthy())
        self.bind()
        self.assertFalse(self.relay.healthy())
        write_executable(self.add, "#!/bin/sh\nsleep 30\n")
        before = time.monotonic()
        self.assertFalse(self.relay.healthy())
        self.assertLess(time.monotonic() - before, 3)

    def test_missing_ssh_add_is_not_health(self) -> None:
        with mock.patch.object(self.module.shutil, "which", return_value=None):
            with self.assertRaisesRegex(self.module.Refused, "ssh-add is missing"):
                self.relay.healthy()

    def test_missing_dependencies_do_not_create_state_or_unlink(self) -> None:
        self.bind()
        self.relay.socat = None
        with self.assertRaisesRegex(self.module.Refused, "missing"):
            self.relay.ensure(False)
        self.assertTrue(self.socket_path.exists())
        self.assertFalse(self.relay.state.exists())

    def test_symlinks_and_public_socket_parent_are_refused(self) -> None:
        target = self.iso.root / "target"
        target.write_text("owned\n")
        self.socket_path.symlink_to(target)
        with self.assertRaisesRegex(self.module.Refused, "unsafe socket"):
            self.relay.healthy()
        self.socket_path.unlink()
        self.bind()
        self.socket_path.parent.chmod(0o777)
        self.addCleanup(self.socket_path.parent.chmod, 0o700)
        with self.assertRaisesRegex(self.module.Refused, "unsafe socket directory"):
            self.relay.healthy()

    def test_symlinked_state_is_refused(self) -> None:
        self.relay.state.parent.mkdir(parents=True)
        self.relay.state.symlink_to(self.iso.root, target_is_directory=True)
        with self.assertRaisesRegex(self.module.Refused, "symlinked directory"):
            self.relay.ensure(False)

    def test_foreign_socket_is_refused(self) -> None:
        self.bind()
        with mock.patch.object(self.module.os, "getuid", return_value=os.getuid() + 1):
            with self.assertRaisesRegex(self.module.Refused, "unsafe socket"):
                self.relay.healthy()

    def test_pid_reuse_and_unsafe_records_are_refused(self) -> None:
        self.module.secure_directory(self.relay.state)
        record = self.record()
        self.relay.write_record(record)
        with mock.patch.object(self.relay, "identity", return_value=self.record(start="11")):
            with self.assertRaisesRegex(self.module.Refused, "PID reuse"):
                self.relay.recorded()
        self.relay.record_path.chmod(0o644)
        with self.assertRaisesRegex(self.module.Refused, "unsafe relay PID"):
            self.relay.recorded()

    def test_changed_pid_before_signal_is_refused(self) -> None:
        with mock.patch.object(self.module.os, "pidfd_open", return_value=99, create=True), \
             mock.patch.object(self.module.os, "close"), \
             mock.patch.object(self.module.signal, "pidfd_send_signal", create=True) as send, \
             mock.patch.object(self.relay, "identity", return_value=self.record(start="11")):
            with self.assertRaisesRegex(self.module.Refused, "ownership changed"):
                self.relay.stop(self.record())
            send.assert_not_called()

    def test_prior_boot_record_does_not_trust_or_inspect_reused_pid(self) -> None:
        self.module.secure_directory(self.relay.state)
        self.relay.write_record({**self.record(), "boot": "prior-boot"})
        with mock.patch.object(self.relay, "identity", side_effect=self.module.Refused("foreign PID")) as identity:
            self.assertIsNone(self.relay.recorded())
            identity.assert_not_called()

    def run_mocked_restart(self, *, legacy: bool = False, fail: bool = False) -> None:
        process = mock.Mock(pid=12345)
        process.poll.return_value = None
        process.wait.return_value = 0
        record = self.record()
        health = [False, False, False, True] if not fail else [False, False, False]
        with mock.patch.object(self.relay, "healthy", side_effect=health), \
             mock.patch.object(self.relay, "listener", side_effect=[record if legacy else None, None]), \
             mock.patch.object(self.relay, "identity", return_value=record), \
             mock.patch.object(self.relay, "stop") as stop, \
             mock.patch.object(self.module.subprocess, "Popen", return_value=process) as spawn, \
             mock.patch.object(self.module.time, "sleep"):
            if fail:
                process.poll.return_value = 1
                with self.assertRaisesRegex(self.module.Refused, "socat exited"):
                    self.relay.ensure(False)
            else:
                self.assertTrue(self.relay.ensure(False))
                self.assertTrue(self.relay.repaired)
                self.assertEqual(json.loads(self.relay.record_path.read_text()), record)
            self.assertEqual(spawn.call_count, 1)
            if legacy:
                stop.assert_called_once_with(record)

    def test_one_restart_and_private_record(self) -> None:
        self.run_mocked_restart()
        self.assertEqual(self.relay.record_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.relay.state.stat().st_mode & 0o777, 0o700)

    def test_verified_legacy_listener_is_adopted(self) -> None:
        self.run_mocked_restart(legacy=True)

    def test_failed_restart_is_not_repeated(self) -> None:
        self.run_mocked_restart(fail=True)

    def test_ambiguous_listener_does_not_spawn_or_unlink(self) -> None:
        self.bind()
        with mock.patch.object(self.relay, "listener", side_effect=self.module.Refused("ambiguous ownership")), \
             mock.patch.object(self.module.subprocess, "Popen") as spawn, \
             mock.patch.object(self.relay, "healthy", return_value=False):
            with self.assertRaisesRegex(self.module.Refused, "ambiguous"):
                self.relay.ensure(False)
            spawn.assert_not_called()
            self.assertTrue(self.socket_path.exists())

    def test_health_rechecked_after_lock_does_not_restart(self) -> None:
        with mock.patch.object(self.relay, "healthy", side_effect=[False, True]), \
             mock.patch.object(self.module.subprocess, "Popen") as spawn:
            self.assertTrue(self.relay.ensure(False))
            spawn.assert_not_called()

    def test_lock_wait_obeys_total_deadline(self) -> None:
        self.relay.deadline = time.monotonic() + 0.1
        with mock.patch.object(self.relay, "healthy", return_value=False), \
             mock.patch.object(self.module.fcntl, "flock", side_effect=BlockingIOError):
            before = time.monotonic()
            with self.assertRaisesRegex(self.module.Refused, "budget"):
                self.relay.ensure(False)
            self.assertLess(time.monotonic() - before, 0.5)

    def test_other_platform_container_and_other_socket_do_not_repair(self) -> None:
        with mock.patch.object(self.module.os, "uname", return_value=types.SimpleNamespace(release="generic")):
            self.assertFalse(self.module.eligible(self.env))
        with mock.patch.object(self.module.os, "uname", return_value=types.SimpleNamespace(release="microsoft-WSL2")), \
             mock.patch.object(self.module.Path, "exists", return_value=False):
            self.assertFalse(self.module.eligible({**self.env, "DEVCONTAINER": "1"}))
            self.assertTrue(self.module.eligible(self.env))
        output = io.StringIO()
        with mock.patch.dict(os.environ, {**self.env, "SSH_AUTH_SOCK": "/other/agent"}, clear=True), \
             mock.patch.object(self.module, "eligible", return_value=True), \
             mock.patch.object(self.module.Relay, "ensure") as ensure, contextlib.redirect_stdout(output):
            self.assertEqual(self.module.main(["--ensure", "--json"]), 0)
            ensure.assert_not_called()
            self.assertTrue(json.loads(output.getvalue())["skipped"])


if __name__ == "__main__":
    unittest.main()
