param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('classify', 'dryrun-if-managed', 'check')]
  [string] $Command,

  [Parameter(Mandatory = $true)]
  [string] $RelSrc
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Normalize common user input forms on Windows (e.g. ".\ansible\\foo.yml")
# into repo-relative paths that match our classification rules.
$RelSrc = $RelSrc.Trim()
$RelSrc = $RelSrc -replace '^[.][\\/]', ''
$RelSrc = $RelSrc -replace '^[\\/]+', ''
$RelSrc = $RelSrc -replace '\\', '/'

function HaveCmd($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Write-Info([string]$Message) {
  [Console]::Error.WriteLine("INFO: $Message")
}

function Get-RepoRoot {
  $scriptDir = [IO.Path]::GetFullPath($PSScriptRoot)
  return [IO.Path]::GetFullPath((Join-Path $scriptDir '..'))
}

function Import-AuditEnv([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }

  foreach ($line in (Get-Content -LiteralPath $Path)) {
    $l = $line.Trim()
    if ([string]::IsNullOrEmpty($l)) { continue }
    if ($l.StartsWith('#')) { continue }

    $m = [regex]::Match($l, '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$')
    if (-not $m.Success) { continue }

    $key = $m.Groups[1].Value
    $val = $m.Groups[2].Value

    Set-Item -Path "env:$key" -Value $val
  }
}

function Ensure-AuditDefaults {
  if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('CZ_AUDIT_STRICT'))) {
    $env:CZ_AUDIT_STRICT = '0'
  }
  if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('CZ_AUDIT_SHOW'))) {
    $env:CZ_AUDIT_SHOW = '0'
  }
  if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('CZ_AUDIT_LOGDIR'))) {
    $env:CZ_AUDIT_LOGDIR = '.cz-audit'
  }
  if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable('CZ_AUDIT_CLEAN_LOGS'))) {
    $env:CZ_AUDIT_CLEAN_LOGS = '1'
  }
}

$script:AuditLogsCleaned = $false

function Get-AuditLogDir {
  $d = $env:CZ_AUDIT_LOGDIR
  if ([string]::IsNullOrEmpty($d)) { $d = '.cz-audit' }
  $null = New-Item -ItemType Directory -Path $d -Force
  return $d
}

function Clear-AuditLogsOnce {
  if ($script:AuditLogsCleaned) { return }
  $script:AuditLogsCleaned = $true

  if ($env:CZ_AUDIT_CLEAN_LOGS -ne '1') { return }
  $d = Get-AuditLogDir

  try {
    Remove-Item -Path (Join-Path $d '*.log') -Force -ErrorAction SilentlyContinue
  } catch {
    # Best effort
  }
}

function Sanitize-Key([string]$Key) {
  if ([string]::IsNullOrEmpty($Key)) { return 'unknown' }
  $s = ($Key -replace '[^A-Za-z0-9._-]', '_')
  return ($s -replace '/', '_')
}

$script:AUDIT_OUT = ''
$script:AUDIT_RC = 0

function Invoke-AuditCapture([scriptblock]$ScriptBlock) {
  $script:AUDIT_OUT = ''
  $script:AUDIT_RC = 0

  try {
    $script:AUDIT_OUT = (& $ScriptBlock 2>&1 | Out-String)
    if ($null -eq $script:AUDIT_OUT) { $script:AUDIT_OUT = '' }
    $script:AUDIT_OUT = $script:AUDIT_OUT.TrimEnd("`r", "`n")

    $rc = $LASTEXITCODE
    if ($null -eq $rc) { $rc = 0 }
    $script:AUDIT_RC = [int]$rc
  } catch {
    $script:AUDIT_OUT = $_ | Out-String
    $script:AUDIT_OUT = $script:AUDIT_OUT.TrimEnd("`r", "`n")
    $script:AUDIT_RC = 1
  }
}

