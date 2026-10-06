#!/usr/bin/env python3
"""Host-side relay that lets the devcontainer open URLs and read clipboard images.

The container reaches this server over a unix socket or an authenticated TCP
connection. Every request is one line, `<op> [arg]`, and every reply is `ok <nbytes>\\n<body>` or
`error <reason>\\n`. Only allowlisted URLs are opened and only image clipboard
content is returned; text clipboard reads are deliberately absent.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shutil
import socket
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SOCKET_DIR = "sock"
SOCKET_NAME = "relay.sock"
CONNECTION_NAME = "connection.json"
KNOWN_OPS = ("open", "targets", "image", "ping")
PID_NAME = "relay.pid"
LOCK_NAME = "relay.lock"
LOG_NAME = "relay.log"
MAX_REQUEST_BYTES = 8192
MAX_CONNECTION_BYTES = 4096
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_LOG_BYTES = 256 * 1024
COMMAND_TIMEOUT = 10
IMAGE_TYPES = ("png", "bmp", "jpeg", "gif", "webp")
CLAUDE_AUTHORIZE = {
    ("claude.com", "/cai/oauth/authorize"),
    ("claude.ai", "/oauth/authorize"),
}
CLAUDE_MANUAL_REDIRECT = "https://platform.claude.com/oauth/code/callback"
LOCAL_CALLBACK = re.compile(r"http://localhost:[0-9]{1,5}/callback")
PATH_CHARS = re.compile(r"[A-Za-z0-9._~&+=:%/?#-]*")
POWERSHELL_PNG = (
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
    "$i=[System.Windows.Forms.Clipboard]::GetImage();"
    "if($i -eq $null){exit 3};"
    "$m=New-Object System.IO.MemoryStream;"
    "$i.Save($m,[System.Drawing.Imaging.ImageFormat]::Png);"
    "[Console]::Out.Write([Convert]::ToBase64String($m.ToArray()))"
)
POWERSHELL_HAS_IMAGE = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "if([System.Windows.Forms.Clipboard]::ContainsImage()){exit 0};exit 3"
)


POWERSHELL_SLOT = threading.BoundedSemaphore(1)


class RelayError(Exception):
    pass


def parse_ranges(spec: str) -> list[tuple[int, int]]:
    ranges = []
    for part in spec.split(","):
        match = re.fullmatch(r"([0-9]{1,5})(?:-([0-9]{1,5}))?", part.strip())
        if not match:
            raise ValueError(f"invalid port range: {part!r}")
        low = int(match.group(1))
        high = int(match.group(2) or low)
        if not 0 < low <= high <= 65535:
            raise ValueError(f"invalid port range: {part!r}")
        ranges.append((low, high))
    if not ranges:
        raise ValueError("at least one port range is required")
    return ranges


def host_platform() -> str:
    override = os.environ.get("DEVCONTAINER_HOST_RELAY_PLATFORM")
    if override:
        return override
    if sys.platform == "darwin":
        return "darwin"
    with contextlib.suppress(OSError):
        if "microsoft" in Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").lower():
            return "wsl"
    return "linux"


def checked_url(url: str, ranges: list[tuple[int, int]]) -> str:
    """Return the URL to open, or raise RelayError when policy rejects it."""
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in url):
        raise RelayError("url contains whitespace, control, or non-ASCII characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise RelayError("malformed url") from exc
    if parts.username is not None or parts.password is not None:
        raise RelayError("url must not carry credentials")
    if not PATH_CHARS.fullmatch(url.split("://", 1)[-1]):
        raise RelayError("url contains disallowed characters")

    if parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1"):
        if port is None or not any(low <= port <= high for low, high in ranges):
            raise RelayError("localhost port is outside the allowed ranges")
        return url

    if (
        parts.scheme == "https"
        and port is None
        and (parts.hostname, parts.path) in CLAUDE_AUTHORIZE
        and parts.netloc == parts.hostname
    ):
        query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=False)
        redirects = [value for key, value in query if key == "redirect_uri"]
        if len(redirects) != 1 or not (
            LOCAL_CALLBACK.fullmatch(redirects[0]) or redirects[0] == CLAUDE_MANUAL_REDIRECT
        ):
            raise RelayError("claude authorize url has an unexpected redirect_uri")
        rewritten = [
            (key, CLAUDE_MANUAL_REDIRECT) if key == "redirect_uri" else (key, value)
            for key, value in query
        ]
        return urlunsplit(("https", parts.netloc, parts.path, urlencode(rewritten), ""))

    raise RelayError("url is not on the relay allowlist")


def opener_command(url: str) -> list[str]:
    override = os.environ.get("DEVCONTAINER_HOST_RELAY_OPENER")
    if override:
        return [override, url]
    current = host_platform()
    if current == "darwin":
        return ["open", url]
    if current == "wsl":
        wsl_open = Path.home() / "bin" / "wsl-open"
        return [str(wsl_open) if os.access(wsl_open, os.X_OK) else "wsl-open", url]
    return ["xdg-open", url]


def open_url(url: str, ranges: list[tuple[int, int]]) -> bytes:
    target = checked_url(url, ranges)
    try:
        result = subprocess.run(
            opener_command(target),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RelayError("browser opener failed to run") from exc
    if result.returncode != 0:
        raise RelayError(f"browser opener exited {result.returncode}")
    return b""


def run_bytes(argv: list[str]) -> bytes:
    if shutil.which(argv[0]) is None:
        return b""
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return b""
    return result.stdout if result.returncode == 0 else b""


def run_succeeds(argv: list[str]) -> bool:
    if shutil.which(argv[0]) is None:
        return False
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT,
            check=False,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def powershell_has_image() -> bool:
    with POWERSHELL_SLOT:
        return run_succeeds(powershell(POWERSHELL_HAS_IMAGE))


def powershell(script: str) -> list[str]:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


def wsl_png_via_powershell() -> bytes:
    with POWERSHELL_SLOT:
        encoded = run_bytes(powershell(POWERSHELL_PNG))
    try:
        return base64.b64decode(encoded.strip(), validate=True)
    except ValueError:
        return b""


def darwin_png() -> bytes:
    with tempfile.TemporaryDirectory(prefix="devcontainer-relay-") as temp_dir:
        target = Path(temp_dir) / "clipboard.png"
        run_bytes([
            "osascript",
            "-e", "set png_data to (the clipboard as «class PNGf»)",
            "-e", f'set fp to open for access POSIX file "{target}" with write permission',
            "-e", "write png_data to fp",
            "-e", "close access fp",
        ])
        with contextlib.suppress(OSError):
            return target.read_bytes()
    return b""


def clipboard_targets() -> bytes:
    current = host_platform()
    found: list[str] = []
    if current == "darwin":
        if "PNGf" in run_bytes(["osascript", "-e", "clipboard info"]).decode("utf-8", "replace"):
            found.append("image/png")
    else:
        listed = run_bytes(["wl-paste", "--list-types"]).decode("utf-8", "replace").split()
        found = [
            item for item in listed
            if item.startswith("image/") and item.removeprefix("image/") in IMAGE_TYPES
        ]
        if current == "wsl" and "image/png" not in found and (
            found or powershell_has_image()
        ):
            found.insert(0, "image/png")
    return "".join(f"{item}\n" for item in dict.fromkeys(found)).encode("ascii")


def clipboard_image(kind: str) -> bytes:
    if kind not in IMAGE_TYPES:
        raise RelayError("unsupported image type")
    current = host_platform()
    if current == "darwin":
        data = darwin_png() if kind == "png" else b""
    else:
        data = run_bytes(["wl-paste", "--no-newline", "--type", f"image/{kind}"])
        if not data and kind == "png" and current == "wsl":
            data = wsl_png_via_powershell()
    if not data:
        raise RelayError(f"clipboard holds no image/{kind}")
    if len(data) > MAX_IMAGE_BYTES:
        raise RelayError("clipboard image exceeds the size cap")
    return data


def relay_identity(ranges_spec: str, transport: str = "unix") -> str:
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
    return f"{transport} {ranges_spec} {digest}"


def handle_request(line: str, ranges: list[tuple[int, int]], identity: str) -> bytes:
    op, _, arg = line.partition(" ")
    if op == "ping" and not arg:
        return identity.encode("ascii")
    if op == "open" and arg:
        return open_url(arg, ranges)
    if op == "targets" and not arg:
        return clipboard_targets()
    if op == "image" and arg:
        return clipboard_image(arg)
    raise RelayError("unknown request")


def log_event(directory: Path, op: str, verdict: str) -> None:
    log_path = directory / LOG_NAME
    with contextlib.suppress(OSError):
        if log_path.exists() and log_path.stat().st_size > MAX_LOG_BYTES:
            os.replace(log_path, log_path.with_name(LOG_NAME + ".1"))
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {op} {verdict}\n")


def read_request(stream) -> str:
    line = stream.readline(MAX_REQUEST_BYTES + 1)
    if len(line) > MAX_REQUEST_BYTES:
        raise RelayError("request too long")
    if not line.endswith(b"\n"):
        raise RelayError("request must end with a newline")
    try:
        return line[:-1].decode("ascii").rstrip("\r")
    except UnicodeDecodeError as exc:
        raise RelayError("request must be ASCII") from exc


def make_handler(directory: Path, ranges: list[tuple[int, int]], identity: str, token: str | None = None):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(COMMAND_TIMEOUT)
            op = "?"
            try:
                with self.request.makefile("rb") as stream:
                    if token is not None:
                        auth = read_request(stream)
                        if not hmac.compare_digest(auth, f"auth {token}"):
                            raise RelayError("unauthorized")
                    line = read_request(stream)
                first = line.partition(" ")[0]
                op = first if first in KNOWN_OPS else "?"
                body = handle_request(line, ranges, identity)
            except RelayError as exc:
                reason = str(exc)
                if op != "ping":
                    log_event(directory, op, f"error: {reason}")
                with contextlib.suppress(OSError):
                    self.request.sendall(f"error {reason}\n".encode("ascii", "replace"))
                return
            except (OSError, subprocess.SubprocessError) as exc:
                log_event(directory, op, f"error: {type(exc).__name__}")
                with contextlib.suppress(OSError):
                    self.request.sendall(b"error internal failure\n")
                return
            if op != "ping":
                log_event(directory, op, f"ok {len(body)}")
            with contextlib.suppress(OSError):
                self.request.sendall(f"ok {len(body)}\n".encode("ascii") + body)

    return Handler


class RelayServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class TcpRelayServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True


def remove_socket(path: Path) -> None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(st.st_mode):
        raise RuntimeError(f"refusing to replace non-socket path: {path}")
    os.unlink(path)


def prepare_directory(directory: Path) -> None:
    """Create the host-only state directory and the socket subdirectory the container mounts.

    Only `sock/` is shared with the container; the pid and log files stay in the
    parent so the host never writes through a path the container controls.
    """
    for path in (directory, directory / SOCKET_DIR):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise RuntimeError(f"relay directory must be a directory owned by this user: {path}")
        os.chmod(path, 0o700)


def socket_path(directory: Path) -> Path:
    return directory / SOCKET_DIR / SOCKET_NAME


def connection_path(directory: Path) -> Path:
    return directory / SOCKET_DIR / CONNECTION_NAME


def write_connection(directory: Path, port: int, token: str) -> None:
    target = connection_path(directory)
    fd, temporary = tempfile.mkstemp(prefix=".connection-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            json.dump({"port": port, "token": token}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def read_connection(directory: Path) -> tuple[int, str]:
    target = connection_path(directory)
    st = os.lstat(target)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise RelayError("insecure relay connection descriptor")
    if st.st_size > MAX_CONNECTION_BYTES:
        raise RelayError("relay connection descriptor too large")
    data = json.loads(target.read_text(encoding="ascii"))
    port, token = data["port"], data["token"]
    if type(port) is not int or not 0 < port <= 65535 or not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise RelayError("invalid relay connection descriptor")
    return port, token


def serve(directory: Path, ranges_spec: str, transport: str = "unix") -> int:
    ranges = parse_ranges(ranges_spec)
    prepare_directory(directory)
    if transport == "tcp":
        token = secrets.token_hex(32)
        server = TcpRelayServer(("127.0.0.1", 0), make_handler(directory, ranges, relay_identity(ranges_spec, transport), token))
        try:
            write_connection(directory, server.server_address[1], token)
        except OSError:
            server.server_close()
            raise
    else:
        sock_path = socket_path(directory)
        remove_socket(sock_path)
        old_umask = os.umask(0o177)
        try:
            server = RelayServer(str(sock_path), make_handler(directory, ranges, relay_identity(ranges_spec)))
        finally:
            os.umask(old_umask)
    (directory / PID_NAME).write_text(f"{os.getpid()}\n", encoding="ascii")
    with server:
        server.serve_forever()
    return 0


def ping(directory: Path, transport: str = "unix", timeout: float = 2.0) -> str | None:
    try:
        if transport == "tcp":
            port, token = read_connection(directory)
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            address = ("127.0.0.1", port)
            payload = f"auth {token}\nping\n".encode("ascii")
        else:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            address = str(socket_path(directory))
            payload = b"ping\n"
        with client:
            client.settimeout(timeout)
            client.connect(address)
            client.sendall(payload)
            reply = b""
            while chunk := client.recv(4096):
                reply += chunk
                if len(reply) > MAX_REQUEST_BYTES:
                    return None
    except (OSError, RelayError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    header, _, body = reply.partition(b"\n")
    if not header.startswith(b"ok "):
        return None
    return body.decode("ascii", "replace")


def process_command(pid: int) -> bytes:
    with contextlib.suppress(OSError):
        return Path(f"/proc/{pid}/cmdline").read_bytes()
    try:
        return subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        ).stdout
    except OSError:
        return b""


def stop_previous(directory: Path) -> None:
    pid_path = directory / PID_NAME
    try:
        pid = int(pid_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return
    cmdline = process_command(pid)
    if b"devcontainer_host_relay" in cmdline and b"serve" in cmdline:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, 15)


def start(directory: Path, ranges_spec: str, transport: str = "unix") -> int:
    parse_ranges(ranges_spec)
    prepare_directory(directory)
    with (directory / LOCK_NAME).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return start_locked(directory, ranges_spec, transport)


def start_locked(directory: Path, ranges_spec: str, transport: str = "unix") -> int:
    identity = relay_identity(ranges_spec, transport)
    if ping(directory, transport) == identity:
        return 0
    stop_previous(directory)
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "serve", "--dir", str(directory), "--ports", ranges_spec, "--transport", transport],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    for _ in range(50):
        if ping(directory, transport) == identity:
            return 0
        time.sleep(0.1)
    print(f"devcontainer-host-relay: {transport} relay did not come up in {directory}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("start", "serve"))
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--ports", required=True, help="comma-separated ports or ranges, e.g. 9993-9999,10004-10009")
    parser.add_argument("--transport", choices=("unix", "tcp"), default="unix")
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return serve(args.dir, args.ports, args.transport)
        return start(args.dir, args.ports, args.transport)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"devcontainer-host-relay: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
