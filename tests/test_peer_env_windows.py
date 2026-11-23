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
    LAUNCHER = load_script_module(ROOT / "assets/peer_env_windows_launcher.py", "peer_env_launcher_tests")


def elevated_process():
    try:
        return os.name == "nt" and WIN.elevated()
    except OSError:
        return False


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

    def test_cli_missing_config_and_tools(self):
        with mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(MODULE.main(["probe", "--json"]), 2)
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


STUB_ENTRYPOINT = r"""
$mode = if (Test-Path -LiteralPath 'stub-mode.txt') { (Get-Content -LiteralPath 'stub-mode.txt' -Raw).Trim() } else { 'ok' }
New-Item -ItemType Directory -Force -Path '.cz-audit' | Out-Null
@{
  args = @($args)
  cwd = (Get-Location).Path
  names = @([Environment]::GetEnvironmentVariables().Keys | Sort-Object)
  hint = $env:CZ_AUDIT_PEER_ENV
  peer = $env:PEER_ENV
} | ConvertTo-Json | Set-Content -LiteralPath '.cz-audit/__NAME__.json' -Encoding utf8
Write-Output "stub __NAME__ $($args -join ' ')"
switch ($mode) {
  'fail' { exit 3 }
  'error-line' { Write-Output 'ERROR: stub'; exit 0 }
  'sleep' {
    $child = Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList '-NoProfile', '-Command', 'Start-Sleep 120' -PassThru -NoNewWindow
    Set-Content -LiteralPath '.cz-audit/grandchild.pid' -Value $child.Id
    Start-Sleep 120
  }
}
exit 0
"""

FAKE_SSH = """import os, subprocess, sys
env = dict(os.environ, SSH_ORIGINAL_COMMAND=sys.argv[-1], SSH_CLIENT="192.0.2.1 50000 22")
helper = os.path.join(os.environ["PEER_FIXTURE"], "assets", "peer-env")
sys.exit(subprocess.run([sys.executable, helper, "serve"], env=env).returncode)
"""


