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
    / ".chezmoiscripts/run_after_install_codebase-memory-mcp.ps1.tmpl"
)
PWSH = shutil.which("pwsh")


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(PWSH, "native pwsh is not installed")
class WindowsCodebaseMemoryMcpInstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.rendered = self.root / "install-codebase-memory-mcp.ps1"
        self.render_template()

    def render_template(self) -> None:
        source = TEMPLATE.read_text(encoding="utf-8")
        controls = {
            '{{- if ne .chezmoi.os "windows" -}}',
            "{{- /* not Windows: do nothing */ -}}",
            "{{- else -}}",
            "{{- end -}}",
            "{{- end }}",
        }
        source = "\n".join(
            line for line in source.splitlines() if line not in controls
        )
        source = source.replace("{{ .codebase_memory_mcp_version }}", "0.10.2")
        self.rendered.write_text(source + "\n", encoding="utf-8")

    def run_pwsh(self, body: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update({"HOME": str(self.home), "USERPROFILE": str(self.home)})
        env.pop("CBM_CACHE_DIR", None)
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

    def test_dot_source_does_not_run_installer(self) -> None:
        result = self.run_pwsh(
            "[pscustomobject]@{ CacheExists = Test-Path -LiteralPath $CacheDir; "
            "InstallerDefined = [bool](Get-Command Invoke-CbmInstaller) } "
            "| ConvertTo-Json -Compress"
        )
        data = self.read_json(result)
        self.assertFalse(data["CacheExists"])
        self.assertTrue(data["InstallerDefined"])

    def test_claude_and_opencode_processes_are_blocking(self) -> None:
        body = """
          function Get-CimInstance {
            param([string] $ClassName, [string] $Filter, $ErrorAction)
            @(
              [pscustomobject]@{ Name = 'claude.exe'; ProcessId = 41; SessionId = 1; ParentProcessId = 7; ExecutablePath = 'C:\\Tools\\claude.exe' },
              [pscustomobject]@{ Name = 'opencode.exe'; ProcessId = 42; SessionId = 1; ParentProcessId = 8; ExecutablePath = 'C:\\Tools\\opencode.exe' }
            )
          }
          $state = Get-CodingAgentProcessState
          $proceed = Test-CodingAgentsStopped -Phase 'test upgrade' -WarningVariable warning -WarningAction SilentlyContinue
          [pscustomobject]@{
            Status = $state.Status
            Names = @($state.Processes.Name)
            Proceed = $proceed
            Warning = ($warning -join ' ')
          } | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(data["Status"], "Running")
        self.assertEqual(data["Names"], ["claude.exe", "opencode.exe"])
        self.assertFalse(data["Proceed"])
        self.assertIn("deferred test upgrade", str(data["Warning"]))
        self.assertIn("pid=41", result.stdout)
        self.assertIn("pid=42", result.stdout)

    def test_process_inspection_failure_defers_upgrade(self) -> None:
        body = """
          function Get-CimInstance { throw 'cim denied' }
          function Get-Process { throw 'process denied' }
          $state = Get-CodingAgentProcessState
          $proceed = Test-CodingAgentsStopped -Phase 'test upgrade' -WarningVariable warning -WarningAction SilentlyContinue
          [pscustomobject]@{
            Status = $state.Status
            Error = $state.Error
            Proceed = $proceed
            Warning = ($warning -join ' ')
          } | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["Status"], "Unknown")
        self.assertIn("cim denied", str(data["Error"]))
        self.assertIn("process denied", str(data["Error"]))
        self.assertFalse(data["Proceed"])
        self.assertIn("deferred test upgrade", str(data["Warning"]))

    def test_managed_process_query_uses_exact_executable_path(self) -> None:
        managed = self.home / ".local" / "codebase-memory-mcp.exe"
        other = self.root / "other" / "codebase-memory-mcp.exe"
        body = f"""
          function Get-CimInstance {{
            param([string] $ClassName, [string] $Filter, $ErrorAction)
            @(
              [pscustomobject]@{{ Name = 'codebase-memory-mcp.exe'; ProcessId = 51; SessionId = 1; ParentProcessId = 7; ExecutablePath = {ps_quote(managed)} }},
              [pscustomobject]@{{ Name = 'codebase-memory-mcp.exe'; ProcessId = 52; SessionId = 1; ParentProcessId = 8; ExecutablePath = {ps_quote(other)} }}
            )
          }}
          $state = Get-ManagedCbmProcessState -ManagedPath {ps_quote(str(managed).upper())}
          [pscustomobject]@{{ Status = $state.Status; Ids = @($state.Processes.Id); Paths = @($state.Processes.Path) }} | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["Status"], "Known")
        self.assertEqual(data["Ids"], [51])
        self.assertEqual(data["Paths"], [str(managed)])

    def test_graceful_shutdown_needs_no_forced_cleanup(self) -> None:
        managed = self.home / ".local" / "codebase-memory-mcp.exe"
        body = f"""
          $script:Forced = $false
          $script:AgentGuarded = $false
          function Invoke-CbmNative {{ [pscustomobject]@{{ ExitCode = 0; Output = ''; TimedOut = $false; StillRunning = $false; ProcessId = 60 }} }}
          function Get-ManagedCbmProcessState {{ [pscustomobject]@{{ Status = 'Known'; Processes = @(); Error = $null }} }}
          function Test-CodingAgentsStopped {{ $script:AgentGuarded = $true; return $true }}
          function Stop-CbmProcessById {{ $script:Forced = $true }}
          $proceed = Stop-CbmForUpgrade -ManagedPath {ps_quote(managed)}
          [pscustomobject]@{{ Forced = $script:Forced; AgentGuarded = $script:AgentGuarded; Proceed = $proceed }} | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertFalse(data["Forced"])
        self.assertFalse(data["AgentGuarded"])
        self.assertTrue(data["Proceed"])

    def test_timeout_forces_only_preselected_managed_processes(self) -> None:
        managed = self.home / ".local" / "codebase-memory-mcp.exe"
        body = f"""
          $script:Stopped = @()
          $script:GuardCount = 0
          function Invoke-CbmNative {{ [pscustomobject]@{{ ExitCode = $null; Output = ''; TimedOut = $true; StillRunning = $false; ProcessId = 60 }} }}
          function Get-ManagedCbmProcessState {{
            [pscustomobject]@{{ Status = 'Known'; Processes = @([pscustomobject]@{{ Id = 61; SessionId = 1; ParentProcessId = 9; Path = {ps_quote(managed)} }}); Error = $null }}
          }}
          function Test-CodingAgentsStopped {{ $script:GuardCount++; return $true }}
          function Stop-CbmProcessById {{ param([int] $Id, [string] $ManagedPath); $script:Stopped += $Id }}
          function Wait-ForManagedCbmProcessesToExit {{ [pscustomobject]@{{ Status = 'Known'; Processes = @(); Error = $null }} }}
          $proceed = Stop-CbmForUpgrade -ManagedPath {ps_quote(managed)}
          [pscustomobject]@{{ Stopped = @($script:Stopped); GuardCount = $script:GuardCount; Proceed = $proceed }} | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(data["Stopped"], [61])
        self.assertEqual(data["GuardCount"], 1)
        self.assertTrue(data["Proceed"])
        self.assertIn("timed out", result.stdout)

    def test_agent_appearing_before_forced_cleanup_defers(self) -> None:
        managed = self.home / ".local" / "codebase-memory-mcp.exe"
        body = f"""
          $script:Forced = $false
          function Invoke-CbmNative {{ [pscustomobject]@{{ ExitCode = 1; Output = ''; TimedOut = $false; StillRunning = $false; ProcessId = 60 }} }}
          function Get-ManagedCbmProcessState {{
            [pscustomobject]@{{ Status = 'Known'; Processes = @([pscustomobject]@{{ Id = 61; Path = {ps_quote(managed)} }}); Error = $null }}
          }}
          function Test-CodingAgentsStopped {{ return $false }}
          function Stop-CbmProcessById {{ $script:Forced = $true }}
          $proceed = Stop-CbmForUpgrade -ManagedPath {ps_quote(managed)}
          [pscustomobject]@{{ Forced = $script:Forced; Proceed = $proceed }} | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(data["Forced"])
        self.assertFalse(data["Proceed"])

    def test_surviving_managed_process_aborts_publication(self) -> None:
        managed = self.home / ".local" / "codebase-memory-mcp.exe"
        body = f"""
          $process = [pscustomobject]@{{ Id = 61; SessionId = 1; ParentProcessId = 9; Path = {ps_quote(managed)} }}
          function Invoke-CbmNative {{ [pscustomobject]@{{ ExitCode = 1; Output = ''; TimedOut = $false; StillRunning = $false; ProcessId = 60 }} }}
          function Get-ManagedCbmProcessState {{ [pscustomobject]@{{ Status = 'Known'; Processes = @($process); Error = $null }} }}
          function Test-CodingAgentsStopped {{ return $true }}
          function Stop-CbmProcessById {{ }}
          function Wait-ForManagedCbmProcessesToExit {{ [pscustomobject]@{{ Status = 'Known'; Processes = @($process); Error = $null }} }}
          $message = $null
          try {{ Stop-CbmForUpgrade -ManagedPath {ps_quote(managed)} }} catch {{ $message = $_.Exception.Message }}
          [pscustomobject]@{{ Message = $message }} | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertIn("existing binary was left in place", str(data["Message"]))
        self.assertIn("pid=61", result.stdout)
        self.assertIn(str(managed), result.stdout)

    def test_version_probe_uses_bounded_native_runner(self) -> None:
        candidate = self.root / "codebase-memory-mcp.exe"
        candidate.write_bytes(b"test fixture")
        body = f"""
          $script:Observed = $null
          function Invoke-CbmNative {{
            param([string] $Path, [string[]] $Arguments, [int] $TimeoutSeconds)
            $script:Observed = [pscustomobject]@{{ Path = $Path; Arguments = @($Arguments); Timeout = $TimeoutSeconds }}
            [pscustomobject]@{{ ExitCode = 0; Output = 'codebase-memory-mcp 0.10.2'; TimedOut = $false; StillRunning = $false; ProcessId = 70 }}
          }}
          $read = Get-CbmVersion -Path {ps_quote(candidate)}
          [pscustomobject]@{{ Version = $read.Version; TimedOut = $read.TimedOut; Arguments = @($script:Observed.Arguments); Timeout = $script:Observed.Timeout }} | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertEqual(data["Version"], "0.10.2")
        self.assertFalse(data["TimedOut"])
        self.assertEqual(data["Arguments"], ["--version"])
        self.assertGreater(int(data["Timeout"]), 0)

    def test_native_runner_terminates_a_timed_out_command(self) -> None:
        body = """
          $childCommand = '$null = Start-Process -FilePath (Join-Path $PSHOME ''pwsh.exe'') -ArgumentList @(''-NoProfile'', ''-Command'', ''Start-Sleep -Seconds 30'') -NoNewWindow -PassThru; Start-Sleep -Seconds 30'
          $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
          $result = Invoke-CbmNative -Path (Join-Path $PSHOME 'pwsh.exe') -Arguments @('-NoProfile', '-Command', $childCommand) -TimeoutSeconds 1
          $stopwatch.Stop()
          [pscustomobject]@{
            TimedOut = $result.TimedOut
            StillRunning = $result.StillRunning
            ElapsedSeconds = $stopwatch.Elapsed.TotalSeconds
          } | ConvertTo-Json -Compress
        """
        data = self.read_json(self.run_pwsh(body))
        self.assertTrue(data["TimedOut"])
        self.assertFalse(data["StillRunning"])
        self.assertLess(float(data["ElapsedSeconds"]), 8.0)

    def test_installer_has_no_direct_unbounded_cbm_invocation(self) -> None:
        source = TEMPLATE.read_text(encoding="utf-8")
        self.assertNotIn("& $BinPath daemon stop", source)
        self.assertNotIn("& $Path --version", source)
        self.assertIn(
            'Invoke-CbmNative -Path $ManagedPath -Arguments @("daemon", "stop")',
            source,
        )
        self.assertLess(
            source.index('Test-CodingAgentsStopped -Phase "codebase-memory-mcp download"'),
            source.index("Invoke-WebRequest"),
        )
        self.assertLess(
            source.index('Test-CodingAgentsStopped -Phase "stale codebase-memory-mcp cleanup"'),
            source.index("Stop-CbmProcessById -Id"),
        )

    def test_hook_runs_on_every_apply(self) -> None:
        self.assertEqual(
            TEMPLATE.name,
            "run_after_install_codebase-memory-mcp.ps1.tmpl",
            "a deferral that exits zero under run_onchange_ would record its "
            "content hash and never retry",
        )
        self.assertLess(
            TEMPLATE.name,
            "run_after_zz-configure-codebase-memory-mcp.ps1.tmpl",
            "the installer must still sort before the auto-index hook",
        )

    def test_download_deferral_skips_without_failing(self) -> None:
        body = """
          $script:Downloaded = $false
          function Get-CbmVersion { [pscustomobject]@{ Version = '0.9.9'; TimedOut = $false } }
          function Test-CodingAgentsStopped { return $false }
          function Invoke-WebRequest { $script:Downloaded = $true }
          Invoke-CbmInstaller
          [pscustomobject]@{ Downloaded = $script:Downloaded } | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(data["Downloaded"])
        self.assertIn("Updating codebase-memory-mcp: 0.9.9 -> 0.10.2", result.stdout)

    def test_replacement_deferral_leaves_no_partial_state(self) -> None:
        install_dir = self.home / ".local"
        install_dir.mkdir(parents=True)
        existing = install_dir / "codebase-memory-mcp.exe"
        existing.write_text("original", encoding="utf-8")
        body = f"""
          $script:Phases = @()
          $script:StagedAtGate = -1
          $asset = "codebase-memory-mcp-windows-$(Get-NativeReleaseArchitecture).zip"
          function Get-CbmVersion {{
            param([string] $Path)
            if ($Path -eq {ps_quote(existing)}) {{
              return [pscustomobject]@{{ Version = '0.9.9'; TimedOut = $false }}
            }}
            return [pscustomobject]@{{ Version = '0.10.2'; TimedOut = $false }}
          }}
          function Test-CodingAgentsStopped {{
            param([string] $Phase)
            $script:Phases += $Phase
            if ($Phase -eq 'codebase-memory-mcp binary replacement') {{
              $script:StagedAtGate = @(
                Get-ChildItem -LiteralPath {ps_quote(install_dir)} -File -Filter 'codebase-memory-mcp.exe.new-*'
              ).Count
            }}
            return ($Phase -eq 'codebase-memory-mcp download')
          }}
          function Invoke-WebRequest {{
            param([string] $Uri, [string] $OutFile, [switch] $UseBasicParsing)
            if ($Uri -like '*checksums.txt') {{
              Set-Content -LiteralPath $OutFile -Value ("$('a' * 64)  $asset")
            }} else {{
              Set-Content -LiteralPath $OutFile -Value 'archive'
            }}
          }}
          function Get-FileHash {{ [pscustomobject]@{{ Hash = ('A' * 64) }} }}
          function Expand-Archive {{
            param([string] $LiteralPath, [string] $DestinationPath, [switch] $Force)
            $script:TempDir = Split-Path -Parent $DestinationPath
            New-Item -ItemType Directory -Force -Path $DestinationPath | Out-Null
            Set-Content -LiteralPath (Join-Path $DestinationPath 'codebase-memory-mcp.exe') -Value 'replacement'
          }}
          Invoke-CbmInstaller 3>&1 | Out-Null
          [pscustomobject]@{{
            Phases = @($script:Phases)
            StagedAtGate = $script:StagedAtGate
            TempDirRemoved = (-not (Test-Path -LiteralPath $script:TempDir))
            Content = (Get-Content -LiteralPath {ps_quote(existing)} -Raw)
            Remaining = @((Get-ChildItem -LiteralPath {ps_quote(install_dir)} -File).Name)
          }} | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            data["Phases"],
            ["codebase-memory-mcp download", "codebase-memory-mcp binary replacement"],
            "the test must reach the replacement gate, not stop at the download gate",
        )
        self.assertEqual(
            data["StagedAtGate"],
            1,
            "the staged binary must already be on disk at the replacement gate, "
            "or this test proves nothing about the finally block",
        )
        self.assertTrue(
            data["TempDirRemoved"],
            "the finally block must also prune the download directory",
        )
        self.assertEqual(data["Content"], "original")
        self.assertEqual(data["Remaining"], ["codebase-memory-mcp.exe"])

    def test_unreadable_installed_version_defers_instead_of_reinstalling(self) -> None:
        body = """
          $script:Downloaded = $false
          function Get-CbmVersion { [pscustomobject]@{ Version = ''; TimedOut = $true } }
          function Test-CodingAgentsStopped { return $true }
          function Invoke-WebRequest { $script:Downloaded = $true }
          $warning = (Invoke-CbmInstaller 3>&1 | Out-String)
          [pscustomobject]@{ Downloaded = $script:Downloaded; Warning = $warning } | ConvertTo-Json -Compress
        """
        result = self.run_pwsh(body)
        data = self.read_json(result)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(data["Downloaded"])
        self.assertIn(
            "rather than treating it as a fresh install", str(data["Warning"])
        )


if __name__ == "__main__":
    unittest.main()
