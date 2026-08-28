from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
NFS_SOURCE = REPO_ROOT / "bin/executable_nfs_dot_clean.sh"
VNC_SOURCE = REPO_ROOT / "bin/executable_vnc_monitor.sh"


def run_script(
    script: Path,
    *args: str,
    env: dict[str, str],
    timeout: float = 10,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def json_lines(path: Path) -> list[object]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class NfsDotCleanTests(unittest.TestCase):
    def prepare(self, fixture) -> tuple[Path, Path, Path, dict[str, str]]:
        script = write_executable(fixture.root / "nfs-dot-clean", NFS_SOURCE.read_text())
        paths_file = fixture.root / "paths with spaces"
        call_log = fixture.root / "dot-clean.jsonl"
        mount = write_executable(
            fixture.fake_bin / "mount-fixture",
            "#!/bin/sh\n"
            "printf '%s\\n' 'server:/data on /Volumes/Test Share (nfs, nodev)'\n",
        )
        dot_clean = write_executable(
            fixture.fake_bin / "dot-clean-fixture",
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            f"path = pathlib.Path({str(call_log)!r})\n"
            "with path.open('a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n",
        )
        env = fixture.env | {
            "NFS_DOT_CLEAN_PATHS_FILE": str(paths_file),
            "NFS_DOT_CLEAN_MOUNT_BIN": str(mount),
            "NFS_DOT_CLEAN_BIN": str(dot_clean),
            "XDG_STATE_HOME": str(fixture.root / "state with spaces"),
        }
        return script, paths_file, call_log, env

    def state_dir(self, fixture) -> Path:
        return fixture.root / "state with spaces/nfs-dot-clean"

    def test_cleans_only_configured_paths_under_nfs_mounts(self) -> None:
        with isolated_environment(prefix="nfs paths ") as fixture:
            script, paths_file, call_log, env = self.prepare(fixture)
            home_path = fixture.home / "mounted child"
            paths_file.write_text(
                "# fixture paths\n"
                "  /Volumes/Test Share/project/  \n"
                f"{home_path}\n"
                "/Volumes/Other/not-nfs\n",
                encoding="utf-8",
            )
            mount = Path(env["NFS_DOT_CLEAN_MOUNT_BIN"])
            mount.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' 'server:/data on /Volumes/Test Share (nfs, nodev)' "
                f"'server:/home on {fixture.home} (nfs)'\n",
                encoding="utf-8",
            )

            result = run_script(script, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json_lines(call_log),
                [["-m", "/Volumes/Test Share/project"], ["-m", str(home_path)]],
            )
            self.assertFalse((self.state_dir(fixture) / "lock").exists())
            self.assertFalse((self.state_dir(fixture) / "dot-clean.pid").exists())

    def test_active_lock_and_previous_child_skip_without_invocation(self) -> None:
        with isolated_environment(prefix="nfs active ") as fixture:
            script, paths_file, call_log, env = self.prepare(fixture)
            paths_file.write_text("/Volumes/Test Share/project\n", encoding="utf-8")
            lock = self.state_dir(fixture) / "lock"
            lock.mkdir(parents=True)
            (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

            locked = run_script(script, env=env)
            self.assertEqual(locked.returncode, 0, locked.stderr)
            self.assertFalse(call_log.exists())

            (lock / "pid").write_text("99999999\n", encoding="utf-8")
            pid_file = self.state_dir(fixture) / "dot-clean.pid"
            pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
            previous_child = run_script(script, env=env)
            self.assertEqual(previous_child.returncode, 0, previous_child.stderr)
            self.assertFalse(call_log.exists())
            self.assertFalse(lock.exists())
            self.assertEqual(pid_file.read_text(encoding="utf-8"), f"{os.getpid()}\n")

    def test_stale_lock_is_recovered_and_failed_output_is_redacted_to_path(self) -> None:
        with isolated_environment(prefix="nfs stale ") as fixture:
            script, paths_file, _call_log, env = self.prepare(fixture)
            configured_path = "/Volumes/Test Share/synthetic"
            paths_file.write_text(configured_path + "\n", encoding="utf-8")
            lock = self.state_dir(fixture) / "lock"
            lock.mkdir(parents=True)
            (lock / "pid").write_text("99999999\n", encoding="utf-8")
            dot_clean = Path(env["NFS_DOT_CLEAN_BIN"])
            dot_clean.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' 'Operation not permitted' >&2\n"
                "exit 7\n",
                encoding="utf-8",
            )

            result = run_script(script, env=env)

            self.assertEqual(result.returncode, 1)
            self.assertIn(f"dot_clean failed for: {configured_path}", result.stderr)
            self.assertIn("grant Full Disk Access", result.stderr)
            self.assertFalse(lock.exists())
            self.assertFalse((self.state_dir(fixture) / "dot-clean.pid").exists())
            self.assertEqual(list(self.state_dir(fixture).glob("*.err")), [])

    def test_timeout_terminates_child_and_cleans_runtime_state(self) -> None:
        with isolated_environment(prefix="nfs timeout ") as fixture:
            script, paths_file, _call_log, env = self.prepare(fixture)
            configured_path = "/Volumes/Test Share/slow"
            paths_file.write_text(configured_path + "\n", encoding="utf-8")
            dot_clean = Path(env["NFS_DOT_CLEAN_BIN"])
            dot_clean.write_text(
                "#!/bin/sh\ntrap 'exit 0' TERM INT\nwhile :; do sleep 1; done\n",
                encoding="utf-8",
            )
            env["NFS_DOT_CLEAN_TIMEOUT_SECONDS"] = "0"

            result = run_script(script, env=env)

            self.assertEqual(result.returncode, 1)
            self.assertIn("timed out after 0s", result.stderr)
            self.assertFalse((self.state_dir(fixture) / "lock").exists())
            self.assertFalse((self.state_dir(fixture) / "dot-clean.pid").exists())
            self.assertEqual(list(self.state_dir(fixture).glob("*.err")), [])

    def test_missing_tool_fails_but_missing_paths_file_is_a_noop(self) -> None:
        with isolated_environment(prefix="nfs missing ") as fixture:
            script, paths_file, call_log, env = self.prepare(fixture)

            missing_paths = run_script(script, env=env)
            self.assertEqual(missing_paths.returncode, 0, missing_paths.stderr)
            self.assertFalse(call_log.exists())

            paths_file.write_text("/Volumes/Test Share/project\n", encoding="utf-8")
            env["NFS_DOT_CLEAN_BIN"] = str(fixture.root / "missing dot-clean")
            missing_tool = run_script(script, env=env)
            self.assertEqual(missing_tool.returncode, 1)
            self.assertIn("not found or not executable", missing_tool.stderr)
            self.assertFalse((self.state_dir(fixture) / "lock").exists())


