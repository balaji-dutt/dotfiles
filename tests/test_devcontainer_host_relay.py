from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from tests.support.fixtures import isolated_environment, write_executable
from tests.test_chezmoi_lifecycle_render import CHEZMOI, render_template


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    ROOT
    / "private_Documents/development/container-dotfiles/devcontainers"
    / "gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer"
)
RELAY_PATH = RUNTIME_DIR / "devcontainer_host_relay.py"
TEMPLATE = (RUNTIME_DIR / "devcontainer.json.tmpl").relative_to(ROOT).as_posix()
CONTAINER_BIN = ROOT / "private_Documents/development/container-dotfiles/dotfiles/dot_local/bin"
CLIENT_PATH = CONTAINER_BIN / "executable_devcontainer-host-relay"
OPEN_URL_PATH = CONTAINER_BIN / "executable_devcontainer-open-url"
SHIM_PATH = CONTAINER_BIN / "executable_devcontainer-clipboard-osc52"
POST_START = RUNTIME_DIR / "postStart.sh"
BASH = shutil.which("bash")
RANGES = "9993-9998,9999,10004-10009,10014-10019"
LOCAL_CALLBACK = "http%3A%2F%2Flocalhost%3A51234%2Fcallback"
MANUAL_CALLBACK = "https://platform.claude.com/oauth/code/callback"


if os.name == "nt":
    raise unittest.SkipTest("the host relay needs fcntl and AF_UNIX sockets")


