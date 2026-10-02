from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, write_executable
from tests.support.powershell import pester_version, resolve_powershell_runtime, run_pester
from tests.test_chezmoi_lifecycle_render import CHEZMOI, render_template


REPO_ROOT = Path(__file__).resolve().parents[1]
COMMAND = REPO_ROOT / "bin" / "executable_ffpwa-refresh"
MACOS_HOOK = ".chezmoiscripts/run_after_macos-ffpwa-runtime.sh.tmpl"
WINDOWS_HOOK = ".chezmoiscripts/run_after_windows-ffpwa-runtime.ps1.tmpl"
PESTER_TEST = REPO_ROOT / "tests" / "powershell" / "FfpwaRefresh.Tests.ps1"
PWSH = resolve_powershell_runtime()
PESTER = pester_version(PWSH) if PWSH else None
SITES = (
    ("01JKR0YG7ESP2E7XHWQ8P92V3C", "Toodledo", "https://www.toodledo.com/signin.php"),
    ("01JKR2ZM8G31NPX0GJC8MRCMZT", "BookFusion", "https://www.bookfusion.com/bookshelf"),
    ("01JKR3661HQ8222BBVWCNCYTDN", "Wanderlog", "https://wanderlog.com/home"),
)
PROFILE_LIST = (
    "========================= Default ==========================\n"
    "Description: * Nothing *\n"
    "ID: 00000000000000000000000000\n"
    "\n"
    "========================= Apps =========================\n"
    "Description: * Nothing *\n"
    "ID: 01JKR0YG6XK739XZ9QYWTNKGEJ\n"
    "\n"
    "Apps:\n"
    + "".join(f"- {name}: {url} ({ulid})\n" for ulid, name, url in SITES)
)
FAKE_FIREFOXPWA = f"""#!{sys.executable}
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
if args == ["profile", "list"]:
    sys.stdout.write({PROFILE_LIST!r})
    raise SystemExit(0)
if args == ["runtime", "install"]:
    code = int(os.environ.get("FAKE_INSTALL_EXIT", "0"))
    if code == 0:
        ini = pathlib.Path(os.environ["FAKE_RUNTIME_INI"])
        ini.parent.mkdir(parents=True, exist_ok=True)
        ini.write_text("[App]\\nVersion=" + os.environ["FAKE_NEW_RUNTIME_VERSION"] + "\\n", encoding="utf-8")
    raise SystemExit(code)
if args[:2] == ["site", "update"]:
    raise SystemExit(7 if args[2] == os.environ.get("FAKE_FAIL_SITE") else 0)
raise SystemExit(99)
"""
FAKE_PGREP = f"""#!{sys.executable}
import json, os, sys
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(["pgrep", *sys.argv[1:]]) + "\\n")
raise SystemExit(int(os.environ.get("FAKE_PGREP_EXIT", "1")))
"""