class VncMonitorTests(unittest.TestCase):
    def prepare(self, fixture) -> tuple[Path, Path, dict[str, str]]:
        script = write_executable(fixture.root / "vnc-monitor", VNC_SOURCE.read_text())
        command_log = fixture.root / "vnc-commands.jsonl"
        idle_state = fixture.root / "idle-state"
        dock_state = fixture.root / "dock-state"
        netstat_count = fixture.root / "netstat-count"
        idle_state.write_text("600", encoding="utf-8")
        dock_state.write_text("true", encoding="utf-8")

        write_executable(
            fixture.fake_bin / "defaults",
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            f"state = pathlib.Path({str(idle_state)!r})\n"
            f"log = pathlib.Path({str(command_log)!r})\n"
            "with log.open('a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(['defaults', *sys.argv[1:]]) + '\\n')\n"
            "if 'read-type' in sys.argv:\n"
            "    print('Type is integer')\n"
            "elif 'write' in sys.argv:\n"
            "    state.write_text(sys.argv[-1], encoding='utf-8')\n"
            "else:\n"
            "    print(state.read_text(encoding='utf-8'))\n",
        )
        write_executable(
            fixture.fake_bin / "osascript",
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            f"state = pathlib.Path({str(dock_state)!r})\n"
            f"log = pathlib.Path({str(command_log)!r})\n"
            "with log.open('a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(['osascript', *sys.argv[1:]]) + '\\n')\n"
            "statement = sys.argv[-1]\n"
            "if ' to get ' in statement:\n"
            "    print(state.read_text(encoding='utf-8'))\n"
            "elif statement.endswith('true'):\n"
            "    state.write_text('true', encoding='utf-8')\n"
            "elif statement.endswith('false'):\n"
            "    state.write_text('false', encoding='utf-8')\n",
        )
        write_executable(
            fixture.fake_bin / "killall",
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            f"with pathlib.Path({str(command_log)!r}).open('a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(['killall', *sys.argv[1:]]) + '\\n')\n",
        )
        write_executable(
            fixture.fake_bin / "netstat",
            f"#!{sys.executable}\n"
            "import pathlib\n"
            f"count_path = pathlib.Path({str(netstat_count)!r})\n"
            "count = int(count_path.read_text()) if count_path.exists() else 0\n"
            "count_path.write_text(str(count + 1))\n"
            "if count < 2:\n"
            "    print('tcp4 0 0 127.0.0.1.5900 127.0.0.1.50000 ESTABLISHED')\n",
        )
        write_executable(
            fixture.fake_bin / "caffeinate",
            f"#!{sys.executable}\n"
            "import json, pathlib, signal, sys\n"
            f"log = pathlib.Path({str(command_log)!r})\n"
            "with log.open('a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(['caffeinate', *sys.argv[1:]]) + '\\n')\n"
            "if '-d' in sys.argv:\n"
            "    signal.signal(signal.SIGTERM, lambda *_: raise_exit())\n"
            "    def wait_forever():\n"
            "        while True:\n"
            "            signal.pause()\n"
            "    def raise_exit():\n"
            "        raise SystemExit(0)\n"
            "    wait_forever()\n",
        )
        write_executable(fixture.fake_bin / "ps", "#!/bin/sh\nexit 1\n")
        write_executable(fixture.fake_bin / "sleep", "#!/bin/sh\n/bin/sleep 0.05\n")

        env = fixture.env | {
            "VNC_MONITOR_RESTORE_TIMEOUT": "600",
            "VNC_MONITOR_CHECK_INTERVAL": "0",
            "VNC_MONITOR_MAX_CHECKS": "3",
            "VNC_MONITOR_PID_FILE": str(fixture.root / "vnc state/caffeinate.pid"),
        }
        return script, command_log, env

    def test_connected_repeated_and_disconnected_transitions_are_idempotent(self) -> None:
        with isolated_environment(prefix="vnc transitions ") as fixture:
            script, command_log, env = self.prepare(fixture)
            Path(env["VNC_MONITOR_PID_FILE"]).parent.mkdir(parents=True)

            result = run_script(script, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = json_lines(command_log)
            caffeinate_calls = [call for call in calls if call[0] == "caffeinate"]
            self.assertEqual(caffeinate_calls.count(["caffeinate", "-d", "-i"]), 1)
            self.assertEqual(caffeinate_calls.count(["caffeinate", "-u", "-t", "5"]), 2)
            defaults_writes = [call for call in calls if call[:5] == ["defaults", "-currentHost", "write", "com.apple.screensaver", "idleTime"]]
            self.assertEqual([call[-1] for call in defaults_writes], ["0", "600"])
            dock_sets = [call[-1] for call in calls if call[0] == "osascript" and " to set " in call[-1]]
            self.assertEqual(dock_sets, [
                'tell application "System Events" to set autohide of dock preferences to false',
                'tell application "System Events" to set autohide of dock preferences to true',
            ])
            self.assertFalse(Path(env["VNC_MONITOR_PID_FILE"]).exists())

    def test_cleanup_uses_overridden_state_path_and_restores_preferences(self) -> None:
        with isolated_environment(prefix="vnc cleanup ") as fixture:
            script, command_log, env = self.prepare(fixture)
            env["VNC_MONITOR_RESTORE_TIMEOUT"] = "321"
            (fixture.root / "idle-state").write_text("0", encoding="utf-8")
            (fixture.root / "dock-state").write_text("false", encoding="utf-8")
            pid_file = Path(env["VNC_MONITOR_PID_FILE"])
            pid_file.parent.mkdir(parents=True)
            sleeper = subprocess.Popen(["/bin/sleep", "30"])
            self.addCleanup(lambda: sleeper.poll() is None and sleeper.terminate())
            pid_file.write_text(f"{sleeper.pid}\n", encoding="utf-8")

            result = run_script(script, "--cleanup", env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            sleeper.wait(timeout=2)
            self.assertFalse(pid_file.exists())
            calls = json_lines(command_log)
            defaults_writes = [call for call in calls if call[0] == "defaults" and "write" in call]
            self.assertEqual(defaults_writes[-1][-1], "321")
            self.assertTrue(any(call[0] == "osascript" and call[-1].endswith("true") for call in calls))

    def test_stale_pid_is_discarded_without_signalling_unrelated_processes(self) -> None:
        with isolated_environment(prefix="vnc stale ") as fixture:
            script, _command_log, env = self.prepare(fixture)
            env["VNC_MONITOR_MAX_CHECKS"] = "1"
            pid_file = Path(env["VNC_MONITOR_PID_FILE"])
            pid_file.parent.mkdir(parents=True)
            pid_file.write_text("99999999\n", encoding="utf-8")

            result = run_script(script, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(pid_file.exists())

    @unittest.skipUnless(sys.platform == "darwin", "macOS host smoke")
    def test_host_bash_parses_maintenance_sources_without_execution(self) -> None:
        for source in (NFS_SOURCE, VNC_SOURCE):
            result = subprocess.run(
                ["/bin/bash", "-n", str(source)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, f"{source}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
