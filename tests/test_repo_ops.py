from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tests.support.fixtures import isolated_environment, read_json_lines, write_executable

if os.name == "posix":
    import fcntl
    import pty
    import struct
    import termios


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "bin/executable_repo-ops"


@contextmanager
def tmux_environment(fixture):
    with tempfile.TemporaryDirectory(prefix="ro-", dir="/tmp") as socket_dir:
        env = dict(fixture.env, TMUX_TMPDIR=socket_dir)
        env.pop("TMUX", None)
        try:
            yield env
        finally:
            result = subprocess.run(["tmux", "-L", "repo-ops", "kill-server"], env=env,
                                    capture_output=True, text=True)
            socket = Path(socket_dir) / f"tmux-{os.getuid()}" / "repo-ops"
            missing_server = (result.stderr.startswith("no server running on ") or
                              (result.stderr.endswith("(No such file or directory)\n") and
                               not socket.exists()))
            if result.returncode and not missing_server:
                raise RuntimeError(f"repo-ops tmux cleanup failed: {result.stderr}")


class RepoOpsTests(unittest.TestCase):
    def assert_server_exited(self, pid):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail(f"tmux server {pid} is still running")

    @contextmanager
    def theme_server(self):
        with isolated_environment(prefix="repo-ops-theme-") as fixture, tmux_environment(fixture) as env:
            theme = fixture.home / ".config/tmux/repo-ops.conf"
            theme.parent.mkdir(parents=True)
            theme.write_text((ROOT / "private_dot_config/tmux/repo-ops.conf").read_text())
            env["TERM"] = "xterm-256color"
            tmux = ["tmux", "-L", "repo-ops", "-f", str(theme)]

            def run(*args):
                return subprocess.run([*tmux, *args], env=env, capture_output=True, text=True)

            yield fixture, env, tmux, run

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux"), "requires tmux on POSIX")
    def test_socket_path_is_short_under_deep_temporary_root(self):
        self.require_theme_compatible_tmux()
        with tempfile.TemporaryDirectory() as temp_dir:
            deep_root = Path(temp_dir) / ("nested-directory-" * 5)
            deep_root.mkdir()
            with mock.patch.object(tempfile, "tempdir", str(deep_root)):
                with self.theme_server() as (fixture, env, _, run):
                    self.assertTrue(fixture.root.is_relative_to(deep_root))
                    self.assertEqual(env["TMPDIR"], str(fixture.root / "tmp"))
                    old_socket = fixture.root / "tmp" / f"tmux-{os.getuid()}" / "repo-ops"
                    self.assertGreaterEqual(len(os.fsencode(str(old_socket.resolve()))), 104)
                    started = run("new-session", "-d", "-s", "repo-ops-check", "sleep 30")
                    self.assertEqual(started.returncode, 0, started.stderr)
                    socket = run("display-message", "-p", "-t", "repo-ops-check", "#{socket_path}")
                    self.assertEqual(socket.returncode, 0, socket.stderr)
                    self.assertEqual(Path(socket.stdout.strip()).resolve().parent.parent,
                                     Path(env["TMUX_TMPDIR"]).resolve())
                    self.assertLess(len(os.fsencode(str(Path(socket.stdout.strip()).resolve()))), 104)

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux"), "requires tmux on POSIX")
    def test_socket_directory_and_server_cleanup(self):
        self.require_theme_compatible_tmux()
        with isolated_environment(prefix="repo-ops-theme-") as fixture:
            with tmux_environment(fixture) as env:
                socket_dir = Path(env["TMUX_TMPDIR"])
                self.assertTrue(socket_dir.is_dir())
            self.assertFalse(socket_dir.exists())

        for fail_inside in (False, True):
            with self.subTest(fail_inside=fail_inside):
                if fail_inside:
                    with self.assertRaisesRegex(RuntimeError, "intentional failure"):
                        with self.theme_server() as (_, env, _, run):
                            socket_dir = Path(env["TMUX_TMPDIR"])
                            started = run("new-session", "-d", "-s", "repo-ops-check", "sleep 30")
                            self.assertEqual(started.returncode, 0, started.stderr)
                            pid_result = run("display-message", "-p", "-t", "repo-ops-check", "#{pid}")
                            self.assertEqual(pid_result.returncode, 0, pid_result.stderr)
                            pid = int(pid_result.stdout.strip())
                            raise RuntimeError("intentional failure")
                else:
                    with self.theme_server() as (_, env, _, run):
                        socket_dir = Path(env["TMUX_TMPDIR"])
                        started = run("new-session", "-d", "-s", "repo-ops-check", "sleep 30")
                        self.assertEqual(started.returncode, 0, started.stderr)
                        pid_result = run("display-message", "-p", "-t", "repo-ops-check", "#{pid}")
                        self.assertEqual(pid_result.returncode, 0, pid_result.stderr)
                        pid = int(pid_result.stdout.strip())
                self.assertFalse(socket_dir.exists())
                self.assert_server_exited(pid)

    @contextmanager
    def attached_client(self, tmux, env, target, columns=100):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 25, columns, 0, 0))
        process = subprocess.Popen([*tmux, "attach-session", "-t", target], env=env,
                                   stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        try:
            yield master, process
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            os.close(master)

    def wait_for_output(self, master, expected, timeout=4):
        output = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    break
                if expected in output:
                    return output
        self.fail(f"missing {expected!r} in PTY output {output[-1500:]!r}")

    def wait_for_format(self, run, target, fmt, expected, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = run("display-message", "-p", "-t", target, fmt)
            if result.stdout.strip() == expected:
                return
            time.sleep(0.05)
        self.fail(f"expected {expected!r}, got {result.stdout!r} {result.stderr!r}")

    def tmux_format(self, run, target, fmt):
        result = run("display-message", "-p", "-t", target, fmt)
        self.assertEqual(result.returncode, 0, f"{target} {fmt}: {result.stderr}")
        return result.stdout.strip()

    def disable_paste_detection(self, run, *sessions):
        for session in sessions:
            result = run("set-option", "-t", session, "assume-paste-time", "0")
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run("show-option", "-v", "-t", session, "assume-paste-time")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "0", session)

    def wait_for_command_prompt(self, master, timeout=4):
        output = b""
        deadline = time.monotonic() + timeout
        prompt = re.compile(rb"(?:\x1b\[[0-9;]+[Hd]|\r\n)(?:\x1b\[[0-9;]*m)*:")
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    break
                if prompt.search(output):
                    return
        self.fail(f"missing command prompt in PTY output {output[-1500:]!r}")

    def wait_for_control_restart(self, run, target, previous_pid, timeout=4):
        deadline = time.monotonic() + timeout
        fmt = "#{pane_pid} #{pane_dead} #{@repo_ops_command}"
        while time.monotonic() < deadline:
            observed = self.tmux_format(run, target, fmt)
            pid, _, rest = observed.partition(" ")
            if pid != previous_pid and rest == "0":
                return
            time.sleep(0.05)
        self.fail(f"{target} restart: expected new live pid and cleared command tag; "
                  f"got {observed!r}, previous pid {previous_pid!r}")

    def wait_for_control_session_gone(self, run, target, survivor, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            missing = run("has-session", "-t", target)
            alive = run("has-session", "-t", survivor)
            self.assertEqual(alive.returncode, 0, f"{survivor}: {alive.stderr}")
            if missing.returncode != 0:
                return
            time.sleep(0.05)
        self.fail(f"{target} still exists after confirmation; {survivor} remains alive")

    def require_theme_compatible_tmux(self):
        version = subprocess.run(["tmux", "-V"], capture_output=True, text=True)
        match = re.search(r"(\d+)\.(\d+)", version.stdout)
        if version.returncode or not match or tuple(map(int, match.groups())) < (3, 3):
            self.skipTest("repo-ops theme requires tmux 3.3 or newer")

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux"), "requires tmux on POSIX")
    def test_dedicated_theme_and_titles(self):
        self.require_theme_compatible_tmux()
        with isolated_environment(prefix="repo-ops-theme-") as fixture, tmux_environment(fixture) as env:
            theme = fixture.home / ".config/tmux/repo-ops.conf"
            theme.parent.mkdir(parents=True)
            theme.write_text((ROOT / "private_dot_config/tmux/repo-ops.conf").read_text())
            tmux = ["tmux", "-L", "repo-ops", "-f", str(theme)]

            def run_tmux(*args):
                return subprocess.run([*tmux, *args], env=env, capture_output=True, text=True)

            def option(name):
                result = run_tmux("show-option", "-gv", name)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            def title():
                result = run_tmux("display-message", "-p", "-t", "repo-ops-check",
                                  titles_format)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            started = run_tmux("new-session", "-d", "-s", "repo-ops-check", "-n", "idle",
                               "bash --noprofile --norc -i")
            self.assertEqual(started.returncode, 0, started.stderr)
            titles_format = option("set-titles-string")
            self.assertEqual(option("mouse"), "on")
            self.assertEqual(option("allow-rename"), "off")
            self.assertEqual(option("automatic-rename"), "off")
            self.assertEqual(option("set-titles"), "on")
            self.assertEqual(option("status-justify"), "absolute-centre")
            self.assertEqual(option("status-left-length"), "64")
            self.assertIn("Layout:", option("status-left"))
            left = run_tmux("display-message", "-p", "-t", "repo-ops-check",
                           option("status-left"))
            self.assertIn("Layout: check", left.stdout)
            self.assertEqual(option("status-interval"), "1")
            for style in ("message-style", "message-command-style"):
                self.assertIn("fill=colour239", option(style))
            self.assertIn("colour214", option("window-status-current-style"))

            for _ in range(20):
                current = run_tmux("display-message", "-p", "-t", "repo-ops-check",
                                   "#{pane_current_command}").stdout.strip()
                if current == "bash":
                    break
                time.sleep(0.05)
            self.assertEqual(current, "bash")
            self.assertEqual(title(), "repo-ops: idle")

            running = run_tmux("new-window", "-d", "-t", "repo-ops-check", "-n", "running", "sleep 60")
            self.assertEqual(running.returncode, 0, running.stderr)
            selected = run_tmux("select-window", "-t", "repo-ops-check:running")
            self.assertEqual(selected.returncode, 0, selected.stderr)
            for _ in range(20):
                if title() == "repo-ops: running | sleep":
                    break
                time.sleep(0.05)
            self.assertEqual(title(), "repo-ops: running | sleep")
            self.assertEqual(run_tmux("select-window", "-t", "repo-ops-check:idle").returncode, 0)
            self.assertEqual(title(), "repo-ops: idle")

            unnamed = run_tmux("new-window", "-dP", "-F", "#{window_id}",
                               "-t", "repo-ops-check", "bash --noprofile --norc -i")
            self.assertEqual(unnamed.returncode, 0, unnamed.stderr)
            self.assertEqual(run_tmux("select-window", "-t", unnamed.stdout.strip()).returncode, 0)
            window = run_tmux("display-message", "-p", "-t", "repo-ops-check", "#{window_name}")
            self.assertEqual(window.returncode, 0, window.stderr)
            self.assertEqual(title(), f"repo-ops: {window.stdout.strip()}")

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux"), "requires tmux on POSIX")
    def test_attached_foreground_status_and_title(self):
        self.require_theme_compatible_tmux()
        with self.theme_server() as (_, env, tmux, run):
            started = run("new-session", "-d", "-s", "repo-ops-core", "-n", "dotfiles",
                          "bash --noprofile --norc -i")
            self.assertEqual(started.returncode, 0, started.stderr)
            self.wait_for_format(run, "repo-ops-core:dotfiles", "#{pane_current_command}", "bash")
            self.assertEqual(run("new-window", "-d", "-t", "repo-ops-core", "-n", "other",
                                 "sleep 60").returncode, 0)

            def expanded(option, target="repo-ops-core:dotfiles"):
                fmt = run("show-option", "-gv", option).stdout.strip()
                result = run("display-message", "-p", "-t", target, fmt)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            self.assertEqual(expanded("window-status-current-format"), "0:dotfiles")
            self.assertEqual(expanded("set-titles-string"), "repo-ops: dotfiles")
            self.assertEqual(expanded("window-status-format", "repo-ops-core:other"), "1:other | sleep")
            with self.attached_client(tmux, env, "repo-ops-core:dotfiles") as (master, _):
                self.wait_for_output(master, b"\x1b]0;repo-ops: dotfiles\x07")
                os.write(master, b"sleep 30\n")
                self.wait_for_output(master, b"sleep 30")
                self.wait_for_format(run, "repo-ops-core:dotfiles", "#{pane_current_command}", "sleep")
                self.assertEqual(expanded("window-status-current-format"), "0:dotfiles | sleep")
                self.assertEqual(expanded("set-titles-string"), "repo-ops: dotfiles | sleep")
                self.wait_for_output(master, b"\x1b]0;repo-ops: dotfiles | sleep\x07")
                os.write(master, b"\x03")
                self.wait_for_format(run, "repo-ops-core:dotfiles", "#{pane_current_command}", "bash")
                self.wait_for_output(master, b"\x1b]0;repo-ops: dotfiles\x07")
                self.assertEqual(expanded("window-status-current-format"), "0:dotfiles")
                self.assertEqual(run("display-message", "-p", "-t", "repo-ops-core:dotfiles",
                                     "#{window_name}").stdout.strip(), "dotfiles")

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux"), "requires tmux on POSIX")
    def test_wrapper_detection_and_dead_pane_titles(self):
        self.require_theme_compatible_tmux()
        with self.theme_server() as (fixture, env, tmux, run):
            for name in ("git", "bd"):
                write_executable(fixture.fake_bin / name, "#!/bin/sh\nsleep 30\n")
            started = run("new-session", "-d", "-s", "repo-ops-core", "-n", "dotfiles",
                          "bash --noprofile --norc -i")
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertEqual(run("new-window", "-d", "-t", "repo-ops-core", "-n", "ephemeral",
                                 "sleep 60").returncode, 0)

            def expanded(option, target="repo-ops-core:dotfiles"):
                fmt = run("show-option", "-gv", option).stdout.strip()
                return run("display-message", "-p", "-t", target, fmt).stdout.strip()

            with self.attached_client(tmux, env, "repo-ops-core:dotfiles") as (master, _):
                self.wait_for_output(master, b"\x1b]0;repo-ops: dotfiles\x07")
                for invocation in ("git push --follow-tags secret-token", "bd dolt push secret-token"):
                    os.write(master, (invocation + "\n").encode())
                    self.wait_for_output(master, invocation.encode())
                    self.wait_for_format(run, "repo-ops-core:dotfiles", "#{pane_current_command}", "sh")
                    self.assertEqual(expanded("window-status-current-format"), "0:dotfiles")
                    self.assertEqual(expanded("set-titles-string"), "repo-ops: dotfiles")
                    self.assertNotIn("secret-token", expanded("set-titles-string"))
                    os.write(master, b"\x03")
                    self.wait_for_format(run, "repo-ops-core:dotfiles", "#{pane_current_command}", "bash")

                self.assertEqual(run("select-window", "-t", "repo-ops-core:ephemeral").returncode, 0)
                self.wait_for_output(master, b"\x1b]0;repo-ops: ephemeral | sleep\x07")
                self.assertEqual(expanded("window-status-format"), "0:dotfiles")
                self.assertEqual(run("send-keys", "-t", "repo-ops-core:ephemeral", "C-c").returncode, 0)
                self.wait_for_format(run, "repo-ops-core:ephemeral", "#{pane_dead}", "1")
                self.assertEqual(expanded("window-status-current-format", "repo-ops-core:ephemeral"),
                                 "1:ephemeral")
                self.assertEqual(expanded("set-titles-string", "repo-ops-core:ephemeral"),
                                 "repo-ops: ephemeral")
                self.wait_for_output(master, b"\x1b]0;repo-ops: ephemeral\x07")
                self.assertEqual(run("select-window", "-t", "repo-ops-core:dotfiles").returncode, 0)
                self.wait_for_output(master, b"\x1b]0;repo-ops: dotfiles\x07")

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux") and shutil.which("zsh"),
                         "requires tmux and zsh on POSIX")
    def test_zsh_command_titles_for_wrapped_programs(self):
        self.require_theme_compatible_tmux()
        with self.theme_server() as (fixture, env, tmux, run):
            (fixture.home / ".zshrc").write_text(
                f"source {ROOT / 'dot_local/share/zsh/55-terminal-title.zsh.tmpl'}\n")
            env["ZDOTDIR"] = str(fixture.home)
            for name in ("git", "bd"):
                write_executable(fixture.fake_bin / name, "#!/bin/sh\nsleep 30\n")
            started = run("new-session", "-d", "-s", "repo-ops-core", "-n", "dotfiles", "zsh -i")
            self.assertEqual(started.returncode, 0, started.stderr)

            def expanded(option):
                fmt = run("show-option", "-gv", option).stdout.strip()
                return run("display-message", "-p", "-t", "repo-ops-core:dotfiles", fmt).stdout.strip()

            with self.attached_client(tmux, env, "repo-ops-core:dotfiles") as (master, _):
                self.wait_for_output(master, b"\x1b]0;repo-ops: dotfiles\x07")
                for name, invocation in (("git", "git push --follow-tags secret-token"),
                                         ("bd", "bd dolt push secret-token")):
                    os.write(master, (invocation + "\n").encode())
                    self.wait_for_format(run, "repo-ops-core:dotfiles", "#{@repo_ops_command}", name)
                    self.assertEqual(expanded("window-status-current-format"), f"0:dotfiles | {name}")
                    self.assertEqual(expanded("set-titles-string"), f"repo-ops: dotfiles | {name}")
                    self.wait_for_output(master, f"\x1b]0;repo-ops: dotfiles | {name}\x07".encode())
                    self.assertNotIn("secret-token", expanded("set-titles-string"))
                    os.write(master, b"\x03")
                    self.wait_for_format(run, "repo-ops-core:dotfiles", "#{@repo_ops_command}", "")
                    self.wait_for_output(master, b"\x1b]0;repo-ops: dotfiles\x07")

            self.assertEqual(run("new-session", "-d", "-s", "unrelated", "sleep 30").returncode, 0)
            self.assertEqual(run("display-message", "-p", "-t", "unrelated",
                                 "#{@repo_ops_command}").stdout.strip(), "")

            other_tmux = ["tmux", "-L", "other", "-f", str(fixture.home / ".config/tmux/repo-ops.conf")]

            def other(*args):
                return subprocess.run([*other_tmux, *args], env=env, capture_output=True, text=True)

            try:
                started = other("new-session", "-d", "-s", "ordinary", "-n", "shell", "zsh -i")
                self.assertEqual(started.returncode, 0, started.stderr)
                with self.attached_client(other_tmux, env, "ordinary") as (master, _):
                    self.wait_for_output(master, b"repo-ops: shell")
                    os.write(master, b"git push --follow-tags\n")
                    self.wait_for_output(master, b"git push --follow-tags")
                    self.assertEqual(other("display-message", "-p", "-t", "ordinary",
                                           "#{@repo_ops_command}").stdout.strip(), "")
                    os.write(master, b"\x03")
            finally:
                other("kill-server")

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux"), "requires tmux on POSIX")
    def test_command_prompt_and_confirmation_at_terminal_widths(self):
        self.require_theme_compatible_tmux()
        with self.theme_server() as (_, env, tmux, run):
            started = run("new-session", "-d", "-s", "repo-ops-core", "-n", "dotfiles", "sleep 120")
            self.assertEqual(started.returncode, 0, started.stderr)
            self.disable_paste_detection(run, "repo-ops-core")
            original_pid = self.tmux_format(run, "repo-ops-core:0.0", "#{pane_pid}")
            for columns in (100, 46):
                with self.attached_client(tmux, env, "repo-ops-core", columns) as (master, _):
                    self.wait_for_output(master, b"Layout: core")
                    os.write(master, b"\x02:")
                    self.wait_for_command_prompt(master)
                    os.write(master, b"display-message should-not-run")
                    self.wait_for_output(master, b":display-message should-not-run")
                    os.write(master, b"\x7f\x03")
                    self.wait_for_output(master, b"Layout: core")
                    self.assertEqual(self.tmux_format(run, "repo-ops-core:0.0", "#{pane_pid} #{pane_dead}"),
                                     f"{original_pid} 0", f"command prompt cancellation at {columns} columns")
                    os.write(master, b"\x02K")
                    self.wait_for_output(master, b"Kill session repo-ops-core")
                    os.write(master, b"n")
                    self.wait_for_output(master, b"Layout: core")
                    self.assertEqual(run("has-session", "-t", "repo-ops-core").returncode, 0)
                    self.assertEqual(self.tmux_format(run, "repo-ops-core:0.0", "#{pane_pid} #{pane_dead}"),
                                     f"{original_pid} 0", f"rejected kill at {columns} columns")

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux"), "requires tmux on POSIX")
    def test_reload_updates_existing_session_without_replacing_panes(self):
        self.require_theme_compatible_tmux()
        with self.theme_server() as (fixture, _, _, run):
            started = run("new-session", "-d", "-s", "repo-ops-core", "-n", "dotfiles", "sleep 120")
            self.assertEqual(started.returncode, 0, started.stderr)
            original = run("display-message", "-p", "-t", "repo-ops-core",
                           "#{session_id} #{pane_id}").stdout.strip()
            self.assertEqual(run("set-option", "-g", "status-left", "old label").returncode, 0)
            theme = fixture.home / ".config/tmux/repo-ops.conf"
            self.assertEqual(run("source-file", str(theme)).returncode, 0)
            self.assertIn("Layout: core", run("display-message", "-p", "-t", "repo-ops-core",
                                               run("show-option", "-gv", "status-left").stdout.strip()).stdout)
            self.assertEqual(run("display-message", "-p", "-t", "repo-ops-core",
                                 "#{session_id} #{pane_id}").stdout.strip(), original)

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux"), "requires tmux on POSIX")
    def test_confirmed_controls_keep_original_targets(self):
        self.require_theme_compatible_tmux()
        with self.theme_server() as (fixture, env, tmux, run):
            started = run("new-session", "-d", "-s", "repo-ops-core", "-n", "dotfiles", "sleep 120")
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertEqual(run("new-session", "-d", "-s", "repo-ops-other", "sleep 120").returncode, 0)
            self.disable_paste_detection(run, "repo-ops-core", "repo-ops-other")
            sibling = run("split-window", "-dP", "-F", "#{pane_id}", "-t", "repo-ops-core:0",
                          "sleep 120").stdout.strip()
            original = run("display-message", "-p", "-t", "repo-ops-core:0.0", "#{pane_id}").stdout.strip()
            self.assertNotEqual(original, sibling)
            original_pid = self.tmux_format(run, original, "#{pane_pid}")
            sibling_pid = self.tmux_format(run, sibling, "#{pane_pid}")
            original_session = self.tmux_format(run, original, "#{session_id}")
            self.assertEqual(run("set-option", "-p", "-t", original, "@repo_ops_role", "shell").returncode, 0)
            self.assertEqual(run("select-pane", "-t", original).returncode, 0)
            bindings = run("list-keys", "-T", "prefix")
            self.assertEqual(bindings.returncode, 0, bindings.stderr)
            descriptions = run("list-keys", "-N", "-T", "prefix")
            self.assertIn("Kill current repo-ops session", descriptions.stdout)
            self.assertIn("Restart current pane command", descriptions.stdout)
            for key in ("K", "R"):
                binding = next(line for line in bindings.stdout.splitlines()
                               if re.match(rf"bind-key\s+-T prefix {key}\s+confirm-before", line))
                self.assertIn("confirm-before", binding)
            self.assertIn("repo-ops focus", bindings.stdout)

            with self.attached_client(tmux, env, "repo-ops-core:0") as (master, _):
                self.wait_for_output(master, b"Layout: core")
                os.write(master, b"\x02R")
                self.wait_for_output(master, b"restart pane")
                os.write(master, b"n")
                self.wait_for_output(master, b"Layout: core")
                self.assertEqual(self.tmux_format(run, original, "#{pane_pid} #{pane_dead}"),
                                 f"{original_pid} 0")
                os.write(master, b"\x02R")
                self.wait_for_output(master, b"restart pane")
                self.assertEqual(run("select-pane", "-t", sibling).returncode, 0)
                self.assertEqual(run("set-option", "-p", "-t", original,
                                     "@repo_ops_command", "git").returncode, 0)
                os.write(master, b"y")
                self.wait_for_control_restart(run, original, original_pid)
                self.wait_for_format(run, original, "#{pane_current_command}", "sleep")
                self.assertEqual(self.tmux_format(run, original, "#{@repo_ops_command}"), "")
                self.assertEqual(self.tmux_format(run, original, "#{@repo_ops_role}"), "shell")
                self.assertEqual(self.tmux_format(run, sibling, "#{pane_pid} #{pane_dead}"),
                                  f"{sibling_pid} 0")
                self.assertEqual(self.tmux_format(run, original, "#{pane_id}"), original)
                self.assertEqual(self.tmux_format(run, original, "#{session_id}"), original_session)
                restarted_pid = self.tmux_format(run, original, "#{pane_pid}")
                os.write(master, b"\x02K")
                self.wait_for_output(master, b"Kill session repo-ops-core")
                os.write(master, b"n")
                self.wait_for_output(master, b"Layout: core")
                self.assertEqual(run("has-session", "-t", "repo-ops-core").returncode, 0)
                self.assertEqual(self.tmux_format(run, original, "#{pane_pid} #{pane_dead}"),
                                  f"{restarted_pid} 0")
                self.assertEqual(self.tmux_format(run, sibling, "#{pane_pid} #{pane_dead}"),
                                  f"{sibling_pid} 0")
                os.write(master, b"\x02K")
                self.wait_for_output(master, b"Kill session repo-ops-core")
                self.assertEqual(run("switch-client", "-t", "repo-ops-other").returncode, 0)
                os.write(master, b"y")
                self.wait_for_control_session_gone(run, original_session, "repo-ops-other")

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux"), "requires tmux on POSIX")
    def test_respawn_exited_launcher_retains_command(self):
        self.require_theme_compatible_tmux()
        with self.theme_server() as (fixture, _, _, run):
            launch_log = fixture.root / "launches"
            write_executable(fixture.fake_bin / "fixture-launcher",
                             "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$OPS_LAUNCH_LOG\"\nexit 19\n")
            started = run("new-session", "-d", "-s", "repo-ops-core", "-n", "container",
                          f"OPS_LAUNCH_LOG={launch_log} {fixture.fake_bin}/fixture-launcher container shell")
            self.assertEqual(started.returncode, 0, started.stderr)
            pane = run("display-message", "-p", "-t", "repo-ops-core", "#{pane_id}").stdout.strip()
            self.wait_for_format(run, pane, "#{pane_dead}", "1")
            self.assertEqual(launch_log.read_text().splitlines(), ["container shell"])
            self.assertEqual(run("set-option", "-p", "-t", pane, "@repo_ops_role", "shell").returncode, 0)
            self.assertEqual(run("respawn-pane", "-t", pane).returncode, 0)
            self.wait_for_format(run, pane, "#{pane_dead}", "1")
            self.assertEqual(launch_log.read_text().splitlines(), ["container shell", "container shell"])
            self.assertEqual(run("show-option", "-qpv", "-t", pane, "@repo_ops_role").stdout.strip(), "shell")

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux") and shutil.which("tmuxp"),
                         "requires tmux, tmuxp, and a POSIX PTY")
    def test_real_tmuxp_detached_load_and_repeat_attach(self):
        self.require_theme_compatible_tmux()
        with isolated_environment(prefix="repo-ops-live-") as fixture, tmux_environment(fixture) as env:
            theme = fixture.home / ".config/tmux/repo-ops.conf"
            theme.parent.mkdir(parents=True)
            theme.write_text((ROOT / "private_dot_config/tmux/repo-ops.conf").read_text())
            layouts = fixture.home / ".config/tmuxp"
            layouts.mkdir(parents=True)
            for layout in ("alpha", "beta"):
                (layouts / f"{layout}.yaml").write_text(
                    f"session_name: repo-ops-{layout}\n"
                    "windows:\n"
                    f"  - window_name: sample\n    start_directory: {fixture.home}\n"
                    "    panes:\n      - shell_command:\n          - repo-ops tag shell\n"
                )
            launcher = write_executable(fixture.fake_bin / "repo-ops", SOURCE.read_text())
            env.pop("TMUX_PANE", None)
            tmux = ["tmux", "-L", "repo-ops", "-f", str(theme)]

            def run_tmux(*args):
                return subprocess.run([*tmux, *args], env=env, capture_output=True, text=True)

            def attach(layout):
                master, slave = pty.openpty()
                process = subprocess.Popen([str(launcher), layout], env=env, stdin=slave,
                                           stdout=slave, stderr=slave)
                os.close(slave)
                output = b""
                try:
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        readable, _, _ = select.select([master], [], [], 0.1)
                        if readable:
                            try:
                                output += os.read(master, 65536)
                            except OSError:
                                pass
                        if process.poll() is not None:
                            break
                        sessions = run_tmux("list-sessions", "-F", "#{session_name}|#{session_id}|#{session_attached}")
                        attached = next((line.split("|")[1] for line in sessions.stdout.splitlines()
                                         if line.startswith(f"repo-ops-{layout}|") and line.endswith("|1")), None)
                        if attached:
                            detached = run_tmux("detach-client", "-s", attached)
                            self.assertEqual(detached.returncode, 0, detached.stderr)
                            break
                    process.wait(timeout=5)
                    sessions = run_tmux("list-sessions", "-F", "#{session_name} #{session_attached}")
                    self.assertEqual(process.returncode, 0,
                                     f"{output.decode(errors='replace')}\nsessions: {sessions.stdout!r} {sessions.stderr!r}")
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    os.close(master)

            attach("alpha")
            session_id = run_tmux("list-sessions", "-F", "#{session_name}|#{session_id}").stdout.strip().split("|")[1]
            first = run_tmux("list-panes", "-t", session_id, "-F", "#{pane_id} #{pane_dead}")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(run_tmux("show-option", "-qv", "-t", session_id,
                                      "@repo_ops_layout").stdout.strip(), str(layouts / "alpha.yaml"))
            attach("beta")
            self.assertEqual(len(run_tmux("list-sessions", "-F", "#{session_name}").stdout.splitlines()), 2)
            attach("alpha")
            self.assertEqual(run_tmux("list-panes", "-t", session_id, "-F", "#{pane_id} #{pane_dead}").stdout,
                             first.stdout)
            self.assertIn(f"repo-ops-alpha|{session_id}",
                          run_tmux("list-sessions", "-F", "#{session_name}|#{session_id}").stdout.splitlines())

    def prepare(self, fixture):
        home = fixture.home
        theme = home / ".config/tmux/repo-ops.conf"
        theme.parent.mkdir(parents=True)
        theme.write_text("set -g mouse on\n")
        layouts = home / ".config/tmuxp"
        layouts.mkdir(parents=True)
        for name in ("alpha", "beta"):
            (layouts / f"{name}.yaml").write_text("session_name: sample\n")
        launcher = write_executable(fixture.fake_bin / "repo-ops", SOURCE.read_text())
        stub = f"#!{sys.executable}\n" + r'''
import json, os, pathlib, sys
state_path = pathlib.Path(os.environ['OPS_STATE'])
log_path = pathlib.Path(os.environ['OPS_LOG'])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
with log_path.open('a') as stream:
    stream.write(json.dumps([pathlib.Path(sys.argv[0]).name, *args]) + '\n')
if pathlib.Path(sys.argv[0]).name == 'tmuxp':
    session = args[args.index('-s') + 1]
    if not os.environ.get('OPS_TMUXP_NO_SESSION'):
        state.setdefault(session, {'owner': '', 'panes': {}})
else:
    command = next((word for word in args if word in ('list-sessions', 'has-session', 'show-option', 'set-option', 'attach-session', 'display-message', 'list-panes', 'select-pane')), '')
    target = args[args.index('-t') + 1] if '-t' in args else ''
    sessions = [key for key in state if key.startswith('repo-ops-')]
    session = next((key for index, key in enumerate(sessions, 1) if target == f'${index}'), target.lstrip('='))
    if command == 'list-sessions':
        for index, key in enumerate(sessions, 1):
            print(f'{key}|${index}')
    elif command == 'has-session':
        if session not in state:
            sys.exit(1)
    elif command == 'show-option':
        if target.startswith('='):
            sys.exit(1)
        print(state.get(session, {}).get('owner', ''))
    elif command == 'set-option':
        if target.startswith('=') or os.environ.get('OPS_MARK_FAIL'):
            sys.stderr.write(f'no such session: {target}\n')
            sys.exit(1)
        if '-p' in args:
            state.setdefault('role_tags', []).append([target, args[-1]])
        else:
            state[session]['owner'] = args[-1]
    elif command == 'attach-session' and os.environ.get('OPS_ATTACH_FAIL'):
        sys.stderr.write('attach failed\n')
        sys.exit(1)
    elif command == 'display-message':
        if '-p' in args:
            print(os.environ.get('OPS_PANE_INFO', 'repo-ops-alpha|@1|shell|0'))
    elif command == 'list-panes':
        print(os.environ.get('OPS_PANES', '%1|shell|0\n%2|backlog|0'))
    elif command == 'select-pane':
        state['selected'] = target
state_path.write_text(json.dumps(state))
'''
        write_executable(fixture.fake_bin / "tmux", stub)
        write_executable(fixture.fake_bin / "tmuxp", stub)
        state = fixture.root / "state.json"
        state.write_text("{}")
        log = fixture.root / "calls.jsonl"
        env = dict(fixture.env, OPS_STATE=str(state), OPS_LOG=str(log), TMPDIR=str(fixture.root))
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        return launcher, layouts, state, log, env

    def run_ops(self, launcher, env, *args):
        return subprocess.run(["bash", str(launcher), *args], env=env, text=True,
                              capture_output=True, check=False)

    def test_help_lists_session_controls_without_starting_tmux(self):
        with isolated_environment() as fixture:
            launcher, _, state, log, env = self.prepare(fixture)
            for option in ("--help", "-h"):
                result = self.run_ops(launcher, dict(env, TMUX="/tmp/tmux-100/other,1,0"), option)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                for feature in ("repo-ops <layout>", "Ctrl+b, Shift+K", "Ctrl+b, Shift+R",
                                "Ctrl+b, G", "Ctrl+b, d", "exec zsh", "original launch command",
                                "status bar and terminal tab title"):
                    self.assertIn(feature, result.stdout)
                self.assertEqual(json.loads(state.read_text()), {})
                self.assertFalse(log.exists())
            invalid = self.run_ops(launcher, env, "--help", "alpha")
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("usage: repo-ops --help", invalid.stderr)
            self.assertFalse(log.exists())

    def test_distinct_sessions_and_repeat_attach(self):
        with isolated_environment() as fixture:
            launcher, _, state, log, env = self.prepare(fixture)
            for name in ("alpha", "alpha", "beta"):
                result = self.run_ops(launcher, env, name)
                self.assertEqual(result.returncode, 0, result.stderr)
            calls = read_json_lines(log)
            self.assertEqual(len([row for row in calls if row[0] == "tmuxp"]), 2)
            self.assertEqual(len([row for row in calls if "attach-session" in row]), 3)
            for row in calls:
                if any(command in row for command in ("show-option", "set-option", "attach-session")):
                    self.assertFalse(any(arg.startswith("=repo-ops-") for arg in row))
            contents = json.loads(state.read_text())
            self.assertTrue(contents["repo-ops-alpha"]["owner"].endswith("alpha.yaml"))
            self.assertTrue(contents["repo-ops-beta"]["owner"].endswith("beta.yaml"))

    def test_missing_invalid_nested_and_collision(self):
        with isolated_environment() as fixture:
            launcher, layouts, state, log, env = self.prepare(fixture)
            for name in ("../alpha", "unknown", "Alpha"):
                self.assertNotEqual(self.run_ops(launcher, env, name).returncode, 0)
            (layouts / "alpha.yaml").unlink()
            (layouts / "alpha.yaml").symlink_to(layouts / "beta.yaml")
            self.assertNotEqual(self.run_ops(launcher, env, "alpha").returncode, 0)
            (layouts / "alpha.yaml").unlink()
            (layouts / "alpha.yaml").write_text("session_name: sample\n")
            self.assertNotEqual(self.run_ops(launcher, dict(env, TMUX="/tmp/other,1,0"), "alpha").returncode, 0)
            state.write_text(json.dumps({"repo-ops-alpha": {"owner": "", "panes": {}}}))
            self.assertIn("collision", self.run_ops(launcher, env, "alpha").stderr)
            self.assertFalse(any(row[0] == "tmuxp" for row in read_json_lines(log)))

    def test_missing_session_and_mark_failure_do_not_attach(self):
        with isolated_environment() as fixture:
            launcher, _, state, log, env = self.prepare(fixture)
            missing = self.run_ops(launcher, dict(env, OPS_TMUXP_NO_SESSION="1"), "alpha")
            self.assertIn("tmuxp did not create repo-ops-alpha", missing.stderr)
            self.assertFalse(any("attach-session" in row for row in read_json_lines(log)))
            failed = self.run_ops(launcher, dict(env, OPS_MARK_FAIL="1"), "alpha")
            self.assertIn("could not mark repo-ops-alpha", failed.stderr)
            self.assertEqual(json.loads(state.read_text())["repo-ops-alpha"]["owner"], "")
            self.assertFalse(any("attach-session" in row for row in read_json_lines(log)))

    def test_failed_attach_preserves_owned_session_for_retry(self):
        with isolated_environment() as fixture:
            launcher, _, state, log, env = self.prepare(fixture)
            failure = self.run_ops(launcher, dict(env, OPS_ATTACH_FAIL="1"), "alpha")
            self.assertIn("could not attach repo-ops-alpha", failure.stderr)
            self.assertTrue(json.loads(state.read_text())["repo-ops-alpha"]["owner"].endswith("alpha.yaml"))
            self.assertEqual(self.run_ops(launcher, env, "alpha").returncode, 0)
            self.assertEqual(len([row for row in read_json_lines(log) if row[0] == "tmuxp"]), 1)

    def test_focus_uses_role_not_position_and_fails_closed(self):
        with isolated_environment() as fixture:
            launcher, _, state, log, env = self.prepare(fixture)
            config = fixture.home / ".config/tmuxp/alpha.yaml"
            state.write_text(json.dumps({"repo-ops-alpha": {"owner": str(config)}}))
            good = dict(env, OPS_PANES="%9||0\n%7|backlog|0\n%8|shell|0")
            result = self.run_ops(launcher, good, "focus", "%8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(state.read_text())["selected"], "%7")
            for panes in ("%7|backlog|1", "%7|backlog|0\n%6|backlog|0", "%9||0"):
                state.write_text(json.dumps({"repo-ops-alpha": {"owner": str(config)}}))
                self.assertNotEqual(self.run_ops(launcher, dict(env, OPS_PANES=panes), "focus", "%8").returncode, 0)
                self.assertNotIn("selected", json.loads(state.read_text()))
            self.assertFalse(any("select-pane" in row for row in read_json_lines(log)[-6:]))

    def test_tag_rejects_other_servers(self):
        with isolated_environment() as fixture:
            launcher, _, state, _, env = self.prepare(fixture)
            self.assertNotEqual(self.run_ops(launcher, env, "tag", "shell").returncode, 0)
            inside = dict(env, TMUX="/tmp/tmux-100/repo-ops,1,0", TMUX_PANE="%4")
            self.assertEqual(self.run_ops(launcher, inside, "tag", "shell").returncode, 0)
            self.assertEqual(json.loads(state.read_text())["role_tags"], [["%4", "shell"]])
            self.assertNotEqual(self.run_ops(launcher, dict(inside, TMUX="/tmp/tmux-100/default,1,0"), "tag", "shell").returncode, 0)

    def test_container_pane_requires_existing_container_and_never_opens_host_shell(self):
        with isolated_environment() as fixture:
            launcher, _, state, _, env = self.prepare(fixture)
            write_executable(
                fixture.fake_bin / "devcontainer-launch",
                "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$OPS_CONTAINER_LOG\"\nexit 73\n",
            )
            container_log = fixture.root / "container.log"
            inside = dict(env, TMUX="/tmp/tmux-100/repo-ops,1,0", TMUX_PANE="%4",
                          OPS_CONTAINER_LOG=str(container_log))
            result = self.run_ops(launcher, inside, "pane", "container", "sample", "shell", "--")
            self.assertEqual(result.returncode, 73, result.stderr)
            self.assertEqual(container_log.read_text().strip(), "sample exec --existing -- zsh -i")
            self.assertEqual(json.loads(state.read_text())["role_tags"], [["%4", "shell"]])
            self.assertNotEqual(self.run_ops(launcher, inside, "pane", "container", "../sample", "shell", "--").returncode, 0)
            self.assertEqual(len(container_log.read_text().splitlines()), 1)

    def test_pinned_uv_tool_only_installs_when_version_differs(self):
        source = (ROOT / ".chezmoiscripts/run_onchange_after_install_packages.sh.tmpl").read_text()
        segment = source.split("# 6. HYDRATE UV TOOLS", 1)[1].split('echo "Hydration Complete."', 1)[0]
        self.assertIn('uv tool install', segment)
        with isolated_environment() as fixture:
            config_dir = fixture.root / "configs"
            config_dir.mkdir()
            (config_dir / "uv_tools.txt").write_text("tmuxp==1.74.0\n")
            script = """set -e
resolve_native_command() { return 0; }
run_native_command() {
  if [[ "$1 $2" == 'uv tool' && "$3" == list ]]; then
    printf '%s\\n' "$INSTALLED_TOOLS"
  else
    printf '%s\\n' "$*" >> "$OPS_INSTALL_LOG"
  fi
}
""" + segment
            log = fixture.root / "install.log"
            env = dict(fixture.env, CONFIG_DIR=str(config_dir), OPS_INSTALL_LOG=str(log),
                       INSTALLED_TOOLS="tmuxp v1.74.0")
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(log.exists())
            result = subprocess.run(["bash", "-c", script], env=dict(env, INSTALLED_TOOLS="tmuxp v1.73.0"),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.read_text().strip(),
                             "uv tool install tmuxp==1.74.0 --force --no-build --no-python-downloads")
            missing_uv = script.replace('resolve_native_command() { return 0; }',
                                        'resolve_native_command() { return 1; }')
            log.unlink()
            result = subprocess.run(["bash", "-c", missing_uv], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("uv binary not found", result.stderr)
            self.assertFalse(log.exists())


if __name__ == "__main__":
    unittest.main()