def write_ini(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[App]\nVendor=Mozilla\nName=Firefox\nVersion={version}\nBuildID=1\n", encoding="utf-8")


@unittest.skipIf(os.name == "nt", "bash fixtures are POSIX-only")
class FfpwaRefreshCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        context = isolated_environment(prefix="ffpwa-refresh-")
        self.fixture = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        root = self.fixture.root
        self.log = root / "calls.jsonl"
        self.runtime_ini = (
            self.fixture.home
            / "Library/Application Support/firefoxpwa/runtime/Firefox.app/Contents/Resources/application.ini"
        )
        self.firefox_app = root / "Applications" / "Firefox.app"
        self.firefox_ini = self.firefox_app / "Contents/Resources/application.ini"
        write_executable(self.fixture.fake_bin / "firefoxpwa", FAKE_FIREFOXPWA)
        write_executable(self.fixture.fake_bin / "pgrep", FAKE_PGREP)
        self.env = dict(self.fixture.env)
        self.env.update(
            {
                "PATH": f"{self.fixture.fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
                "FFPWA_REFRESH_FIREFOX_DIR": str(self.firefox_app),
                "FAKE_LOG": str(self.log),
                "FAKE_RUNTIME_INI": str(self.runtime_ini),
                "FAKE_NEW_RUNTIME_VERSION": "157.0.1",
            }
        )

    def run_command(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(COMMAND), *args],
            env=self.env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def site_updates(self) -> list[str]:
        return [call[2] for call in self.calls() if call[:2] == ["site", "update"]]

    def assert_no_install(self) -> None:
        self.assertNotIn(["runtime", "install"], self.calls())

    def test_auto_does_nothing_when_runtime_matches_firefox(self) -> None:
        write_ini(self.runtime_ini, "157.0")
        write_ini(self.firefox_ini, "157.0")

        result = self.run_command("auto")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("up to date (runtime 157.0, Firefox 157.0)", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_auto_does_nothing_when_runtime_is_newer_than_firefox(self) -> None:
        write_ini(self.runtime_ini, "157.0.1")
        write_ini(self.firefox_ini, "157.0")

        result = self.run_command("auto")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_install()

    def test_auto_reinstalls_runtime_and_updates_every_site_when_stale(self) -> None:
        write_ini(self.runtime_ini, "156.0.1")
        write_ini(self.firefox_ini, "157.0")

        result = self.run_command("auto")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(calls[0][0], "pgrep")
        self.assertIn(
            "Library/Application Support/firefoxpwa/runtime/Firefox.app/Contents/MacOS/firefox",
            calls[0][-1],
        )
        self.assertEqual(calls[1], ["pgrep", "-f", "--", calls[0][-1]])
        self.assertEqual(calls[2], ["runtime", "install"])
        self.assertEqual(self.site_updates(), [ulid for ulid, _, _ in SITES])
        self.assertIn("runtime is now Firefox 157.0.1", result.stdout)
        self.assertIn("Updated 3 of 3 web apps", result.stdout)

    def test_auto_skips_with_warning_while_web_apps_run(self) -> None:
        write_ini(self.runtime_ini, "156.0.1")
        write_ini(self.firefox_ini, "157.0")
        self.env["FAKE_PGREP_EXIT"] = "0"

        result = self.run_command("auto")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: FirefoxPWA web apps are running", result.stderr)
        self.assert_no_install()
        self.assertEqual(self.site_updates(), [])

    def test_auto_fails_when_running_apps_cannot_be_checked(self) -> None:
        write_ini(self.runtime_ini, "156.0.1")
        write_ini(self.firefox_ini, "157.0")
        self.env["FAKE_PGREP_EXIT"] = "3"

        result = self.run_command("auto")

        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: Cannot check for running web apps", result.stderr)
        self.assert_no_install()

    def test_auto_fails_without_updating_sites_when_install_fails(self) -> None:
        write_ini(self.runtime_ini, "156.0.1")
        write_ini(self.firefox_ini, "157.0")
        self.env["FAKE_INSTALL_EXIT"] = "1"

        result = self.run_command("auto")

        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: firefoxpwa runtime install failed", result.stderr)
        self.assertEqual(self.site_updates(), [])

    def test_auto_skips_quietly_when_prerequisites_are_missing(self) -> None:
        write_ini(self.firefox_ini, "157.0")
        result = self.run_command("auto")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runtime is not installed", result.stdout)

        write_ini(self.runtime_ini, "156.0.1")
        self.env["FFPWA_REFRESH_FIREFOX_DIR"] = str(self.firefox_app.parent / "Missing.app")
        result = self.run_command("auto")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Firefox not found", result.stdout)

        self.env["FFPWA_REFRESH_FIREFOX_DIR"] = str(self.firefox_app)
        write_ini(self.firefox_ini, "158.0b3")
        result = self.run_command("auto")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Cannot compare runtime 156.0.1 with Firefox 158.0b3", result.stdout)

        (self.fixture.fake_bin / "firefoxpwa").unlink()
        result = self.run_command("auto")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("firefoxpwa is not installed", result.stdout)
        self.assert_no_install()

    def test_sites_continues_past_a_failed_site_and_exits_nonzero(self) -> None:
        self.env["FAKE_FAIL_SITE"] = SITES[1][0]

        result = self.run_command()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.site_updates(), [ulid for ulid, _, _ in SITES])
        self.assertIn("Updated 2 of 3 web apps", result.stdout)
        self.assertIn("ERROR: Failed to update: BookFusion", result.stderr)
        self.assertNotIn("00000000000000000000000000", self.site_updates())

    def test_sites_fails_when_firefoxpwa_is_missing(self) -> None:
        (self.fixture.fake_bin / "firefoxpwa").unlink()

        result = self.run_command("sites")

        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: firefoxpwa is not on PATH", result.stderr)

    def test_runtime_refuses_while_web_apps_run(self) -> None:
        self.env["FAKE_PGREP_EXIT"] = "0"

        result = self.run_command("runtime")

        self.assertEqual(result.returncode, 2)
        self.assertIn("quit them and run ffpwa-refresh runtime again", result.stderr)
        self.assert_no_install()

    def test_unknown_subcommand_prints_usage(self) -> None:
        result = self.run_command("bogus")

        self.assertEqual(result.returncode, 64)
        self.assertIn("usage: ffpwa-refresh", result.stderr)
        self.assertEqual(self.calls(), [])


@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class FfpwaRuntimeHookRenderTests(unittest.TestCase):
    def test_hooks_render_only_on_their_platform(self) -> None:
        for platform in ("linux", "windows", "wsl2"):
            self.assertEqual(render_template(MACOS_HOOK, platform).strip(), "")
        for platform in ("linux", "macos", "wsl2"):
            self.assertEqual(render_template(WINDOWS_HOOK, platform).strip(), "")
        self.assertIn("ffpwa-refresh.ps1", render_template(WINDOWS_HOOK, "windows"))


@unittest.skipIf(os.name == "nt", "bash fixtures are POSIX-only")
@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class FfpwaMacosHookTests(unittest.TestCase):
    def run_hook(self, refresh_exit: int | None) -> subprocess.CompletedProcess[str]:
        with isolated_environment(prefix="ffpwa-hook-") as fixture:
            if refresh_exit is not None:
                write_executable(
                    fixture.home / "bin" / "ffpwa-refresh",
                    f"#!/bin/sh\n[ \"$1\" = auto ] || exit 98\nexit {refresh_exit}\n",
                )
            hook = fixture.root / "hook.sh"
            hook.write_text(render_template(MACOS_HOOK, "macos"), encoding="utf-8")
            return subprocess.run(
                ["/bin/bash", str(hook)],
                env=fixture.env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )

    def test_macos_hook_skips_when_command_is_not_deployed(self) -> None:
        result = self.run_hook(None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("INFO:", result.stdout)

    def test_macos_hook_warns_and_continues_when_refresh_fails(self) -> None:
        result = self.run_hook(1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: FirefoxPWA runtime refresh failed", result.stderr)

    def test_macos_hook_is_quiet_when_refresh_succeeds(self) -> None:
        result = self.run_hook(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")


@unittest.skipUnless(PWSH and PESTER and PESTER >= (3, 4), "Pester 3.4+ is required")
class FfpwaRefreshPesterTests(unittest.TestCase):
    def test_windows_command_behavior(self) -> None:
        result = run_pester((PESTER_TEST,), repo_root=REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