function Invoke-AuditHandle(
  [Parameter(Mandatory = $true)][string]$Check,
  [Parameter(Mandatory = $false)][string]$Subject = 'unknown',
  [Parameter(Mandatory = $false)][int]$Advisory = 1
) {
  $strictVar = "CZ_AUDIT_STRICT_$Check"
  $showVar = "CZ_AUDIT_SHOW_$Check"

  $strict = [Environment]::GetEnvironmentVariable($strictVar)
  $show = [Environment]::GetEnvironmentVariable($showVar)

  if ([string]::IsNullOrEmpty($strict)) { $strict = $env:CZ_AUDIT_STRICT }
  if ([string]::IsNullOrEmpty($show)) { $show = $env:CZ_AUDIT_SHOW }

  if ([string]::IsNullOrEmpty($strict)) { $strict = '0' }
  if ([string]::IsNullOrEmpty($show)) { $show = '0' }

  if ($script:AUDIT_RC -eq 0) {
    if ($show -eq '1' -and -not [string]::IsNullOrEmpty($script:AUDIT_OUT)) {
      [Console]::Error.WriteLine($script:AUDIT_OUT)
    }
    return
  }

  if ($Advisory -eq 0) {
    if (-not [string]::IsNullOrEmpty($script:AUDIT_OUT)) {
      [Console]::Error.WriteLine($script:AUDIT_OUT)
    }
    throw "$Check failed ($($script:AUDIT_RC))"
  }

  if ($strict -eq '1') {
    if (-not [string]::IsNullOrEmpty($script:AUDIT_OUT)) {
      [Console]::Error.WriteLine($script:AUDIT_OUT)
    }
    throw "$Check found issues (strict mode) ($($script:AUDIT_RC))"
  }

  $logdir = Get-AuditLogDir
  $key = Sanitize-Key $Subject
  $logfile = Join-Path $logdir "$Check.$key.log"

  Set-Content -LiteralPath $logfile -Value $script:AUDIT_OUT -Encoding UTF8

  Write-Info "$Check found issues (advisory). Full output saved to: $logfile"
  Write-Info "Set $strictVar=1 (or CZ_AUDIT_STRICT=1) to enforce; set $showVar=1 (or CZ_AUDIT_SHOW=1) to print output."
}

function Get-ContainerRuntime {
  if (HaveCmd 'docker') { return 'docker' }
  if (HaveCmd 'podman') { return 'podman' }
  return $null
}

function Invoke-Cz([string[]]$CzArgs) {
  $exe = (Get-Command cz -ErrorAction SilentlyContinue)
  if (-not $exe) { $exe = (Get-Command chezmoi -ErrorAction SilentlyContinue) }
  if (-not $exe) { throw "Neither 'cz' nor 'chezmoi' found in PATH" }
  & $exe @CzArgs
}

function Get-SourceDir { (Invoke-Cz @('source-path')).Trim() }
function Get-DestDir { (Invoke-Cz @('target-path')).Trim() }

function Get-TargetFromSourceRel([string]$relsrc) {
  $srcdir = Get-SourceDir
  $src = Join-Path $srcdir $relsrc
  (Invoke-Cz @('target-path', $src)).Trim()
}