def load_relay():
    spec = importlib.util.spec_from_file_location("devcontainer_host_relay", RELAY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load relay module from {RELAY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


relay = load_relay()


class UrlPolicyTests(unittest.TestCase):
    ranges = relay.parse_ranges(RANGES)

    def assert_rejected(self, url: str) -> None:
        with self.assertRaises(relay.RelayError, msg=url):
            relay.checked_url(url, self.ranges)

    def test_plannotator_urls_inside_the_ranges_pass_unchanged(self) -> None:
        for url in (
            "http://localhost:9993/",
            "http://localhost:9999/",
            "http://127.0.0.1:10019/review?x=1#top",
        ):
            self.assertEqual(relay.checked_url(url, self.ranges), url)

    def test_other_urls_are_rejected(self) -> None:
        for url in (
            "http://localhost:8999/",
            "http://localhost:10010/",
            "http://localhost/",
            "https://localhost:10004/",
            "http://example.com:10004/",
            "http://user@localhost:10004/",
            "http://localhost:10004/a b",
            "http://localhost:10004/$(id)",
            "http://localhost:10004/é",
            "http://localhost:10004?x=$(id)",
            "http://localhost:10004#'quoted'",
            "file:///etc/passwd",
            "https://claude.com/other",
            "https://claude.com.evil/cai/oauth/authorize?redirect_uri=" + LOCAL_CALLBACK,
            "https://claude.com:8443/cai/oauth/authorize?redirect_uri=" + LOCAL_CALLBACK,
            "http://claude.com/cai/oauth/authorize?redirect_uri=" + LOCAL_CALLBACK,
        ):
            self.assert_rejected(url)

    def test_claude_authorize_url_is_rewritten_to_the_manual_redirect(self) -> None:
        for base in ("https://claude.com/cai/oauth/authorize", "https://claude.ai/oauth/authorize"):
            url = (
                f"{base}?code=true&client_id=abc&response_type=code&redirect_uri={LOCAL_CALLBACK}"
                "&scope=user%3Aprofile+user%3Ainference&code_challenge=xyz&state=s1"
            )
            opened = urlsplit(relay.checked_url(url, self.ranges))
            self.assertEqual(f"{opened.scheme}://{opened.netloc}{opened.path}", base)
            self.assertEqual(
                parse_qs(opened.query),
                {
                    "code": ["true"],
                    "client_id": ["abc"],
                    "response_type": ["code"],
                    "redirect_uri": [MANUAL_CALLBACK],
                    "scope": ["user:profile user:inference"],
                    "code_challenge": ["xyz"],
                    "state": ["s1"],
                },
            )

    def test_claude_authorize_url_requires_a_known_redirect(self) -> None:
        base = "https://claude.com/cai/oauth/authorize?code=true"
        self.assert_rejected(base)
        self.assert_rejected(base + "&redirect_uri=https%3A%2F%2Fevil.example%2Fcallback")
        self.assert_rejected(base + f"&redirect_uri={LOCAL_CALLBACK}&redirect_uri={LOCAL_CALLBACK}")
        manual = base + "&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback"
        self.assertIn("redirect_uri=", relay.checked_url(manual, self.ranges))

    def test_port_ranges_are_validated(self) -> None:
        self.assertEqual(relay.parse_ranges("9999,10004-10009"), [(9999, 9999), (10004, 10009)])
        for spec in ("", "abc", "10009-10004", "0", "70000", "1-2-3"):
            with self.assertRaises(ValueError, msg=spec):
                relay.parse_ranges(spec)


@unittest.skipUnless(os.name != "nt" and BASH, "POSIX bash is required")
class ClipboardTests(unittest.TestCase):
    def setUp(self) -> None:
        context = isolated_environment(prefix="host-relay-clipboard-")
        self.fixture = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        patcher = mock.patch.dict(os.environ, {"PATH": str(self.fixture.fake_bin)}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake(self, name: str, body: str) -> None:
        write_executable(self.fixture.fake_bin / name, f"#!{BASH}\n{body}\n")

    def test_wsl_png_falls_back_to_powershell_when_wslg_offers_only_bmp(self) -> None:
        self.fake("wl-paste", 'case "$*" in "--list-types") echo image/bmp; echo text/plain ;; '
                  '"--no-newline --type image/bmp") printf BMDATA ;; *) exit 1 ;; esac')
        self.fake("powershell.exe", "printf '%s' UE5HREFUQQ==")
        with mock.patch.dict(os.environ, {"DEVCONTAINER_HOST_RELAY_PLATFORM": "wsl"}):
            self.assertEqual(relay.clipboard_targets(), b"image/png\nimage/bmp\n")
            self.assertEqual(relay.clipboard_image("png"), b"PNGDATA")
            self.assertEqual(relay.clipboard_image("bmp"), b"BMDATA")

    def test_text_only_clipboard_reports_no_targets_and_no_image(self) -> None:
        self.fake("wl-paste", 'case "$*" in "--list-types") echo text/plain ;; *) exit 1 ;; esac')
        self.fake("powershell.exe", "exit 3")
        with mock.patch.dict(os.environ, {"DEVCONTAINER_HOST_RELAY_PLATFORM": "wsl"}):
            self.assertEqual(relay.clipboard_targets(), b"")
            with self.assertRaises(relay.RelayError):
                relay.clipboard_image("png")
            with self.assertRaises(relay.RelayError):
                relay.clipboard_image("text/plain")

    def test_darwin_reads_png_through_osascript(self) -> None:
        self.fake("osascript", r'''
if [[ "$*" == "-e clipboard info" ]]; then echo "«class PNGf», 1234, «class 8BPS», 99"; exit 0; fi
for arg in "$@"; do
  if [[ "$arg" =~ POSIX\ file\ \"([^\"]+)\" ]]; then printf DARWINPNG > "${BASH_REMATCH[1]}"; fi
done
''')
        with mock.patch.dict(os.environ, {"DEVCONTAINER_HOST_RELAY_PLATFORM": "darwin"}):
            self.assertEqual(relay.clipboard_targets(), b"image/png\n")
            self.assertEqual(relay.clipboard_image("png"), b"DARWINPNG")
            with self.assertRaises(relay.RelayError):
                relay.clipboard_image("bmp")

    def test_darwin_without_an_image_reports_nothing(self) -> None:
        self.fake("osascript", 'echo "«class utf8», 12"')
        with mock.patch.dict(os.environ, {"DEVCONTAINER_HOST_RELAY_PLATFORM": "darwin"}):
            self.assertEqual(relay.clipboard_targets(), b"")
            with self.assertRaises(relay.RelayError):
                relay.clipboard_image("png")

    def test_opener_matches_the_host_platform(self) -> None:
        url = "http://localhost:9999/"
        home = self.fixture.home
        cases = (
            ("darwin", ["open", url]),
            ("linux", ["xdg-open", url]),
            ("wsl", ["wsl-open", url]),
        )
        for platform_name, expected in cases:
            with self.subTest(platform=platform_name), mock.patch.dict(
                os.environ, {"DEVCONTAINER_HOST_RELAY_PLATFORM": platform_name, "HOME": str(home)}
            ):
                os.environ.pop("DEVCONTAINER_HOST_RELAY_OPENER", None)
                self.assertEqual(relay.opener_command(url), expected)
        wsl_open = write_executable(home / "bin" / "wsl-open", "#!/bin/sh\n")
        with mock.patch.dict(os.environ, {"DEVCONTAINER_HOST_RELAY_PLATFORM": "wsl", "HOME": str(home)}):
            os.environ.pop("DEVCONTAINER_HOST_RELAY_OPENER", None)
            self.assertEqual(relay.opener_command(url), [str(wsl_open), url])

    def test_process_command_falls_back_to_ps_and_tolerates_its_absence(self) -> None:
        with mock.patch.object(relay.Path, "read_bytes", side_effect=OSError):
            self.assertEqual(relay.process_command(4242), b"")
            self.fake("ps", 'echo "python3 devcontainer_host_relay.py serve --dir x"')
            self.assertIn(b"devcontainer_host_relay.py serve", relay.process_command(4242))

    def test_oversized_images_are_refused(self) -> None:
        self.fake("wl-paste", "printf 0123456789")
        with mock.patch.dict(os.environ, {"DEVCONTAINER_HOST_RELAY_PLATFORM": "linux"}), \
                mock.patch.object(relay, "MAX_IMAGE_BYTES", 4):
            with self.assertRaises(relay.RelayError):
                relay.clipboard_image("png")


@unittest.skipUnless(os.name != "nt" and BASH, "POSIX bash is required")
class RelayEndToEndTests(unittest.TestCase):
    def make_run_dir(self) -> Path:
        runtime = tempfile.TemporaryDirectory(prefix="hr-", dir="/tmp")
        self.addCleanup(runtime.cleanup)
        return Path(runtime.name).resolve() / "run"

    def setUp(self) -> None:
        context = isolated_environment(prefix="host-relay-")
        self.fixture = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.run_dir = self.make_run_dir()
        self.opened = self.fixture.root / "opened"
        opener = write_executable(
            self.fixture.root / "opener", f'#!/bin/sh\nprintf "%s\\n" "$1" >> "{self.opened}"\n'
        )
        wl_paste = 'case "$*" in "--list-types") echo image/png ;; *) printf PNGBYTES ;; esac'
        write_executable(self.fixture.fake_bin / "wl-paste", f"#!{BASH}\n{wl_paste}\n")
        self.env = dict(
            self.fixture.env,
            DEVCONTAINER_HOST_RELAY_OPENER=str(opener),
            DEVCONTAINER_HOST_RELAY_PLATFORM="linux",
            DEVCONTAINER_HOST_RELAY_SOCKET=str(self.run_dir / "sock" / "relay.sock"),
        )
        self.container_bin = self.fixture.root / "container-bin"
        self.container_bin.mkdir()
        for source in (CLIENT_PATH, OPEN_URL_PATH):
            target = self.container_bin / source.name.removeprefix("executable_")
            shutil.copy2(source, target)
            target.chmod(0o755)
        self.addCleanup(self.stop_relay)

    def stop_relay(self) -> None:
        pid_path = self.run_dir / "relay.pid"
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
            except ValueError:
                self.fail("invalid relay PID")

            def running() -> bool:
                command = relay.process_command(pid)
                return b"devcontainer_host_relay" in command and os.fsencode(self.run_dir) in command

            for sig, grace in ((signal.SIGTERM, 3), (signal.SIGKILL, 2)):
                if not running():
                    return
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    return
                deadline = time.monotonic() + grace
                while running() and time.monotonic() < deadline:
                    time.sleep(0.05)
            self.assertFalse(running(), "relay still running during fixture cleanup")

    def start(self, ranges: str = RANGES, script: Path = RELAY_PATH, transport: str = "unix") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), "start", "--dir", str(self.run_dir), "--ports", ranges, "--transport", transport],
            env=self.env, text=True, capture_output=True, check=False, timeout=30,
        )

    def client(self, *args: str, name: str = "devcontainer-host-relay") -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [str(self.container_bin / name), *args],
            env=self.env, capture_output=True, check=False, timeout=30,
        )

    def test_start_is_idempotent_and_private(self) -> None:
        self.assertEqual(self.start().returncode, 0)
        first_pid = (self.run_dir / "relay.pid").read_text()
        self.assertEqual(self.start().returncode, 0)
        self.assertEqual((self.run_dir / "relay.pid").read_text(), first_pid)
        self.assertEqual(self.run_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.run_dir / "sock").stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.run_dir / "sock/relay.sock").stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(path.name for path in (self.run_dir / "sock").iterdir()), ["relay.sock"])

    def test_unix_socket_stays_short_with_a_long_temporary_parent(self) -> None:
        long_parent = self.fixture.root / ("nested-" + "x" * 80) / "tmp"
        long_parent.mkdir(parents=True)
        with mock.patch.dict(os.environ, {"TMPDIR": str(long_parent)}), mock.patch.object(
            tempfile, "tempdir", str(long_parent)
        ):
            with isolated_environment(prefix="host-relay-") as nested:
                self.assertGreater(len(os.fsencode(nested.root / "run/sock/relay.sock")), 107)
            self.run_dir = self.make_run_dir()
            self.addCleanup(self.stop_relay)
            self.env["DEVCONTAINER_HOST_RELAY_SOCKET"] = str(self.run_dir / "sock/relay.sock")
            self.assertLess(len(os.fsencode(self.run_dir / "sock/relay.sock")), 100)
            self.assertEqual(self.start().returncode, 0)
            self.assertEqual(self.client("ping").returncode, 0)

    @unittest.skipUnless(shutil.which("pgrep"), "pgrep is required")
    def test_concurrent_starts_leave_one_relay(self) -> None:
        command = [sys.executable, str(RELAY_PATH), "start", "--dir", str(self.run_dir), "--ports", RANGES]
        starts = [subprocess.Popen(command, env=self.env) for _ in range(4)]
        self.assertEqual([process.wait(timeout=30) for process in starts], [0, 0, 0, 0])
        time.sleep(0.5)
        pid = int((self.run_dir / "relay.pid").read_text())
        serving = subprocess.run(
            ["pgrep", "-f", f"devcontainer_host_relay.py serve --dir {self.run_dir} "],
            capture_output=True, text=True, check=False,
        ).stdout.split()
        self.assertEqual(serving, [str(pid)])

    def assert_replaced(self, first_pid: int) -> None:
        self.assertNotEqual(int((self.run_dir / "relay.pid").read_text()), first_pid)
        for _ in range(50):
            try:
                os.kill(first_pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        self.fail("previous relay is still running")

    def test_start_replaces_a_relay_with_different_ranges(self) -> None:
        self.assertEqual(self.start("9999").returncode, 0)
        first_pid = int((self.run_dir / "relay.pid").read_text())
        self.assertEqual(self.start().returncode, 0)
        self.assert_replaced(first_pid)

    def test_start_replaces_a_relay_running_older_code(self) -> None:
        older = self.fixture.root / "older" / "devcontainer_host_relay.py"
        older.parent.mkdir()
        older.write_text(RELAY_PATH.read_text(encoding="utf-8") + "\n# older build\n", encoding="utf-8")
        self.assertEqual(self.start(script=older).returncode, 0)
        first_pid = int((self.run_dir / "relay.pid").read_text())
        self.assertEqual(self.start().returncode, 0)
        self.assert_replaced(first_pid)

    def raw_request(self, payload: bytes) -> bytes:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(str(self.run_dir / "sock" / "relay.sock"))
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            chunks = []
            while chunk := client.recv(4096):
                chunks.append(chunk)
        return b"".join(chunks)

    def test_malformed_requests_are_refused_and_logged_without_content(self) -> None:
        self.assertEqual(self.start().returncode, 0)
        self.assertEqual(self.raw_request(b"targets"), b"error request must end with a newline\n")
        self.assertEqual(self.raw_request(b"x" * 9000 + b"\n"), b"error request too long\n")
        self.assertEqual(self.raw_request(b"$(touch-me) now\n"), b"error unknown request\n")
        self.assertEqual(self.raw_request(b"open\n"), b"error unknown request\n")
        log = (self.run_dir / "relay.log").read_text()
        self.assertNotIn("touch-me", log)
        self.assertEqual(len(log.splitlines()), 4)

    def test_open_requests_follow_the_policy(self) -> None:
        self.assertEqual(self.start().returncode, 0)
        self.assertEqual(self.client("open", "http://localhost:10004/").returncode, 0)
        refused = self.client("open", "http://localhost:8080/")
        self.assertEqual(refused.returncode, 1)
        self.assertIn(b"outside the allowed ranges", refused.stderr)
        wrapped = self.client("http://localhost:9999/", name="devcontainer-open-url")
        self.assertEqual(wrapped.returncode, 0, wrapped.stderr)
        self.assertEqual(
            self.opened.read_text().splitlines(),
            ["http://localhost:10004/", "http://localhost:9999/"],
        )
        log = (self.run_dir / "relay.log").read_text()
        for fragment in ("http", "10004", "8080", "9999"):
            self.assertNotIn(fragment, log)

    def tcp_client(self, *args: str, name: str = "devcontainer-host-relay") -> subprocess.CompletedProcess[bytes]:
        with mock.patch.dict(self.env, {
            "DEVCONTAINER_HOST_RELAY_TRANSPORT": "tcp",
            "DEVCONTAINER_HOST_RELAY_CONNECTION": str(self.run_dir / "sock" / "connection.json"),
            "DEVCONTAINER_HOST_RELAY_HOST": "127.0.0.1",
        }):
            return self.client(*args, name=name)

    def tcp_raw_request(self, payload: bytes) -> bytes:
        port, _ = relay.read_connection(self.run_dir)
        with socket.create_connection(("127.0.0.1", port), timeout=10) as client:
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            chunks = []
            while chunk := client.recv(4096):
                chunks.append(chunk)
        return b"".join(chunks)

    def test_tcp_is_private_authenticated_and_reuses_its_descriptor(self) -> None:
        self.assertEqual(self.start(transport="tcp").returncode, 0)
        descriptor = self.run_dir / "sock" / "connection.json"
        self.assertEqual(descriptor.stat().st_mode & 0o777, 0o600)
        port, token = relay.read_connection(self.run_dir)
        self.assertGreater(port, 0)
        self.assertEqual(len(token), 64)
        self.assertEqual(self.tcp_raw_request(b"ping\n"), b"error unauthorized\n")
        self.assertEqual(self.tcp_raw_request(b"auth bad\nping\n"), b"error unauthorized\n")
        self.assertEqual(self.tcp_raw_request(f"auth {token}\nping\n".encode("ascii")),
                         f"ok {len(relay.relay_identity(RANGES, 'tcp'))}\n{relay.relay_identity(RANGES, 'tcp')}".encode("ascii"))
        self.assertEqual(self.tcp_raw_request(f"auth {token}\nopen http://localhost:8080/\n".encode("ascii")),
                         b"error localhost port is outside the allowed ranges\n")
        self.assertEqual(self.tcp_raw_request(f"auth {token}\n".encode("ascii") + b"x" * 9000 + b"\n"),
                         b"error request too long\n")
        self.assertEqual(self.tcp_client("ping").returncode, 0)
        self.assertEqual(self.tcp_client("http://localhost:10014/", name="devcontainer-open-url").returncode, 0)
        self.assertEqual(self.opened.read_text().splitlines(), ["http://localhost:10014/"])
        self.assertEqual(self.tcp_client("targets").stdout, b"image/png\n")
        self.assertEqual(self.tcp_client("image", "png").stdout, b"PNGBYTES")
        pid = (self.run_dir / "relay.pid").read_text()
        self.assertEqual(self.start(transport="tcp").returncode, 0)
        self.assertEqual((self.run_dir / "relay.pid").read_text(), pid)
        self.assertEqual(relay.read_connection(self.run_dir), (port, token))
        log = (self.run_dir / "relay.log").read_text()
        self.assertNotIn(token, log)
        self.assertNotIn("http://localhost", log)

    def test_tcp_descriptor_rejects_missing_malformed_and_world_readable_credentials(self) -> None:
        self.assertNotEqual(self.tcp_client("ping").returncode, 0)
        self.assertEqual(self.start(transport="tcp").returncode, 0)
        descriptor = self.run_dir / "sock" / "connection.json"
        descriptor.chmod(0o644)
        self.assertNotEqual(self.tcp_client("ping").returncode, 0)
        descriptor.chmod(0o600)
        original = descriptor.read_bytes()
        descriptor.write_text('{"port": 0, "token": "bad"}')
        self.assertNotEqual(self.tcp_client("ping").returncode, 0)
        descriptor.write_bytes(original)
        self.assertEqual(self.tcp_client("ping").returncode, 0)

    def test_tcp_replaces_unix_and_rotates_credentials_on_restart(self) -> None:
        self.assertEqual(self.start().returncode, 0)
        first_pid = int((self.run_dir / "relay.pid").read_text())
        self.assertEqual(self.start(transport="tcp").returncode, 0)
        self.assert_replaced(first_pid)
        _, token = relay.read_connection(self.run_dir)
        first_pid = int((self.run_dir / "relay.pid").read_text())
        self.assertEqual(self.start("9999", transport="tcp").returncode, 0)
        self.assert_replaced(first_pid)
        self.assertNotEqual(relay.read_connection(self.run_dir)[1], token)
        self.assertEqual(self.tcp_client("ping").returncode, 0)

    def test_post_start_warns_if_relay_ping_fails_without_blocking(self) -> None:
        relay_client = self.fixture.home / ".local/bin/devcontainer-host-relay"
        relay_client.parent.mkdir(parents=True)
        for code, warning in ((1, True), (0, False)):
            write_executable(relay_client, f"#!/bin/sh\nexit {code}\n")
            result = subprocess.run(
                ["bash", "-c", f'source "{POST_START}"; post_start_check_host_relay'],
                env=self.env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual("WARN: devcontainer host relay is unreachable" in result.stderr, warning)

    def test_clipboard_requests_return_image_bytes(self) -> None:
        self.assertEqual(self.start().returncode, 0)
        self.assertEqual(self.client("targets").stdout, b"image/png\n")
        self.assertEqual(self.client("image", "png").stdout, b"PNGBYTES")
        self.assertEqual(self.client("image", "tiff").returncode, 1)

    def test_client_fails_cleanly_without_a_relay(self) -> None:
        result = self.client("targets")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"host relay unavailable", result.stderr)
        self.assertEqual(self.client("bogus").returncode, 2)

    def test_xclip_shim_routes_image_reads_and_rejects_text_reads(self) -> None:
        shim_dir = self.fixture.root / "shim"
        shim_dir.mkdir()
        log = self.fixture.root / "relay-args"
        write_executable(shim_dir / "devcontainer-host-relay", f'#!/bin/sh\necho "$*" >> "{log}"\n')
        shim = shim_dir / "devcontainer-clipboard-osc52"
        shutil.copy2(SHIM_PATH, shim)
        shim.chmod(0o755)
        (shim_dir / "xclip").symlink_to(shim)
        xclip = str(shim_dir / "xclip")
        for argv in (
            ["-selection", "clipboard", "-t", "TARGETS", "-o"],
            ["-selection", "clipboard", "-t", "image/png", "-o"],
            ["-selection", "clipboard", "-t", "image/bmp", "-o"],
        ):
            result = subprocess.run([xclip, *argv], env=self.env, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log.read_text().splitlines(), ["targets", "image png", "image bmp"])
        for argv in (
            ["-selection", "clipboard", "-t", "text/plain", "-o"],
            ["-selection", "clipboard", "-o"],
            ["-selection", "clipboard", "-t", "image/png"],
        ):
            result = subprocess.run([xclip, *argv], env=self.env, capture_output=True, check=False,
                                    stdin=subprocess.DEVNULL)
            self.assertEqual(result.returncode, 1, argv)
            self.assertIn(b"only supports", result.stderr)


@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class TemplateRenderTests(unittest.TestCase):
    def rendered(self, platform: str) -> str:
        def configure(data: dict[str, object]) -> None:
            data.update(CERTFILES_RAW="", CERTPATH="/fixture/keys")
            ports = data["plannotator_ports"]["devcontainer"]  # type: ignore[index]
            self.expected_ranges = f"{ports['build']},9999,{ports['custom']},{ports['claude']}"

        return render_template(TEMPLATE, platform, configure)

    def test_published_ports_match_the_relay_and_chezmoidata(self) -> None:
        for platform in ("macos", "wsl2"):
            with self.subTest(platform=platform):
                text = self.rendered(platform)
                published = [
                    line.strip().strip('",').split(":", 1)[1].split(":", 1)[0]
                    for line in text.splitlines()
                    if line.strip().startswith('"127.0.0.1:')
                ]
                self.assertEqual(",".join(published), self.expected_ranges)
                self.assertIn(f"--ports {self.expected_ranges}", text)
                ui_port = re.search(r'"PLANNOTATOR_PORT": "([0-9]+)"', text)
                self.assertIsNotNone(ui_port)
                self.assertIn(ui_port.group(1), published)
                self.assertIn('"BROWSER": "/home/vscode/.local/bin/devcontainer-open-url"', text)
                transport = "tcp" if platform == "macos" else "unix"
                self.assertIn(f'"DEVCONTAINER_HOST_RELAY_TRANSPORT": "{transport}"', text)
                mount = ("source=${localEnv:HOME}/.cache/devcontainer-host-relay/homelab-iac/sock,"
                         "target=/tmp/host-relay,type=bind")
                self.assertIn(mount + (",readonly" if platform == "macos" else '"'), text)

    def test_initialize_command_starts_the_relay_on_each_platform(self) -> None:
        for platform, has_agent in (("macos", False), ("wsl2", True)):
            with self.subTest(platform=platform):
                lines = [
                    line for line in self.rendered(platform).splitlines()
                    if line.strip().startswith('"initializeCommand"')
                ]
                self.assertEqual(len(lines), 1)
                argv = json.loads(lines[0].split(":", 1)[1].strip().rstrip(","))
                self.assertEqual(argv[:2], ["bash", "-lc"])
                self.assertEqual("wsl2-ssh-agent" in argv[2], has_agent)
                self.assertIn("devcontainer_host_relay.py\" start --dir", argv[2])
                self.assertIn("--transport " + ("tcp" if platform == "macos" else "unix"), argv[2])
                syntax = subprocess.run(["bash", "-n", "-c", argv[2]], capture_output=True, check=False)
                self.assertEqual(syntax.returncode, 0, syntax.stderr)


if __name__ == "__main__":
    unittest.main()
