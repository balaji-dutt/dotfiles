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

    def test_manifest_contract_marker_fails_closed(self) -> None:
        manifest = self.root / "invalid-manifest.jsonc"
        manifest.write_text(
            '{"$schema":"./schemas/wrong.schema.json","schema_version":1}\n',
            encoding="utf-8",
        )
        result = self.run_pwsh(
            f"$ConfigPath = {ps_quote(manifest)}; $code = Invoke-HostAiPluginRefresh; Write-Host \"CODE=$code\""
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("$schema must be", result.stdout)
        self.assertIn("CODE=1", result.stdout)

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

    def test_claude_plugin_identity_and_marketplace_deduplication(self) -> None:
        body = """
          $first = ConvertTo-ClaudePluginIdentity -Id 'one@example-marketplace'
          $second = ConvertTo-ClaudePluginIdentity -Id 'two@example-marketplace'
          $third = ConvertTo-ClaudePluginIdentity -Id 'three@other-marketplace'
          $plugins = @(
            [pscustomobject]@{ Marketplace = $first.Marketplace },
            [pscustomobject]@{ Marketplace = $second.Marketplace },
            [pscustomobject]@{ Marketplace = $third.Marketplace }
          )
          $bad = @()
          foreach ($candidate in @('missing', '@marketplace', 'plugin@')) {
            try { ConvertTo-ClaudePluginIdentity -Id $candidate | Out-Null }
            catch { $bad += $candidate }
          }
          [pscustomobject]@{
            Name = $first.Name
            Marketplace = $first.Marketplace
            Names = @(Get-ClaudeMarketplaceNames -Plugins $plugins)
            Rejected = @($bad)
          } | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["Name"], "one")
        self.assertEqual(data["Marketplace"], "example-marketplace")
        self.assertEqual(
            data["Names"], ["example-marketplace", "other-marketplace"]
        )
        self.assertEqual(
            data["Rejected"], ["missing", "@marketplace", "plugin@"]
        )

    def test_claude_preservation_environment_is_scoped_and_restored(self) -> None:
        body = """
          $script:Observed = @()
          function Invoke-External {
            param([string] $Command, [string[]] $Arguments)
            $script:Observed += $env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE
            [pscustomobject]@{ ExitCode = 0; Output = '' }
          }
          Remove-Item Env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE -ErrorAction SilentlyContinue
          Invoke-ClaudeExternal -Arguments @('plugin', 'marketplace', 'update', 'one') | Out-Null
          $absentRestored = -not (Test-Path Env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE)
          $env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE = 'caller-value'
          Invoke-ClaudeExternal -Arguments @('plugin', 'update', 'one@one') | Out-Null
          [pscustomobject]@{
            Observed = @($script:Observed)
            AbsentRestored = $absentRestored
            ExistingRestored = $env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE
          } | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["Observed"], ["1", "1"])
        self.assertTrue(data["AbsentRestored"])
        self.assertEqual(data["ExistingRestored"], "caller-value")

    def test_marketplace_catalog_requires_expected_plugin_and_unique_record(self) -> None:
        marketplace = self.root / "marketplace"
        catalog = marketplace / ".claude-plugin" / "marketplace.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(
            json.dumps({"plugins": [{"name": "expected-plugin"}]}),
            encoding="utf-8",
        )
        body = f"""
          $plugins = @([pscustomobject]@{{ Name = 'expected-plugin' }})
          $record = [pscustomobject]@{{ name = 'example'; installLocation = {ps_quote(marketplace)} }}
          $valid = Test-ClaudeMarketplaceCatalog -Marketplace 'example' -Plugins $plugins -Records @($record)
          $missing = Test-ClaudeMarketplaceCatalog -Marketplace 'example' -Plugins @([pscustomobject]@{{ Name = 'missing-plugin' }}) -Records @($record)
          $duplicate = Test-ClaudeMarketplaceCatalog -Marketplace 'example' -Plugins $plugins -Records @($record, $record)
          [pscustomobject]@{{
            Valid = $valid.Available
            Missing = $missing.Available
            MissingError = $missing.Error
            Duplicate = $duplicate.Available
            DuplicateError = $duplicate.Error
          }} | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertTrue(data["Valid"], result.stdout)
        self.assertFalse(data["Missing"])
        self.assertIn("missing-plugin", data["MissingError"])
        self.assertFalse(data["Duplicate"])
        self.assertIn("found 2", data["DuplicateError"])

    def test_named_marketplace_refresh_skips_only_unavailable_catalog(self) -> None:
        healthy = self.root / "healthy-marketplace"
        healthy_catalog = healthy / ".claude-plugin" / "marketplace.json"
        healthy_catalog.parent.mkdir(parents=True)
        healthy_catalog.write_text(
            json.dumps({"plugins": [{"name": "healthy-plugin"}]}),
            encoding="utf-8",
        )
        empty = self.root / "empty-marketplace"
        empty.mkdir()
        body = f"""
          $plugins = @(
            [pscustomobject]@{{ Id = 'healthy-plugin@healthy'; Name = 'healthy-plugin'; Marketplace = 'healthy'; Scope = 'user' }},
            [pscustomobject]@{{ Id = 'broken-plugin@broken'; Name = 'broken-plugin'; Marketplace = 'broken'; Scope = 'user' }}
          )
          $script:ClaudeFailures = @()
          $script:Commands = @()
          $script:Synced = @()
          function Invoke-External {{
            param([string] $Command, [string[]] $Arguments)
            $script:Commands += ,@($Arguments)
            [pscustomobject]@{{ ExitCode = 0; Output = '' }}
          }}
          function Get-ClaudeMarketplaceInventory {{
            [pscustomobject]@{{
              Ok = $true
              Error = $null
              Records = @(
                [pscustomobject]@{{ name = 'healthy'; installLocation = {ps_quote(healthy)} }},
                [pscustomobject]@{{ name = 'broken'; installLocation = {ps_quote(empty)} }}
              )
            }}
          }}
          function Get-InstalledClaudePluginIds {{
            $ids = [System.Collections.Generic.HashSet[string]]::new()
            return ,$ids
          }}
          function Sync-ClaudePlugin {{
            param($Plugin, [string[]] $Actions)
            $script:Synced += $Plugin.Id
            return $true
          }}
          Update-ClaudePlugins -Plugins $plugins
          [pscustomobject]@{{
            Commands = @($script:Commands | ForEach-Object {{ $_ -join ' ' }})
            Synced = @($script:Synced)
            Failures = @($script:ClaudeFailures)
          }} | ConvertTo-Json -Depth 5 -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(
            data["Commands"],
            [
                "plugin marketplace update healthy",
                "plugin marketplace update broken",
            ],
        )
        self.assertEqual(data["Synced"], ["healthy-plugin@healthy"], result.stdout)
        self.assertEqual(data["Failures"], ["marketplace:broken"])
        self.assertIn("catalog is missing", result.stdout)
        self.assertIn("Skipping Claude Code plugin 'broken-plugin@broken'", result.stdout)

    def test_failed_named_update_uses_valid_preserved_catalog(self) -> None:
        marketplace = self.root / "preserved-marketplace"
        catalog = marketplace / ".claude-plugin" / "marketplace.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(
            json.dumps({"plugins": [{"name": "preserved-plugin"}]}),
            encoding="utf-8",
        )
        body = f"""
          $plugin = [pscustomobject]@{{ Id = 'preserved-plugin@preserved'; Name = 'preserved-plugin'; Marketplace = 'preserved'; Scope = 'user' }}
          $script:ClaudeFailures = @()
          $script:Synced = $false
          function Invoke-External {{
            param([string] $Command, [string[]] $Arguments)
            [pscustomobject]@{{ ExitCode = 1; Output = 'network unavailable' }}
          }}
          function Get-ClaudeMarketplaceInventory {{
            [pscustomobject]@{{ Ok = $true; Error = $null; Records = @([pscustomobject]@{{ name = 'preserved'; installLocation = {ps_quote(marketplace)} }}) }}
          }}
          function Get-InstalledClaudePluginIds {{
            $ids = [System.Collections.Generic.HashSet[string]]::new()
            return ,$ids
          }}
          function Sync-ClaudePlugin {{ param($Plugin, [string[]] $Actions); $script:Synced = $true; return $true }}
          Update-ClaudePlugins -Plugins @($plugin)
          [pscustomobject]@{{ Synced = $script:Synced; Failures = @($script:ClaudeFailures) }} | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertTrue(data["Synced"], result.stdout)
        self.assertEqual(data["Failures"], ["marketplace:preserved"])
        self.assertIn("Validated Claude Code plugin marketplace", result.stdout)

    def test_marketplace_availability_keys_are_case_sensitive(self) -> None:
        healthy = self.root / "lowercase-marketplace"
        catalog = healthy / ".claude-plugin" / "marketplace.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(
            json.dumps({"plugins": [{"name": "lower-plugin"}]}),
            encoding="utf-8",
        )
        missing = self.root / "uppercase-marketplace"
        missing.mkdir()
        body = f"""
          $plugins = @(
            [pscustomobject]@{{ Id = 'upper-plugin@Case'; Name = 'upper-plugin'; Marketplace = 'Case'; Scope = 'user' }},
            [pscustomobject]@{{ Id = 'lower-plugin@case'; Name = 'lower-plugin'; Marketplace = 'case'; Scope = 'user' }}
          )
          $script:ClaudeFailures = @()
          $script:Synced = @()
          function Invoke-External {{ [pscustomobject]@{{ ExitCode = 0; Output = '' }} }}
          function Get-ClaudeMarketplaceInventory {{
            [pscustomobject]@{{
              Ok = $true
              Error = $null
              Records = @(
                [pscustomobject]@{{ name = 'Case'; installLocation = {ps_quote(missing)} }},
                [pscustomobject]@{{ name = 'case'; installLocation = {ps_quote(healthy)} }}
              )
            }}
          }}
          function Get-InstalledClaudePluginIds {{
            $ids = [System.Collections.Generic.HashSet[string]]::new()
            return ,$ids
          }}
          function Sync-ClaudePlugin {{ param($Plugin, [string[]] $Actions); $script:Synced += $Plugin.Id; return $true }}
          Update-ClaudePlugins -Plugins $plugins
          [pscustomobject]@{{ Synced = @($script:Synced); Failures = @($script:ClaudeFailures) }} | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(data["Synced"], ["lower-plugin@case"], result.stdout)
        self.assertEqual(data["Failures"], ["marketplace:Case"])

    def test_claude_dry_run_previews_named_marketplaces_without_commands(self) -> None:
        body = """
          $plugins = @(
            [pscustomobject]@{ Id = 'one@shared'; Name = 'one'; Marketplace = 'shared'; Scope = 'user' },
            [pscustomobject]@{ Id = 'two@shared'; Name = 'two'; Marketplace = 'shared'; Scope = 'user' }
          )
          $script:Called = $false
          function Invoke-External { $script:Called = $true; throw 'must not run' }
          function Get-InstalledClaudePluginIds {
            $ids = [System.Collections.Generic.HashSet[string]]::new()
            return ,$ids
          }
          Update-ClaudePlugins -Plugins $plugins
          [pscustomobject]@{ Called = $script:Called } | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(
            body,
            env_updates={"HOST_AI_PLUGIN_REFRESH_DRY_RUN": "1"},
        )
        data = self.read_json(result)
        self.assertFalse(data["Called"])
        self.assertEqual(
            result.stdout.count("claude plugin marketplace update shared"), 1
        )
        self.assertNotIn("is unavailable", result.stdout)
        self.assertIn(
            "Would run: claude plugin install --scope user one@shared",
            result.stdout,
        )
        self.assertIn(
            "Would run: claude plugin install --scope user two@shared",
            result.stdout,
        )

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
