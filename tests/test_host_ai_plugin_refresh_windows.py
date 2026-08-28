from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
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


def ps_encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


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
          $script:Timeouts = @()
          function Get-ClaudeOpenSshApplication { [pscustomobject]@{ Source = 'C:\\Windows\\System32\\OpenSSH\\ssh.exe' } }
          function Invoke-JobContainedExternal {
            param([string] $Command, [string[]] $Arguments, [int] $TimeoutSeconds, [hashtable] $EnvironmentVariables)
            $script:Observed += [pscustomobject]@{
              Preserve = $EnvironmentVariables['CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE']
              Prompt = $EnvironmentVariables['GIT_TERMINAL_PROMPT']
              SshCommand = $EnvironmentVariables['GIT_SSH_COMMAND']
            }
            $script:Timeouts += $TimeoutSeconds
            [pscustomobject]@{ ExitCode = 0; Output = ''; TimedOut = $false }
          }
          Remove-Item Env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE -ErrorAction SilentlyContinue
          Remove-Item Env:GIT_SSH_COMMAND -ErrorAction SilentlyContinue
          $env:GIT_SSH = 'C:\\Program Files\\PuTTY\\PLINK.EXE'
          Invoke-ClaudeExternal -Arguments @('plugin', 'marketplace', 'update', 'one') | Out-Null
          $absentRestored = -not (Test-Path Env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE)
          $sshCommandAbsent = -not (Test-Path Env:GIT_SSH_COMMAND)
          $env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE = 'caller-value'
          $env:GIT_SSH_COMMAND = 'caller-ssh-command'
          Invoke-ClaudeExternal -Arguments @('plugin', 'update', 'one@one') | Out-Null
          [pscustomobject]@{
            Observed = @($script:Observed)
            Timeouts = @($script:Timeouts)
            AbsentRestored = $absentRestored
            SshCommandAbsent = $sshCommandAbsent
            ExistingRestored = $env:CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE
            ParentSshCommand = $env:GIT_SSH_COMMAND
            ParentGitSsh = $env:GIT_SSH
          } | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(
            [entry["Preserve"] for entry in data["Observed"]], ["1", "1"]
        )
        self.assertEqual(
            [entry["Prompt"] for entry in data["Observed"]], ["0", "0"]
        )
        self.assertEqual(
            [entry["SshCommand"] for entry in data["Observed"]],
            [
                "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "
                "-o ConnectTimeout=15 -o ConnectionAttempts=1"
            ]
            * 2,
        )
        self.assertEqual(data["Timeouts"], [120, 120])
        self.assertTrue(data["AbsentRestored"])
        self.assertTrue(data["SshCommandAbsent"])
        self.assertEqual(data["ExistingRestored"], "caller-value")
        self.assertEqual(data["ParentSshCommand"], "caller-ssh-command")
        self.assertEqual(data["ParentGitSsh"], r"C:\Program Files\PuTTY\PLINK.EXE")

    def test_missing_openssh_fails_before_claude_launch(self) -> None:
        body = """
          $script:Called = $false
          function Get-ClaudeOpenSshApplication { return $null }
          function Invoke-JobContainedExternal { $script:Called = $true; throw 'must not launch' }
          $result = Invoke-ClaudeExternal -Arguments @('plugin', 'marketplace', 'update', 'one')
          [pscustomobject]@{
            ExitCode = $result.ExitCode
            TimedOut = $result.TimedOut
            Output = $result.Output
            Called = $script:Called
          } | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["ExitCode"], 125)
        self.assertFalse(data["TimedOut"])
        self.assertFalse(data["Called"])
        self.assertIn("OpenSSH 'ssh' Application", data["Output"])
        self.assertIn("refusing to inherit interactive PuTTY/Plink", data["Output"])

    def test_host_key_failure_includes_verified_known_hosts_remediation(self) -> None:
        body = """
          function Get-ClaudeOpenSshApplication { [pscustomobject]@{ Source = 'ssh.exe' } }
          function Invoke-JobContainedExternal {
            [pscustomobject]@{ ExitCode = 255; Output = 'Host key verification failed.'; TimedOut = $false }
          }
          Invoke-ClaudeExternal -Arguments @('plugin', 'marketplace', 'update', 'one') | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["ExitCode"], 255)
        self.assertFalse(data["TimedOut"])
        self.assertIn("ssh-keyscan github.com", data["Output"])
        self.assertIn("githubs-ssh-key-fingerprints", data["Output"])
        self.assertIn("only verified keys", data["Output"])
        self.assertIn("PuTTY/Plink uses a separate host-key cache", data["Output"])

    @unittest.skipUnless(os.name == "nt", "Windows Job Objects require native Windows")
    def test_contained_runner_preserves_fast_output_exit_and_environment(self) -> None:
        target_script = "[Console]::Out.Write($env:DOTFILES_TEST_VALUE); exit 0"
        body = f"""
          $env:DOTFILES_TEST_VALUE = 'caller'
          $result = Invoke-JobContainedExternal `
            -Command {ps_quote(PWSH)} `
            -Arguments @('-NoProfile', '-EncodedCommand', {ps_quote(ps_encoded(target_script))}) `
            -TimeoutSeconds 5 `
            -EnvironmentVariables @{{ DOTFILES_TEST_VALUE = 'child' }}
          [pscustomobject]@{{
            ExitCode = $result.ExitCode
            Output = $result.Output
            TimedOut = $result.TimedOut
            ParentValue = $env:DOTFILES_TEST_VALUE
          }} | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["ExitCode"], 0)
        self.assertEqual(data["Output"], "child")
        self.assertFalse(data["TimedOut"])
        self.assertEqual(data["ParentValue"], "caller")

    @unittest.skipUnless(os.name == "nt", "Windows Job Objects require native Windows")
    def test_contained_runner_preserves_native_failure_output_and_exit(self) -> None:
        target_script = (
            '[Console]::Out.WriteLine("stdout-marker"); '
            '[Console]::Error.WriteLine("stderr-marker"); exit 3'
        )
        body = f"""
          $result = Invoke-JobContainedExternal `
            -Command {ps_quote(PWSH)} `
            -Arguments @('-NoProfile', '-EncodedCommand', {ps_quote(ps_encoded(target_script))}) `
            -TimeoutSeconds 5 `
            -EnvironmentVariables @{{}}
          $result | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["ExitCode"], 3)
        self.assertFalse(data["TimedOut"])
        self.assertIn("stdout-marker", data["Output"])
        self.assertIn("stderr-marker", data["Output"])

    @unittest.skipUnless(os.name == "nt", "Windows Job Objects require native Windows")
    def test_contained_runner_reaps_descendant_without_touching_sentinel(self) -> None:
        sleeper = ps_encoded("Start-Sleep -Seconds 60")
        parent = ps_encoded(
            "$child = Start-Process -FilePath "
            f"{ps_quote(PWSH)} "
            "-ArgumentList @('-NoProfile', '-EncodedCommand', "
            f"{ps_quote(sleeper)}) -PassThru; "
            "[pscustomobject]@{ Pid = $child.Id; Started = $child.StartTime.ToUniversalTime().Ticks } "
            "| ConvertTo-Json -Compress | Write-Output"
        )
        body = f"""
          $sentinel = Start-Process -FilePath {ps_quote(PWSH)} -ArgumentList @('-NoProfile', '-EncodedCommand', {ps_quote(sleeper)}) -PassThru
          $childId = $null
          try {{
            $result = Invoke-JobContainedExternal `
              -Command {ps_quote(PWSH)} `
              -Arguments @('-NoProfile', '-EncodedCommand', {ps_quote(parent)}) `
              -TimeoutSeconds 5 `
              -EnvironmentVariables @{{}}
            $identity = ConvertFrom-Json -InputObject $result.Output
            $childId = [int] $identity.Pid
            $candidate = Get-Process -Id $childId -ErrorAction SilentlyContinue
            $childIdentityAlive = $null -ne $candidate -and $candidate.StartTime.ToUniversalTime().Ticks -eq [long] $identity.Started
            $sentinelAlive = -not $sentinel.HasExited
            $data = [pscustomobject]@{{
              ExitCode = $result.ExitCode
              TimedOut = $result.TimedOut
              ChildIdentityAlive = $childIdentityAlive
              SentinelAlive = $sentinelAlive
            }}
          }} finally {{
            if ($null -ne $childId) {{ Stop-Process -Id $childId -Force -ErrorAction SilentlyContinue }}
            if (-not $sentinel.HasExited) {{ $sentinel.Kill($true); $sentinel.WaitForExit() }}
            $sentinel.Dispose()
          }}
          $data | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["ExitCode"], 0)
        self.assertFalse(data["TimedOut"])
        self.assertFalse(data["ChildIdentityAlive"])
        self.assertTrue(data["SentinelAlive"])

    @unittest.skipUnless(os.name == "nt", "Windows Job Objects require native Windows")
    def test_contained_runner_times_out_and_reaps_command_tree(self) -> None:
        pid_file = self.root / "timeout-processes.json"
        marker = f"dots-vin4-{self.root.name}"
        target = (
            "import json, os, subprocess, sys, time; "
            f"marker={marker!r}; "
            "child=subprocess.Popen([sys.executable, '-c', "
            "f'import time; marker={marker!r}; time.sleep(60)']); "
            "json.dump({'Parent': os.getpid(), 'Child': child.pid, 'Marker': marker}, "
            "open(os.environ['DOTFILES_TEST_PID_FILE'], 'w')); "
            "time.sleep(60)"
        )
        body = f"""
          $ids = @()
          try {{
            $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
            $result = Invoke-JobContainedExternal `
              -Command {ps_quote(sys.executable)} `
              -Arguments @('-c', {ps_quote(target)}) `
              -TimeoutSeconds 1 `
              -EnvironmentVariables @{{ DOTFILES_TEST_PID_FILE = {ps_quote(pid_file)} }}
            $stopwatch.Stop()
            $identity = Get-Content -Raw -LiteralPath {ps_quote(pid_file)} | ConvertFrom-Json
            $ids = @([int] $identity.Parent, [int] $identity.Child)
            $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($identity.Parent)" -ErrorAction SilentlyContinue
            $child = Get-CimInstance Win32_Process -Filter "ProcessId = $($identity.Child)" -ErrorAction SilentlyContinue
            $parentIdentityAlive = $null -ne $parent -and $parent.ExecutablePath -eq {ps_quote(sys.executable)} -and $parent.CommandLine -like "*$($identity.Marker)*"
            $childIdentityAlive = $null -ne $child -and $child.ExecutablePath -eq {ps_quote(sys.executable)} -and $child.CommandLine -like "*$($identity.Marker)*"
            $data = [pscustomobject]@{{
              ExitCode = $result.ExitCode
              Output = $result.Output
              TimedOut = $result.TimedOut
              ElapsedSeconds = $stopwatch.Elapsed.TotalSeconds
              ParentIdentityAlive = $parentIdentityAlive
              ChildIdentityAlive = $childIdentityAlive
            }}
          }} finally {{
            foreach ($processId in $ids) {{ Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue }}
          }}
          $data | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["ExitCode"], 124)
        self.assertTrue(data["TimedOut"])
        self.assertIn("timed out after 1 seconds", data["Output"])
        self.assertLess(data["ElapsedSeconds"], 10)
        self.assertFalse(data["ParentIdentityAlive"])
        self.assertFalse(data["ChildIdentityAlive"])

    @unittest.skipUnless(os.name == "nt", "Windows Job Objects require native Windows")
    def test_timeout_metadata_survives_cleanup_failure(self) -> None:
        target = ps_encoded("Start-Sleep -Seconds 60")
        body = f"""
          function Stop-ClaudeProcessJob {{
            param($Job, [int] $TimeoutMilliseconds)
            $Job.Terminate(124)
            return $false
          }}
          $result = Invoke-JobContainedExternal `
            -Command {ps_quote(PWSH)} `
            -Arguments @('-NoProfile', '-EncodedCommand', {ps_quote(target)}) `
            -TimeoutSeconds 1 `
            -EnvironmentVariables @{{}}
          $result | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["ExitCode"], 125)
        self.assertTrue(data["TimedOut"])
        self.assertIn("timed out after 1 seconds", data["Output"])
        self.assertIn("process cleanup failed", data["Output"])

    @unittest.skipUnless(os.name == "nt", "Windows Job Objects require native Windows")
    def test_containment_failure_does_not_release_target(self) -> None:
        marker = self.root / "must-not-exist.marker"
        target = ps_encoded(
            f"Set-Content -LiteralPath {ps_quote(marker)} -Value 'launched'"
        )
        body = f"""
          function Add-ClaudeWorkerToJob {{ throw 'forced assignment failure' }}
          $result = Invoke-JobContainedExternal `
            -Command {ps_quote(PWSH)} `
            -Arguments @('-NoProfile', '-EncodedCommand', {ps_quote(target)}) `
            -TimeoutSeconds 5 `
            -EnvironmentVariables @{{}}
          [pscustomobject]@{{
            ExitCode = $result.ExitCode
            Output = $result.Output
            TimedOut = $result.TimedOut
            TargetLaunched = Test-Path -LiteralPath {ps_quote(marker)}
          }} | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["ExitCode"], 125)
        self.assertFalse(data["TimedOut"])
        self.assertIn("forced assignment failure", data["Output"])
        self.assertFalse(data["TargetLaunched"])

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
          function Test-ClaudeCommandAvailable {{ return $true }}
          function Get-ClaudeOpenSshApplication {{ [pscustomobject]@{{ Source = 'ssh.exe' }} }}
          function Invoke-JobContainedExternal {{
            param([string] $Command, [string[]] $Arguments, [int] $TimeoutSeconds, [hashtable] $EnvironmentVariables)
            $script:Commands += ,@($Arguments)
            [pscustomobject]@{{ ExitCode = 0; Output = ''; TimedOut = $false }}
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
            param($Plugin, [string[]] $Actions, [hashtable] $EnvironmentVariables)
            $script:Synced += $Plugin.Id
            return [pscustomobject]@{{ Succeeded = $true; TimedOut = $false }}
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

    def test_missing_openssh_stops_claude_and_continues_opencode(self) -> None:
        manifest = self.root / "missing-openssh-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "$schema": "./schemas/host-ai-plugin-refresh.v1.schema.json",
                    "schema_version": 1,
                }
            ),
            encoding="utf-8",
        )
        body = f"""
          $plugins = @(
            [pscustomobject]@{{ Id = 'one@marketplace'; Name = 'one'; Marketplace = 'marketplace'; Scope = 'user' }}
          )
          $script:TestPlugins = $plugins
          $script:Commands = @()
          $script:OpenCodeCalled = $false
          function Test-ClaudeCommandAvailable {{ return $true }}
          function Get-ClaudeOpenSshApplication {{ return $null }}
          function Invoke-JobContainedExternal {{
            $script:Commands += 'unexpected'
            throw 'Claude must not launch without OpenSSH'
          }}
          function Get-ClaudeMarketplaceInventory {{ throw 'inventory must not run without OpenSSH' }}
          function Get-InstalledClaudePluginIds {{ throw 'plugin list must not run without OpenSSH' }}
          function Sync-ClaudePlugin {{ throw 'plugin mutation must not run without OpenSSH' }}
          function Get-ClaudePlugins {{ return $script:TestPlugins }}
          function Test-ClaudeEnablement {{}}
          function Get-OpenCodePlugins {{ return @() }}
          function Update-OpenCodeCache {{ $script:OpenCodeCalled = $true; return $true }}
          $ConfigPath = {ps_quote(manifest)}
          $code = Invoke-HostAiPluginRefresh
          [pscustomobject]@{{
            Code = $code
            Commands = @($script:Commands)
            OpenCodeCalled = $script:OpenCodeCalled
            Failures = @($script:ClaudeFailures)
          }} | ConvertTo-Json -Depth 5 -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(data["Commands"], [])
        self.assertTrue(data["OpenCodeCalled"])
        self.assertEqual(data["Failures"], ["transport:openssh"])
        self.assertEqual(data["Code"], 1)
        self.assertIn("OpenSSH 'ssh' Application", result.stdout)
        self.assertIn("refusing to inherit interactive PuTTY/Plink", result.stdout)
        self.assertIn("chezmoi will retry", result.stdout)

    def test_marketplace_timeout_stops_claude_and_continues_opencode(self) -> None:
        manifest = self.root / "timeout-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "$schema": "./schemas/host-ai-plugin-refresh.v1.schema.json",
                    "schema_version": 1,
                }
            ),
            encoding="utf-8",
        )
        body = f"""
          $plugins = @(
            [pscustomobject]@{{ Id = 'first-plugin@first'; Name = 'first-plugin'; Marketplace = 'first'; Scope = 'user' }},
            [pscustomobject]@{{ Id = 'second-plugin@second'; Name = 'second-plugin'; Marketplace = 'second'; Scope = 'user' }}
          )
          $script:ClaudeFailures = @()
          $script:TestPlugins = $plugins
          $script:Commands = @()
          $script:OpenCodeCalled = $false
          function Test-ClaudeCommandAvailable {{ return $true }}
          function Get-ClaudeOpenSshApplication {{ [pscustomobject]@{{ Source = 'ssh.exe' }} }}
          function Invoke-JobContainedExternal {{
            param([string] $Command, [string[]] $Arguments, [int] $TimeoutSeconds, [hashtable] $EnvironmentVariables)
            $script:Commands += ,@($Arguments)
            return [pscustomobject]@{{ ExitCode = 124; Output = 'Claude command timed out after 120 seconds.'; TimedOut = $true }}
          }}
          function Get-ClaudeMarketplaceInventory {{ throw 'inventory must not run after timeout' }}
          function Get-InstalledClaudePluginIds {{ throw 'plugin list must not run after timeout' }}
          function Sync-ClaudePlugin {{ throw 'plugin mutation must not run after timeout' }}
          function Get-ClaudePlugins {{ return $script:TestPlugins }}
          function Test-ClaudeEnablement {{}}
          function Get-OpenCodePlugins {{ return @() }}
          function Update-OpenCodeCache {{ $script:OpenCodeCalled = $true; return $true }}
          $ConfigPath = {ps_quote(manifest)}
          $code = Invoke-HostAiPluginRefresh
          [pscustomobject]@{{
            Code = $code
            Commands = @($script:Commands | ForEach-Object {{ $_ -join ' ' }})
            OpenCodeCalled = $script:OpenCodeCalled
            Failures = @($script:ClaudeFailures)
          }} | ConvertTo-Json -Depth 5 -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(data["Commands"], ["plugin marketplace update first"])
        self.assertTrue(data["OpenCodeCalled"])
        self.assertEqual(data["Failures"], ["marketplace:first"])
        self.assertEqual(data["Code"], 1)
        self.assertIn("timed out after 120 seconds", result.stdout)
        self.assertIn("Stopping remaining Claude Code mutations", result.stdout)
        self.assertIn("chezmoi will retry", result.stdout)
        self.assertIn("One or more Claude Code plugin refreshes failed", result.stdout)

    def test_plugin_timeout_stops_later_plugins_and_continues_opencode(self) -> None:
        first = self.root / "first-plugin-marketplace"
        second = self.root / "second-plugin-marketplace"
        manifest = self.root / "plugin-timeout-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "$schema": "./schemas/host-ai-plugin-refresh.v1.schema.json",
                    "schema_version": 1,
                }
            ),
            encoding="utf-8",
        )
        for path, plugin in ((first, "first-plugin"), (second, "second-plugin")):
            catalog = path / ".claude-plugin" / "marketplace.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(
                json.dumps({"plugins": [{"name": plugin}]}), encoding="utf-8"
            )
        body = f"""
          $plugins = @(
            [pscustomobject]@{{ Id = 'first-plugin@first'; Name = 'first-plugin'; Marketplace = 'first'; Scope = 'user' }},
            [pscustomobject]@{{ Id = 'second-plugin@second'; Name = 'second-plugin'; Marketplace = 'second'; Scope = 'user' }}
          )
          $script:TestPlugins = $plugins
          $script:Commands = @()
          $script:OpenCodeCalled = $false
          function Test-ClaudeCommandAvailable {{ return $true }}
          function Get-ClaudeOpenSshApplication {{ [pscustomobject]@{{ Source = 'ssh.exe' }} }}
          function Invoke-JobContainedExternal {{
            param([string] $Command, [string[]] $Arguments, [int] $TimeoutSeconds, [hashtable] $EnvironmentVariables)
            $script:Commands += ,@($Arguments)
            if ($Arguments[-1] -eq 'first-plugin@first') {{
              return [pscustomobject]@{{ ExitCode = 124; Output = 'Claude command timed out after 120 seconds.'; TimedOut = $true }}
            }}
            return [pscustomobject]@{{ ExitCode = 0; Output = ''; TimedOut = $false }}
          }}
          function Get-ClaudeMarketplaceInventory {{
            [pscustomobject]@{{
              Ok = $true
              Error = $null
              Records = @(
                [pscustomobject]@{{ name = 'first'; installLocation = {ps_quote(first)} }},
                [pscustomobject]@{{ name = 'second'; installLocation = {ps_quote(second)} }}
              )
            }}
          }}
          function Get-InstalledClaudePluginIds {{
            $ids = [System.Collections.Generic.HashSet[string]]::new()
            return ,$ids
          }}
          function Get-ClaudePlugins {{ return $script:TestPlugins }}
          function Test-ClaudeEnablement {{}}
          function Get-OpenCodePlugins {{ return @() }}
          function Update-OpenCodeCache {{ $script:OpenCodeCalled = $true; return $true }}
          $ConfigPath = {ps_quote(manifest)}
          $code = Invoke-HostAiPluginRefresh
          [pscustomobject]@{{
            Code = $code
            Commands = @($script:Commands | ForEach-Object {{ $_ -join ' ' }})
            OpenCodeCalled = $script:OpenCodeCalled
            Failures = @($script:ClaudeFailures)
          }} | ConvertTo-Json -Depth 5 -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(
            data["Commands"],
            [
                "plugin marketplace update first",
                "plugin marketplace update second",
                "plugin install --scope user first-plugin@first",
            ],
        )
        self.assertTrue(data["OpenCodeCalled"])
        self.assertEqual(data["Failures"], ["first-plugin@first"])
        self.assertEqual(data["Code"], 1)
        self.assertIn("Stopping remaining Claude Code plugin mutations", result.stdout)
        self.assertIn("chezmoi will retry", result.stdout)

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
          function Test-ClaudeCommandAvailable {{ return $true }}
          function Get-ClaudeOpenSshApplication {{ [pscustomobject]@{{ Source = 'ssh.exe' }} }}
          function Invoke-JobContainedExternal {{
            param([string] $Command, [string[]] $Arguments, [int] $TimeoutSeconds, [hashtable] $EnvironmentVariables)
            [pscustomobject]@{{ ExitCode = 1; Output = 'network unavailable'; TimedOut = $false }}
          }}
          function Get-ClaudeMarketplaceInventory {{
            [pscustomobject]@{{ Ok = $true; Error = $null; Records = @([pscustomobject]@{{ name = 'preserved'; installLocation = {ps_quote(marketplace)} }}) }}
          }}
          function Get-InstalledClaudePluginIds {{
            $ids = [System.Collections.Generic.HashSet[string]]::new()
            return ,$ids
          }}
          function Sync-ClaudePlugin {{
            param($Plugin, [string[]] $Actions, [hashtable] $EnvironmentVariables)
            $script:Synced = $true
            return [pscustomobject]@{{ Succeeded = $true; TimedOut = $false }}
          }}
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
          function Test-ClaudeCommandAvailable {{ return $true }}
          function Get-ClaudeOpenSshApplication {{ [pscustomobject]@{{ Source = 'ssh.exe' }} }}
          function Invoke-JobContainedExternal {{ [pscustomobject]@{{ ExitCode = 0; Output = ''; TimedOut = $false }} }}
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
          function Sync-ClaudePlugin {{
            param($Plugin, [string[]] $Actions, [hashtable] $EnvironmentVariables)
            $script:Synced += $Plugin.Id
            return [pscustomobject]@{{ Succeeded = $true; TimedOut = $false }}
          }}
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
          function Test-ClaudeCommandAvailable { return $true }
          function Invoke-JobContainedExternal { $script:Called = $true; throw 'must not run' }
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
          function Get-ClaudeOpenSshApplication { [pscustomobject]@{ Source = 'ssh.exe' } }
          function Invoke-JobContainedExternal {
            param([string] $Command, [string[]] $Arguments, [int] $TimeoutSeconds, [hashtable] $EnvironmentVariables)
            $action = $Arguments[1]
            $script:Attempts += $action
            if ($action -eq 'update') {
              return [pscustomobject]@{ ExitCode = 1; Output = 'Plugin "plannotator" not found'; TimedOut = $false }
            }
            return [pscustomobject]@{ ExitCode = 0; Output = ''; TimedOut = $false }
          }
          $sync = Sync-ClaudePlugin -Plugin $plugin -Actions $actions
          [pscustomobject]@{
            Ok = $sync.Succeeded
            TimedOut = $sync.TimedOut
            Actions = @($actions)
            Attempts = @($script:Attempts)
          } | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertTrue(data["Ok"])
        self.assertFalse(data["TimedOut"])
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
          function Get-ClaudeOpenSshApplication { [pscustomobject]@{ Source = 'ssh.exe' } }
          function Invoke-JobContainedExternal {
            param([string] $Command, [string[]] $Arguments, [int] $TimeoutSeconds, [hashtable] $EnvironmentVariables)
            $script:Attempts += $Arguments[1]
            return [pscustomobject]@{ ExitCode = 1; Output = 'network unavailable'; TimedOut = $false }
          }
          $sync = Sync-ClaudePlugin -Plugin $plugin -Actions $actions
          [pscustomobject]@{ Ok = $sync.Succeeded; TimedOut = $sync.TimedOut; Attempts = @($script:Attempts) } | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertFalse(data["Ok"])
        self.assertFalse(data["TimedOut"])
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