function Get-TargetRelFromSourceRel([string]$relsrc) {
  $dest = [IO.Path]::GetFullPath((Get-DestDir))
  $target = [IO.Path]::GetFullPath((Get-TargetFromSourceRel $relsrc))

  $cmp = [System.StringComparison]::OrdinalIgnoreCase
  if (-not $target.StartsWith($dest, $cmp)) {
    throw "Computed target '$target' is not under destDir '$dest'"
  }

  $target.Substring($dest.Length).TrimStart('\', '/')
}

function Test-ManagedSourceRel([string]$relsrc) {
  $src = Join-Path (Get-SourceDir) $relsrc
  if (-not (Test-Path -LiteralPath $src)) {
    Write-Info "source file not found, skipping for audit verification: $relsrc"
    return $false
  }

  try {
    $rel = Get-TargetRelFromSourceRel $relsrc
  } catch {
    return $false
  }

  $managed = Invoke-Cz @('managed')
  return ($managed -split "`r?`n" | Where-Object { $_ -ceq $rel } | Measure-Object).Count -gt 0
}

function Test-ChezmoiConfigFile([string]$relsrc) {
  return $relsrc -match '^(?:\.chezmoiignore(?:\.tmpl)?|\.chezmoiremove(?:\.tmpl)?|\.chezmoi\.toml(?:\.tmpl)?|\.chezmoidata\..+|\.chezmoiroot)$'
}

function Classify([string]$relsrc) {
  if (Test-ChezmoiConfigFile $relsrc) { return "chezmoi-config:$relsrc" }
  if (Test-ManagedSourceRel $relsrc) { return "managed:$((Get-TargetFromSourceRel $relsrc))" }

  switch -Wildcard ($relsrc) {
    '.chezmoiscripts/*' { return "chezmoiscript:$relsrc" }
    'ansible/*' { return "ansible:$relsrc" }
    'assets/*' { return "assets:$relsrc" }
    'configs/*' { return "configs:$relsrc" }
    'docs/*' { return "docs:$relsrc" }
    'CLAUDE.md' { return "docs:$relsrc" }
    'AGENTS.md' { return "docs:$relsrc" }
    'README.md' { return "docs:$relsrc" }
    'TODO.md' { return "docs:$relsrc" }
    'bootstrap-wsl.sh' { return "bootstrap:$relsrc" }
    default { return "repo:$relsrc" }
  }
}

function DryRun-IfManaged([string]$relsrc) {
  if (Test-ChezmoiConfigFile $relsrc) {
    Write-Info "chezmoi-config file; skipping apply/diff: $relsrc"
    Write-Info "Run: pwsh ./assets/cz-audit.ps1 check $relsrc"
    return
  }

  if (-not (Test-ManagedSourceRel $relsrc)) {
    Write-Info "Not a managed chezmoi target: $relsrc"
    Write-Info "Classification: $(Classify $relsrc)"
    return
  }

  $t = Get-TargetFromSourceRel $relsrc
  Invoke-Cz @('diff', '--use-builtin-diff', '--no-pager', '--verbose', $t)
  Invoke-Cz @('apply', '--use-builtin-diff', '--no-pager', '--dry-run', '--verbose', $t)
}

function Invoke-ShellCheckContainer([string]$fileRel) {
  $rt = Get-ContainerRuntime
  if (-not $rt) {
    Write-Info 'No docker/podman; shellcheck skipped'
    return
  }

  # koalaman/shellcheck image uses shellcheck as ENTRYPOINT
  & $rt run --rm -v "$($script:ROOT):/work" -w /work koalaman/shellcheck:stable $fileRel
}

function Invoke-ShellCheck([string]$fileRel) {
  if (HaveCmd 'shellcheck') {
    shellcheck $fileRel
    return
  }

  Invoke-ShellCheckContainer $fileRel
}

function Invoke-AnsibleContainerSyntax([string]$fileRel) {
  $rt = Get-ContainerRuntime
  if (-not $rt) {
    Write-Info 'No docker/podman; ansible syntax-check skipped'
    return
  }

  $image = 'local/ansible-syntax:repo'
  & $rt run --rm -t -v "$($script:ROOT):/work" -w /work $image ansible-playbook -i localhost, --syntax-check $fileRel
}

function Invoke-AnsibleContainerLint([string]$fileRel) {
  $rt = Get-ContainerRuntime
  if (-not $rt) {
    Write-Info 'No docker/podman; ansible-lint skipped'
    return
  }

  $image = 'local/ansible-syntax:repo'
  $cfg = 'ansible/.ansible-lint.yml'
  if (Test-Path -LiteralPath $cfg -PathType Leaf) {
    & $rt run --rm -t -v "$($script:ROOT):/work" -w /work $image ansible-lint -c $cfg $fileRel
  } else {
    & $rt run --rm -t -v "$($script:ROOT):/work" -w /work $image ansible-lint $fileRel
  }
}

function Invoke-AnsibleSyntax([string]$fileRel) {
  if (HaveCmd 'ansible-playbook') {
    ansible-playbook -i localhost, --syntax-check $fileRel
    return
  }

  Invoke-AnsibleContainerSyntax $fileRel
}

function Invoke-AnsibleLint([string]$fileRel) {
  if (HaveCmd 'ansible-lint') {
    $cfg = 'ansible/.ansible-lint.yml'
    if (Test-Path -LiteralPath $cfg -PathType Leaf) {
      ansible-lint -c $cfg $fileRel
    } else {
      ansible-lint $fileRel
    }
    return
  }

  Invoke-AnsibleContainerLint $fileRel
}

function Get-PythonCmd {
  if (HaveCmd 'python3') { return 'python3' }
  if (HaveCmd 'python') { return 'python' }
  return $null
}

function Test-PythonAtLeast311([string]$PyCmd) {
  try {
    $out = & $PyCmd -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>$null
    $v = ($out | Out-String).Trim()
    $parts = $v.Split('.')
    if ($parts.Count -lt 2) { return $false }
    $maj = [int]$parts[0]
    $min = [int]$parts[1]
    return ($maj -gt 3) -or ($maj -eq 3 -and $min -ge 11)
  } catch {
    return $false
  }
}

function Get-PythonCmdForToml {
  foreach ($cand in @('python3.12', 'python3.11', 'python3', 'python')) {
    if (-not (HaveCmd $cand)) { continue }
    if (Test-PythonAtLeast311 $cand) { return $cand }
  }
  return $null
}

function Invoke-YamlParse([string]$fileRel) {
  $py = Get-PythonCmd
  if (-not $py) {
    Write-Info 'python3/python not available; YAML parse skipped'
    return
  }

  Invoke-AuditCapture {
    & $py -c @'
import sys
try:
  import yaml
except Exception:
  print("PyYAML not installed; YAML parse skipped", file=sys.stderr)
  sys.exit(0)
with open(sys.argv[1], "r", encoding="utf-8") as f:
  yaml.safe_load(f)
print("YAML OK")
'@ $fileRel
  }
  Invoke-AuditHandle -Check 'YAML' -Subject $fileRel -Advisory 1
}

function Invoke-TomlParse([string]$fileRel) {
  $py = Get-PythonCmdForToml
  if (-not $py) {
    $fallback = Get-PythonCmd
    if ($fallback) {
      Write-Info "TOML parse skipped (need Python 3.11+ for tomllib; found $fallback but version too old)"
    } else {
      Write-Info 'python3/python not available; TOML parse skipped'
    }
    return
  }

  Invoke-AuditCapture {
    & $py -c @'
import sys
try:
  import tomllib
except Exception:
  print("tomllib not available (need Python 3.11+); TOML parse skipped", file=sys.stderr)
  sys.exit(0)
with open(sys.argv[1], "rb") as f:
  tomllib.load(f)
print("TOML OK")
'@ $fileRel
  }
  Invoke-AuditHandle -Check 'TOML' -Subject $fileRel -Advisory 1
}

function Check-ConfigsFileRel([string]$fileRel) {
  switch -Wildcard ($fileRel) {
    '*.yml' { Invoke-YamlParse $fileRel }
    '*.yaml' { Invoke-YamlParse $fileRel }
    '*.toml' { Invoke-TomlParse $fileRel }
  }
}

function Get-WslPath([string]$WindowsPath) {
  if (-not (HaveCmd 'wsl')) { return $null }
  try {
    $out = & wsl wslpath -a $WindowsPath 2>$null
    return ($out | Out-String).Trim()
  } catch {
    return $null
  }
}

function Invoke-BashSyntaxCheckPath([string]$Path) {
  if (HaveCmd 'bash') {
    bash -n $Path
    return
  }

  if (-not (HaveCmd 'wsl')) {
    Write-Info 'bash not available; bash -n skipped'
    return
  }

  $full = [IO.Path]::GetFullPath($Path)
  $wslPath = Get-WslPath $full
  if ([string]::IsNullOrEmpty($wslPath)) {
    Write-Info 'WSL available but wslpath failed; bash -n skipped'
    return
  }

  & wsl bash -n $wslPath
}

function Check-ShellFileRel([string]$fileRel) {
  Invoke-BashSyntaxCheckPath $fileRel

  Invoke-AuditCapture { Invoke-ShellCheck $fileRel }
  Invoke-AuditHandle -Check 'SHELLCHECK' -Subject $fileRel -Advisory 1
}

function Check-AnsibleFileRel([string]$fileRel) {
  Invoke-AuditCapture { Invoke-AnsibleSyntax $fileRel }
  Invoke-AuditHandle -Check 'ANSIBLE_SYNTAX' -Subject $fileRel -Advisory 0

  Invoke-AuditCapture { Invoke-AnsibleLint $fileRel }
  Invoke-AuditHandle -Check 'ANSIBLE_LINT' -Subject $fileRel -Advisory 1
}

function Check-ChezmoiConfig([string]$relsrc) {
  $abs = Join-Path (Get-SourceDir) $relsrc
  Write-Info "Validating chezmoi config file: $relsrc"

  if ($relsrc -like '*.tmpl') {
    Invoke-Cz @('execute-template', '-f', $abs) | Out-Null
    Write-Info "Template renders OK: $relsrc"
  }

  Invoke-AuditCapture { Invoke-Cz @('doctor') }
  Invoke-AuditHandle -Check 'CHEZMOI_DOCTOR' -Subject $relsrc -Advisory 1
}

function Check-PowerShellFileRel([string]$relsrc) {
  if ($relsrc -like '*.ps1.tmpl') {
    $abs = Join-Path (Get-SourceDir) $relsrc
    $tmp = New-TemporaryFile
    try {
      Invoke-Cz @('execute-template', '-f', $abs) | Set-Content -LiteralPath $tmp -Encoding UTF8
      $null = [System.Management.Automation.Language.Parser]::ParseFile($tmp, [ref]$null, [ref]$null)
      Write-Info "PowerShell template renders + parses OK: $relsrc"
    } finally {
      Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }

    return
  }

  if ($relsrc -like '*.ps1') {
    if (Test-Path -LiteralPath $relsrc -PathType Leaf) {
      $path = [IO.Path]::GetFullPath($relsrc)
      $null = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$null)
      Write-Info "PowerShell parse OK: $relsrc"
    }

    return
  }
}

