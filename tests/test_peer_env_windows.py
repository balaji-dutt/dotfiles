from __future__ import annotations

import _thread
import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from tests.support.fixtures import load_script_module

ROOT = Path(__file__).resolve().parents[1]
MODULE = load_script_module(ROOT / "assets/peer-env", "peer_env_native_tests", add_parent_to_path=True)
if os.name == "nt":
    WIN = MODULE.windows


@unittest.skipUnless(os.name == "nt", "native Windows client primitives")
class WindowsClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="peer windows '")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.env = dict(os.environ, USERPROFILE=str(self.root), HOME=str(self.root),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.peer = MODULE.Peer("macos", "M4MacBook", "macos", "~/dotfiles", "~\\key's ü.pub", 3, False)

    def cache(self):
        return self.state / "probe.json"

    def capture(self, source, *, timeout=5, input_text=None):
        return WIN.capture([sys.executable, "-c", source], timeout=timeout, input_text=input_text,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def configure(self):
        path = MODULE.config_path(self.env)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"schema_version": 1, "peers": {
            "macos": {"platform": "macos", "ssh_host": "M4MacBook", "repo": "~/dotfiles"},
            "wsl2": {"platform": "wsl2", "ssh_host": "WSL2Debian", "repo": "~/dotfiles"}}}), encoding="utf-8")
        return MODULE.load_config(self.env)

    def identity(self):
        return MODULE.ProbeResult("macos", "M4MacBook", "macos", "reachable", "fixture", True, {}, 0.1)

    def set_dacl(self, path, suffix):
        sd = WIN.P()
        sid = WIN.user_sid()
        sddl = f"O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)" + suffix
        WIN.checked(WIN.descriptor(sddl, 1, ctypes.byref(sd), None), "fixture descriptor")
        try:
            setter = WIN.bind(WIN.advapi, "SetFileSecurityW", WIN.W.BOOL, WIN.W.LPCWSTR, WIN.DWORD, WIN.P)
            WIN.checked(setter(str(path), 0x80000004, sd), "fixture DACL")
        finally:
            WIN.free(sd)

    def test_private_state_create_reuse_and_atomic_replacement(self):
        WIN.atomic_write(self.cache(), "first ü")
        self.assertEqual(WIN.read_private(self.cache()), "first ü")
        WIN.atomic_write(self.cache(), "second")
        WIN.private_directory(self.state)
        self.assertEqual(WIN.read_private(self.cache()), "second")
        WIN.unlink_private(self.cache())
        self.assertFalse(self.cache().exists())

    def test_failed_replace_preserves_cache_and_removes_temporary(self):
        WIN.atomic_write(self.cache(), "old")
        with mock.patch.object(WIN.os, "replace", side_effect=PermissionError("fixture")):
            with self.assertRaises(PermissionError):
                WIN.atomic_write(self.cache(), "new")
        self.assertEqual(WIN.read_private(self.cache()), "old")
        self.assertEqual([p.name for p in self.state.iterdir()], ["probe.json"])

    def test_unsafe_directory_and_cache_refused_before_read(self):
        WIN.atomic_write(self.cache(), "cached reachable")
        self.set_dacl(self.cache(), "(A;;FR;;;BU)")
        with self.assertRaisesRegex(OSError, "untrusted principal"):
            WIN.read_private(self.cache())
        with self.assertRaises(OSError):
            WIN.atomic_write(self.cache(), "new")
        self.set_dacl(self.cache(), "")
        self.set_dacl(self.state, "(A;;FA;;;BU)")
        with self.assertRaises(OSError):
            WIN.read_private(self.cache())

    def test_unprotected_existing_state_is_not_silently_repaired(self):
        self.state.mkdir()
        with self.assertRaisesRegex(OSError, "protected"):
            WIN.private_directory(self.state)

    def test_hard_link_cache_refused(self):
        WIN.atomic_write(self.cache(), "cached")
        os.link(self.cache(), self.state / "alias")
        with self.assertRaisesRegex(OSError, "hard link"):
            WIN.read_private(self.cache())

    def test_junction_ancestor_is_refused(self):
        target = self.root / "target"
        target.mkdir()
        junction = self.root / "junction"
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        try:
            with self.assertRaisesRegex(OSError, "reparse"):
                WIN.private_directory(junction / "private")
            self.assertFalse((target / "private").exists())
        finally:
            junction.rmdir()

    def test_unverifiable_security_refused(self):
        with mock.patch.object(WIN, "security_info", return_value=5):
            with self.assertRaises(OSError):
                WIN.private_directory(self.state)
        with self.assertRaisesRegex(OSError, "local filesystem"):
            WIN.private_directory(Path(r"\\fixture\share\state"))
        with mock.patch.object(WIN, "drive_type", return_value=4):
            with self.assertRaisesRegex(WIN.Refused, "local fixed drive"):
                WIN.private_directory(self.state)

    def test_cache_ttl_fingerprint_schema_and_unsafe_reuse(self):
        config = self.configure()
        MODULE.save_cache(config, {"macos": self.identity()}, self.env, time.time())
        self.assertIn("macos", MODULE.load_cache(config, self.env, 300)[0])
        self.assertIsNone(MODULE.load_cache(config, self.env, 0))
        other = MODULE.Config(config.path, config.self_platform, config.peers, "different")
        self.assertIsNone(MODULE.load_cache(other, self.env, 300))
        path = MODULE.cache_path(self.env)
        WIN.atomic_write(path, "{")
        self.assertIsNone(MODULE.load_cache(config, self.env, 300))
        self.set_dacl(path, "(A;;FR;;;BU)")
        with self.assertRaises(OSError):
            MODULE.load_cache(config, self.env, 300)

    def test_cli_missing_config_tools_and_unsupported_serve(self):
        for args in (["probe", "--json"], ["serve", MODULE.PROTOCOL, "identity"]):
            with mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(MODULE.main(args), 2)
        with self.assertRaises(WIN.Refused):
            WIN.ssh_executable({"SystemRoot": str(self.root)})

    def test_windows_selects_all_wsl_and_macos_peers(self):
        config = self.configure()
        self.assertEqual(config.self_platform, "windows")
        self.assertEqual({p.platform for p in MODULE.select_peers(config, "all")}, {"wsl2", "macos"})
        self.assertIsNone(MODULE.control_path())

    def test_probe_human_json_and_cache_without_real_transport(self):
        self.configure()
        fake = {name: MODULE.ProbeResult(name, peer.ssh_host, peer.platform, "reachable", "fixture", True, {}, 0.1)
                for name, peer in MODULE.load_config(self.env).peers.items()}
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(MODULE, "probe_all", return_value=fake) as probe:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(MODULE.main(["probe", "--json"]), 0)
            self.assertEqual(json.loads(out.getvalue())["self_platform"], "windows")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(MODULE.main(["probe"]), 0)
            self.assertIn("2 reachable", out.getvalue())
            self.assertEqual(probe.call_count, 1)

    def test_pinned_native_ssh_and_shell_command_quoting(self):
        native = WIN.ssh_executable(self.env)
        self.assertTrue(native.lower().endswith(r"system32\openssh\ssh.exe"))
        with mock.patch.object(shutil, "which", return_value=r"C:\Program Files\Git\usr\bin\ssh.exe"):
            argv = MODULE.ssh_argv(self.peer, [MODULE.PROTOCOL, "identity"], self.env)
        self.assertEqual(argv[0], native)
        self.assertIn("ControlPath=none", argv)
        self.assertIn(str(self.root) + "\\key's ü.pub", argv)
        result = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(MODULE, "git", return_value=result) as git:
            MODULE.publish(self.peer, self.root, "a" * 40, "fixture", self.env)
        env = git.call_args.kwargs["env"]
        command = shlex.split(env["GIT_SSH_COMMAND"])
        self.assertEqual(command[0], native.replace("\\", "/"))
        self.assertEqual(command[command.index("-i") + 1], (str(self.root) + "\\key's ü.pub").replace("\\", "/"))
        self.assertEqual(env["GIT_SSH_VARIANT"], "ssh")

    def test_capture_input_unicode_exit_and_streaming(self):
        result = self.capture("import sys;print(sys.stdin.read());print('err',file=sys.stderr);sys.exit(7)", input_text="ü input")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (7, "ü input\n", "err\n"))
        self.assertEqual(WIN.capture([sys.executable, "-c", "pass"], timeout=5).returncode, 0)
        missing = WIN.capture([str(self.root / "absent.exe")], timeout=5, stderr=subprocess.PIPE)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("cannot launch", missing.stderr)

    def alive(self, pid):
        opener = WIN.bind(WIN.kernel, "OpenProcess", WIN.W.HANDLE, WIN.DWORD, WIN.W.BOOL, WIN.DWORD)
        wait = WIN.bind(WIN.kernel, "WaitForSingleObject", WIN.DWORD, WIN.W.HANDLE, WIN.DWORD)
        handle = opener(0x100000, False, pid)
        if not handle:
            return False
        try:
            return wait(handle, 0) == 258
        finally:
            WIN.close(handle)

    def test_normal_exit_accounts_for_orphan_with_inherited_pipes(self):
        source = "import subprocess,sys;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);print(p.pid,flush=True)"
        started = time.monotonic()
        result = self.capture(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.alive(int(result.stdout)))
        self.assertLess(time.monotonic() - started, 5)

    def test_timeout_kills_child_and_grandchild(self):
        marker = self.root / "pid"
        source = ("import subprocess,sys,time,pathlib;"
                  "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                  f"pathlib.Path({str(marker)!r}).write_text(str(p.pid));time.sleep(60)")
        with self.assertRaises(subprocess.TimeoutExpired):
            self.capture(source, timeout=1)
        self.assertTrue(marker.exists())
        self.assertFalse(self.alive(int(marker.read_text())))

    def test_interrupt_kills_command_tree(self):
        marker = self.root / "pid"
        source = f"import os,pathlib,time;pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(60)"
        timer = threading.Timer(0.5, _thread.interrupt_main)
        timer.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.capture(source)
        finally:
            timer.join()
        self.assertTrue(marker.exists())
        self.assertFalse(self.alive(int(marker.read_text())))

    def test_assignment_failure_never_launches_command(self):
        marker = self.root / "launched"
        with mock.patch.object(WIN.Job, "assign", side_effect=OSError("assignment refused")):
            with self.assertRaisesRegex(OSError, "assignment refused"):
                self.capture(f"from pathlib import Path;Path({str(marker)!r}).touch()")
        self.assertFalse(marker.exists())

    def test_unverified_shutdown_cannot_report_success(self):
        with mock.patch.object(WIN.Job, "stop", side_effect=OSError("fixture accounting failure")):
            with self.assertRaisesRegex(OSError, "shutdown unverified"):
                self.capture("pass")

    def test_snapshot_keeps_head_index_worktree_and_modes(self):
        repo = self.root / "repo"
        repo.mkdir()
        def git(*args):
            return MODULE.git(repo, *args, env=self.env).stdout.strip()
        git("init", "-q")
        (repo / "script.sh").write_bytes(b"#!/bin/sh\necho original\n")
        git("add", "script.sh")
        git("update-index", "--chmod=+x", "script.sh")
        git("commit", "-qm", "fixture")
        head, index = git("rev-parse", "HEAD"), (repo / ".git/index").read_bytes()
        content = b"#!/bin/sh\necho changed\n"
        (repo / "script.sh").write_bytes(content)
        before = git("status", "--porcelain=v1")
        sha = MODULE.snapshot_tree(repo, self.env, tracked_only=False)
        self.assertNotEqual(sha, head)
        self.assertEqual(git("rev-parse", "HEAD"), head)
        self.assertEqual((repo / ".git/index").read_bytes(), index)
        self.assertEqual((repo / "script.sh").read_bytes(), content)
        self.assertEqual(git("status", "--porcelain=v1"), before)
        self.assertTrue(git("ls-tree", sha, "script.sh").startswith("100755"))
        self.assertFalse(list(MODULE.state_dir(self.env).glob("index-*")))

    def test_ancestor_acl_allows_sibling_directories_not_chain_replacement(self):
        self.set_dacl(self.root, '(A;;0x4;;;BU)')
        WIN.atomic_write(self.cache(), 'private')
        self.assertEqual(WIN.read_private(self.cache()), 'private')
        self.set_dacl(self.root, '(A;;0x40;;;BU)')
        with self.assertRaises(WIN.Refused):
            WIN.read_private(self.cache())

    def test_concurrent_probe_interrupt_stops_workers_and_resets_cancel(self):
        markers = [self.root / f'worker-{index}' for index in range(2)]
        config = self.configure()
        peers = list(config.peers.values())
        def probe(peer, *args):
            marker = markers[peers.index(peer)]
            source = f"import os,pathlib,time;pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(60)"
            self.capture(source, timeout=10)
        timer = threading.Timer(0.7, _thread.interrupt_main)
        timer.start()
        started = time.monotonic()
        try:
            with mock.patch.object(MODULE, 'probe_peer', side_effect=probe), self.assertRaises(KeyboardInterrupt):
                MODULE.probe_all(peers, self.env, None)
        finally:
            timer.join()
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(WIN.CANCEL.is_set())
        for marker in markers:
            self.assertTrue(marker.exists())
            self.assertFalse(self.alive(int(marker.read_text())))
        self.assertEqual(self.capture('pass').returncode, 0)

    def test_stream_timeout_and_missing_cache_clean(self):
        with mock.patch.object(MODULE, 'ssh_argv', return_value=[sys.executable, '-c', 'pass']):
            self.assertEqual(MODULE.ssh_stream(self.peer, [], self.env, timeout=5), 0)
        with mock.patch.object(MODULE, 'ssh_argv', return_value=[sys.executable, '-c', 'import time;time.sleep(60)']), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(MODULE.ssh_stream(self.peer, [], self.env, timeout=0.1), 124)
        self.configure()
        args = MODULE.build_parser().parse_args(['clean', 'macos'])
        with mock.patch.object(MODULE, 'resolve_probe', return_value=({'macos': self.identity()}, False, time.time())), mock.patch.object(MODULE, 'remote_request'), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(MODULE.cmd_clean(args, self.env), 0)

    def test_refusal_and_operational_failure_have_distinct_exits(self):
        self.configure()
        for error, code in ((WIN.Refused('unsafe state'), 2), (OSError('shutdown unverified'), 1)):
            with mock.patch.object(MODULE, 'resolve_probe', side_effect=error), mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(MODULE.main(['probe']), code)

    def test_git_resolution_ignores_implicit_and_relative_current_directory(self):
        (self.root / 'git.exe').touch()
        with self.assertRaises(WIN.Refused):
            WIN.git_executable({'PATH': ';.'})
        resolved = WIN.git_executable(self.env)
        self.assertTrue(Path(resolved).is_absolute())
        with mock.patch.object(MODULE, 'capture', return_value=subprocess.CompletedProcess([], 0, '', '')) as capture:
            MODULE.git(self.root, 'version', env=self.env)
        self.assertEqual(capture.call_args.args[0][0], resolved)


if __name__ == "__main__":
    unittest.main()
