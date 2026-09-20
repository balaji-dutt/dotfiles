from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.support.powershell import (
    is_wsl,
    pester_version,
    powershell_path,
    resolve_powershell_runtime,
    run_pester,
)
from tests.test_chezmoi_lifecycle_render import render_template


REPO_ROOT = Path(__file__).resolve().parents[1]
PWSH = resolve_powershell_runtime()
PESTER = pester_version(PWSH) if PWSH else None
PESTER_TEST_NAMES = {
    "WindowsDriveMapping.Tests.ps1",
    "WindowsShellProfile.Tests.ps1",
    "WslPageant.Tests.ps1",
}
PESTER_TESTS = tuple(
    REPO_ROOT / "tests" / "powershell" / name
    for name in sorted(PESTER_TEST_NAMES)
)
TEMPLATES = {
    "Start-WslSshPageant.ps1": "private_Documents/PowerShell/Scripts/Start-WslSshPageant.ps1.tmpl",
    "aliases.ps1": "private_dot_config/powershell/aliases.ps1.tmpl",
    "beads-env.ps1": "private_dot_config/powershell/beads-env.ps1.tmpl",
    "claude-env.ps1": "private_dot_config/powershell/claude-env.ps1.tmpl",
    "git.ps1": "private_dot_config/powershell/git.ps1.tmpl",
    "opencode-env.ps1": "private_dot_config/powershell/opencode-env.ps1.tmpl",
    "packages.ps1": "private_dot_config/powershell/packages.ps1.tmpl",
    "prompt.ps1": "private_dot_config/powershell/prompt.ps1.tmpl",
    "repair-path.ps1": "private_dot_config/powershell/repair-path.ps1.tmpl",
}
STATIC_SOURCES = {
    "MapDrives.ps1": "private_Documents/PowerShell/Scripts/MapDrives.ps1",
    "Microsoft.PowerShell_profile.ps1": "private_Documents/PowerShell/Microsoft.PowerShell_profile.ps1",
}


def prepare_render_dir(destination: Path) -> None:
    for target, source in TEMPLATES.items():
        (destination / target).write_text(
            render_template(source, "windows"),
            encoding="utf-8",
        )
    for target, source in STATIC_SOURCES.items():
        shutil.copy2(REPO_ROOT / source, destination / target)


def pester_environment(render_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DOTFILES_TEST_RENDER_DIR"] = powershell_path(render_dir, PWSH)
    if PWSH and PWSH.lower().endswith(".exe"):
        forwarded = [item for item in env.get("WSLENV", "").split(":") if item]
        if "DOTFILES_TEST_RENDER_DIR" not in forwarded:
            forwarded.append("DOTFILES_TEST_RENDER_DIR")
        env["WSLENV"] = ":".join(forwarded)
    return env


@unittest.skipUnless(PWSH and PESTER and PESTER >= (3, 4), "Pester 3.4+ is required")
class WindowsShellPesterTests(unittest.TestCase):
    def test_profile_drive_and_pageant_behavior(self) -> None:
        for path in PESTER_TESTS:
            self.assertTrue(path.is_file(), path)
        with tempfile.TemporaryDirectory(prefix="windows-shell-") as temporary:
            render_dir = Path(temporary)
            prepare_render_dir(render_dir)
            result = run_pester(
                PESTER_TESTS,
                repo_root=REPO_ROOT,
                environ=pester_environment(render_dir),
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(
    PWSH and PWSH.lower().endswith(".exe") and is_wsl(),
    "WSL-accessible native Windows PowerShell is required",
)
class NativeWindowsShellSmokeTests(unittest.TestCase):
    def test_dot_sources_shell_helpers_over_wsl_unc_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="windows-shell-smoke-") as temporary:
            render_dir = Path(temporary)
            prepare_render_dir(render_dir)
            smoke = render_dir / "native smoke.ps1"
            marker = render_dir / "native marker.txt"
            smoke.write_text(
                "param([string]$RenderDir, [string]$Marker)\n"
                ". (Join-Path $RenderDir 'MapDrives.ps1')\n"
                ". (Join-Path $RenderDir 'Start-WslSshPageant.ps1')\n"
                "if (-not (Get-Command Invoke-MapDrives -ErrorAction SilentlyContinue)) { exit 2 }\n"
                "if (-not (Get-Command Invoke-WslSshPageant -ErrorAction SilentlyContinue)) { exit 3 }\n"
                "Set-Content -LiteralPath $Marker -Value (Convert-ToCmdQuoted 'value with spaces')\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    PWSH,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    powershell_path(smoke, PWSH),
                    "-RenderDir",
                    powershell_path(render_dir, PWSH),
                    "-Marker",
                    powershell_path(marker, PWSH),
                ],
                cwd=REPO_ROOT,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), '"value with spaces"')


if __name__ == "__main__":
    unittest.main()
