from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tests.support.powershell import resolve_powershell_runtime
from tests.test_chezmoi_lifecycle_render import render_template


ROOT = Path(__file__).resolve().parents[1]
PWSH = resolve_powershell_runtime()
HOOKS = {
    "commit": "run_after_10-dotfiles-commit-template.ps1.tmpl",
    "git-hooks": "run_after_20-git-template-hooks.ps1.tmpl",
    "beads-client": "run_after_windows-beads-client.ps1.tmpl",
    "beads-pin": "run_after_windows-beads-pin.ps1.tmpl",
    "quota": "run_once_after_98-migrate-opencode-quota.ps1.tmpl",
    "sublime": "run_once_before_copy_sublime_merge_packages.ps1.tmpl",
    "browser": "run_onchange_after_browser-policies.ps1.tmpl",
    "mcp": "run_onchange_after_claude_mcp_servers.ps1.tmpl",
    "kanban": "run_onchange_after_install_better_beads_kanban.ps1.tmpl",
    "plannotator": "run_onchange_after_install_plannotator.ps1.tmpl",
}
DENY_EXTERNAL = r'''
function Invoke-WebRequest { throw 'fixture blocked external network' }
function Invoke-RestMethod { throw 'fixture blocked external network' }
function reg.exe { throw 'fixture blocked registry import' }
function wsl.exe { throw 'fixture blocked WSL' }
function winget.exe { throw 'fixture blocked Winget' }
function bd.exe { throw 'fixture blocked Beads' }
function claude { throw 'fixture blocked Claude' }
function code { throw 'fixture blocked VS Code' }
function git { throw 'fixture blocked Git' }
'''