function Check([string]$relsrc) {
  Clear-AuditLogsOnce

  $kind = Classify $relsrc
  Write-Info "Classification: $kind"

  if ($kind.StartsWith('chezmoi-config:')) {
    Check-ChezmoiConfig $relsrc
    return
  }

  if ($kind.StartsWith('managed:')) {
    DryRun-IfManaged $relsrc
    return
  }

  if ($kind.StartsWith('bootstrap:')) {
    Check-ShellFileRel $relsrc
    return
  }

  if ($kind.StartsWith('chezmoiscript:')) {
    if ($relsrc -like '*.sh.tmpl') {
      $abs = Join-Path (Get-SourceDir) $relsrc
      $tmp = New-TemporaryFile
      try {
        Invoke-Cz @('execute-template', '-f', $abs) | Set-Content -LiteralPath $tmp -Encoding UTF8
        Invoke-BashSyntaxCheckPath $tmp
      } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
      }

      return
    }

    if ($relsrc -like '*.sh') {
      Check-ShellFileRel $relsrc
      return
    }

    Write-Info "No automated check for $relsrc (non-shell chezmoi script). Review manually."
    return
  }

  if ($kind.StartsWith('ansible:')) {
    if (Test-Path -LiteralPath $relsrc -PathType Leaf) {
      Check-AnsibleFileRel $relsrc
    } else {
      Write-Info "ansible path isn't a file; run against a playbook (e.g. ansible/site.yml)"
    }
    return
  }

  if ($kind.StartsWith('configs:')) {
    if (Test-Path -LiteralPath $relsrc -PathType Leaf) {
      Check-ConfigsFileRel $relsrc
    }
    return
  }

  if ($kind.StartsWith('assets:')) {
    if ($relsrc -like '*.sh') {
      Check-ShellFileRel $relsrc
    } else {
      Write-Info 'Assets changed; run project-specific checks if any.'
    }
    return
  }

  # Repo-only checks on Windows
  if ($relsrc -like '*.ps1' -or $relsrc -like '*.ps1.tmpl') {
    Check-PowerShellFileRel $relsrc
    return
  }

  if ($relsrc -like '*.sh' -or $relsrc -like '*.sh.tmpl' -or $relsrc -eq 'bootstrap-wsl.sh') {
    Check-ShellFileRel $relsrc
    return
  }

  Write-Info 'Repo-only file; no chezmoi apply/diff required.'
}

$script:ROOT = Get-RepoRoot
Push-Location -LiteralPath $script:ROOT
try {
  Import-AuditEnv (Join-Path $script:ROOT 'assets/cz-audit.env')
  Ensure-AuditDefaults
  Clear-AuditLogsOnce

  switch ($Command) {
    'classify' { Classify $RelSrc | Write-Output }
    'dryrun-if-managed' { DryRun-IfManaged $RelSrc }
    'check' { Check $RelSrc }
  }
} finally {
  Pop-Location
}
