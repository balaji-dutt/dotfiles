from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

from tests.support.fixtures import (
    init_git_repository,
    isolated_environment,
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
    name = "peer_env_test_module"
    loader = SourceFileLoader(name, str(SOURCE_HELPER))
    spec = spec_from_loader(name, loader)
    if spec is None:
        raise RuntimeError("could not create helper module spec")
    module = module_from_spec(spec)
    sys.modules[name] = module
    sys.path.insert(0, str(SOURCE_HELPER.parent))
    try:
        loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


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

    def peer_worktree(self, name: str, slug: str = "main") -> Path:
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

    def audit_record(self, name: str, slug: str = "main") -> str:
        path = self.peer_worktree(name, slug) / ".cz-audit" / "audit-record.txt"
        return path.read_text(encoding="utf-8") if path.exists() else ""


def completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["ssh"], returncode, stdout, stderr)


def identity_line(**overrides) -> str:
    payload = {
        "protocol": MODULE.PROTOCOL,
        "uname": "Linux",
        "kernel": "5.15.0-microsoft-standard-WSL2",
        "platform": "wsl2",
        "restricted": True,
        "tools": {"git": True, "chezmoi": True, "python3": True, "python": False, "pwsh": True},
    }
    payload.update(overrides)
    return MODULE.IDENTITY_MARKER + json.dumps(payload)


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

    def test_all_unreachable_exits_3_and_hang_is_bounded(self) -> None:
        fixture = PeerFixture(
            self,
            {
                "alpha": {"mode": "fail", "platform": OTHER_PLATFORMS[0], "stderr": "Connection refused\n"},
                "beta": {"mode": "hang", "platform": OTHER_PLATFORMS[1], "seconds": 20},
            },
        )
        started = time.monotonic()
        result = fixture.run("probe", "--json", "--connect-timeout", "1")
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertLess(elapsed, 15)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["peers"]["beta"]["status"], "unreachable")
        self.assertIn("timeout after 1s", payload["peers"]["beta"]["detail"])
        self.assertEqual(payload["peers"]["alpha"]["status"], "unreachable")

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
        result = fixture.run("audit", "alpha", "tracked.txt", "untracked.txt")
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
        ref = fixture.peer_git("alpha", "rev-parse", "refs/peer-env/main").stdout.strip()
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
        self.assertEqual(verbs, ["identity", "receive-pack", "prepare", "exec"])

    def test_unchanged_tree_publishes_head_and_rerun_reuses_worktree(self) -> None:
        fixture = self.fixture()
        first = fixture.run("audit", "alpha", "tracked.txt")
        self.assertEqual(first.returncode, 0, first.stderr)
        ref = fixture.peer_git("alpha", "rev-parse", "refs/peer-env/main").stdout.strip()
        self.assertEqual(ref, fixture.head)
        worktree = fixture.peer_worktree("alpha")
        (worktree / "stray.txt").write_text("stray\n", encoding="utf-8")
        (worktree / ".cz-audit" / "keep.log").write_text("keep\n", encoding="utf-8")
        (fixture.local / "tracked.txt").write_text("v3\n", encoding="utf-8")
        second = fixture.run("audit", "alpha", "tracked.txt")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((worktree / "tracked.txt").read_text(encoding="utf-8"), "v3\n")
        self.assertFalse((worktree / "stray.txt").exists())
        self.assertTrue((worktree / ".cz-audit" / "keep.log").exists())
        worktrees = fixture.peer_git("alpha", "worktree", "list", "--porcelain").stdout
        self.assertEqual(worktrees.count("worktree "), 2)

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
        self.assertEqual(fixture.run("audit", "alpha", "tracked.txt").returncode, 0)
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

        self.assertEqual(self.fixture.run("audit", "alpha", "tracked.txt").returncode, 0)
        lock_path = self.fixture.peer_state("alpha") / "lock-main"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            result = self.serve(f"x serve peer-env/1 prepare main {self.fixture.head}")
            self.assertEqual(result.returncode, 75, result.stderr)
            self.assertIn("busy", result.stderr)
            via_client = self.fixture.run("audit", "alpha", "tracked.txt")
            self.assertEqual(via_client.returncode, 75, via_client.stderr)
            self.assertIn("busy", via_client.stderr)


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