@unittest.skipUnless(os.name == "nt", "native Windows serve")
class ServeWindowsTests(unittest.TestCase):
    def setUp(self):
        serve = MODULE.Serve(ROOT, os.environ, True)
        try:
            serve.entrypoint("run-tests")
        except MODULE.Refused as error:
            self.skipTest(str(error))
        self.temp = tempfile.TemporaryDirectory(prefix="peer-serve-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.peer = self.root / "peer"
        self.local = self.root / "local"
        self.env = dict(os.environ, USERPROFILE=str(self.home), FAKE_PROVIDER_KEY="secret",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.env.pop("SSH_ORIGINAL_COMMAND", None)
        self.state = MODULE.state_dir(self.env)
        assets = self.peer / "assets"
        assets.mkdir(parents=True)
        for name in ("peer-env", "peer_env_windows.py"):
            shutil.copy2(ROOT / "assets" / name, assets / name)
        for name in ("cz-audit", "run-tests"):
            (assets / f"{name}.ps1").write_text(STUB_ENTRYPOINT.replace("__NAME__", name), encoding="utf-8")
        (self.peer / ".gitignore").write_text("worktrees/\n", encoding="utf-8")
        self.git(self.peer, "init", "--quiet", "--initial-branch=main")
        self.git(self.peer, "add", "--all")
        self.git(self.peer, "commit", "--quiet", "-m", "fixture")
        self.git(self.root, "clone", "--quiet", str(self.peer), str(self.local))
        self.fake_ssh = self.root / "fake_ssh.py"
        self.fake_ssh.write_text(FAKE_SSH, encoding="utf-8")
        self.count = 0

    def git(self, cwd, *args, check=True):
        result = subprocess.run(["git", "-C", str(cwd), *args], env=self.env, capture_output=True, text=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def push(self, refspec):
        env = dict(self.env, PEER_FIXTURE=str(self.peer), GIT_SSH_VARIANT="ssh",
                   GIT_SSH_COMMAND=f'"{Path(sys.executable).as_posix()}" "{self.fake_ssh.as_posix()}"')
        helper = '"$HOME"/dotfiles/assets/peer-env serve ' + MODULE.PROTOCOL + " receive-pack"
        return subprocess.run(["git", "-C", str(self.local), "push", "--quiet", "--receive-pack", helper,
                               "fixture:dotfiles", refspec], env=env, capture_output=True, text=True)

    def publish(self, mode="ok"):
        self.count += 1
        slug = f"fixture-{self.count}"
        (self.local / "stub-mode.txt").write_text(mode + "\n", encoding="utf-8")
        self.git(self.local, "add", "stub-mode.txt")
        self.git(self.local, "commit", "--quiet", "--allow-empty", "-m", f"{mode} {self.count}")
        sha = self.git(self.local, "rev-parse", "HEAD").stdout.strip()
        pushed = self.push(f"{sha}:{MODULE.REF_NAMESPACE}{slug}")
        self.assertEqual(pushed.returncode, 0, pushed.stderr)
        return slug, sha

    def serve(self, *request, original=None, restricted=True):
        environ = dict(self.env)
        if restricted:
            environ["SSH_ORIGINAL_COMMAND"] = original or '"$HOME"/dotfiles/assets/peer-env serve ' + shlex.join(request)
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(MODULE, "__file__", str(self.peer / "assets" / "peer-env")), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = MODULE.serve_main([] if restricted else list(request), environ)
        return code, stdout.getvalue(), stderr.getvalue()

    def run_request(self, slug, sha, *rest, seconds="120", retention="cleanup"):
        return self.serve(MODULE.PROTOCOL, "run", slug, sha, seconds, retention, *rest)

    def worktree(self, slug):
        return self.peer / "worktrees" / f"peer-env-{slug}"

    def published(self, slug):
        return self.git(self.peer, "rev-parse", "--verify", "--quiet", MODULE.REF_NAMESPACE + slug, check=False).returncode == 0

    def result(self, slug):
        return json.loads((self.state / "runs" / slug / "result.json").read_text(encoding="utf-8"))

    def recorded(self, slug, name):
        return json.loads((self.state / "runs" / slug / f"audit-{name}.json").read_text(encoding="utf-8-sig"))

    def alive(self, pid):
        handle = WIN.open_process(0x100000, False, pid)
        if not handle:
            return False
        try:
            return WIN.wait_handle(handle, 0) == 258
        finally:
            WIN.close(handle)

    def test_identity_reports_windows_tools_and_restriction(self):
        code, out, err = self.serve(MODULE.PROTOCOL, "identity")
        self.assertEqual(code, 0, err)
        identity = MODULE.parse_identity(out)
        self.assertEqual((identity["platform"], identity["uname"], identity["restricted"]), ("windows", "Windows", True))
        self.assertEqual(identity["protocol"], MODULE.PROTOCOL)
        self.assertIn(MODULE.RUN_CAPABILITY, identity["capabilities"])
        self.assertTrue(identity["tools"]["git"] and identity["tools"]["pwsh"])
        self.assertIsInstance(identity["link_following"], bool)
        code, out, _ = self.serve(MODULE.PROTOCOL, "identity", restricted=False)
        self.assertEqual(code, 0)
        self.assertFalse(MODULE.parse_identity(out)["restricted"])
        log = [json.loads(line) for line in (self.state / "serve.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([entry["verb"] for entry in log], ["identity", "identity"])

    def test_requests_outside_the_protocol_are_refused(self):
        requests = (["peer-env/0", "identity"], [MODULE.PROTOCOL, "shell"], [MODULE.PROTOCOL, "identity", "extra"],
                    [MODULE.PROTOCOL, "run", "bad slug", "0" * 40, "5", "cleanup", "audit", "assets/peer-env"])
        for request in requests:
            with self.subTest(request=request):
                code, _, err = self.serve(*request)
                self.assertEqual(code, 2)
                self.assertIn("refused", err)
        code, _, err = self.serve(original="whoami")
        self.assertEqual(code, 2)
        self.assertIn("only peer-env serve requests", err)

    def test_elevated_serve_refuses_before_touching_state(self):
        stderr = io.StringIO()
        with mock.patch.object(WIN, "elevated", return_value=True), \
                mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stderr(stderr):
            self.assertEqual(MODULE.main(["serve", MODULE.PROTOCOL, "identity"]), 2)
        self.assertIn("elevated", stderr.getvalue())
        self.assertFalse(self.state.exists())

    def test_receive_pack_accepts_only_the_helper_namespace(self):
        slug, sha = self.publish()
        self.assertTrue(self.published(slug))
        for ref in ("refs/heads/evil", MODULE.REF_NAMESPACE + "nested/slug"):
            with self.subTest(ref=ref):
                refused = self.push(f"{sha}:{ref}")
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("outside " + MODULE.REF_NAMESPACE, refused.stderr)
                self.assertNotEqual(self.git(self.peer, "rev-parse", "--verify", "--quiet", ref, check=False).returncode, 0)

    def test_run_scrubs_the_environment_and_cleans_up(self):
        slug, sha = self.publish()
        code, out, err = self.run_request(slug, sha, "audit", "assets/peer-env")
        self.assertEqual(code, 0, out + err)
        self.assertIn("stub cz-audit check assets/peer-env", out)
        record = self.recorded(slug, "cz-audit")
        self.assertEqual(record["args"], ["check", "assets/peer-env"])
        self.assertEqual(Path(record["cwd"]), self.worktree(slug))
        self.assertNotIn("FAKE_PROVIDER_KEY", record["names"])
        self.assertNotIn("SSH_ORIGINAL_COMMAND", record["names"])
        self.assertEqual((record["hint"], record["peer"]), ("0", "1"))
        result = self.result(slug)
        self.assertEqual((result["completed"], result["prepared"], result["exit"], result["cleanup"]),
                         (True, True, 0, "removed"))
        self.assertFalse(self.worktree(slug).exists())
        self.assertFalse(self.published(slug))

        other = slug + "-suite"
        self.assertEqual(self.push(f"{sha}:{MODULE.REF_NAMESPACE}{other}").returncode, 0)
        code, out, err = self.run_request(other, sha, "test", "fast", "--require-capabilities")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.recorded(other, "run-tests")["args"], ["fast", "--require-capabilities"])

    def test_entrypoint_failure_and_error_line_fail_the_run(self):
        slug, sha = self.publish("fail")
        code, out, err = self.run_request(slug, sha, "test", "fast")
        self.assertEqual(code, 3, out + err)
        slug, sha = self.publish("error-line")
        code, out, err = self.run_request(slug, sha, "audit", "assets/peer-env")
        self.assertEqual(code, 1, out + err)
        self.assertIn("ERROR: stub", out)
        self.assertEqual(self.result(slug)["cleanup"], "removed")

    def test_deadline_stops_the_command_tree(self):
        slug, sha = self.publish("sleep")
        code, out, err = self.run_request(slug, sha, "audit", "assets/peer-env", seconds="30")
        self.assertEqual(code, 124, out + err)
        self.assertIn("deadline exceeded", err)
        grandchild = int((self.state / "runs" / slug / "audit-grandchild.pid").read_text(encoding="utf-8-sig").strip())
        self.assertFalse(self.alive(grandchild))
        self.assertEqual(self.result(slug)["cleanup"], "removed")
        self.assertFalse(self.worktree(slug).exists())

    def test_busy_slug_exits_75(self):
        slug, sha = self.publish()
        with WIN.lock_file(self.state / f"lock-{slug}"):
            code, _, err = self.run_request(slug, sha, "audit", "assets/peer-env")
        self.assertEqual(code, 75)
        self.assertIn("busy", err)
        self.assertTrue(self.published(slug))

    def test_keep_then_remove_and_clean(self):
        slug, sha = self.publish()
        code, out, err = self.run_request(slug, sha, "audit", "assets/peer-env", retention="keep")
        self.assertEqual(code, 0, out + err)
        self.assertTrue(self.worktree(slug).is_dir())
        code, _, err = self.serve(MODULE.PROTOCOL, "remove", slug, sha)
        self.assertEqual(code, 0, err)
        self.assertFalse(self.worktree(slug).exists())
        self.assertFalse(self.published(slug))

        slug, sha = self.publish()
        self.assertEqual(self.run_request(slug, sha, "audit", "assets/peer-env", retention="keep")[0], 0)
        code, _, err = self.serve(MODULE.PROTOCOL, "clean")
        self.assertEqual(code, 0, err)
        self.assertFalse(self.worktree(slug).exists())
        self.assertFalse(self.published(slug))

    def test_unverified_shutdown_retains_worktree_and_ref(self):
        slug, sha = self.publish()
        real = WIN.stream_command

        def unverified(argv, **kwargs):
            kwargs["stopped"] = lambda: None
            return real(argv, **kwargs)

        with mock.patch.object(WIN, "stream_command", side_effect=unverified):
            code, out, err = self.run_request(slug, sha, "audit", "assets/peer-env")
        self.assertEqual(code, 1, out + err)
        self.assertIn("unverified", err)
        self.assertTrue(self.result(slug)["children_unverified"])
        self.assertTrue(self.worktree(slug).is_dir())
        code, _, err = self.serve(MODULE.PROTOCOL, "remove", slug, sha)
        self.assertEqual(code, 2)
        self.assertIn("unverified", err)
        self.assertTrue(self.published(slug))

    def test_linked_worktree_parent_is_refused(self):
        slug, sha = self.publish()
        target = self.root / "elsewhere"
        target.mkdir()
        made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(self.peer / "worktrees"), str(target)],
                              capture_output=True, text=True)
        self.assertEqual(made.returncode, 0, made.stderr)
        try:
            code, out, err = self.run_request(slug, sha, "audit", "assets/peer-env")
        finally:
            (self.peer / "worktrees").rmdir()
        self.assertEqual(code, 2, out + err)
        self.assertIn("worktree parent is a link", err)
        self.assertEqual(list(target.iterdir()), [])


@unittest.skipUnless(os.name == "nt", "native Windows serve launcher")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="peer-launcher-")
        self.addCleanup(self.temp.cleanup)
        self.env = dict(os.environ, USERPROFILE=self.temp.name)

    def launch(self, *args, original=None):
        env = dict(self.env)
        env.pop("SSH_ORIGINAL_COMMAND", None)
        if original is not None:
            env["SSH_ORIGINAL_COMMAND"] = original
        return subprocess.run([sys.executable, "-I", str(ROOT / "assets" / "peer_env_windows_launcher.py"), *args],
                              env=env, capture_output=True, text=True, timeout=120)

    def test_relays_serve_output_and_exit_status(self):
        served = self.launch(str(ROOT), original=f'"$HOME"/dotfiles/assets/peer-env serve {MODULE.PROTOCOL} identity')
        self.assertEqual(served.returncode, 0, served.stderr)
        identity = MODULE.parse_identity(served.stdout)
        self.assertEqual((identity["platform"], identity["restricted"]), ("windows", True))
        refused = self.launch(str(ROOT), original="whoami")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("only peer-env serve requests", refused.stderr)

    def test_unusable_checkout_roots_are_refused(self):
        for args, message in (([], "usage"), (["relative"], "absolute"), ([self.temp.name], "no peer-env helper")):
            with self.subTest(args=args):
                refused = self.launch(*args)
                self.assertEqual(refused.returncode, 2)
                self.assertIn("peer-env launcher: refused", refused.stderr)
                self.assertIn(message, refused.stderr)

    def test_a_token_that_stays_elevated_starts_nothing(self):
        stderr = io.StringIO()
        with mock.patch.object(LAUNCHER, "token_elevated", return_value=True), \
                mock.patch.object(LAUNCHER, "create_as_user") as create, contextlib.redirect_stderr(stderr):
            self.assertEqual(LAUNCHER.main([str(ROOT)]), 2)
        self.assertIn("still elevated", stderr.getvalue())
        create.assert_not_called()

    @unittest.skipUnless(elevated_process(), "needs an elevated test process")
    def test_elevated_session_reaches_serve_unelevated(self):
        served = self.launch(str(ROOT), original=f'"$HOME"/dotfiles/assets/peer-env serve {MODULE.PROTOCOL} identity')
        self.assertEqual(served.returncode, 0, served.stderr)


if __name__ == "__main__":
    unittest.main()
