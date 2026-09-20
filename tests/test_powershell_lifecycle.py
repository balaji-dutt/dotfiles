from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from tests.support.powershell import (
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
    "WindowsBootstrap.Tests.ps1",
    "WindowsFileLifecycle.Tests.ps1",
    "WindowsStartupTask.Tests.ps1",
}
PESTER_TESTS = tuple(
    REPO_ROOT / "tests" / "powershell" / name
    for name in sorted(PESTER_TEST_NAMES)
)
TEMPLATES = {
    "windows-bootstrap.ps1": ".chezmoiscripts/run_onchange_after_windows-bootstrap.ps1.tmpl",
    "windows-cleanup.ps1": ".chezmoiscripts/run_once_after_99-cleanup-wrong-apply.ps1.tmpl",
    "windows-startup-task.ps1": ".chezmoiscripts/run_after_windows-zz-register-startup-tasks.ps1.tmpl",
    "windows-sync.ps1": ".chezmoiscripts/run_after_windows-sync.ps1.tmpl",
}


@unittest.skipUnless(PWSH and PESTER and PESTER >= (3, 4), "Pester 3.4+ is required")
class PowerShellLifecyclePesterTests(unittest.TestCase):
    def test_rendered_lifecycle_behavior(self) -> None:
        self.assertEqual({path.name for path in PESTER_TESTS}, PESTER_TEST_NAMES)
        for path in PESTER_TESTS:
            self.assertTrue(path.is_file(), path)
        with tempfile.TemporaryDirectory(prefix="powershell-lifecycle-") as temporary:
            render_dir = Path(temporary)
            for destination, template in TEMPLATES.items():
                (render_dir / destination).write_text(
                    render_template(template, "windows"),
                    encoding="utf-8",
                )

            env = os.environ.copy()
            env["DOTFILES_TEST_RENDER_DIR"] = powershell_path(render_dir, PWSH)
            if PWSH.lower().endswith(".exe"):
                forwarded = [
                    item
                    for item in env.get("WSLENV", "").split(":")
                    if item
                ]
                if "DOTFILES_TEST_RENDER_DIR" not in forwarded:
                    forwarded.append("DOTFILES_TEST_RENDER_DIR")
                env["WSLENV"] = ":".join(forwarded)
            result = run_pester(PESTER_TESTS, repo_root=REPO_ROOT, environ=env)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
