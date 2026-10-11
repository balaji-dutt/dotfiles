from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import struct
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
        sockets = tempfile.TemporaryDirectory(prefix="pg-", dir=Path("/tmp").resolve())
        self.addCleanup(sockets.cleanup)
        self.socket_path = Path(sockets.name) / "agent.sock"
        self.env = {**self.iso.env, "HOME": str(self.iso.home.resolve()),
                    "SSH_AUTH_SOCK": str(self.socket_path),
                    "WSL2_PAGEANT_SSH_AUTH_SOCK": str(self.socket_path)}
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
                write_executable(self.add, f"#!/bin/sh\nprintf '%s\\n' 'The agent has no identities.'\nexit {code}\n")
                self.assertTrue(self.relay.ensure(False))
                self.assertFalse(self.relay.state.exists())
                self.assertFalse(self.relay.repaired)

    def test_exit_one_communication_errors_are_not_healthy_empty_agents(self) -> None:
        self.bind()
        cases = (
            ("", "error fetching identities: communication with agent failed"),
            ("", ""),
            ("The agent has no identities.", "error fetching identities: invalid format"),
            ("unexpected output", ""),
            ("The agent has no identities.\nextra output", ""),
        )
        for output, error in cases:
            with self.subTest(output=output, error=error):
                write_executable(self.add, f"#!{sys.executable}\nimport sys\nprint({output!r})\nprint({error!r}, file=sys.stderr)\nsys.exit(1)\n")
                self.assertFalse(self.relay.healthy())
        with mock.patch.dict(os.environ, self.env, clear=True), \
             mock.patch.object(self.module, "eligible", return_value=True), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.module.main(["--check", "--json"]), 1)
        self.assertFalse(json.loads(output.getvalue())["healthy"])

    def test_communication_failure_reaches_one_supervised_restart(self) -> None:
        self.bind()
        calls = self.iso.root / "health-count"
        write_executable(self.add, f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\ncount = Path({str(calls)!r})\nn = int(count.read_text()) + 1 if count.exists() else 1\ncount.write_text(str(n))\nassert os.environ['LC_ALL'] == 'C'\nif n < 3:\n    print('error fetching identities: communication with agent failed', file=sys.stderr)\nelse:\n    print('The agent has no identities.')\nsys.exit(1)\n")
        process = mock.Mock(pid=12345)
        process.poll.return_value = None
        real_spawn = subprocess.Popen

        def spawn(command, **kwargs):
            if command[0] == self.relay.socat:
                self.bind()
                return process
            return real_spawn(command, **kwargs)

        with mock.patch.object(self.relay, "listener", return_value=None), \
             mock.patch.object(self.relay, "identity", return_value=self.record()), \
             mock.patch.object(self.module.subprocess, "Popen", side_effect=spawn) as start:
            self.assertTrue(self.relay.ensure(False))
            self.assertTrue(self.relay.repaired)
            self.assertEqual(sum(call.args[0][0] == self.relay.socat for call in start.call_args_list), 1)
        self.assertEqual(calls.read_text(), "3")

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

    def test_owned_legacy_socket_directory_is_hardened_without_restart(self) -> None:
        self.bind()
        write_executable(self.add, "#!/bin/sh\nprintf '%s\\n' 'The agent has no identities.'\nexit 1\n")
        for mode in (0o775, 0o777):
            with self.subTest(mode=oct(mode)):
                self.socket_path.parent.chmod(mode)
                self.assertTrue(self.relay.ensure(False))
                self.assertEqual(self.socket_path.parent.stat().st_mode & 0o777, 0o700)
                self.assertFalse(self.relay.repaired)
                self.assertFalse(self.relay.record_path.exists())

    def test_directory_adoption_refuses_foreign_owners_and_symlinks(self) -> None:
        self.socket_path.parent.chmod(0o775)
        self.addCleanup(self.socket_path.parent.chmod, 0o700)
        with mock.patch.object(self.module.os, "getuid", return_value=os.getuid() + 1), \
             mock.patch.object(self.module.os, "fchmod") as chmod:
            with self.assertRaisesRegex(self.module.Refused, "uid.*mode"):
                self.module.secure_directory(self.socket_path.parent, repair_permissions=True)
            chmod.assert_not_called()
        link = self.iso.root / "socket-directory"
        link.symlink_to(self.socket_path.parent, target_is_directory=True)
        with self.assertRaisesRegex(self.module.Refused, "symlinked directory"):
            self.module.secure_directory(link, repair_permissions=True)
        self.assertEqual(self.socket_path.parent.stat().st_mode & 0o777, 0o775)

    def test_directory_identity_change_during_hardening_is_refused(self) -> None:
        self.socket_path.parent.chmod(0o775)
        original_lstat = self.module.Path.lstat
        real_chmod = self.module.os.fchmod
        secured = False

        def chmod(descriptor: int, mode: int) -> None:
            nonlocal secured
            real_chmod(descriptor, mode)
            secured = True

        def lstat(directory: Path):
            info = original_lstat(directory)
            if directory == self.socket_path.parent and secured:
                return types.SimpleNamespace(st_ino=info.st_ino + 1, st_dev=info.st_dev)
            return info

        with mock.patch.object(self.module.os, "fchmod", side_effect=chmod), \
             mock.patch.object(self.module.Path, "lstat", autospec=True, side_effect=lstat):
            with self.assertRaisesRegex(self.module.Refused, "directory changed"):
                self.module.secure_directory(self.socket_path.parent, repair_permissions=True)

    def test_hardening_does_not_trust_a_foreign_socket_in_legacy_directory(self) -> None:
        self.bind()
        self.socket_path.parent.chmod(0o777)
        original_lstat = self.module.Path.lstat

        def lstat(file_path: Path):
            info = original_lstat(file_path)
            if file_path == self.socket_path:
                return types.SimpleNamespace(st_mode=info.st_mode, st_uid=os.getuid() + 1)
            return info

        with mock.patch.object(self.module.Path, "lstat", autospec=True, side_effect=lstat), \
             mock.patch.object(self.module.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(self.module.Refused, "unsafe socket ownership"):
                self.relay.ensure(False)
            spawn.assert_not_called()
        self.assertTrue(self.socket_path.exists())
        self.assertEqual(self.socket_path.parent.stat().st_mode & 0o777, 0o700)

    @unittest.skipUnless(sys.platform == "linux", "Linux socket diagnostics required")
    def test_listener_uses_current_socket_inode_not_duplicate_pathnames(self) -> None:
        previous = self.bind()
        previous.listen()
        self.socket_path.unlink()
        current = self.bind()
        current.listen()
        record = self.record(pid=os.getpid())
        with mock.patch.object(self.relay, "identity", return_value=record) as identity:
            self.assertEqual(self.relay.listener(), record)
            identity.assert_called_once_with(os.getpid())

    @unittest.skipUnless(sys.platform == "linux", "Linux socket diagnostics required")
    def test_unlinked_listener_is_not_the_selected_socket(self) -> None:
        previous = self.bind()
        previous.listen()
        self.socket_path.unlink()
        with mock.patch.object(self.relay, "identity") as identity:
            self.assertIsNone(self.relay.listener())
            identity.assert_not_called()

    def test_unmatched_live_socket_is_refused_instead_of_unlinked(self) -> None:
        listener = self.bind()
        listener.listen()
        with mock.patch.object(self.module, "unix_listener_inodes", return_value=set()), \
             mock.patch.object(self.relay, "identity") as identity:
            with self.assertRaisesRegex(self.module.Refused, "unidentified live listener"):
                self.relay.listener()
            identity.assert_not_called()
        self.assertTrue(self.socket_path.exists())

    def test_bound_non_listening_socket_is_identified_as_stale(self) -> None:
        self.bind()
        with mock.patch.object(self.module, "unix_listener_inodes", return_value=set()), \
             mock.patch.object(self.relay, "identity") as identity:
            self.assertIsNone(self.relay.listener())
            identity.assert_not_called()
        self.assertTrue(self.socket_path.exists())

    def diag_message(self, kind: int, payload: bytes = b"", flags: int = 0) -> bytes:
        return struct.pack("=IHHII", 16 + len(payload), kind, flags, 1, 0) + payload

    def diag_listener(self, inode: int, filesystem_inode: int, device: int) -> bytes:
        payload = struct.pack("=BBBBIII", socket.AF_UNIX, socket.SOCK_STREAM, 10, 0, inode, 0, 0)
        payload += struct.pack("=HHII", 12, 1, filesystem_inode, device)
        return self.diag_message(20, payload)

    def diagnose(self, responses: list[tuple], deadline: float | None = None) -> set[int]:
        info = types.SimpleNamespace(st_ino=123, st_dev=os.makedev(1, 2))
        transport = mock.MagicMock()
        transport.__enter__.return_value = transport
        transport.recvmsg.side_effect = responses
        with mock.patch.object(self.module.socket, "AF_NETLINK", 16, create=True), \
             mock.patch.object(self.module.socket, "socket", return_value=transport):
            return self.module.unix_listener_inodes(info, deadline or self.relay.deadline)

    def test_diagnostics_distinguish_same_inode_on_other_distro_devices(self) -> None:
        info = types.SimpleNamespace(st_ino=123, st_dev=os.makedev(1, 2))
        kernel_device = (os.major(info.st_dev) << 20) | os.minor(info.st_dev)
        data = (self.diag_listener(111, info.st_ino, kernel_device + 1)
                + self.diag_listener(222, info.st_ino, kernel_device)
                + self.diag_message(3, struct.pack("=i", 0)))
        self.assertEqual(self.diagnose([(data, [], 0, (0, 0))]), {222})

    def test_incomplete_untrusted_or_failed_diagnostics_are_refused(self) -> None:
        cases = [
            (self.diag_message(3, flags=0x10), 0, (0, 0), self.module.Refused),
            (self.diag_message(2, struct.pack("=i", -1)), 0, (0, 0), OSError),
            (self.diag_message(2), 0, (0, 0), self.module.Refused),
            (self.diag_message(3), socket.MSG_TRUNC, (0, 0), self.module.Refused),
            (self.diag_message(3), 0, (1, 0), self.module.Refused),
            (b"short", 0, (0, 0), self.module.Refused),
            (self.diag_message(20, b"short"), 0, (0, 0), self.module.Refused),
        ]
        for data, flags, sender, exception in cases:
            with self.subTest(data=data, flags=flags, sender=sender):
                with self.assertRaises(exception):
                    self.diagnose([(data, [], flags, sender)])
        with self.assertRaisesRegex(self.module.Refused, "budget"):
            self.diagnose([], time.monotonic() - 1)

    def test_diagnostic_timeout_is_a_clean_main_failure_without_restart(self) -> None:
        self.bind()
        output = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), \
             mock.patch.object(self.module, "eligible", return_value=True), \
             mock.patch.object(self.module, "RELAY", str(self.executable)), \
             mock.patch.object(self.module, "unix_listener_inodes", side_effect=TimeoutError("diagnostic timeout")), \
             mock.patch.object(self.module.subprocess, "Popen") as spawn, contextlib.redirect_stdout(output):
            self.assertEqual(self.module.main(["--reset", "--json"]), 1)
            spawn.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["detail"], "diagnostic timeout")
        self.assertEqual(json.loads(output.getvalue())["socket"], str(self.socket_path))

    def test_unrepresentable_diagnostic_identity_is_refused(self) -> None:
        for inode, major, minor in ((1 << 32, 0, 0), (1, 0x1000, 1), (1, 1, 0x100000)):
            info = types.SimpleNamespace(st_ino=inode, st_dev=0)
            with self.subTest(inode=inode, major=major, minor=minor), \
                 mock.patch.object(self.module.os, "major", return_value=major), \
                 mock.patch.object(self.module.os, "minor", return_value=minor), \
                 self.assertRaisesRegex(self.module.Refused, "identity range"):
                self.module.unix_listener_inodes(info, self.relay.deadline)

    def fake_proc(self, pid: int, *, accessible: bool) -> mock.MagicMock:
        proc = mock.MagicMock()
        proc.name = str(pid)
        proc.stat.return_value = types.SimpleNamespace(st_uid=os.getuid())
        if accessible:
            proc.__truediv__.return_value.iterdir.return_value = ["owned-fd"]
        else:
            proc.__truediv__.return_value.iterdir.side_effect = PermissionError("unrelated protected process")
        return proc

    def test_unrelated_inaccessible_process_does_not_block_verified_listener(self) -> None:
        self.bind()
        record = self.record()
        processes = [self.fake_proc(1, accessible=False), self.fake_proc(record["pid"], accessible=True)]
        with mock.patch.object(self.module, "unix_listener_inodes", return_value={333}), \
             mock.patch.object(self.module.Path, "iterdir", return_value=processes), \
             mock.patch.object(self.module.os, "readlink", return_value="socket:[333]"), \
             mock.patch.object(self.relay, "identity", return_value=record) as identity:
            self.assertEqual(self.relay.listener(), record)
            identity.assert_called_once_with(record["pid"])

    def test_unknown_or_multiple_visible_socket_owners_remain_refused(self) -> None:
        self.bind()
        for processes in ([self.fake_proc(1, accessible=False)],
                          [self.fake_proc(2, accessible=True), self.fake_proc(3, accessible=True)]):
            with self.subTest(owners=len(processes)), \
                 mock.patch.object(self.module, "unix_listener_inodes", return_value={333}), \
                 mock.patch.object(self.module.Path, "iterdir", return_value=processes), \
                 mock.patch.object(self.module.os, "readlink", return_value="socket:[333]"), \
                 mock.patch.object(self.relay, "identity") as identity:
                with self.assertRaisesRegex(self.module.Refused, "unique socket listener"):
                    self.relay.listener()
                identity.assert_not_called()

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

    def proc_identity(self, argv: list[str]) -> dict | None:
        proc_root = self.iso.root / "proc"
        proc = proc_root / "12345"
        proc.mkdir(parents=True, exist_ok=True)
        (proc / "stat").write_text("12345 (socat) " + " ".join(["S", *["0"] * 18, "10"]))
        (proc / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
        if not (proc / "exe").exists():
            (proc / "exe").symlink_to(self.relay.socat)
        with mock.patch.object(self.module, "Path", side_effect=lambda value: proc_root if value == "/proc" else Path(value)):
            return self.relay.identity(12345)

    def test_legacy_exec_quoting_and_private_socket_mode_are_adoptable(self) -> None:
        for executable in (str(self.executable), f'"{self.executable}"', f"'{self.executable}'"):
            for options in ("unlink-close,fork", "unlink-close,fork,mode=600"):
                with self.subTest(executable=executable, options=options):
                    argv = ["socat", f"EXEC:{executable} -ei -s {self.module.PIPE}",
                            f"UNIX-LISTEN:{self.socket_path},{options}"]
                    self.assertEqual(self.proc_identity(argv), {**self.record(), "argv": argv})
        self.relay.executable = self.iso.root / "Windows User" / "npiperelay.exe"
        self.assertIsNotNone(self.proc_identity(self.relay.command()))

    def test_legacy_adoption_does_not_allow_extra_execution_or_listen_options(self) -> None:
        command = self.relay.command()
        unsafe = [
            [command[0], command[1] + " extra", command[2]],
            [command[0], command[1].replace(" -ei", " -evil"), command[2]],
            [command[0], command[1].replace(self.module.PIPE, "//./pipe/another"), command[2]],
            [command[0], "EXEC:/other/npiperelay.exe -ei -s " + self.module.PIPE, command[2]],
            [command[0], command[1], command[2] + ",user=nobody"],
            [command[0], command[1], command[2].replace("mode=600", "mode=666")],
            [command[0], command[1], command[2].replace(str(self.socket_path), "/other/socket")],
        ]
        for argv in unsafe:
            with self.subTest(argv=argv), self.assertRaisesRegex(self.module.Refused, "different socket/relay"):
                self.proc_identity(argv)

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