@unittest.skipUnless(os.name == "nt" and PWSH, "native Windows PowerShell is required")
class WindowsLifecycleBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="lifecycle-boundaries-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        child_environment = {
            name: os.environ[name]
            for name in ("PATH", "PATHEXT", "SystemRoot", "WINDIR", "ComSpec")
            if name in os.environ
        }
        self.env = {
            **child_environment,
            "APPDATA": str(self.root / "appdata"),
            "LOCALAPPDATA": str(self.root / "localappdata"),
            "TEMP": str(self.root / "temp"),
            "TMP": str(self.root / "temp"),
            "HOME": str(self.root / "home"),
            "USERPROFILE": str(self.root / "home"),
            "GITHUB_API_TOKEN": "",
        }
        for name in ("appdata", "localappdata", "temp", "home"):
            (self.root / name).mkdir()
        (self.root / "appdata" / "unrelated.txt").write_text("preserve", encoding="utf-8")

    def render(self, name: str, configure=None) -> str:
        return render_template(f".chezmoiscripts/{HOOKS[name]}", "windows", configure)

    def run_hook(self, name: str, *, source: str | None = None, before: str = "") -> subprocess.CompletedProcess[str]:
        script = self.root / f"{name}.ps1"
        script.write_text(
            "$ErrorActionPreference = 'Stop'\n" + DENY_EXTERNAL + before + "\n"
            + (source if source is not None else self.render(name)),
            encoding="utf-8",
        )
        result = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
            env=self.env,
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        self.assertEqual(
            (self.root / "appdata" / "unrelated.txt").read_text(encoding="utf-8"),
            "preserve",
        )
        return result

    def assert_ok(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("fixture blocked", result.stdout + result.stderr)

    def test_commit_template_stays_inside_temporary_git_repo(self) -> None:
        git = shutil.which("git")
        self.assertIsNotNone(git)
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run([git, "init", "--quiet", str(repo)], check=True, env=self.env)
        (repo / ".gitmessage").write_text("fixture", encoding="utf-8")
        script = self.render("commit", lambda data: data["chezmoi"].update(workingTree=str(repo)))
        self.assertIn(f'$repoRoot = "{repo}"', script)
        self.env["FIXTURE_REPO"] = str(repo)
        self.env["FIXTURE_GIT"] = git
        before = r'''
function git {
  if ($args.Count -lt 5 -or $args[0] -ne '-C' -or $args[1] -ne $env:FIXTURE_REPO -or
      $args[2] -ne 'config' -or ($args[3] -ne '--local' -and $args[3] -ne '--path')) {
    throw 'Git arguments escaped the fixture repo'
  }
  & $env:FIXTURE_GIT @args
}
'''
        self.assert_ok(self.run_hook("commit", source=script, before=before))
        self.assert_ok(self.run_hook("commit", source=script, before=before))
        value = subprocess.run(
            [git, "-C", str(repo), "config", "--local", "--get", "commit.template"],
            capture_output=True, text=True, check=True, env=self.env,
        )
        self.assertEqual(value.stdout.strip(), ".gitmessage")

    def test_git_hook_sync_never_invokes_host_python_or_hardcoded_root(self) -> None:
        before = r'''
function Get-Command {
  param([string]$Name, $ErrorAction)
  if ($Name -notin @('python3', 'python', 'py')) { throw 'unexpected discovery' }
  return $null
}
'''
        result = self.run_hook("git-hooks", before=before)
        self.assert_ok(result)
        self.assertIn("Python 3.9 or newer is unavailable", result.stdout + result.stderr)

    def test_git_hook_sync_forwards_roots_to_fixture_only(self) -> None:
        self.env["FIXTURE_PYTHON_LOG"] = str(self.root / "python.json")
        before = r'''
function Get-Command {
  param([string]$Name, $ErrorAction)
  if ($Name -ne 'python3') { throw 'unexpected discovery' }
  return [pscustomobject]@{ Source = 'Invoke-FixturePython' }
}
function Invoke-FixturePython {
  $global:LASTEXITCODE = 0
  if ($args[0] -eq '-c') { return }
  @($args) | ConvertTo-Json -Compress | Set-Content -LiteralPath $env:FIXTURE_PYTHON_LOG
}
'''
        self.assert_ok(self.run_hook("git-hooks", before=before))
        arguments = json.loads(Path(self.env["FIXTURE_PYTHON_LOG"]).read_text(encoding="utf-8"))
        self.assertIn("--template-hooks-dir", arguments)
        self.assertEqual(arguments.count("--repo-root"), 3)

    def test_beads_client_validates_port_without_wsl_or_user_scope_mutation(self) -> None:
        source = self.render("beads-client")
        self.assertTrue(source.rstrip().endswith("Configure-BeadsClient\nexit 0"))
        definitions = source.rstrip().removesuffix("Configure-BeadsClient\nexit 0")
        before = r'''
function Get-Command {
  param([string]$Name, $CommandType, $ErrorAction)
  if ($Name -ne 'wsl.exe') { throw 'unexpected discovery' }
  return [pscustomobject]@{ Source = 'fixture-not-executed' }
}
'''
        for port in ("51337", "0", "65536", "not-a-port"):
            with self.subTest(port=port):
                self.env["FIXTURE_PORT"] = port
                self.env["FIXTURE_LOG"] = str(self.root / f"beads-{port}.json")
                mocks = r'''
function Read-BeadsClientPort {
  param($Wsl, $Distro, $RepoRel)
  if ($Wsl -ne 'fixture-not-executed' -or $Distro -ne 'FixtureDistro' -or
      $RepoRel -ne 'fixture/dotfiles') { throw 'unexpected WSL call' }
  $global:LASTEXITCODE = 0
  return $env:FIXTURE_PORT
}
function Set-UserScopeVariable {
  param($Name, $Value)
  $script:calls += [pscustomobject]@{ Name = $Name; Value = $Value }
}
$script:calls = @()
Configure-BeadsClient
ConvertTo-Json -Compress -InputObject @($script:calls) | Set-Content -LiteralPath $env:FIXTURE_LOG
exit 0
'''
                result = self.run_hook("beads-client", source=definitions + before + mocks)
                self.assert_ok(result)
                log = Path(self.env["FIXTURE_LOG"])
                calls = json.loads(log.read_text(encoding="utf-8"))
                if port == "51337":
                    self.assertEqual(
                        {item["Name"]: item["Value"] for item in calls},
                        {
                            "BEADS_DOLT_SERVER_PORT": "51337",
                            "BEADS_CLIENT_WSL_DISTRO": "FixtureDistro",
                            "BEADS_CLIENT_WSL_REPO_REL": "fixture/dotfiles",
                        },
                    )
                else:
                    self.assertEqual(calls, [])

        self.env["FIXTURE_LOG"] = str(self.root / "beads-missing-wsl.json")
        missing_wsl = before.replace(
            "return [pscustomobject]@{ Source = 'fixture-not-executed' }",
            "return $null",
        )
        no_wsl = self.run_hook(
            "beads-client",
            source=definitions + missing_wsl + r'''
function Set-UserScopeVariable { throw 'unexpected user-scope write' }
Configure-BeadsClient
exit 0
''',
        )
        self.assert_ok(no_wsl)
        self.assertIn("wsl.exe not found", no_wsl.stdout)

        failed_read = self.run_hook(
            "beads-client",
            source=definitions + before + r'''
function Read-BeadsClientPort { $global:LASTEXITCODE = 1; return $null }
function Set-UserScopeVariable { throw 'unexpected user-scope write' }
Configure-BeadsClient
exit 0
''',
        )
        self.assert_ok(failed_read)
        self.assertIn("no readable Beads port", failed_read.stdout)

    def test_beads_pin_dry_run_never_invokes_winget(self) -> None:
        self.env["BEADS_WINGET_PIN_DRY_RUN"] = "1"
        before = r'''
function Get-Command {
  param([string]$Name, $CommandType, $ErrorAction)
  if ($Name -eq 'winget.exe') { return [pscustomobject]@{ Source = 'fixture-not-executed' } }
  if ($Name -eq 'bd.exe') { return [pscustomobject]@{ Source = 'Invoke-FixtureBd' } }
  throw 'unexpected discovery'
}
function Invoke-FixtureBd {
  if ($args[0] -ne 'version') { throw 'unexpected Beads command' }
  $global:LASTEXITCODE = 0
  'bd version 1.2.3-fixture'
}
'''
        result = self.run_hook("beads-pin", before=before)
        self.assert_ok(result)
        self.assertIn("Would run: winget.exe pin add", result.stdout)

    def test_quota_migration_deletes_only_fixture_old_config(self) -> None:
        script = self.render("quota", lambda data: data["chezmoi"].update(destDir=str(self.root)))
        self.assertIn(f'$Dest = "{self.root}"', script)
        old = self.root / ".config" / "opencode" / "opencode-quota" / "quota-toast.json"
        new = self.root / "AppData" / "Roaming" / "opencode" / "opencode-quota" / "quota-toast.json"
        old.parent.mkdir(parents=True)
        new.parent.mkdir(parents=True)
        old.write_text("old", encoding="utf-8")
        new.write_text("new", encoding="utf-8")
        self.assert_ok(self.run_hook("quota", source=script))
        self.assertFalse(old.exists())
        self.assertEqual(new.read_text(encoding="utf-8"), "new")
        self.assert_ok(self.run_hook("quota", source=script))

    def test_sublime_requires_token_and_never_contacts_github(self) -> None:
        missing = self.run_hook("sublime")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("GITHUB_API_TOKEN is not set", missing.stdout)
        self.env["GITHUB_API_TOKEN"] = "fixture-token-not-a-credential"
        before = r'''
function Invoke-RestMethod {
  param($Uri, $Headers, $Method)
  if ($Uri -notlike 'https://api.github.com/repos/sublimehq/Packages/*' -or
      $Headers.Authorization -ne 'Bearer fixture-token-not-a-credential') {
    throw 'unexpected synthetic API request'
  }
  throw 'fixture network denied'
}
'''
        result = self.run_hook("sublime", before=before)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture network denied", result.stderr)
        self.assertNotIn(self.env["GITHUB_API_TOKEN"], result.stdout + result.stderr)
        self.assertEqual(list((self.root / "temp").iterdir()), [])

    def test_browser_disabled_does_not_import_registry(self) -> None:
        result = self.run_hook("browser")
        self.assert_ok(result)
        self.assertIn("disabled in chezmoi data", result.stdout)

    def test_browser_unelevated_branch_emits_handoff_without_registry_import(self) -> None:
        source = self.render(
            "browser",
            lambda data: data["browser_policies"]["justthebrowser"].update(enabled=True),
        )
        elevation = (
            "function Test-IsElevated {\n"
            "  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()\n"
            "  $principal = New-Object Security.Principal.WindowsPrincipal($identity)\n"
            "  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)\n"
            "}"
        )
        self.assertEqual(source.count(elevation), 1)
        source = source.replace(elevation, "function Test-IsElevated { return $false }")
        result = self.run_hook("browser", source=source)
        self.assert_ok(result)
        self.assertIn("-EncodedCommand", result.stdout)
        self.assertIn("Skipping registry import", result.stdout + result.stderr)

    def test_mcp_dry_run_uses_synthetic_config_without_claude(self) -> None:
        config = self.root / "source" / "configs" / "claude-mcp.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({
            "$schema": "./schemas/claude-mcp.v1.schema.json",
            "schema_version": 1,
            "servers": {"fixture": {"transport": "http", "url": "https://example.invalid/mcp"}},
        }), encoding="utf-8")
        source = self.render("mcp")
        original = f'Join-Path "{ROOT}" "configs\\claude-mcp.json"'
        self.assertEqual(source.count(original), 1)
        source = source.replace(original, f'Join-Path "{config.parent.parent}" "configs\\claude-mcp.json"')
        self.env["CLAUDE_MCP_DRY_RUN"] = "1"
        before = r'''
function Get-Command {
  param([string]$Name, $CommandType, $ErrorAction)
  if ($Name -ne 'claude') { throw 'unexpected discovery' }
  return [pscustomobject]@{ Source = 'fixture-not-executed' }
}
'''
        result = self.run_hook("mcp", source=source, before=before)
        self.assert_ok(result)
        self.assertIn("Would configure Claude MCP server 'fixture'", result.stdout)

    def test_kanban_idempotent_fixture_does_not_install_or_download(self) -> None:
        source = self.render("kanban")
        sha = "7bf8f1073d527424bcfc46dde132a79b35f013d28209f21a0efa67169d8afcfa"
        self.assertIn(f'$ExpectedSha             = "{sha}"', source)
        cache = self.root / "localappdata" / "dotfiles" / "better-beads-kanban-vsix"
        cache.mkdir(parents=True)
        marker = cache / "v2.2.2.installed"
        marker.write_text(sha, encoding="utf-8")
        self.env["FIXTURE_CODE_LOG"] = str(self.root / "code.log")
        before = r'''
function Get-Command {
  param([string]$Name, $ErrorAction)
  if ($Name -ne 'code') { throw 'unexpected discovery' }
  return [pscustomobject]@{ Source = 'code' }
}
function code {
  Add-Content -LiteralPath $env:FIXTURE_CODE_LOG -Value ($args -join ' ')
  $global:LASTEXITCODE = 0
  if ($args[0] -eq '--list-extensions') { 'balaji-dutt.better-beads-kanban@2.2.2' }
  elseif ($args[0] -ne '--uninstall-extension') { throw 'unexpected VS Code command' }
}
'''
        result = self.run_hook("kanban", source=source, before=before)
        self.assert_ok(result)
        self.assertIn("already installed", result.stdout)
        calls = Path(self.env["FIXTURE_CODE_LOG"]).read_text(encoding="utf-8")
        self.assertIn("--uninstall-extension davidcforbes.beads-kanban", calls)
        self.assertIn("--uninstall-extension balaji-dutt.beads-kanban-bd-fixes", calls)
        self.assertNotIn("--install-extension", calls)
        self.assertEqual(marker.read_text(encoding="utf-8"), sha)

    def test_plannotator_dry_run_never_reads_or_writes_host_home(self) -> None:
        self.env["PLANNOTATOR_INSTALL_DRY_RUN"] = "1"
        before = r'''
function Test-Path {
  param($LiteralPath, $PathType)
  if ($LiteralPath -notlike '*plannotator.exe') { throw 'unexpected path probe' }
  return $false
}
'''
        result = self.run_hook("plannotator", before=before)
        self.assert_ok(result)
        self.assertIn("Would install plannotator", result.stdout)


if __name__ == "__main__":
    unittest.main()
