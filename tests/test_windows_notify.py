from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "dot_local/windows-notify.ps1"
TEMPLATE = ROOT / "private_dot_config/opencode/opencode-notifier.json.tmpl"
IGNORE = ROOT / ".chezmoiignore"


class WindowsNotifyTests(unittest.TestCase):
    def test_helper_has_noninteractive_data_only_contract(self) -> None:
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn("[string]$Title", text)
        self.assertIn("[AllowEmptyString()][string]$Message", text)
        self.assertIn("$text.Item(0).InnerText = $Title", text)
        self.assertIn("$text.Item(1).InnerText = $Message", text)
        self.assertIn("$audio.SetAttribute('silent', 'true')", text)
        self.assertNotIn("Wscript.Shell", text)
        self.assertNotIn("Popup(", text)

    def test_powershell_parser_accepts_helper_without_displaying_a_banner(self) -> None:
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell is unavailable")
        script = str(HELPER)
        if powershell.lower().endswith("powershell.exe"):
            wslpath = shutil.which("wslpath")
            if wslpath:
                script = subprocess.check_output([wslpath, "-w", script], text=True).strip()
        quoted = script.replace("'", "''")
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command",
             f"$tokens=$null; $errors=$null; [System.Management.Automation.Language.Parser]::ParseFile('{quoted}',[ref]$tokens,[ref]$errors) | Out-Null; if ($errors.Count) {{ $errors | Out-String | Write-Error; exit 1 }}"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("chezmoi"), "chezmoi is unavailable")
    def test_platform_rendering_preserves_macos_and_linux(self) -> None:
        cases = (
            ("windows", False, "powershell.exe"),
            ("darwin", False, "opencode-notifier-bridge"),
            ("linux", True, "opencode-notifier-bridge"),
            ("linux", False, "powershell.exe"),
        )
        for platform, is_wsl2, path in cases:
            with self.subTest(platform=platform, is_wsl2=is_wsl2):
                data = {"chezmoi": {"os": platform, "homeDir": "C:\\Users\\Test", "kernel": {"osrelease": "6.1.0"}}, "isWSL2": is_wsl2}
                result = subprocess.run(
                    ["chezmoi", "--source", str(ROOT), "execute-template",
                     "--override-data", json.dumps(data), "-f", str(TEMPLATE)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                rendered = json.loads(result.stdout)
                self.assertEqual(rendered["command"]["path"], path)
                args = rendered["command"]["args"]
                if platform == "windows":
                    self.assertIn("C:/Users/Test/.local/windows-notify.ps1", args)
                    self.assertNotIn("-Command", args)
                    self.assertEqual(args[args.index("-ExecutionPolicy") + 1], "Bypass")
                    self.assertNotIn("bash", json.dumps(rendered))
                elif platform == "darwin":
                    self.assertEqual(args, ["--title", "OpenCode - {event}", "--message", "{message}", "--event", "{event}"])
                self.assertEqual(rendered["suppressWhenFocused"], platform != "darwin")
                self.assertFalse(rendered["sound"])
                self.assertFalse(rendered["bell"])
                for event in ("user_message", "subagent_complete", "user_cancelled", "session_started", "client_connected"):
                    self.assertFalse(rendered["events"][event]["command"])

    @unittest.skipUnless(shutil.which("chezmoi"), "chezmoi is unavailable")
    def test_helper_ignore_rules_select_only_wsl2_host_and_windows(self) -> None:
        clean_env = {key: value for key, value in os.environ.items() if key != "DEVCONTAINER"}
        host_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
        for platform, is_wsl2, is_container, expected_ignored in (
            ("windows", False, False, False),
            ("darwin", False, False, True),
            ("linux", True, False, False),
            ("linux", True, True, True),
            ("linux", False, False, True),
        ):
            with self.subTest(platform=platform, is_wsl2=is_wsl2, is_container=is_container):
                if host_container and platform == "linux" and is_wsl2 and not is_container:
                    continue
                data = {"chezmoi": {"os": platform, "kernel": {"osrelease": "6.1.0"}}, "isWSL2": is_wsl2, "isDevcontainer": is_container}
                result = subprocess.run(
                    ["chezmoi", "--source", str(ROOT), "execute-template",
                     "--override-data", json.dumps(data), "-f", str(IGNORE)],
                    env=clean_env, capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                rules = result.stdout.splitlines()
                self.assertEqual(".local/windows-notify.ps1" in rules, expected_ignored)
                self.assertEqual("!/.local/windows-notify.ps1" in rules, platform == "windows")

        result = subprocess.run(
            ["chezmoi", "--source", str(ROOT), "execute-template", "--override-data",
             json.dumps({"chezmoi": {"os": "linux"}, "isWSL2": True}), "-f", str(IGNORE)],
            env={**os.environ, "DEVCONTAINER": "1"}, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(".local/windows-notify.ps1", result.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
