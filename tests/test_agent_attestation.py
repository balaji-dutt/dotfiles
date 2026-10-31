import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment
from tests.support.powershell import require_fixture_powershell


class AgentAttestationTests(unittest.TestCase):
    def test_packaging(self):
        root = Path(__file__).resolve().parents[1]
        canonical = root / 'dot_claude/skills/agent-attestation/lib/shared'
        container = root / 'private_Documents/development/container-dotfiles/dotfiles'
        for source in canonical.glob('*.mjs'):
            with self.subTest(module=source.name):
                self.assertEqual(source.read_bytes(), (root / 'private_dot_config/opencode/attestation/shared' / source.name).read_bytes())
        for relative in ['dot_claude/skills/agent-attestation', 'private_dot_config/opencode/attestation']:
            for source in (root / relative).rglob('*'):
                if source.is_file():
                    self.assertEqual(source.read_bytes(), (container / source.relative_to(root)).read_bytes())
        rules = (root / '.chezmoiignore').read_text(encoding='utf-8')
        self.assertIn('!.config/opencode/attestation/**', rules)
        mirrors = (root / 'configs/devcontainer-sync.jsonc').read_text(encoding='utf-8')
        self.assertLess(mirrors.index('agent-attestation-shared'), mirrors.index('opencode-user-config'))

    @unittest.skipUnless(shutil.which("node"), "Node is required")
    def test_behavior(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["node", "--test", "--test-reporter=tap", "tests/support/test_agent_attestation.mjs"],
            cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PowerShellCommitWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = require_fixture_powershell()

    def test_lifecycle_without_jq(self):
        for wrapper, tool in (("oc", "opencode"), ("cc", "claude-code")):
            with self.subTest(wrapper=wrapper, backend=self.backend.label):
                self.run_lifecycle(wrapper, tool, hide_jq=True)

    def test_lifecycle_with_inherited_parser_selection(self):
        for wrapper, tool in (("oc", "opencode"), ("cc", "claude-code")):
            with self.subTest(wrapper=wrapper, backend=self.backend.label):
                report = self.run_lifecycle(wrapper, tool, hide_jq=False)
                with self.subTest(parser="jq-present"):
                    if report["jq"] is None:
                        self.skipTest(f"{self.backend.label}: optional jq-present case unavailable; inherited .NET parser passed")
                    self.assertEqual(report["parser"], "jq")
                    self.assertTrue(report["jq_probe"])

    def run_lifecycle(self, wrapper, tool, *, hide_jq):
        with isolated_environment(prefix="attestation-pwsh-") as fixture:
            root = fixture.root
            source = Path(__file__).resolve().parents[1] / "dot_local" / f"executable_{wrapper}-commit.ps1"
            staged = root / f"{wrapper}-commit.ps1"
            shutil.copyfile(source, staged)
            sentinel = root / "jq-sentinel"
            sentinel.mkdir()
            poison = sentinel / "jq"
            poison.write_text("#!/bin/sh\nprintf invoked > jq-invoked\nexit 99\n", encoding="utf-8", newline="\n")
            poison.chmod(0o755)
            (sentinel / "jq.cmd").write_text("@echo invoked>jq-invoked\n@exit /b 99\n", encoding="utf-8", newline="\n")
            (root / "child.ps1").write_text(r'''
param([string]$Wrapper, [string]$HideJq, [string]$Handoff, [string]$Subject)
$ErrorActionPreference = 'Stop'
if ($HideJq -eq 'yes') {
    $sentinel = Join-Path $PSScriptRoot 'jq-sentinel'
    $env:PATH = $sentinel + [System.IO.Path]::PathSeparator + $env:PATH
    $selected = Microsoft.PowerShell.Core\Get-Command jq -CommandType Application -ErrorAction Stop
    if ((Split-Path $selected.Source) -ne $sentinel) { throw 'jq sentinel not selected' }
}
function global:Get-Command {
    [CmdletBinding()]
    param([Parameter(Position=0)][string[]]$Name, [string]$CommandType)
    if ($Name.Count -eq 1 -and $Name[0] -eq 'jq') {
        $selected = $null
        if ($HideJq -ne 'yes') { $selected = Microsoft.PowerShell.Core\Get-Command @PSBoundParameters }
        $selection = if ($selected) { $selected.Source } else { '.NET' }
        [System.IO.File]::AppendAllText((Join-Path $PSScriptRoot 'jq-lookups'), $selection + "`n")
        return $selected
    }
    Microsoft.PowerShell.Core\Get-Command @PSBoundParameters
}
if ($Handoff -eq 'True') {
    $env:AI_ATTESTATION_JSON = '{"schemaVersion":1,"participants":[{"tool":"environment-tool"}]}'
} else {
    Remove-Item Env:AI_ATTESTATION_JSON -ErrorAction SilentlyContinue
}
& $Wrapper --allow-empty -m $Subject
exit $LASTEXITCODE
''', encoding="utf-8", newline="\n")
            driver = root / "driver.ps1"
            driver.write_text(r'''
param([string]$Wrapper, [string]$StateName, [string]$DefaultTool, [string]$HideJq)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
Remove-Item Env:AI_ATTESTATION_JSON -ErrorAction SilentlyContinue
function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Run-Git([string[]]$GitArgs) {
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) { throw "git failed: $GitArgs ($LASTEXITCODE)" }
}
$root = $PSScriptRoot
$jq = Get-Command jq -CommandType Application -ErrorAction SilentlyContinue
$jqProbe = $false
if ($jq -and $HideJq -ne 'yes') {
    $parsed = '{"schemaVersion":1,"participants":[{"tool":"environment-tool"}]}' | & $jq.Source -c .
    Assert ($LASTEXITCODE -eq 0) 'selected jq failed synthetic payload probe'
    Assert (($parsed | ConvertFrom-Json).participants[0].tool -eq 'environment-tool') 'selected jq changed synthetic payload'
    $jqProbe = $true
}
$repo = Join-Path $root 'repo'
New-Item -ItemType Directory $repo | Out-Null
Set-Location $repo
Run-Git @('init', '-q', '.')
Run-Git @('config', 'user.name', 'Fixture')
Run-Git @('config', 'user.email', 'fixture@example.invalid')
Run-Git @('config', 'commit.gpgsign', 'false')
$state = Join-Path $repo (Run-Git @('rev-parse', '--git-path', $StateName))
$hook = Join-Path $repo '.git/hooks/commit-msg'
[System.IO.File]::WriteAllText($hook, @'
#!/bin/sh
test -z "${AI_ATTESTATION_JSON+x}" || exit 97
printf clean > handoff-env
cp "$1" attempted-message
if test -f reject-commit; then exit 27; fi
'@ + "`n")
if (-not $IsWindows) { & chmod +x $hook }
$pwsh = Join-Path $PSHOME $(if ($IsWindows) { 'pwsh.exe' } else { 'pwsh' })
$child = Join-Path $root 'child.ps1'
$hidden = $HideJq -eq 'yes'
function Invoke-Wrapper([bool]$Handoff, [string]$Subject) {
    Remove-Item 'handoff-env' -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $root 'jq-lookups') -ErrorAction SilentlyContinue
    & $pwsh -NoLogo -NoProfile -NonInteractive -File $child -Wrapper $Wrapper -HideJq $HideJq -Handoff $Handoff.ToString() -Subject $Subject | Out-Host
    $code = $LASTEXITCODE
    Assert (-not (Test-Path 'jq-invoked')) 'wrapper invoked suppressed jq sentinel'
    if ($Handoff) {
        $lookups = @(Get-Content (Join-Path $root 'jq-lookups'))
        Assert ($lookups.Count -eq 1) 'wrapper jq lookup was not observed exactly once'
        if ($hidden -or -not $jq) {
            Assert ($lookups[0] -eq '.NET') 'wrapper did not select absent-jq route'
            $script:parser = '.NET'
        } else {
            Assert ($lookups[0] -eq $jq.Source) 'wrapper changed inherited jq selection'
            $script:parser = 'jq'
        }
    } else {
        Assert (-not (Test-Path (Join-Path $root 'jq-lookups'))) 'retry queried parser without payload'
    }
    Assert ((Get-Content 'handoff-env' -Raw) -eq 'clean') 'handoff reached Git hook'
    Assert (-not (Test-Path $state)) 'state was not consumed'
    return $code
}
function Write-State {
    [System.IO.File]::WriteAllText($state, '{"schemaVersion":1,"participants":[{"tool":"state-tool"}]}')
}
function Assert-EnvironmentTrailer([string]$Message) {
    Assert ($Message -match '(?m)^AI-Participant: tool=environment-tool\r?$') 'environment participant missing'
    Assert ($Message -notmatch 'state-tool') 'state participant won over environment'
}
Write-State
Assert ((Invoke-Wrapper $true 'success') -eq 0) 'success commit failed'
Assert-EnvironmentTrailer ((Run-Git @('log', '-1', '--format=%B')) -join "`n")
New-Item 'reject-commit' -ItemType File | Out-Null
& git commit --allow-empty -m 'direct failure'
$directFailure = $LASTEXITCODE
Assert ($directFailure -ne 0) 'fixture hook did not reject direct Git'
Write-State
Assert ((Invoke-Wrapper $true 'failure') -eq $directFailure) 'wrapper changed Git failure status'
Assert-EnvironmentTrailer (Get-Content 'attempted-message' -Raw)
Remove-Item 'reject-commit'
Assert ((Invoke-Wrapper $false 'retry') -eq 0) 'retry failed'
$message = (Run-Git @('log', '-1', '--format=%B')) -join "`n"
Assert ($message -match "(?m)^AI-Participant: tool=$DefaultTool`r?$") 'retry fallback missing'
Assert ($message -notmatch 'environment-tool|state-tool') 'retry replayed participant'
@{ jq = $(if ($jq) { $jq.Source } else { $null }); parser = $script:parser; jq_probe = $jqProbe } |
    ConvertTo-Json -Compress | Set-Content (Join-Path $root 'report.json')
''', encoding="utf-8", newline="\n")
            result = self.backend.run(
                root, script=driver,
                args=(self.backend.path(root, staged), f"ai-attestation-{tool}.json", tool,
                      "yes" if hide_jq else "no"),
                environ=fixture.env,
                env_keys=("HOME", "USERPROFILE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                          "GIT_TERMINAL_PROMPT", "TMPDIR", "TMP", "TEMP"),
                path_keys=("HOME", "USERPROFILE", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                           "TMPDIR", "TMP", "TEMP"),
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads((root / "report.json").read_text(encoding="utf-8-sig"))
            print(f"PowerShell lifecycle: {wrapper}-commit; backend={self.backend.label}; "
                  f"hide_jq={hide_jq}; parser={report['parser']}; jq={report['jq'] or 'absent'}", file=sys.stderr)
            if hide_jq:
                self.assertEqual(report["parser"], ".NET")
            return report


if __name__ == "__main__":
    unittest.main()
