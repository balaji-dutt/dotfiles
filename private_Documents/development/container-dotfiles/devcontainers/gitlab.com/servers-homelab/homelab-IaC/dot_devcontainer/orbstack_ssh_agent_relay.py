#!/usr/bin/env python3

import argparse
import atexit
import contextlib
import grp
import os
import pwd
import selectors
import signal
import socket
import stat
import sys
import threading


MAX_PENDING_BYTES = 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Relay OrbStack SSH agent access to a user-owned socket."
    )
    parser.add_argument("--listen", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--group")
    parser.add_argument("--mode", default="0600")
    return parser.parse_args()


def remove_socket(path: str) -> None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return

    if not stat.S_ISSOCK(st.st_mode):
        raise RuntimeError(f"refusing to replace non-socket path: {path}")

    os.unlink(path)


def relay_stream(client: socket.socket, upstream_path: str) -> None:
    upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        upstream.connect(upstream_path)
        client.setblocking(False)
        upstream.setblocking(False)
        peers = {client: upstream, upstream: client}
        pending = {client: bytearray(), upstream: bytearray()}
        read_open = {client: True, upstream: True}
        registered: set[socket.socket] = set()

        with selectors.DefaultSelector() as selector:
            while any(read_open.values()) or any(pending.values()):
                for connection in (client, upstream):
                    events = 0
                    if (
                        read_open[connection]
                        and len(pending[peers[connection]]) < MAX_PENDING_BYTES
                    ):
                        events |= selectors.EVENT_READ
                    if pending[connection]:
                        events |= selectors.EVENT_WRITE
                    if events and connection in registered:
                        selector.modify(connection, events)
                    elif events:
                        selector.register(connection, events)
                        registered.add(connection)
                    elif connection in registered:
                        selector.unregister(connection)
                        registered.remove(connection)

                for key, event_mask in selector.select():
                    connection = key.fileobj
                    if event_mask & selectors.EVENT_READ:
                        try:
                            chunk = connection.recv(65536)
                        except BlockingIOError:
                            chunk = None
                        if chunk:
                            pending[peers[connection]].extend(chunk)
                        elif chunk == b"":
                            read_open[connection] = False
                            if not pending[peers[connection]]:
                                with contextlib.suppress(OSError):
                                    peers[connection].shutdown(socket.SHUT_WR)

                    if event_mask & selectors.EVENT_WRITE and pending[connection]:
                        try:
                            sent = connection.send(pending[connection])
                        except BlockingIOError:
                            continue
                        except (BrokenPipeError, ConnectionResetError):
                            return
                        del pending[connection][:sent]
                        source = peers[connection]
                        if not pending[connection] and not read_open[source]:
                            with contextlib.suppress(OSError):
                                connection.shutdown(socket.SHUT_WR)
    finally:
        try:
            upstream.close()
        finally:
            client.close()


def main() -> int:
    args = parse_args()
    uid = pwd.getpwnam(args.user).pw_uid
    gid = grp.getgrnam(args.group or args.user).gr_gid
    mode = int(args.mode, 8)
    listen_dir = os.path.dirname(args.listen) or "."

    os.umask(0o077)
    os.makedirs(listen_dir, mode=0o700, exist_ok=True)
    os.chown(listen_dir, uid, gid)
    os.chmod(listen_dir, 0o700)
    remove_socket(args.listen)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(args.listen)
    os.chown(args.listen, uid, gid)
    os.chmod(args.listen, mode)
    server.listen(32)

    def cleanup(*_args: object) -> None:
        try:
            server.close()
        finally:
            try:
                remove_socket(args.listen)
            except FileNotFoundError:
                pass
        raise SystemExit(0)

    atexit.register(lambda: remove_socket(args.listen))
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    while True:
        client, _ = server.accept()
        thread = threading.Thread(
            target=relay_stream,
            args=(client, args.upstream),
            daemon=True,
        )
        thread.start()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
    except Exception as exc:  # pragma: no cover - operational diagnostics
        print(f"orbstack_ssh_agent_relay: {exc}", file=sys.stderr)
        raise SystemExit(1)
