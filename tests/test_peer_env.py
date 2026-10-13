from __future__ import annotations

import json
import contextlib
import io
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

from tests.support.fixtures import (
    init_git_repository,
    isolated_environment,
    load_script_module,
    read_json_lines,
    run_git,
    write_executable,
    write_json,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_HELPER = REPO_ROOT / "assets" / "peer-env"
SOURCE_RESOLVER = REPO_ROOT / "assets" / "resolve-python3"
SH = shutil.which("sh")
# The fake ssh shim cannot shadow ssh.exe and the helper's #!/bin/sh entry point
# needs a real sh, so the subprocess suites run on POSIX hosts only.
POSIX_ONLY = unittest.skipIf(os.name == "nt" or SH is None, "requires a POSIX sh and PATH shims")
UNAME = {
    "macos": ("Darwin", "24.6.0"),
    "wsl2": ("Linux", "5.15.167.4-microsoft-standard-WSL2"),
    "linux": ("Linux", "6.1.0-generic"),
}
REPO_REL = "Documents/development/dotfiles"

FAKE_SSH = f"""#!{sys.executable}
import json, os, pathlib, subprocess, sys, time
state = json.loads(pathlib.Path(os.environ["FAKE_SSH_STATE"]).read_text(encoding="utf-8"))
log = pathlib.Path(os.environ["FAKE_SSH_LOG"])
args = sys.argv[1:]
host = None
command = None
value_flags = {{"-o", "-i", "-p", "-l", "-F", "-E"}}
index = 0
while index < len(args):
    item = args[index]
    if host is None:
        if item == "--":
            host = args[index + 1]
            index += 2
            continue
        if item in value_flags:
            index += 2
            continue
        if item.startswith("-"):
            index += 1
            continue
        host = item
        index += 1
        continue
    command = " ".join(args[index:])
    break
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"argv": args, "host": host, "command": command}}) + "\\n")
peer = state.get(host)
if peer is None:
    sys.stderr.write(f"ssh: Could not resolve hostname {{host}}: nodename nor servname provided\\n")
    sys.exit(255)
mode = peer["mode"]
if mode == "fail":
    sys.stderr.write(peer.get("stderr", ""))
    sys.exit(peer.get("exit", 255))
if mode == "hang":
    time.sleep(peer.get("seconds", 30))
    sys.exit(255)
env = {{k: v for k, v in os.environ.items() if k not in ("FAKE_SSH_STATE", "FAKE_SSH_LOG", "SSH_ORIGINAL_COMMAND")}}
for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
    env.pop(name, None)
env.update({{
    "HOME": peer["home"],
    "USER": "peeruser",
    "LOGNAME": "peeruser",
    "SHELL": "/bin/sh",
    "PATH": peer["bin"] + os.pathsep + env.get("PATH", ""),
    "FAKE_PROVIDER_KEY": "sk-secret-value",
    "SSH_CLIENT": "192.0.2.10 50000 22",
}})
if mode == "restricted":
    env["SSH_ORIGINAL_COMMAND"] = command
    rc = subprocess.call([peer["helper"], "serve"], env=env, cwd=peer["home"])
else:
    rc = subprocess.call(["sh", "-c", command], env=env, cwd=peer["home"])
sys.exit(rc)
"""

STUB_AUDIT = """#!/bin/sh
mkdir -p .cz-audit
{
  printf 'argv=%s\\n' "$*"
  printf 'cwd=%s\\n' "$(pwd)"
  env | sort
  printf 'END\\n'
} >> .cz-audit/audit-record.txt
mode=ok
[ -f stub-mode.txt ] && mode=$(cat stub-mode.txt)
case "$mode" in
  slow) echo $$ > .cz-audit/child.pid; sleep 30 ;;
  delay) sleep 0.4 ;;
  error) echo "ERROR: stub failure for $2" >&2; exit 0 ;;
  exit7) exit 7 ;;
esac
echo "INFO: stub audit ok $*" >&2
"""

STUB_TESTS = """#!/bin/sh
mkdir -p .cz-audit
{
  printf 'argv=%s\\n' "$*"
  env | sort
  printf 'END\\n'
} >> .cz-audit/tests-record.txt
mode=ok
[ -f stub-mode.txt ] && mode=$(cat stub-mode.txt)
case "$mode" in exit7) exit 7 ;; esac
echo "stub tests ran: $*"
"""


def load_helper_module():
    return load_script_module(SOURCE_HELPER, "peer_env_test_module", add_parent_to_path=True)


MODULE = load_helper_module()
SELF_PLATFORM = MODULE.current_platform()
OTHER_PLATFORMS = [platform for platform in ("wsl2", "macos", "linux") if platform != SELF_PLATFORM]


def peer_config(**peers: dict) -> dict:
    return {"schema_version": 1, "peers": peers}


class PeerFixture:
    """A local checkout, fake peers reached through a fake ssh, and the config."""

    def __init__(self, test: unittest.TestCase, peers: dict[str, dict]) -> None:
        context = isolated_environment(prefix="peer-env-test-")
        self.iso = context.__enter__()
        test.addCleanup(context.__exit__, None, None, None)
        self.env = dict(self.iso.env)
        self.root = self.iso.root
        self.home = self.iso.home
        self.local = self.root / "local" / "dotfiles"
        init_git_repository(self.local, env=self.env)
        run_git(self.local, "symbolic-ref", "HEAD", "refs/heads/main", env=self.env)
        (self.local / ".gitignore").write_text("worktrees/\n.cz-audit/\nignored.txt\n", encoding="utf-8")
        assets = self.local / "assets"
        assets.mkdir()
        for source in (SOURCE_HELPER, SOURCE_RESOLVER):
            destination = assets / source.name
            shutil.copy2(source, destination)
            destination.chmod(0o755)
        write_executable(assets / "cz-audit.sh", STUB_AUDIT)
        write_executable(assets / "run-tests.sh", STUB_TESTS)
        (self.local / "tracked.txt").write_text("v1\n", encoding="utf-8")
        (self.local / "gone.txt").write_text("doomed\n", encoding="utf-8")
        run_git(self.local, "add", "-A", env=self.env)
        run_git(self.local, "commit", "-q", "-m", "init", env=self.env)
        self.head = run_git(self.local, "rev-parse", "HEAD", env=self.env).stdout.strip()
        self.ssh_state: dict[str, dict] = {}
        self.ssh_log = self.root / "ssh-log.jsonl"
        self.state_path = self.root / "ssh-state.json"
        self.peer_homes: dict[str, Path] = {}
        config: dict[str, dict] = {}
        for name, spec in peers.items():
            config[name] = self.add_peer(name, spec)
        write_json(self.state_path, self.ssh_state)
        self.config_path = self.home / ".config" / "dotfiles" / "peer-envs.json"
        write_json(self.config_path, peer_config(**config))
        write_executable(self.iso.fake_bin / "ssh", FAKE_SSH)
        self.env.update({"FAKE_SSH_STATE": str(self.state_path), "FAKE_SSH_LOG": str(self.ssh_log)})

    def add_peer(self, name: str, spec: dict) -> dict:
        host = spec.get("ssh_host", f"{name}-host")
        platform = spec.get("platform", OTHER_PLATFORMS[0])
        peer_home = self.root / "peers" / name / "home"
        repo = peer_home / REPO_REL
        peer_bin = self.root / "peers" / name / "bin"
        peer_bin.mkdir(parents=True)
        uname, kernel = UNAME[platform]
        write_executable(
            peer_bin / "uname",
            f'#!/bin/sh\ncase "$1" in -s) echo {uname} ;; -r) echo {kernel} ;; *) echo {uname} ;; esac\n',
        )
        if spec.get("mode", "restricted") in ("restricted", "unrestricted"):
            run_git(self.root, "clone", "-q", str(self.local), str(repo), env=self.env)
        self.peer_homes[name] = peer_home
        self.ssh_state[host] = {
            "mode": spec.get("mode", "restricted"),
            "home": str(peer_home),
            "bin": str(peer_bin),
            "helper": str(repo / "assets" / "peer-env"),
            "stderr": spec.get("stderr", ""),
            "exit": spec.get("exit", 255),
            "seconds": spec.get("seconds", 30),
        }
        entry = {"ssh_host": host, "platform": platform, "repo": f"~/{REPO_REL}"}
        for key in ("identity_file", "allow_unrestricted", "connect_timeout"):
            if key in spec:
                entry[key] = spec[key]
        return entry

    def peer_repo(self, name: str) -> Path:
        return self.peer_homes[name] / REPO_REL

    def last_slug(self, name: str) -> str:
        calls = [shlex.split(call["command"]) for call in self.ssh_calls()
                 if call["host"] == f"{name}-host" and " run " in call["command"]]
        return calls[-1][calls[-1].index("run") + 1] if calls else "main"

    def peer_worktree(self, name: str, slug: str | None = None) -> Path:
        slug = slug or self.last_slug(name)
        return self.peer_repo(name) / "worktrees" / f"peer-env-{slug}"

    def peer_state(self, name: str) -> Path:
        return self.peer_homes[name] / ".local" / "state" / "dotfiles" / "peer-env"

    def run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.local / "assets" / "peer-env"), *args],
            cwd=cwd or self.local,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def ssh_calls(self) -> list[dict]:
        return read_json_lines(self.ssh_log)

    def local_git(self, *args: str) -> str:
        return run_git(self.local, *args, env=self.env).stdout

    def peer_git(self, name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run_git(self.peer_repo(name), *args, env=self.env, check=check)

    def audit_record(self, name: str, slug: str | None = None) -> str:
        path = self.peer_worktree(name, slug) / ".cz-audit" / "audit-record.txt"
        if not path.exists():
            path = self.peer_state(name) / "runs" / (slug or self.last_slug(name)) / "audit-audit-record.txt"
        return path.read_text(encoding="utf-8") if path.exists() else ""


def completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["ssh"], returncode, stdout, stderr)


