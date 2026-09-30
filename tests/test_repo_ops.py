from __future__ import annotations

import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, read_json_lines, write_executable

if os.name == "posix":
    import pty


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "bin/executable_repo-ops"


class RepoOpsTests(unittest.TestCase):
    def require_theme_compatible_tmux(self):
        version = subprocess.run(["tmux", "-V"], capture_output=True, text=True)
        match = re.search(r"(\d+)\.(\d+)", version.stdout)
        if version.returncode or not match or tuple(map(int, match.groups())) < (3, 3):
            self.skipTest("repo-ops theme requires tmux 3.3 or newer")

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux"), "requires tmux on POSIX")
    def test_dedicated_theme_and_titles(self):
        self.require_theme_compatible_tmux()
        with isolated_environment(prefix="repo-ops-theme-") as fixture:
            theme = fixture.home / ".config/tmux/repo-ops.conf"
            theme.parent.mkdir(parents=True)
            theme.write_text((ROOT / "private_dot_config/tmux/repo-ops.conf").read_text())
            env = dict(fixture.env, TMUX_TMPDIR=str(fixture.root / "tmp"))
            env.pop("TMUX", None)
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
            try:
                titles_format = option("set-titles-string")
                self.assertEqual(option("mouse"), "on")
                self.assertEqual(option("allow-rename"), "off")
                self.assertEqual(option("automatic-rename"), "off")
                self.assertEqual(option("set-titles"), "on")
                self.assertEqual(option("status-justify"), "absolute-centre")
                self.assertEqual(option("status-left-length"), "64")
                self.assertIn("#S", option("status-left"))
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
            finally:
                run_tmux("kill-server")

    @unittest.skipUnless(os.name == "posix" and shutil.which("tmux") and shutil.which("tmuxp"),
                         "requires tmux, tmuxp, and a POSIX PTY")
    def test_real_tmuxp_detached_load_and_repeat_attach(self):
        self.require_theme_compatible_tmux()
        with isolated_environment(prefix="repo-ops-live-") as fixture:
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
            env = dict(fixture.env, TMUX_TMPDIR=str(fixture.root / "tmp"))
            env.pop("TMUX", None)
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

            try:
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
            finally:
                run_tmux("kill-server")

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
