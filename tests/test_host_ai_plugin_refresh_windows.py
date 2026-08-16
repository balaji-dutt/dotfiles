from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (
    REPO_ROOT
    / ".chezmoiscripts/run_onchange_after_host_ai_plugin_refresh.ps1.tmpl"
)
PWSH = shutil.which("pwsh")


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(PWSH, "native pwsh is not installed")
class WindowsHostAiPluginRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.rendered = self.root / "host-ai-plugin-refresh.ps1"
        self.render_template()

    def render_template(self) -> None:
        source = TEMPLATE.read_text(encoding="utf-8")
        controls = {
            '{{- if ne .chezmoi.os "windows" -}}',
            "{{- /* not Windows: do nothing */ -}}",
            "{{- else -}}",
            "{{- end -}}",
        }
        source = "\n".join(
            line for line in source.splitlines() if line not in controls
        )
        source = source.replace(
            '{{ include "configs/host-ai-plugin-refresh.jsonc" | sha256sum }}',
            "test-manifest-hash",
        )
        source = source.replace(
            "{{ .chezmoi.sourceDir }}",
            str(REPO_ROOT).replace("'", "''"),
        )
        self.rendered.write_text(source + "\n", encoding="utf-8")

    def run_pwsh(
        self,
        body: str,
        *,
        env_updates: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "USERPROFILE": str(self.home),
            }
        )
        env.pop("XDG_CACHE_HOME", None)
        env.pop("HOST_AI_PLUGIN_REFRESH_DRY_RUN", None)
        if env_updates:
            env.update(env_updates)
        command = (
            "$ErrorActionPreference = 'Stop'; "
            f". {ps_quote(self.rendered)}; "
            + body
        )
        return subprocess.run(
            [PWSH, "-NoProfile", "-Command", command],
            cwd=self.root,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def read_json(self, result: subprocess.CompletedProcess[str]) -> dict[str, object]:
        self.assertEqual(result.returncode, 0, result.stderr)
        json_line = next(
            line for line in reversed(result.stdout.splitlines()) if line.startswith("{")
        )
        return json.loads(json_line)

    def default_cache(self) -> Path:
        return self.home / ".cache" / "opencode"

    def test_default_and_explicit_xdg_cache_roots_are_accepted(self) -> None:
        xdg = self.root / "xdg-cache"
        body = f"""
          $default = New-OpenCodeCachePaths -CacheRoot (Join-Path $HOME '.cache\\opencode')
          $env:XDG_CACHE_HOME = {ps_quote(xdg)}
          $xdg = New-OpenCodeCachePaths -CacheRoot (Join-Path $env:XDG_CACHE_HOME 'opencode')
          [pscustomobject]@{{
            DefaultRoot = $default.CacheRoot
            DefaultLeaf = Split-Path -Leaf $default.PackagesDir
            XdgRoot = $xdg.CacheRoot
          }} | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(Path(str(data["DefaultRoot"])), self.default_cache())
        self.assertEqual(data["DefaultLeaf"], "packages")
        self.assertEqual(Path(str(data["XdgRoot"])), xdg / "opencode")

    def test_unsafe_or_unexpected_cache_roots_are_rejected(self) -> None:
        filesystem_root = Path(self.home.anchor)
        candidates = {
            "relative": r"relative\opencode",
            "filesystem-root": str(filesystem_root),
            "home": str(self.home),
            "sibling": str(self.home / ".cache" / "sibling"),
            "unexpected-parent": str(self.home / "elsewhere" / "opencode"),
            "traversal": str(self.home) + r"\.cache\other\..\opencode",
        }
        for label, candidate in candidates.items():
            with self.subTest(label=label):
                body = f"""
                  try {{
                    New-OpenCodeCachePaths -CacheRoot {ps_quote(candidate)} | Out-Null
                    'ACCEPTED'
                  }} catch {{
                    'REJECTED: ' + $_.Exception.Message
                  }}
                """
                result = self.run_pwsh(body)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("REJECTED:", result.stdout)
                self.assertNotIn("ACCEPTED", result.stdout)

    def test_debug_path_must_match_a_trusted_root(self) -> None:
        untrusted = self.root / "other" / "opencode"
        body = f"""
          function Get-OpenCodeDebugCachePath {{
            [pscustomobject]@{{ Status = 'Known'; Path = {ps_quote(untrusted)}; Error = $null }}
          }}
          try {{
            Get-OpenCodeCachePaths | Out-Null
            'ACCEPTED'
          }} catch {{
            'REJECTED: ' + $_.Exception.Message
          }}
        """
        result = self.run_pwsh(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("untrusted cache root", result.stdout)
        self.assertNotIn("ACCEPTED", result.stdout)

    def test_reparse_cache_boundary_is_rejected_when_supported(self) -> None:
        outside = self.root / "outside"
        cache = self.default_cache()
        body = f"""
          New-Item -ItemType Directory -Path {ps_quote(outside)} -Force | Out-Null
          New-Item -ItemType Directory -Path {ps_quote(cache.parent)} -Force | Out-Null
          try {{
            New-Item -ItemType Junction -Path {ps_quote(cache)} -Target {ps_quote(outside)} | Out-Null
          }} catch {{
            'SKIP: junction creation unavailable: ' + $_.Exception.Message
            return
          }}
          try {{
            New-OpenCodeCachePaths -CacheRoot {ps_quote(cache)} | Out-Null
            'ACCEPTED'
          }} catch {{
            'REJECTED: ' + $_.Exception.Message
          }}
        """
        result = self.run_pwsh(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        if "SKIP:" in result.stdout:
            self.skipTest(result.stdout.strip())
        self.assertIn("reparse point", result.stdout)
        self.assertNotIn("ACCEPTED", result.stdout)

    def test_active_or_unknown_opencode_runs_claude_then_defers(self) -> None:
        states = {
            "Running": "active OpenCode",
            "Unknown": "inspection denied",
        }
        for state, error in states.items():
            with self.subTest(state=state):
                body = f"""
                  $script:ClaudeCalled = $false
                  $script:CacheMutationCalled = $false
                  function Update-ClaudePlugins {{
                    param([object[]] $Plugins)
                    $script:ClaudeCalled = $true
                    Write-Host 'TEST: Claude refresh invoked'
                  }}
                  function Get-OpenCodeProcessState {{
                    [pscustomobject]@{{
                      Status = {ps_quote(state)}
                      Processes = @([pscustomobject]@{{ Id = 42; SessionId = 1; ParentProcessId = 7; Path = 'opencode.exe'; CommandLine = 'opencode' }})
                      Error = {ps_quote(error)}
                    }}
                  }}
                  function Get-OpenCodeDebugCachePath {{ throw 'cache lookup must not run' }}
                  function Remove-ValidatedOpenCodePath {{ $script:CacheMutationCalled = $true }}
                  function Install-OpenCodeCachePlugin {{ $script:CacheMutationCalled = $true; return $true }}
                  $code = Invoke-HostAiPluginRefresh
                  [pscustomobject]@{{
                    Code = $code
                    ClaudeCalled = $script:ClaudeCalled
                    CacheMutationCalled = $script:CacheMutationCalled
                  }} | ConvertTo-Json -Compress
                """
                result = self.run_pwsh(body)
                data = self.read_json(result)
                self.assertEqual(data["Code"], 1)
                self.assertTrue(data["ClaudeCalled"])
                self.assertFalse(data["CacheMutationCalled"])
                self.assertIn("chezmoi will retry", result.stdout)
                self.assertIn("standalone PowerShell", result.stdout)

    def test_serve_process_is_ignored_but_interactive_process_blocks(self) -> None:
        body = """
          $script:ProcessMode = 'serve'
          function Get-CimInstance {
            param([string] $ClassName, [string] $Filter, $ErrorAction)
            $commandLine = if ($script:ProcessMode -eq 'serve') {
              '"C:\\ProgramData\\chocolatey\\lib\\opencode\\tools\\opencode.exe" serve --hostname=127.0.0.1 --port=4096'
            } else {
              '"C:\\ProgramData\\chocolatey\\lib\\opencode\\tools\\opencode.exe"'
            }
            [pscustomobject]@{
              ProcessId = 11412
              SessionId = 1
              ParentProcessId = 47588
              ExecutablePath = 'C:\\ProgramData\\chocolatey\\lib\\opencode\\tools\\opencode.exe'
              CommandLine = $commandLine
            }
          }
          $serve = Get-OpenCodeProcessState
          Write-OpenCodeProcessDetails -Processes @($serve.Processes)
          $script:ProcessMode = 'interactive'
          $interactive = Get-OpenCodeProcessState
          Write-OpenCodeProcessDetails -Processes @($interactive.Processes)
          $fallback = New-OpenCodeProcessDetail -Id 77 -SessionId 1 -ParentProcessId $null -Path 'opencode.exe' -CommandLine $null
          $nearMiss = Test-OpenCodeServeCommandLine -CommandLine '"C:\\ProgramData\\opencode.exe" run --prompt serve'
          [pscustomobject]@{
            ServeStatus = $serve.Status
            ServeBlocking = $serve.Processes[0].Blocking
            ServeReason = $serve.Processes[0].Reason
            InteractiveStatus = $interactive.Status
            InteractiveBlocking = $interactive.Processes[0].Blocking
            FallbackBlocking = $fallback.Blocking
            NearMissAccepted = $nearMiss
          } | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(data["ServeStatus"], "Stopped")
        self.assertFalse(data["ServeBlocking"])
        self.assertEqual(data["ServeReason"], "serve")
        self.assertEqual(data["InteractiveStatus"], "Running")
        self.assertTrue(data["InteractiveBlocking"])
        self.assertTrue(data["FallbackBlocking"])
        self.assertFalse(data["NearMissAccepted"])
        self.assertIn("Ignoring serve OpenCode process", result.stdout)
        self.assertIn("Blocking OpenCode process", result.stdout)

    def test_stale_claude_install_record_falls_back_to_install(self) -> None:
        body = """
          $plugin = [pscustomobject]@{ Id = 'plannotator@plannotator'; Scope = 'user' }
          $installed = [System.Collections.Generic.HashSet[string]]::new()
          [void] $installed.Add($plugin.Id)
          $actions = @(Get-ClaudePluginActions -Plugin $plugin -InstalledIds $installed)
          $script:Attempts = @()
          function Invoke-External {
            param([string] $Command, [string[]] $Arguments)
            $action = $Arguments[1]
            $script:Attempts += $action
            if ($action -eq 'update') {
              return [pscustomobject]@{ ExitCode = 1; Output = 'Plugin "plannotator" not found' }
            }
            return [pscustomobject]@{ ExitCode = 0; Output = '' }
          }
          $ok = Sync-ClaudePlugin -Plugin $plugin -Actions $actions
          [pscustomobject]@{
            Ok = $ok
            Actions = @($actions)
            Attempts = @($script:Attempts)
          } | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertTrue(data["Ok"])
        self.assertEqual(data["Actions"], ["update", "install"])
        self.assertEqual(data["Attempts"], ["update", "install"])
        self.assertNotIn("ERROR:", result.stdout)

    def test_transient_claude_update_failure_does_not_reinstall(self) -> None:
        body = """
          $plugin = [pscustomobject]@{ Id = 'plannotator@plannotator'; Scope = 'user' }
          $installed = [System.Collections.Generic.HashSet[string]]::new()
          [void] $installed.Add($plugin.Id)
          $actions = @(Get-ClaudePluginActions -Plugin $plugin -InstalledIds $installed)
          $script:Attempts = @()
          function Invoke-External {
            param([string] $Command, [string[]] $Arguments)
            $script:Attempts += $Arguments[1]
            return [pscustomobject]@{ ExitCode = 1; Output = 'network unavailable' }
          }
          $ok = Sync-ClaudePlugin -Plugin $plugin -Actions $actions
          [pscustomobject]@{ Ok = $ok; Attempts = @($script:Attempts) } | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertFalse(data["Ok"])
        self.assertEqual(data["Attempts"], ["update"])
        self.assertIn("ERROR: Failed to update Claude Code plugin", result.stdout)

    def test_process_appearing_before_removal_preserves_cache(self) -> None:
        packages = self.default_cache() / "packages"
        sibling = self.default_cache() / "sibling.marker"
        packages.mkdir(parents=True)
        (packages / "cached.marker").write_text("cached\n", encoding="utf-8")
        sibling.write_text("sibling\n", encoding="utf-8")
        body = f"""
          $script:ProbeCount = 0
          function Get-OpenCodeDebugCachePath {{
            [pscustomobject]@{{ Status = 'Unavailable'; Path = $null; Error = $null }}
          }}
          function Get-OpenCodeProcessState {{
            $script:ProbeCount++
            if ($script:ProbeCount -eq 1) {{
              return [pscustomobject]@{{ Status = 'Stopped'; Processes = @(); Error = $null }}
            }}
            return [pscustomobject]@{{ Status = 'Running'; Processes = @([pscustomobject]@{{ Id = 84; SessionId = 1 }}); Error = $null }}
          }}
          $ok = Update-OpenCodeCache -Plugins @()
          [pscustomobject]@{{
            Ok = $ok
            ProbeCount = $script:ProbeCount
            PackagesExist = Test-Path -LiteralPath {ps_quote(packages)}
            SiblingExists = Test-Path -LiteralPath {ps_quote(sibling)}
          }} | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertFalse(data["Ok"])
        self.assertEqual(data["ProbeCount"], 2)
        self.assertTrue(data["PackagesExist"])
        self.assertTrue(data["SiblingExists"])
        self.assertIn("deferred OpenCode package-cache removal", result.stdout)

    def test_exact_packages_deletion_preserves_cache_sibling(self) -> None:
        packages = self.default_cache() / "packages"
        sibling = self.default_cache() / "sibling.marker"
        packages.mkdir(parents=True)
        (packages / "cached.marker").write_text("cached\n", encoding="utf-8")
        sibling.write_text("sibling\n", encoding="utf-8")
        body = f"""
          function Get-OpenCodeDebugCachePath {{
            [pscustomobject]@{{ Status = 'Unavailable'; Path = $null; Error = $null }}
          }}
          function Get-OpenCodeProcessState {{
            [pscustomobject]@{{ Status = 'Stopped'; Processes = @(); Error = $null }}
          }}
          $ok = Update-OpenCodeCache -Plugins @()
          [pscustomobject]@{{
            Ok = $ok
            PackagesExist = Test-Path -LiteralPath {ps_quote(packages)}
            SiblingExists = Test-Path -LiteralPath {ps_quote(sibling)}
          }} | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertTrue(data["Ok"])
        self.assertFalse(data["PackagesExist"])
        self.assertTrue(data["SiblingExists"])

    def test_dry_run_validates_but_does_not_delete(self) -> None:
        packages = self.default_cache() / "packages"
        marker = packages / "cached.marker"
        packages.mkdir(parents=True)
        marker.write_text("cached\n", encoding="utf-8")
        body = f"""
          function Get-OpenCodeDebugCachePath {{
            [pscustomobject]@{{ Status = 'Unavailable'; Path = $null; Error = $null }}
          }}
          function Get-OpenCodeProcessState {{
            [pscustomobject]@{{ Status = 'Stopped'; Processes = @(); Error = $null }}
          }}
          $ok = Update-OpenCodeCache -Plugins @()
          [pscustomobject]@{{ Ok = $ok; MarkerExists = Test-Path -LiteralPath {ps_quote(marker)} }} | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(
            body,
            env_updates={"HOST_AI_PLUGIN_REFRESH_DRY_RUN": "1"},
        )
        data = self.read_json(result)
        self.assertTrue(data["Ok"])
        self.assertTrue(data["MarkerExists"])
        self.assertIn("Would remove OpenCode packages cache", result.stdout)

    def install_fixture_body(self, process_status: str) -> tuple[str, Path, Path, Path]:
        packages = self.default_cache() / "packages"
        target = packages / "example-plugin@1.0.0"
        sibling = self.default_cache() / "sibling.marker"
        target.mkdir(parents=True)
        (target / "old.marker").write_text("old\n", encoding="utf-8")
        sibling.write_text("sibling\n", encoding="utf-8")
        body = f"""
          $cachePaths = New-OpenCodeCachePaths -CacheRoot {ps_quote(self.default_cache())}
          $plugin = [pscustomobject]@{{
            Package = 'example-plugin'
            Version = '1.0.0'
            CacheInstall = [pscustomobject]@{{
              IgnoreScripts = $true
              LegacyPeerDeps = $false
              Dependencies = @()
            }}
          }}
          function Invoke-External {{
            param([string] $Command, [string[]] $Arguments)
            $prefix = $null
            for ($index = 0; $index -lt $Arguments.Count; $index++) {{
              if ($Arguments[$index] -eq '--prefix') {{ $prefix = $Arguments[$index + 1]; break }}
            }}
            $packageJson = Join-Path $prefix 'node_modules\\example-plugin\\package.json'
            New-Item -ItemType Directory -Path (Split-Path -Parent $packageJson) -Force | Out-Null
            Set-Content -LiteralPath $packageJson -Value '{{"version":"1.0.0"}}'
            [pscustomobject]@{{ ExitCode = 0; Output = '' }}
          }}
          function Get-OpenCodeProcessState {{
            [pscustomobject]@{{
              Status = {ps_quote(process_status)}
              Processes = @([pscustomobject]@{{ Id = 99; SessionId = 1 }})
              Error = 'test process state'
            }}
          }}
          $ok = Install-OpenCodeCachePlugin -CachePaths $cachePaths -Plugin $plugin -CacheWillBeRemoved $false
          [pscustomobject]@{{
            Ok = $ok
            OldMarkerExists = Test-Path -LiteralPath {ps_quote(target / 'old.marker')}
            PackageJsonExists = Test-Path -LiteralPath {ps_quote(target / 'node_modules/example-plugin/package.json')}
            SiblingExists = Test-Path -LiteralPath {ps_quote(sibling)}
            StagingCount = @(Get-ChildItem -LiteralPath {ps_quote(packages)} -Filter '*.installing-*' -Force).Count
          }} | ConvertTo-Json -Compress
        """
        return body, packages, target, sibling

    def test_process_appearing_before_publication_cleans_only_staging(self) -> None:
        body, _, _, _ = self.install_fixture_body("Running")
        data = self.read_json(self.run_pwsh(body))
        self.assertFalse(data["Ok"])
        self.assertTrue(data["OldMarkerExists"])
        self.assertFalse(data["PackageJsonExists"])
        self.assertTrue(data["SiblingExists"])
        self.assertEqual(data["StagingCount"], 0)

    def test_staged_install_replaces_only_validated_target(self) -> None:
        body, _, _, _ = self.install_fixture_body("Stopped")
        data = self.read_json(self.run_pwsh(body))
        self.assertTrue(data["Ok"])
        self.assertFalse(data["OldMarkerExists"])
        self.assertTrue(data["PackageJsonExists"])
        self.assertTrue(data["SiblingExists"])
        self.assertEqual(data["StagingCount"], 0)

    def test_mutated_or_unvalidated_path_objects_are_rejected(self) -> None:
        sibling = self.default_cache() / "sibling"
        body = f"""
          $cachePaths = New-OpenCodeCachePaths -CacheRoot {ps_quote(self.default_cache())}
          $cachePaths.PackagesDir = {ps_quote(sibling)}
          $mutatedRejected = $false
          try {{
            Get-OpenCodeCachePackageDir -CachePaths $cachePaths -Package 'example-plugin' -Version '1.0.0' | Out-Null
          }} catch {{ $mutatedRejected = $true }}
          $rawRejected = $false
          try {{ Remove-ValidatedOpenCodePath -ValidatedPath {ps_quote(sibling)} }} catch {{ $rawRejected = $true }}
          [pscustomobject]@{{ MutatedRejected = $mutatedRejected; RawRejected = $rawRejected }} | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertTrue(data["MutatedRejected"])
        self.assertTrue(data["RawRejected"])


if __name__ == "__main__":
    unittest.main()