def identity_line(**overrides) -> str:
    payload = {
        "protocol": MODULE.PROTOCOL,
        "capabilities": [MODULE.RUN_CAPABILITY],
        "uname": "Linux",
        "kernel": "5.15.0-microsoft-standard-WSL2",
        "platform": "wsl2",
        "restricted": True,
        "tools": {"git": True, "chezmoi": True, "python3": True, "python": False, "pwsh": True},
    }
    payload.update(overrides)
    return MODULE.IDENTITY_MARKER + json.dumps(payload)


@POSIX_ONLY
class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = PeerFixture(self, {"peer": {}})

    def assert_removed(self, slug: str | None = None) -> None:
        self.assertFalse(self.fixture.peer_worktree("peer", slug).exists())
        self.assertEqual(self.fixture.peer_git("peer", "for-each-ref", "--format=%(refname)", MODULE.REF_NAMESPACE).stdout, "")
        self.assertEqual(self.fixture.peer_git("peer", "worktree", "list", "--porcelain").stdout.count("worktree "), 1)

    def server(self):
        env = {key: value for key, value in self.fixture.env.items() if not key.startswith("XDG_")}
        env["HOME"] = str(self.fixture.peer_homes["peer"])
        return MODULE.Serve(self.fixture.peer_repo("peer"), env, True)

    def publish(self, slug: str = "manual") -> str:
        self.fixture.peer_git("peer", "update-ref", MODULE.REF_NAMESPACE + slug, self.fixture.head)
        return self.fixture.head

    def test_default_cleanup_success_failure_and_error_preserves_diagnostics(self) -> None:
        for mode, code in (("ok", 0), ("exit7", 7), ("error", 1)):
            with self.subTest(mode=mode):
                (self.fixture.local / "stub-mode.txt").write_text(mode)
                result = self.fixture.run("audit", "all", "tracked.txt")
                self.assertEqual(result.returncode, code, result.stderr)
                self.assert_removed()
                directory = self.fixture.peer_state("peer") / "runs" / self.fixture.last_slug("peer")
                manifest = json.loads((directory / "result.json").read_text())
                self.assertTrue(manifest["completed"])
                self.assertEqual(manifest["exit"], code)
                self.assertEqual(manifest["cleanup"], "removed")
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                self.assertEqual((directory / "output.log").stat().st_mode & 0o777, 0o600)
                self.assertIn("argv=check tracked.txt", self.fixture.audit_record("peer"))

    def test_aggregate_deadline_is_not_per_path_and_does_not_retry(self) -> None:
        server = self.server()
        sha = self.publish()
        clock = [100.0]
        deadlines = []

        def audit(command, cwd, scan_errors):
            deadlines.append(server.deadline)
            clock[0] += 0.4
            return 124 if clock[0] >= server.deadline else 0

        with mock.patch.object(MODULE.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(server, "run_streamed", side_effect=audit) as run, \
             contextlib.redirect_stderr(io.StringIO()):
            result = server.run(["manual", sha, "0.65", "cleanup", "audit", "tracked.txt", "gone.txt", ".gitignore"])
        self.assertEqual(result, 124)
        self.assertEqual(deadlines, [100.65, 100.65])
        self.assertEqual([call.args[0][-1] for call in run.call_args_list], ["tracked.txt", "gone.txt"])
        self.assert_removed("manual")

    def test_client_forwards_timeout_without_retrying_validation(self) -> None:
        helper = self.fixture.peer_repo("peer") / "assets/peer-env"
        header = "    def run_streamed(self, command: list[str], cwd: Path, scan_errors: bool) -> int:\n"
        helper.write_text(helper.read_text().replace(header, header + "        return EXIT_TIMEOUT\n"))
        result = self.fixture.run("audit", "all", "tracked.txt", "gone.txt", "--timeout", "0.65")
        self.assertEqual(result.returncode, 124, result.stderr)
        calls = [shlex.split(call["command"]) for call in self.fixture.ssh_calls() if " run " in call["command"]]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][calls[0].index("run") + 3], "0.65")
        self.assert_removed()

    def test_keep_and_rm_are_mutually_exclusive(self) -> None:
        result = self.fixture.run("test", "all", "fast", "--rm", "--keep-worktree")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.fixture.ssh_calls(), [])

    def test_invalid_execution_deadlines_are_refused(self) -> None:
        for value in ("nan", "inf", "0", "-1", "86401"):
            with self.subTest(value=value), self.assertRaises(MODULE.ConfigError):
                MODULE.run_timeout(value)

    def test_preparation_failure_still_removes_published_ref(self) -> None:
        server = self.server()
        sha = self.publish()
        with mock.patch.object(server, "prepare_unlocked", side_effect=MODULE.Refused("synthetic prepare failure")), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(server.run(["manual", sha, "1", "cleanup", "audit", "tracked.txt"]), 2)
        self.assert_removed("manual")

    def test_cleanup_failure_preserves_primary_exit_and_records_incomplete(self) -> None:
        for result in (0, 7):
            server = self.server()
            slug = f"failure-{result}"
            sha = self.publish(slug)
            with mock.patch.object(server, "run_streamed", return_value=result), \
                 mock.patch.object(server, "remove_unlocked", side_effect=MODULE.Refused("synthetic cleanup failure")), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(server.run([slug, sha, "1", "cleanup", "audit", "tracked.txt"]), result or 1)
            manifest = json.loads((server.state / "runs" / slug / "result.json").read_text())
            self.assertIn("incomplete", manifest["cleanup"])
            self.assertTrue(self.fixture.peer_worktree("peer", slug).exists())

    def test_expected_sha_and_unrelated_worktrees_are_preserved(self) -> None:
        result = self.fixture.run("test", "all", "fast", "--keep-worktree")
        self.assertEqual(result.returncode, 0, result.stderr)
        slug = self.fixture.last_slug("peer")
        server = self.server()
        with self.assertRaisesRegex(MODULE.Refused, "ownership mismatch"):
            server.remove([slug, "0" * 40])
        unrelated = self.fixture.root / "ordinary"
        self.fixture.peer_git("peer", "worktree", "add", "-b", "ordinary", str(unrelated))
        self.assertEqual(server.clean([]), 0)
        self.assertTrue(unrelated.exists())
        self.assertTrue(self.fixture.peer_git("peer", "rev-parse", "refs/heads/ordinary").stdout)

    def test_missing_directory_registration_is_removed_without_global_prune(self) -> None:
        server = self.server()
        sha = self.publish()
        server.prepare(["manual", sha])
        shutil.rmtree(self.fixture.peer_worktree("peer", "manual"))
        self.assertEqual(server.remove(["manual", sha]), 0)
        self.assert_removed("manual")

    def test_old_client_verbs_remain_available(self) -> None:
        server = self.server()
        sha = self.publish()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(server.prepare(["manual", sha]), 0)
            self.assertEqual(server.exec(["manual", "test", "fast"]), 0)
            self.assertEqual(server.remove(["manual"]), 0)
        self.assert_removed("manual")

    def test_cleanup_accepts_symlinked_state_root_ancestors(self) -> None:
        (self.fixture.peer_homes["peer"] / ".local/state").mkdir(parents=True)
        alias = self.fixture.root / "home-alias"
        alias.symlink_to(self.fixture.peer_homes["peer"], target_is_directory=True)
        for configured in (False, True):
            with self.subTest(xdg_state_home=configured):
                env = {key: value for key, value in self.fixture.env.items() if not key.startswith("XDG_")}
                env["HOME"] = str(alias)
                if configured:
                    env["XDG_STATE_HOME"] = str(alias / ".local/state")
                server = MODULE.Serve(self.fixture.peer_repo("peer"), env, True)
                sha = self.publish()
                server.prepare(["manual", sha])
                write_json(server.state / "runs/manual/result.json", {"completed": True})
                self.assertEqual(server.remove(["manual", sha]), 0)
                self.assert_removed("manual")

    def test_cleanup_refuses_symlinks_inside_state_subtree(self) -> None:
        for component in ("dotfiles", "peer-env", "runs", "manual", "result.json"):
            with self.subTest(component=component):
                env = {**self.fixture.env, "XDG_STATE_HOME": str(self.fixture.root / f"state-{component}")}
                server = MODULE.Serve(self.fixture.peer_repo("peer"), env, True)
                sha = self.publish()
                server.prepare(["manual", sha])
                result = server.state / "runs/manual/result.json"
                write_json(result, {"completed": True})
                target = next(item for item in (result, *result.parents) if item.name == component)
                moved = self.fixture.root / f"moved-{component}"
                target.rename(moved)
                target.symlink_to(moved, target_is_directory=moved.is_dir())
                with self.assertRaises((MODULE.Refused, OSError)):
                    server.remove(["manual", sha])
                self.assertTrue(self.fixture.peer_worktree("peer", "manual").exists())
                self.assertEqual(server.published_sha("manual"), sha)
                target.unlink()
                moved.rename(target)
                self.assertEqual(server.remove(["manual", sha]), 0)
                self.assert_removed("manual")

    def test_old_server_is_refused_before_publish(self) -> None:
        helper = self.fixture.peer_repo("peer") / "assets" / "peer-env"
        helper.write_text(helper.read_text().replace('"capabilities": [RUN_CAPABILITY]', '"capabilities": []'))
        result = self.fixture.run("test", "all", "fast")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("server-first rollout", result.stderr)
        self.assertFalse(any("receive-pack" in call["command"] for call in self.fixture.ssh_calls()))

    def test_server_signal_stops_owned_child_then_cleans(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum):
                slug = f"signal-{signum}"
                sha = self.publish(slug)
                server = self.server()
                command = server.root / "assets" / "cz-audit.sh"
                write_executable(command, f"#!/bin/sh\nmkdir -p .cz-audit\necho {signum} > .cz-audit/signal\necho $$ > .cz-audit/child.pid\nsleep 30\n")
                env = {**server.environ, "PATH": str(Path(self.fixture.ssh_state["peer-host"]["bin"])) + os.pathsep + server.env["PATH"]}
                child_pid = self.fixture.peer_worktree("peer", slug) / ".cz-audit" / "child.pid"
                self.fixture.peer_git("peer", "config", "user.name", "Test")
                self.fixture.peer_git("peer", "config", "user.email", "test@example.com")
                self.fixture.peer_git("peer", "add", "assets/cz-audit.sh")
                self.fixture.peer_git("peer", "commit", "-qm", "fixture signal")
                sha = self.fixture.peer_git("peer", "rev-parse", "HEAD").stdout.strip()
                self.fixture.peer_git("peer", "update-ref", MODULE.REF_NAMESPACE + slug, sha)
                process = subprocess.Popen([str(server.root / "assets" / "peer-env"), "serve", MODULE.PROTOCOL,
                                            "run", slug, sha, "30", "cleanup", "audit", "tracked.txt"],
                                           env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    until = time.monotonic() + 5
                    while not child_pid.exists() and time.monotonic() < until:
                        time.sleep(0.05)
                    self.assertTrue(child_pid.exists(), "owned audit child did not start")
                    pid = int(child_pid.read_text())
                    process.send_signal(signum)
                    _, errors = process.communicate(timeout=15)
                    self.assertEqual(process.returncode, 128 + signum, errors)
                    self.assert_removed(slug)
                    state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
                    self.assertTrue(state.returncode or state.stdout.lstrip().startswith("Z"))
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.communicate(timeout=15)

    def test_forced_runner_shutdown_retains_unverified_worktree(self) -> None:
        server = self.server()
        command = write_executable(self.fixture.root / "run-tests.sh", "#!/bin/sh\ntrap '' TERM\nprintf 'READY\\n'\nwhile :; do sleep 1; done\n")
        server.deadline = time.monotonic() + 5
        ready = []

        def output(text):
            if "READY" in text:
                ready.append(True)
                server.deadline = time.monotonic() + 0.15

        with mock.patch.object(MODULE, "STOP_GRACE", 0.05), \
             contextlib.redirect_stdout(io.StringIO()) as stream, \
             mock.patch.object(stream, "write", side_effect=output), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(server.run_streamed([str(command)], self.fixture.local, False), 124)
        self.assertEqual(ready, [True])
        self.assertFalse(server.children_stopped)
        server.deadline = None
        sha = self.publish()
        with mock.patch.object(server, "run_streamed", return_value=124), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(server.run(["manual", sha, "1", "cleanup", "test", "fast"]), 124)
        self.assertTrue(self.fixture.peer_worktree("peer", "manual").exists())
        manifest = json.loads((server.state / "runs/manual/result.json").read_text())
        self.assertIn("child shutdown is unverified", manifest["cleanup"])

    def test_client_cleanup_cannot_remove_unverified_children(self) -> None:
        helper = self.fixture.peer_repo("peer") / "assets/peer-env"
        header = "    def run_streamed(self, command: list[str], cwd: Path, scan_errors: bool) -> int:\n"
        helper.write_text(helper.read_text().replace(header, header + "        self.children_stopped = False\n        return EXIT_TIMEOUT\n"))
        result = self.fixture.run("test", "all", "fast")
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn("child shutdown is unverified", result.stderr)
        slug = self.fixture.last_slug("peer")
        self.assertTrue(self.fixture.peer_worktree("peer", slug).exists())
        self.assertEqual(self.fixture.peer_git("peer", "rev-parse", MODULE.REF_NAMESPACE + slug).stdout.strip(), self.fixture.head)
        server = self.server()
        with self.assertRaisesRegex(MODULE.Refused, "child shutdown is unverified"):
            server.remove([slug, self.fixture.head])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(server.clean([]), 1)
        self.assertTrue(self.fixture.peer_worktree("peer", slug).exists())

    def test_diagnostic_limits_symlinks_and_active_retention(self) -> None:
        state = self.fixture.root / "diagnostics"
        worktree = self.fixture.root / "log-source"
        (worktree / ".cz-audit").mkdir(parents=True)
        (worktree / ".cz-audit" / "regular").write_bytes(b"a" * 30)
        (worktree / ".cz-audit" / "symlink").symlink_to(self.fixture.local / "tracked.txt")
        with mock.patch.object(MODULE, "DIAGNOSTIC_LIMIT", 12), mock.patch.object(MODULE, "DIAGNOSTIC_RUNS", 2):
            active = MODULE.Diagnostics(state, "active", self.fixture.head, False)
            self.addCleanup(active.output.close)
            for index in range(3):
                item = MODULE.Diagnostics(state, f"complete-{index}", self.fixture.head, False)
                item.write(b"stream")
                item.archive(worktree, time.monotonic() + 1)
                item.finish(0, "removed")
                self.assertTrue(item.manifest["truncated"])
                self.assertFalse((item.directory / "audit-symlink").exists())
                self.assertEqual(sum(log.stat().st_size for log in item.directory.iterdir() if log.name != "result.json"), 12)
            self.assertTrue(active.directory.exists())
            self.assertEqual(len(list((state / "runs").iterdir())), 3)


@POSIX_ONLY
class AgentPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = PeerFixture(self, {"auth": {}, "offline": {}})
        self.log = self.fixture.root / "relay-calls"
        self.helper = self.fixture.home / "bin/wsl2-pageant.py"
        self.args = SimpleNamespace(no_agent_repair=False)

    def stub(self, *, healthy: bool = False, skipped: bool = False) -> None:
        write_executable(self.helper, f"#!{sys.executable}\n"
                         "import json, pathlib, sys\n"
                         f"with pathlib.Path({str(self.log)!r}).open('a') as handle: handle.write(sys.argv[1] + '\\n')\n"
                         f"payload = {{'socket': '/fixture/pageant.sock', 'healthy': {healthy!r} or sys.argv[1] == '--ensure', 'skipped': {skipped!r}, 'repaired': sys.argv[1] == '--ensure', 'generation': 'fresh', 'identities': 'must-not-leak'}}\n"
                         "print(json.dumps(payload))\n")

    def preflight(self, **updates):
        env = {**self.fixture.env, "WSL_DISTRO_NAME": "fixture", **updates}
        env.pop("SSH_AUTH_SOCK", None)
        if "SSH_AUTH_SOCK" in updates:
            env["SSH_AUTH_SOCK"] = updates["SSH_AUTH_SOCK"]
        with mock.patch.object(MODULE, "current_platform", return_value="wsl2"):
            return MODULE.agent_preflight(self.args, env)

    def test_unhealthy_agent_repaired_once_before_ssh_without_rc(self) -> None:
        self.stub()
        (self.fixture.home / ".zshrc").write_text("exit 99\n")
        env, result = self.preflight()
        self.assertEqual(self.log.read_text().splitlines(), ["--check", "--ensure"])
        self.assertEqual(env["SSH_AUTH_SOCK"], "/fixture/pageant.sock")
        self.assertTrue(result["repaired"])
        self.assertNotIn("identities", result)
        self.assertEqual(self.fixture.ssh_calls(), [])

    def test_healthy_no_repair_and_other_agent_are_not_reset(self) -> None:
        for healthy, skipped, no_repair in ((True, False, False), (False, False, True), (False, True, False)):
            with self.subTest(healthy=healthy, skipped=skipped, no_repair=no_repair):
                self.log.unlink(missing_ok=True)
                self.stub(healthy=healthy, skipped=skipped)
                self.args.no_agent_repair = no_repair
                with contextlib.redirect_stderr(io.StringIO()):
                    env, _ = self.preflight(SSH_AUTH_SOCK="/another/agent" if skipped else "")
                self.assertEqual(self.log.read_text().splitlines(), ["--check"])
                if skipped:
                    self.assertEqual(env["SSH_AUTH_SOCK"], "/another/agent")

    def test_config_and_non_wsl_preflight_do_not_invoke_helper(self) -> None:
        self.stub()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(MODULE.cmd_config(SimpleNamespace(json=True), self.fixture.env), 0)
        with mock.patch.object(MODULE, "current_platform", return_value="macos"):
            MODULE.agent_preflight(self.args, self.fixture.env)
        self.assertFalse(self.log.exists())

    def test_repair_refreshes_only_cached_auth_failures_once(self) -> None:
        config = MODULE.load_config(self.fixture.env)
        peers = MODULE.remote_peers(config)
        def result(peer, status):
            return MODULE.ProbeResult(peer.name, peer.ssh_host, peer.platform, status, status, True, None, 0)
        cached = {peer.name: result(peer, "auth-failed" if peer.name == "auth" else "unreachable") for peer in peers}
        checked = time.time()
        MODULE.save_cache(config, cached, self.fixture.env, checked, {"healthy": False, "generation": "old"})
        agent = {"healthy": True, "generation": "fresh", "repaired": True}
        refreshed = {"auth": result(config.peers["auth"], "reachable")}
        with mock.patch.object(MODULE, "probe_all", return_value=refreshed) as probe:
            values, from_cache, when = MODULE.resolve_probe(config, peers, self.fixture.env, fresh=False, ttl=300, connect_timeout=None, agent=agent)
            self.assertFalse(from_cache)
            self.assertEqual(when, checked)
            self.assertEqual(values["offline"].status, "unreachable")
            self.assertEqual([peer.name for peer in probe.call_args.args[0]], ["auth"])
            agent["repaired"] = False
            MODULE.resolve_probe(config, peers, self.fixture.env, fresh=False, ttl=300, connect_timeout=None, agent=agent)
            self.assertEqual(probe.call_count, 1)


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        context = isolated_environment(prefix="peer-env-config-")
        self.iso = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.env = dict(self.iso.env)
        self.config_path = self.iso.home / ".config" / "dotfiles" / "peer-envs.json"

    def load(self, payload: object):
        write_json(self.config_path, payload)
        return MODULE.load_config(self.env)

    def test_missing_config_names_path_and_doc(self) -> None:
        with self.assertRaises(MODULE.ConfigError) as caught:
            MODULE.load_config(self.env)
        self.assertIn(str(self.config_path), str(caught.exception))
        self.assertIn("docs/tooling/peer-environments.md", str(caught.exception))

    def test_windows_and_via_are_rejected(self) -> None:
        for peers in (
            {"win": {"platform": "windows", "repo": "~/dotfiles"}},
            {"win": {"platform": "wsl2", "repo": "~/dotfiles", "via": "wsl2"}},
        ):
            with self.subTest(peers=peers), self.assertRaises(MODULE.ConfigError) as caught:
                self.load(peer_config(**peers))
            self.assertIn(MODULE.WINDOWS_VIA_MESSAGE, str(caught.exception))

    def test_validation_errors(self) -> None:
        cases = {
            "platform": {"p": {"platform": "beos", "repo": "~/x"}},
            "repo must start": {"p": {"platform": "wsl2", "repo": "relative/x"}},
            "unknown key": {"p": {"platform": "wsl2", "repo": "~/x", "colour": "blue"}},
            "whitespace": {"p": {"platform": "wsl2", "repo": "~/x", "ssh_host": "bad host"}},
            "connect_timeout": {"p": {"platform": "wsl2", "repo": "~/x", "connect_timeout": 0}},
            "not be 'all'": {"all": {"platform": "wsl2", "repo": "~/x"}},
            "schema_version": {"schema_version": 2, "peers": {"p": {"platform": "wsl2", "repo": "~/x"}}},
        }
        for fragment, payload in cases.items():
            if "schema_version" not in payload:
                payload = peer_config(**payload)
            with self.subTest(fragment=fragment), self.assertRaises(MODULE.ConfigError) as caught:
                self.load(payload)
            self.assertIn(fragment, str(caught.exception))

    def test_self_platform_is_dropped_and_ssh_host_defaults_to_name(self) -> None:
        other = OTHER_PLATFORMS[0]
        config = self.load(
            peer_config(
                me={"platform": SELF_PLATFORM, "repo": "~/dotfiles"},
                Remote={"platform": other, "repo": "/srv/dotfiles/"},
            )
        )
        self.assertEqual([peer.name for peer in MODULE.remote_peers(config)], ["Remote"])
        self.assertEqual(config.peers["Remote"].ssh_host, "Remote")
        self.assertEqual(config.peers["Remote"].repo, "/srv/dotfiles")
        self.assertEqual([peer.name for peer in MODULE.select_peers(config, "all")], ["Remote"])
        with self.assertRaises(MODULE.ConfigError) as caught:
            MODULE.select_peers(config, "me")
        self.assertIn("is this machine", str(caught.exception))
        with self.assertRaises(MODULE.ConfigError):
            MODULE.select_peers(config, "nope")


class ClassifyTests(unittest.TestCase):
    peer = MODULE.Peer("wsl2", "WSL2Debian", "wsl2", "~/dotfiles", None, 3, False)

    def classify(self, result, timed_out: bool = False):
        return MODULE.classify(self.peer, result, timed_out, 0.5, 3)

    def test_transport_failures(self) -> None:
        self.assertEqual(self.classify(None, True).status, "unreachable")
        self.assertIn("timeout after 3s", self.classify(None, True).detail)
        refused = self.classify(completed(255, "", "ssh: connect to host 192.0.2.1 port 22: Connection refused\n"))
        self.assertEqual(refused.status, "unreachable")
        self.assertIn("Connection refused", refused.detail)
        auth = self.classify(completed(255, "", "balaji@192.0.2.1: Permission denied (publickey).\n"))
        self.assertEqual(auth.status, "auth-failed")
        self.assertIn("IdentityFile ~/.ssh/<key>.pub", auth.detail)

    def test_identity_outcomes(self) -> None:
        self.assertEqual(self.classify(completed(0, "Welcome\n", "")).status, "serve-missing")
        self.assertEqual(self.classify(completed(127, "", "sh: peer-env: not found\n")).status, "serve-missing")
        banner = "motd line\n" + identity_line() + "\n"
        ok = self.classify(completed(0, banner, ""))
        self.assertEqual(ok.status, "reachable")
        self.assertTrue(ok.restricted)
        self.assertIn("git chezmoi python3 pwsh", ok.detail)
        loose = self.classify(completed(0, identity_line(restricted=False), ""))
        self.assertEqual((loose.status, loose.restricted), ("reachable", False))
        self.assertIn("unrestricted key", loose.detail)
        mismatch = self.classify(completed(0, identity_line(uname="Darwin", kernel="24.6.0", platform="macos"), ""))
        self.assertEqual(mismatch.status, "platform-mismatch")
        tools = self.classify(completed(0, identity_line(tools={"git": False, "python3": True}), ""))
        self.assertEqual(tools.status, "tools-missing")
        self.assertIn("git", tools.detail)
        protocol = self.classify(completed(0, identity_line(protocol="peer-env/2"), ""))
        self.assertEqual(protocol.status, "protocol-mismatch")

    def test_platform_from_identity(self) -> None:
        self.assertEqual(MODULE.platform_from_identity("Darwin", "24.6.0"), "macos")
        self.assertEqual(MODULE.platform_from_identity("Linux", "6.6.0-microsoft-standard-WSL2"), "wsl2")
        self.assertEqual(MODULE.platform_from_identity("Linux", "6.1.0-generic"), "linux")
        self.assertEqual(MODULE.platform_from_identity("FreeBSD", "14"), "unknown")

    def test_path_and_secret_validation(self) -> None:
        for bad in ("../x", "a/../b", "/abs", "-flag", "", "a//b", "win\\path"):
            with self.subTest(path=bad), self.assertRaises(MODULE.ConfigError):
                MODULE.validate_relative_path(bad)
        self.assertEqual(MODULE.validate_relative_path("assets/peer-env"), "assets/peer-env")
        flagged = MODULE.secret_matches(["notes.txt", "deploy/.env", "secrets.env", "keys/id_ed25519", "x.tfstate"])
        self.assertEqual(flagged, ["deploy/.env", "secrets.env", "keys/id_ed25519", "x.tfstate"])

    @unittest.skipIf(os.name == "nt", "control sockets are a POSIX feature")
    def test_control_socket_path_respects_the_socket_limit_and_directory_safety(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pe-", dir="/tmp") as temp:
            base = Path(temp)
            short = base / "s"
            self.assertIsNotNone(MODULE.control_path(short))
            self.assertTrue(short.is_dir())
            self.assertEqual(short.stat().st_mode & 0o777, 0o700)

            budget = MODULE.MAX_SOCKET_PATH - MODULE.CONTROL_TEMP_SUFFIX - len("/cm-") - 40
            longest_ok = base / ("l" * (budget - len(str(base)) - 1))
            self.assertIsNotNone(MODULE.control_path(longest_ok), longest_ok)
            one_over = base / ("l" * (budget - len(str(base))))
            self.assertIsNone(MODULE.control_path(one_over))

            shared = base / "shared"
            shared.mkdir(mode=0o755)
            os.chmod(shared, 0o755)
            self.assertIsNone(MODULE.control_path(shared))

        far_away = Path("/tmp") / ("x" * 90)
        self.assertIsNone(MODULE.control_path(far_away))
        self.assertFalse(far_away.exists())

    @unittest.skipIf(os.name == "nt", "control sockets are a POSIX feature")
    def test_ssh_argv_contract(self) -> None:
        env = {"HOME": "/tmp/h", "PATH": "/usr/bin", "XDG_STATE_HOME": "/tmp/h/.local/state"}
        peer = MODULE.Peer("macos", "M4MacBook", "macos", "~/Documents/development/dotfiles", "~/.ssh/key.pub", 3, False)
        argv = MODULE.ssh_argv(peer, [MODULE.PROTOCOL, "identity"], env)
        joined = " ".join(argv)
        for option in (
            "BatchMode=yes",
            "ConnectTimeout=3",
            "ConnectionAttempts=1",
            "StrictHostKeyChecking=yes",
            "LogLevel=ERROR",
            "ForwardAgent=no",
            "ClearAllForwardings=yes",
            "IdentitiesOnly=yes",
            "ControlMaster=auto",
        ):
            self.assertIn(option, joined)
        self.assertIn("/tmp/h/.ssh/key.pub", argv)
        controls = [value for value in argv if value.startswith("ControlPath=")]
        self.assertEqual(len(controls), 1, f"/tmp/peer-env-{os.getuid()} must be a private directory owned by this user")
        self.assertTrue(controls[0].startswith(f"ControlPath=/tmp/peer-env-{os.getuid()}/cm-"), controls[0])
        self.assertEqual(argv[-2], "M4MacBook")
        self.assertEqual(argv[-1], '"$HOME"/Documents/development/dotfiles/assets/peer-env serve peer-env/1 identity')
        self.assertEqual(MODULE.push_url(peer), "M4MacBook:Documents/development/dotfiles")
        self.assertEqual(argv[1], "-T")


@POSIX_ONLY
class ProbeTests(unittest.TestCase):
    def test_statuses_cache_and_exit_codes(self) -> None:
        fixture = PeerFixture(
            self,
            {
                "alpha": {"mode": "restricted", "platform": OTHER_PLATFORMS[0]},
                "beta": {"mode": "fail", "platform": OTHER_PLATFORMS[1], "stderr": "ssh: connect to host beta port 22: Operation timed out\n"},
                "me": {"mode": "fail", "platform": SELF_PLATFORM},
            },
        )
        result = fixture.run("probe", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["self_platform"], SELF_PLATFORM)
        self.assertEqual(sorted(payload["peers"]), ["alpha", "beta"])
        self.assertEqual(payload["peers"]["alpha"]["status"], "reachable")
        self.assertTrue(payload["peers"]["alpha"]["restricted"])
        self.assertEqual(payload["peers"]["alpha"]["platform"], OTHER_PLATFORMS[0])
        self.assertEqual(payload["peers"]["beta"]["status"], "unreachable")
        self.assertIn("Operation timed out", payload["peers"]["beta"]["detail"])
        self.assertFalse(payload["from_cache"])
        calls = fixture.ssh_calls()
        self.assertEqual(len(calls), 2)
        for call in calls:
            argv = " ".join(call["argv"])
            for option in ("BatchMode=yes", "ConnectTimeout=3", "ConnectionAttempts=1", "StrictHostKeyChecking=yes", "ForwardAgent=no"):
                self.assertIn(option, argv)
            self.assertTrue(call["command"].endswith(" serve peer-env/1 identity"), call["command"])

        human = fixture.run("probe")
        self.assertEqual(human.returncode, 0)
        self.assertIn(f"self     {SELF_PLATFORM}", human.stdout)
        self.assertNotIn("me ", human.stdout)
        self.assertIn("1 reachable, 1 not (cached)", human.stdout)
        self.assertEqual(len(fixture.ssh_calls()), 2, "cache hit must not touch ssh")

        fresh = fixture.run("probe", "--fresh", "--json")
        self.assertTrue(len(fixture.ssh_calls()) > 2)
        self.assertFalse(json.loads(fresh.stdout)["from_cache"])
        before = len(fixture.ssh_calls())
        fixture.run("probe", "--ttl", "0")
        self.assertGreater(len(fixture.ssh_calls()), before)

        self.assertEqual(fixture.run("audit", "me", "tracked.txt").returncode, 2)

    def test_all_unreachable_exits_3(self) -> None:
        fixture = PeerFixture(
            self,
            {
                "alpha": {"mode": "fail", "platform": OTHER_PLATFORMS[0], "stderr": "Connection refused\n"},
                "beta": {"mode": "fail", "platform": OTHER_PLATFORMS[1], "stderr": "Operation timed out\n"},
            },
        )
        result = fixture.run("probe", "--json", "--connect-timeout", "1")
        self.assertEqual(result.returncode, 3, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["peers"]["alpha"]["status"], "unreachable")
        self.assertEqual(payload["peers"]["beta"]["status"], "unreachable")
        self.assertEqual(payload["peers"]["beta"]["detail"], "Operation timed out")
        calls = fixture.ssh_calls()
        self.assertEqual(sorted(call["host"] for call in calls), ["alpha-host", "beta-host"])
        for call in calls:
            self.assertIn("ConnectTimeout=1", call["argv"])

    def test_hung_probe_is_killed_at_connect_timeout_plus_margin(self) -> None:
        fixture = PeerFixture(
            self,
            {
                "alpha": {"mode": "fail", "platform": OTHER_PLATFORMS[0], "stderr": "Connection refused\n"},
                "beta": {"mode": "hang", "platform": OTHER_PLATFORMS[1], "seconds": 20},
            },
        )
        peers = list(MODULE.load_config(fixture.env).peers.values())
        with mock.patch.object(MODULE, "PROBE_MARGIN", 1):
            started = time.monotonic()
            results = MODULE.probe_all(peers, fixture.env, 1)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 8)
        self.assertEqual(results["alpha"].status, "unreachable")
        self.assertEqual(results["beta"].status, "unreachable")
        self.assertEqual(results["beta"].detail, "timeout after 1s")
        self.assertGreaterEqual(results["beta"].elapsed, 2)

    def test_identity_file_is_passed_through(self) -> None:
        fixture = PeerFixture(
            self,
            {"alpha": {"mode": "restricted", "platform": OTHER_PLATFORMS[0], "identity_file": "~/.ssh/peer.pub"}},
        )
        self.assertEqual(fixture.run("probe").returncode, 0)
        argv = fixture.ssh_calls()[0]["argv"]
        self.assertIn("IdentitiesOnly=yes", argv)
        self.assertIn(str(fixture.home / ".ssh" / "peer.pub"), argv)


@POSIX_ONLY
class RunTests(unittest.TestCase):
    def fixture(self, **extra: dict) -> PeerFixture:
        peers = {"alpha": {"mode": "restricted", "platform": OTHER_PLATFORMS[0]}}
        peers.update(extra)
        return PeerFixture(self, peers)

    def test_audit_ships_snapshot_without_touching_local_state(self) -> None:
        fixture = self.fixture()
        local = fixture.local
        (local / "tracked.txt").write_text("stashed\n", encoding="utf-8")
        run_git(local, "stash", "push", "-q", "-m", "seed", env=fixture.env)
        (local / "tracked.txt").write_text("v2\n", encoding="utf-8")
        (local / "untracked.txt").write_text("new\n", encoding="utf-8")
        (local / "ignored.txt").write_text("ignored\n", encoding="utf-8")
        (local / "gone.txt").unlink()
        before = {
            "head": fixture.local_git("rev-parse", "HEAD"),
            "status": fixture.local_git("status", "--porcelain"),
            "stash": fixture.local_git("stash", "list"),
            "index": fixture.local_git("diff", "--cached", "--stat"),
        }
        result = fixture.run("audit", "alpha", "tracked.txt", "untracked.txt", "--keep-worktree")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alpha: ok", result.stderr)
        self.assertIn("INFO: stub audit ok check tracked.txt", result.stdout)
        after = {
            "head": fixture.local_git("rev-parse", "HEAD"),
            "status": fixture.local_git("status", "--porcelain"),
            "stash": fixture.local_git("stash", "list"),
            "index": fixture.local_git("diff", "--cached", "--stat"),
        }
        self.assertEqual(before, after)
        worktree = fixture.peer_worktree("alpha")
        self.assertEqual((worktree / "tracked.txt").read_text(encoding="utf-8"), "v2\n")
        self.assertEqual((worktree / "untracked.txt").read_text(encoding="utf-8"), "new\n")
        self.assertFalse((worktree / "ignored.txt").exists())
        self.assertFalse((worktree / "gone.txt").exists())
        ref = fixture.peer_git("alpha", "rev-parse", "refs/peer-env/" + fixture.last_slug("alpha")).stdout.strip()
        self.assertNotEqual(ref, fixture.head)
        author = fixture.peer_git("alpha", "log", "-1", "--format=%an <%ae>", ref).stdout.strip()
        self.assertEqual(author, "peer-env <peer-env@localhost>")
        self.assertEqual(fixture.peer_git("alpha", "rev-parse", "main").stdout.strip(), fixture.head)
        self.assertEqual(fixture.peer_git("alpha", "rev-parse", "HEAD").stdout.strip(), fixture.head)
        record = fixture.audit_record("alpha")
        self.assertIn("argv=check tracked.txt", record)
        self.assertIn("argv=check untracked.txt", record)
        self.assertIn(f"cwd={worktree.resolve()}", record)
        self.assertIn("PEER_ENV=1", record)
        self.assertNotIn("FAKE_PROVIDER_KEY", record)
        self.assertNotIn("SSH_ORIGINAL_COMMAND", record)
        self.assertIn("HOME=" + str(fixture.peer_homes["alpha"]), record)
        verbs = [entry["verb"] for entry in read_json_lines(fixture.peer_state("alpha") / "serve.log")]
        self.assertEqual(verbs, ["identity", "receive-pack", "run"])

    def test_unchanged_tree_publishes_head_and_runs_have_unique_worktrees(self) -> None:
        fixture = self.fixture()
        first = fixture.run("audit", "alpha", "tracked.txt", "--keep-worktree")
        self.assertEqual(first.returncode, 0, first.stderr)
        ref = fixture.peer_git("alpha", "rev-parse", "refs/peer-env/" + fixture.last_slug("alpha")).stdout.strip()
        self.assertEqual(ref, fixture.head)
        worktree = fixture.peer_worktree("alpha")
        (worktree / "stray.txt").write_text("stray\n", encoding="utf-8")
        (worktree / ".cz-audit" / "keep.log").write_text("keep\n", encoding="utf-8")
        (fixture.local / "tracked.txt").write_text("v3\n", encoding="utf-8")
        second = fixture.run("audit", "alpha", "tracked.txt", "--keep-worktree")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(worktree, fixture.peer_worktree("alpha"))
        self.assertEqual((fixture.peer_worktree("alpha") / "tracked.txt").read_text(encoding="utf-8"), "v3\n")
        self.assertTrue((worktree / "stray.txt").exists())
        self.assertTrue((worktree / ".cz-audit" / "keep.log").exists())
        worktrees = fixture.peer_git("alpha", "worktree", "list", "--porcelain").stdout
        self.assertEqual(worktrees.count("worktree "), 3)

    def test_secret_looking_untracked_files_are_refused(self) -> None:
        fixture = self.fixture()
        (fixture.local / "secrets.env").write_text("TOKEN=1\n", encoding="utf-8")
        refused = fixture.run("audit", "alpha", "tracked.txt")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("secrets.env", refused.stderr)
        self.assertIn("--tracked-only", refused.stderr)
        self.assertEqual(fixture.ssh_calls()[-1]["command"].split()[-1], "identity")
        allowed = fixture.run("audit", "alpha", "tracked.txt", "--tracked-only")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertFalse((fixture.peer_worktree("alpha") / "secrets.env").exists())

    def test_remote_failures_propagate(self) -> None:
        fixture = self.fixture()
        (fixture.local / "stub-mode.txt").write_text("exit7\n", encoding="utf-8")
        self.assertEqual(fixture.run("audit", "alpha", "tracked.txt").returncode, 7)
        (fixture.local / "stub-mode.txt").write_text("error\n", encoding="utf-8")
        errored = fixture.run("audit", "alpha", "tracked.txt")
        self.assertEqual(errored.returncode, 1)
        self.assertIn("ERROR: stub failure for tracked.txt", errored.stdout)
        self.assertEqual(fixture.run("audit", "alpha", "../escape").returncode, 2)
        self.assertEqual(fixture.run("audit", "alpha", "missing.txt").returncode, 2)

    def test_test_command_and_rm(self) -> None:
        fixture = self.fixture()
        result = fixture.run("test", "alpha", "fast", "--require-capabilities", "--rm")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stub tests ran: fast --require-capabilities", result.stdout)
        self.assertFalse(fixture.peer_worktree("alpha").exists())
        self.assertNotEqual(fixture.peer_git("alpha", "rev-parse", "--verify", "refs/peer-env/main", check=False).returncode, 0)
        self.assertEqual(fixture.run("test", "alpha", "bogus").returncode, 2)
        (fixture.local / "stub-mode.txt").write_text("exit7\n", encoding="utf-8")
        self.assertEqual(fixture.run("test", "alpha").returncode, 7)

    def test_all_skips_unreachable_and_reports_unverified(self) -> None:
        fixture = self.fixture(
            beta={"mode": "fail", "platform": OTHER_PLATFORMS[1], "stderr": "Connection refused\n"},
        )
        result = fixture.run("audit", "all", "tracked.txt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'not verified on {OTHER_PLATFORMS[1]}: peer unreachable', result.stderr)
        self.assertIn("alpha: ok", result.stderr)
        fixture.ssh_state["alpha-host"]["mode"] = "fail"
        write_json(fixture.state_path, fixture.ssh_state)
        nothing = fixture.run("audit", "all", "tracked.txt", "--fresh")
        self.assertEqual(nothing.returncode, 3)
        self.assertEqual(fixture.run("audit", "alpha", "tracked.txt", "--fresh").returncode, 3)

    def test_unrestricted_peer_requires_opt_in(self) -> None:
        fixture = PeerFixture(
            self,
            {
                "loose": {"mode": "unrestricted", "platform": OTHER_PLATFORMS[0]},
                "allowed": {"mode": "unrestricted", "platform": OTHER_PLATFORMS[0], "allow_unrestricted": True},
            },
        )
        refused = fixture.run("audit", "loose", "tracked.txt")
        self.assertEqual(refused.returncode, 3, refused.stderr)
        self.assertIn(
            f'report "not verified on {OTHER_PLATFORMS[0]}: peer unreachable (unrestricted key)"',
            refused.stderr,
        )
        self.assertIn("only the owner sets allow_unrestricted", refused.stderr)
        self.assertEqual(fixture.audit_record("loose"), "")
        allowed = fixture.run("audit", "allowed", "tracked.txt")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("argv=check tracked.txt", fixture.audit_record("allowed"))
        probe_result = fixture.run("probe", "--json")
        probe = json.loads(probe_result.stdout)
        self.assertFalse(probe["peers"]["loose"]["restricted"])
        self.assertEqual(probe_result.returncode, 0, "the allowed peer keeps probe usable")
        fixture.ssh_state["allowed-host"]["mode"] = "fail"
        write_json(fixture.state_path, fixture.ssh_state)
        only_loose = fixture.run("probe", "--fresh")
        self.assertEqual(only_loose.returncode, 3, only_loose.stdout)
        self.assertIn("0 reachable, 2 not", only_loose.stdout)
        self.assertIn("reachable (unrestricted)", only_loose.stdout)

    def test_clean_removes_refs_worktrees_and_cache(self) -> None:
        fixture = self.fixture()
        self.assertEqual(fixture.run("audit", "alpha", "tracked.txt", "--keep-worktree").returncode, 0)
        cache = fixture.home / ".local" / "state" / "dotfiles" / "peer-env" / "probe.json"
        self.assertTrue(cache.exists())
        result = fixture.run("clean", "alpha")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(fixture.peer_worktree("alpha").exists())
        self.assertEqual(fixture.peer_git("alpha", "for-each-ref", "refs/peer-env/").stdout, "")
        self.assertFalse(cache.exists())


@POSIX_ONLY
class ServeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = PeerFixture(self, {"alpha": {"mode": "restricted", "platform": OTHER_PLATFORMS[0]}})
        self.helper = self.fixture.peer_repo("alpha") / "assets" / "peer-env"

    def peer_env(self) -> dict[str, str]:
        env = {
            name: value
            for name, value in self.fixture.env.items()
            if not name.startswith("XDG_")
        }
        env["HOME"] = str(self.fixture.peer_homes["alpha"])
        return env

    def serve(self, original: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
        env = self.peer_env()
        env["SSH_ORIGINAL_COMMAND"] = original
        env.update(env_overrides)
        return subprocess.run(
            [str(self.helper), "serve"],
            cwd=self.fixture.peer_homes["alpha"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def test_refuses_everything_but_valid_requests(self) -> None:
        cases = {
            "bash -l": "only peer-env serve requests",
            "x serve peer-env/2 identity": "protocol must be peer-env/1",
            "x serve peer-env/1 shell": "unknown verb",
            "x serve peer-env/1 prepare 'bad slug' 0123456789012345678901234567890123456789": "invalid slug",
            "x serve peer-env/1 prepare main notasha": "invalid sha",
            "x serve peer-env/1 prepare main 0123456789012345678901234567890123456789": "publish it first",
            "x serve peer-env/1 exec main audit ../x": "not prepared",
            "x serve peer-env/1 exec ../main audit x": "invalid slug",
            "x serve peer-env/1 identity extra": "takes no arguments",
        }
        for original, fragment in cases.items():
            with self.subTest(command=original):
                result = self.serve(original)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("peer-env serve: refused", result.stderr)
                self.assertIn(fragment, result.stderr)

    def test_identity_reports_restricted_and_platform(self) -> None:
        result = self.serve("x serve peer-env/1 identity", PATH=f"{self.fixture.ssh_state['alpha-host']['bin']}{os.pathsep}{self.fixture.env['PATH']}")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout.strip().split(MODULE.IDENTITY_MARKER, 1)[1])
        self.assertTrue(payload["restricted"])
        self.assertEqual(payload["platform"], OTHER_PLATFORMS[0])
        self.assertTrue(payload["tools"]["git"])
        direct = subprocess.run(
            [str(self.helper), "serve", "peer-env/1", "identity"],
            cwd=self.fixture.peer_homes["alpha"],
            env=self.peer_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertFalse(json.loads(direct.stdout.split(MODULE.IDENTITY_MARKER, 1)[1])["restricted"])

    def test_pre_receive_confines_pushes_to_the_namespace(self) -> None:
        fixture = self.fixture
        env = dict(fixture.env)
        env["GIT_SSH_COMMAND"] = str(fixture.iso.fake_bin / "ssh")
        receive_pack = f'"$HOME"/{REPO_REL}/assets/peer-env serve peer-env/1 receive-pack'
        (fixture.local / "tracked.txt").write_text("pushed\n", encoding="utf-8")
        run_git(fixture.local, "commit", "-q", "-am", "local change", env=env)
        new_head = fixture.local_git("rev-parse", "HEAD").strip()
        for refspec in ("+HEAD:refs/heads/main", "+HEAD:refs/heads/injected", "+HEAD:refs/peer-env/nested/slug", "+HEAD:refs/tags/v1"):
            with self.subTest(refspec=refspec):
                result = run_git(
                    fixture.local,
                    "push",
                    f"--receive-pack={receive_pack}",
                    f"alpha-host:{REPO_REL}",
                    refspec,
                    env=env,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("outside refs/peer-env/", result.stderr)
        ok = run_git(
            fixture.local,
            "push",
            "--quiet",
            f"--receive-pack={receive_pack}",
            f"alpha-host:{REPO_REL}",
            "+HEAD:refs/peer-env/ok-slug",
            env=env,
        )
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(fixture.peer_git("alpha", "rev-parse", "refs/peer-env/ok-slug").stdout.strip(), new_head)
        self.assertEqual(fixture.peer_git("alpha", "rev-parse", "main").stdout.strip(), fixture.head)

    def test_busy_lock_exits_75(self) -> None:
        import fcntl

        self.assertEqual(self.fixture.run("audit", "alpha", "tracked.txt", "--keep-worktree").returncode, 0)
        slug = self.fixture.last_slug("alpha")
        lock_path = self.fixture.peer_state("alpha") / ("lock-" + slug)
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            result = self.serve(f"x serve peer-env/1 prepare {slug} {self.fixture.head}")
            self.assertEqual(result.returncode, 75, result.stderr)
            self.assertIn("busy", result.stderr)
            result = self.serve("x serve peer-env/1 clean")
            self.assertEqual(result.returncode, 75, result.stderr)
            self.assertTrue(self.fixture.peer_worktree("alpha", slug).exists())
            independent = self.fixture.run("audit", "alpha", "tracked.txt")
            self.assertEqual(independent.returncode, 0, independent.stderr)


class HeaderTests(unittest.TestCase):
    def test_sh_dispatch_header(self) -> None:
        lines = SOURCE_HELPER.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertTrue(lines[1].startswith('":" \'\'\''))
        self.assertIn("resolve-python3", "\n".join(lines[:20]))
        self.assertEqual(lines[19], "'''")
        self.assertTrue(lines[21].startswith("from __future__ import annotations"))


if __name__ == "__main__":
    unittest.main()
