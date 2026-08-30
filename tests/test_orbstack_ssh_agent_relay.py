"""Behavioral tests for the OrbStack SSH-agent socket relay."""

from __future__ import annotations

import contextlib
import grp
import importlib.util
import os
import pwd
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from tests.support.fixtures import isolated_environment


ROOT = Path(__file__).resolve().parents[1]
RELAY_PATH = (
    ROOT
    / "private_Documents/development/container-dotfiles/devcontainers"
    / "gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer"
    / "orbstack_ssh_agent_relay.py"
)
HAS_UNIX_SOCKETS = os.name != "nt" and hasattr(socket, "AF_UNIX")


def load_relay_module():
    spec = importlib.util.spec_from_file_location("orbstack_ssh_agent_relay", RELAY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load relay module from {RELAY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def short_socket_directory():
    directory = Path(tempfile.mkdtemp(prefix="dot-relay-", dir="/tmp"))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def receive_exact(connection: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(min(8192, size - len(chunks)))
        if not chunk:
            break
        chunks.extend(chunk)
    return bytes(chunks)


class UnixEchoServer:
    def __init__(self, socket_path: Path, expected_clients: int) -> None:
        self.socket_path = socket_path
        self.expected_clients = expected_clients
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.settimeout(0.2)
        self.listener.bind(str(socket_path))
        self.listener.listen(expected_clients)
        self.stop_event = threading.Event()
        self.errors: list[BaseException] = []
        self.handlers: list[threading.Thread] = []
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop_event.set()
        self.listener.close()
        self.thread.join(timeout=3)
        for handler in self.handlers:
            handler.join(timeout=3)
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        if self.errors and exc_type is None:
            raise AssertionError(f"echo server failed: {self.errors!r}")

    def _serve(self) -> None:
        accepted = 0
        while accepted < self.expected_clients and not self.stop_event.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            accepted += 1
            handler = threading.Thread(
                target=self._echo,
                args=(connection,),
                daemon=True,
            )
            self.handlers.append(handler)
            handler.start()

    def _echo(self, connection: socket.socket) -> None:
        try:
            with connection:
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        return
                    for offset in range(0, len(chunk), 113):
                        connection.sendall(chunk[offset : offset + 113])
        except (BrokenPipeError, ConnectionResetError):
            return
        except BaseException as exc:  # noqa: BLE001
            self.errors.append(exc)


@unittest.skipUnless(HAS_UNIX_SOCKETS, "requires POSIX Unix-domain sockets")
class OrbStackSshAgentRelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_context = isolated_environment(prefix="relay-test-")
        self.fixture = self.fixture_context.__enter__()
        self.module = load_relay_module()
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.group = grp.getgrgid(os.getgid()).gr_name

    def tearDown(self) -> None:
        self.fixture_context.__exit__(None, None, None)

    def relay_argv(
        self,
        listen_path: Path,
        upstream_path: Path,
        *,
        mode: str = "0600",
    ) -> list[str]:
        return [
            sys.executable,
            str(RELAY_PATH),
            "--listen",
            str(listen_path),
            "--upstream",
            str(upstream_path),
            "--user",
            self.user,
            "--group",
            self.group,
            "--mode",
            mode,
        ]

    def start_relay(
        self,
        listen_path: Path,
        upstream_path: Path,
        *,
        mode: str = "0600",
    ) -> subprocess.Popen[str]:
        try:
            previous_socket = listen_path.stat()
            previous_identity = (previous_socket.st_dev, previous_socket.st_ino)
        except FileNotFoundError:
            previous_identity = None
        process = subprocess.Popen(
            self.relay_argv(listen_path, upstream_path, mode=mode),
            env=self.fixture.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if listen_path.exists():
                current_socket = listen_path.stat()
                current_identity = (current_socket.st_dev, current_socket.st_ino)
                if previous_identity is None or current_identity != previous_identity:
                    time.sleep(0.05)
                    if process.poll() is None:
                        return process
            if process.poll() is not None:
                _, stderr = process.communicate(timeout=1)
                self.fail(f"relay exited before creating its socket: {stderr}")
            time.sleep(0.02)
        self.stop_relay(process)
        self.fail(f"relay did not create {listen_path}")

    def stop_relay(
        self,
        process: subprocess.Popen[str],
        *,
        sent_signal: signal.Signals = signal.SIGTERM,
    ) -> tuple[str, str]:
        if process.poll() is None:
            process.send_signal(sent_signal)
        try:
            return process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            self.fail(f"relay did not stop after {sent_signal.name}")

    def test_argument_parsing_requires_endpoints_and_defaults_mode(self) -> None:
        argv = [
            "relay",
            "--listen",
            "/tmp/listen.sock",
            "--upstream",
            "/tmp/upstream.sock",
            "--user",
            self.user,
        ]
        with mock.patch.object(sys, "argv", argv):
            args = self.module.parse_args()
        self.assertEqual(args.mode, "0600")
        self.assertIsNone(args.group)

        with (
            mock.patch.object(sys, "argv", ["relay", "--listen", "/tmp/x"]),
            contextlib.redirect_stderr(StringIO()),
        ):
            with self.assertRaises(SystemExit) as raised:
                self.module.parse_args()
        self.assertEqual(raised.exception.code, 2)

    def test_invalid_modes_and_unknown_accounts_fail_with_diagnostics(self) -> None:
        with short_socket_directory() as directory:
            for mode in ("invalid", "0999"):
                result = subprocess.run(
                    self.relay_argv(
                        directory / f"listen-{mode}.sock",
                        directory / "upstream.sock",
                        mode=mode,
                    ),
                    env=self.fixture.env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("orbstack_ssh_agent_relay:", result.stderr)

            argv = self.relay_argv(
                directory / "unknown-user.sock",
                directory / "upstream.sock",
            )
            argv[argv.index("--user") + 1] = "missing-relay-user"
            result = subprocess.run(
                argv,
                env=self.fixture.env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("missing-relay-user", result.stderr)

    def test_remove_socket_handles_missing_stale_and_unsafe_paths(self) -> None:
        with short_socket_directory() as directory:
            socket_path = directory / "stale.sock"
            self.module.remove_socket(str(socket_path))

            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(str(socket_path))
            stale.close()
            self.module.remove_socket(str(socket_path))
            self.assertFalse(socket_path.exists())

            socket_path.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "refusing to replace non-socket"):
                self.module.remove_socket(str(socket_path))
            self.assertEqual(socket_path.read_text(encoding="utf-8"), "keep")

    def test_relay_stream_handles_large_partial_bidirectional_io(self) -> None:
        with short_socket_directory() as directory:
            upstream_path = directory / "upstream.sock"
            with UnixEchoServer(upstream_path, 1):
                client, relay_client = socket.socketpair()
                client.settimeout(5)
                thread = threading.Thread(
                    target=self.module.relay_stream,
                    args=(relay_client, str(upstream_path)),
                    daemon=True,
                )
                thread.start()
                payload = os.urandom(196_731)
                sender = threading.Thread(target=client.sendall, args=(payload,), daemon=True)
                sender.start()
                self.assertEqual(receive_exact(client, len(payload)), payload)
                sender.join(timeout=5)
                self.assertFalse(sender.is_alive())
                client.close()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_process_relays_concurrent_clients_with_private_socket(self) -> None:
        with short_socket_directory() as directory:
            upstream_path = directory / "upstream.sock"
            listen_path = directory / "relay" / "agent.sock"
            with UnixEchoServer(upstream_path, 4):
                process = self.start_relay(listen_path, upstream_path, mode="0660")
                failures: list[BaseException] = []

                def exchange(index: int) -> None:
                    try:
                        payload = bytes([index]) * (70_000 + index)
                        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                            client.settimeout(5)
                            client.connect(str(listen_path))
                            sender = threading.Thread(
                                target=client.sendall,
                                args=(payload,),
                                daemon=True,
                            )
                            sender.start()
                            echoed = receive_exact(client, len(payload))
                            sender.join(timeout=5)
                            if sender.is_alive():
                                raise TimeoutError("client send timed out")
                        if echoed != payload:
                            raise AssertionError(f"payload mismatch for client {index}")
                    except BaseException as exc:  # noqa: BLE001
                        failures.append(exc)

                clients = [
                    threading.Thread(target=exchange, args=(index,), daemon=True)
                    for index in range(1, 5)
                ]
                for client in clients:
                    client.start()
                for client in clients:
                    client.join(timeout=8)
                    self.assertFalse(client.is_alive(), "client exchange timed out")

                self.assertEqual(failures, [])
                socket_stat = listen_path.stat()
                self.assertEqual(stat.S_IMODE(socket_stat.st_mode), 0o660)
                self.assertEqual(socket_stat.st_uid, os.getuid())
                self.assertEqual(socket_stat.st_gid, os.getgid())
                _, stderr = self.stop_relay(process)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertFalse(listen_path.exists())

    def test_stale_socket_is_replaced_and_signals_cleanup(self) -> None:
        with short_socket_directory() as directory:
            upstream_path = directory / "upstream.sock"
            with UnixEchoServer(upstream_path, 0):
                for sent_signal in (signal.SIGTERM, signal.SIGINT):
                    listen_path = directory / f"relay-{sent_signal.name}.sock"
                    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    stale.bind(str(listen_path))
                    stale.close()

                    process = self.start_relay(listen_path, upstream_path)
                    _, stderr = self.stop_relay(process, sent_signal=sent_signal)
                    self.assertEqual(process.returncode, 0, stderr)
                    self.assertFalse(listen_path.exists())

    def test_non_socket_listen_path_is_preserved(self) -> None:
        with short_socket_directory() as directory:
            listen_path = directory / "agent.sock"
            listen_path.write_text("do not replace", encoding="utf-8")
            result = subprocess.run(
                self.relay_argv(listen_path, directory / "upstream.sock"),
                env=self.fixture.env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("refusing to replace non-socket path", result.stderr)
            self.assertEqual(listen_path.read_text(encoding="utf-8"), "do not replace")

    def test_upstream_disconnect_closes_client_without_hanging(self) -> None:
        with short_socket_directory() as directory:
            upstream_path = directory / "upstream.sock"
            listen_path = directory / "agent.sock"
            upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            process = None
            client = None
            try:
                upstream.bind(str(upstream_path))
                upstream.listen(1)
                process = self.start_relay(listen_path, upstream_path)
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(5)
                client.connect(str(listen_path))
                accepted, _ = upstream.accept()
                accepted.close()
                self.assertEqual(client.recv(1), b"")
            finally:
                if client is not None:
                    client.close()
                upstream.close()
                if process is not None:
                    _, stderr = self.stop_relay(process)
                    self.assertEqual(process.returncode, 0, stderr)

    def test_top_level_broken_pipe_exits_successfully(self) -> None:
        with short_socket_directory() as directory:
            listen_path = directory / "agent.sock"
            code = (
                "import runpy, socket, sys\n"
                "class BrokenSocket:\n"
                "    def bind(self, _path): raise BrokenPipeError()\n"
                "socket.socket = lambda *_args, **_kwargs: BrokenSocket()\n"
                f"sys.argv = {self.relay_argv(listen_path, directory / 'upstream.sock')[1:]!r}\n"
                f"runpy.run_path({str(RELAY_PATH)!r}, run_name='__main__')\n"
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                env=self.fixture.env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
