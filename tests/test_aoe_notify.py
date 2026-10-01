from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "bin/executable_aoe-notify.tmpl"


def run_notify(script: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def write_argv_logger(path: Path, log: Path, *, exit_code: int = 0) -> Path:
    return write_executable(
        path,
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "log.parent.mkdir(parents=True, exist_ok=True)\n"
        "log.write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        f"raise SystemExit({exit_code})\n",
    )


class AoeNotifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SOURCE.read_text(encoding="utf-8")

    def prepare(self, fixture) -> tuple[Path, dict[str, str]]:
        script = write_executable(fixture.root / "aoe-notify", self.source)
        for name in ("curl", "terminal-notifier", "osascript", "powershell.exe", "notify-send"):
            write_argv_logger(
                fixture.fake_bin / name,
                fixture.root / f"backend-{name}.json",
                exit_code=1,
            )
        env = fixture.env.copy()
        for key in tuple(env):
            if key.startswith("AOE_") or key in {"DEVCONTAINER", "DEV_NOTIFY_BRIDGE"}:
                env.pop(key, None)
        env["XDG_CACHE_HOME"] = str(fixture.root / "cache with spaces")
        return script, env

    def read_log(self, fixture) -> str:
        return (fixture.root / "cache with spaces/aoe-notify.log").read_text(
            encoding="utf-8"
        )

    def force_explicit_failed_bridge(self, fixture, env: dict[str, str]) -> None:
        write_executable(fixture.fake_bin / "curl", "#!/bin/sh\nexit 22\n")
        env["AOE_NOTIFY_BRIDGE_URL"] = "http://127.0.0.1:9/notify"

    def test_irrelevant_status_is_logged_without_invoking_a_backend(self) -> None:
        with isolated_environment(prefix="aoe ignored ") as fixture:
            script, env = self.prepare(fixture)
            marker = fixture.root / "backend-called"
            for name in ("curl", "terminal-notifier", "osascript", "powershell.exe", "notify-send"):
                write_executable(
                    fixture.fake_bin / name,
                    f"#!/bin/sh\ntouch {str(marker)!r}\nexit 0\n",
                )

            result = run_notify(script, "Running", env=env | {"AOE_NOTIFY_DEBUG": "1"})

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            self.assertIn("ignored status: running", result.stderr)
            self.assertIn("status=running backend=ignored rc=0", self.read_log(fixture))

    def test_bridge_normalizes_endpoint_and_json_escapes_message(self) -> None:
        with isolated_environment(prefix="aoe bridge ") as fixture:
            script, env = self.prepare(fixture)
            curl_log = fixture.root / "curl.json"
            write_argv_logger(fixture.fake_bin / "curl", curl_log)
            env.update(
                {
                    "AOE_NOTIFY_BRIDGE_URL": "notify.example.test:6789/",
                    "AOE_SESSION_TITLE": 'Review "quoted"\nwork',
                    "AOE_PROJECT_PATH": "/tmp/project with spaces",
                }
            )

            result = run_notify(script, "WAITING", env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            argv = json.loads(curl_log.read_text(encoding="utf-8"))
            self.assertEqual(argv[-1], "http://notify.example.test:6789/notify")
            payload = argv[argv.index("--data") + 1]
            self.assertEqual(
                json.loads(payload),
                {
                    "title": "AoE: Waiting",
                    "message": 'Review "quoted"\nwork is waiting for input in project with spaces',
                },
            )
            self.assertIn("status=waiting backend=bridge rc=0", self.read_log(fixture))

    def test_terminal_notifier_is_preferred_and_error_adds_sound(self) -> None:
        with isolated_environment(prefix="aoe terminal notifier ") as fixture:
            script, env = self.prepare(fixture)
            self.force_explicit_failed_bridge(fixture, env)
            write_executable(fixture.fake_bin / "uname", "#!/bin/sh\nprintf 'Darwin\\n'\n")
            terminal_log = fixture.root / "terminal.json"
            write_argv_logger(fixture.fake_bin / "terminal-notifier", terminal_log)
            osascript_marker = fixture.root / "osascript-called"
            write_executable(
                fixture.fake_bin / "osascript",
                f"#!/bin/sh\ntouch {str(osascript_marker)!r}\nexit 0\n",
            )

            result = run_notify(
                script,
                "error",
                env=env | {"AOE_SESSION_TITLE": "Session; literal"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            argv = json.loads(terminal_log.read_text(encoding="utf-8"))
            self.assertEqual(argv[argv.index("-title") + 1], "AoE: Error")
            self.assertEqual(argv[argv.index("-message") + 1], "Session; literal hit an error")
            self.assertEqual(argv[-2:], ["-sound", "Basso"])
            self.assertFalse(osascript_marker.exists())
            self.assertIn("backend=terminal-notifier rc=0", self.read_log(fixture))

    def test_macos_fallback_preserves_osascript_positional_arguments(self) -> None:
        with isolated_environment(prefix="aoe osascript ") as fixture:
            script, env = self.prepare(fixture)
            self.force_explicit_failed_bridge(fixture, env)
            write_executable(fixture.fake_bin / "uname", "#!/bin/sh\nprintf 'Darwin\\n'\n")
            write_executable(fixture.fake_bin / "terminal-notifier", "#!/bin/sh\nexit 1\n")
            log = fixture.root / "osascript.json"
            write_argv_logger(fixture.fake_bin / "osascript", log)

            result = run_notify(
                script,
                "waiting",
                env=env | {"AOE_SESSION_TITLE": "A title with 'quotes'"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            argv = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(argv[-2], "AoE: Waiting")
            self.assertEqual(argv[-1], "A title with 'quotes' is waiting for input")
            self.assertIn("backend=osascript rc=0", self.read_log(fixture))

    def test_powershell_banner_keeps_arguments_as_data(self) -> None:
        with isolated_environment(prefix="aoe powershell ") as fixture:
            script, env = self.prepare(fixture)
            self.force_explicit_failed_bridge(fixture, env)
            write_executable(fixture.fake_bin / "uname", "#!/bin/sh\nprintf 'Linux\\n'\n")
            log = fixture.root / "powershell.json"
            write_argv_logger(fixture.fake_bin / "powershell.exe", log)
            helper = fixture.home / ".local/windows-notify.ps1"
            helper.parent.mkdir(parents=True)
            helper.write_text("", encoding="utf-8")
            write_executable(fixture.fake_bin / "wslpath", "#!/bin/sh\nprintf '%s' 'C:/windows-notify.ps1'\n")

            result = run_notify(
                script,
                "waiting",
                env=env | {"AOE_SESSION_TITLE": "Agent's <work>\n雪"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            argv = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(argv[argv.index("-File") + 1], "C:/windows-notify.ps1")
            self.assertEqual(argv[argv.index("-Message") + 1], "Agent's <work>\n雪 is waiting for input")
            self.assertNotIn("-Command", argv)
            self.assertIn("backend=powershell-banner rc=0", self.read_log(fixture))

    def test_wsl2_opencode_hooks_are_suppressed_without_affecting_other_agents(self) -> None:
        with isolated_environment(prefix="aoe ownership ") as fixture:
            script, env = self.prepare(fixture)
            self.force_explicit_failed_bridge(fixture, env)
            backend = fixture.root / "powershell.json"
            write_argv_logger(fixture.fake_bin / "powershell.exe", backend)
            helper = fixture.home / ".local/windows-notify.ps1"
            helper.parent.mkdir(parents=True)
            helper.write_text("", encoding="utf-8")
            write_executable(fixture.fake_bin / "wslpath", "#!/bin/sh\nprintf 'C:/windows-notify.ps1'\n")
            for tool in ("opencode", "opencode-custom"):
                for status in ("waiting", "error"):
                    result = run_notify(script, status, env=env | {
                        "AOE_TOOL": tool, "AOE_SESSION_ID": "s-123",
                    })
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(backend.exists())
                    self.assertIn(f"status={status} backend=suppressed-opencode rc=0", self.read_log(fixture))
                    self.assertIn("session_id=s-123 tool=" + tool, self.read_log(fixture))
            for tool in ("claude", "", "opencode-lookalike"):
                result = run_notify(script, "waiting", env=env | {"AOE_TOOL": tool})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(backend.exists())
                backend.unlink()

    def test_devcontainer_retains_aoe_delivery(self) -> None:
        with isolated_environment(prefix="aoe container ") as fixture:
            script, env = self.prepare(fixture)
            bridge = fixture.root / "bridge.json"
            write_argv_logger(fixture.fake_bin / "curl", bridge)
            result = run_notify(script, "error", env=env | {
                "AOE_TOOL": "opencode", "DEVCONTAINER": "1",
                "AOE_NOTIFY_BRIDGE_URL": "http://localhost:6789/notify",
            })
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(bridge.exists())

    def test_notify_send_uses_critical_urgency_for_errors(self) -> None:
        with isolated_environment(prefix="aoe notify send ") as fixture:
            script, env = self.prepare(fixture)
            self.force_explicit_failed_bridge(fixture, env)
            write_executable(fixture.fake_bin / "uname", "#!/bin/sh\nprintf 'Linux\\n'\n")
            log = fixture.root / "notify-send.json"
            write_argv_logger(fixture.fake_bin / "notify-send", log)

            result = run_notify(script, "error", env=env | {"DEVCONTAINER": "1"})

            self.assertEqual(result.returncode, 0, result.stderr)
            argv = json.loads(log.read_text(encoding="utf-8"))
            self.assertIn("--urgency=critical", argv)
            self.assertFalse((fixture.root / "backend-powershell.exe.json").exists())
            self.assertEqual(argv[-2:], ["AoE: Error", "An Agent of Empires session hit an error"])
            self.assertIn("backend=notify-send rc=0", self.read_log(fixture))

    def test_all_backend_failures_are_logged_but_do_not_fail_hook(self) -> None:
        with isolated_environment(prefix="aoe fail open ") as fixture:
            script, env = self.prepare(fixture)
            self.force_explicit_failed_bridge(fixture, env)
            write_executable(fixture.fake_bin / "uname", "#!/bin/sh\nprintf 'Linux\\n'\n")
            write_executable(fixture.fake_bin / "notify-send", "#!/bin/sh\nexit 8\n")

            result = run_notify(script, "waiting", env=env | {"AOE_NOTIFY_DEBUG": "yes"})

            self.assertEqual(result.returncode, 0)
            self.assertFalse((fixture.root / "backend-powershell.exe.json").exists())
            self.assertIn("Windows banner backend failed", result.stderr)
            self.assertIn("status=waiting backend=none rc=1", self.read_log(fixture))

    def test_wsl2_banner_failure_does_not_fallback_to_notify_send(self) -> None:
        with isolated_environment(prefix="aoe banner failure ") as fixture:
            script, env = self.prepare(fixture)
            self.force_explicit_failed_bridge(fixture, env)
            marker = fixture.root / "notify-send.json"
            write_argv_logger(fixture.fake_bin / "notify-send", marker)
            helper = fixture.home / ".local/windows-notify.ps1"
            helper.parent.mkdir(parents=True)
            helper.write_text("", encoding="utf-8")
            write_executable(fixture.fake_bin / "wslpath", "#!/bin/sh\nprintf 'C:/windows-notify.ps1'\n")

            result = run_notify(script, "waiting", env=env | {"AOE_TOOL": "claude"})

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            self.assertIn("backend=none rc=1", self.read_log(fixture))


if __name__ == "__main__":
    unittest.main()
