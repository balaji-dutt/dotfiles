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
  $global:LASTEXITCODE = 0

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

function Test-AuditStrict([string]$Check) {
  $strict = [Environment]::GetEnvironmentVariable("CZ_AUDIT_STRICT_$Check")
  if ([string]::IsNullOrEmpty($strict)) { $strict = $env:CZ_AUDIT_STRICT }
  return $strict -eq '1'
}

function Invoke-AuditUnavailable(
  [Parameter(Mandatory = $true)][string]$Check,
  [Parameter(Mandatory = $false)][string]$Subject = 'unknown',
  [Parameter(Mandatory = $false)][int]$Advisory = 1
) {
  if ($Advisory -eq 0 -or (Test-AuditStrict $Check)) {
    if (-not [string]::IsNullOrEmpty($script:AUDIT_OUT)) {
      [Console]::Error.WriteLine($script:AUDIT_OUT)
    }
    throw "$Check unavailable"
  }

  if (-not [string]::IsNullOrEmpty($script:AUDIT_OUT)) {
    $logdir = Get-AuditLogDir
    $key = Sanitize-Key $Subject
    $logfile = Join-Path $logdir "$Check.$key.unavailable.log"
    Set-Content -LiteralPath $logfile -Value $script:AUDIT_OUT -Encoding UTF8
    Write-Info "$Check unavailable; check skipped. Details saved to: $logfile"
  } else {
    Write-Info "$Check unavailable; check skipped."
  }
  Write-Info "Set CZ_AUDIT_STRICT_$Check=1 (or CZ_AUDIT_STRICT=1) to require this validator."
}

function Invoke-AuditResult(
  [Parameter(Mandatory = $true)][string]$Check,
  [Parameter(Mandatory = $false)][string]$Subject = 'unknown',
  [Parameter(Mandatory = $false)][int]$Advisory = 1
) {
  if ($script:AUDIT_RC -in @(125, 126, 127)) {
    Invoke-AuditUnavailable -Check $Check -Subject $Subject -Advisory $Advisory
    return
  }
  Invoke-AuditHandle -Check $Check -Subject $Subject -Advisory $Advisory
}

function Get-ContainerRuntime {
  if (HaveCmd 'docker') { return 'docker' }
  if (HaveCmd 'podman') { return 'podman' }
  return $null
}

function Set-UnavailableExitCode([string]$Message) {
  Write-Output $Message
  $global:LASTEXITCODE = 127
}

function Ensure-ContainerImage(
  [Parameter(Mandatory = $true)][string]$Runtime,
  [Parameter(Mandatory = $true)][string]$Image,
  [Parameter(Mandatory = $true)][string]$Dockerfile
) {
  & $Runtime image inspect $Image *> $null
  if ($LASTEXITCODE -eq 0) { return $true }

  Write-Info "Building missing audit image $Image from $Dockerfile; this may access the network."
  & $Runtime build -f $Dockerfile -t $Image $script:ROOT
  if ($LASTEXITCODE -ne 0) {
    Set-UnavailableExitCode "Unable to build required audit image: $Image"
    return $false
  }

  return $true
}

function Get-CzExecutable {
  $exe = (Get-Command cz -ErrorAction SilentlyContinue)
  if (-not $exe) { $exe = (Get-Command chezmoi -ErrorAction SilentlyContinue) }
  if (-not $exe) { throw "Neither 'cz' nor 'chezmoi' found in PATH" }
  return $exe
}

function Get-NormalizedDirectoryPath([string]$Path) {
  try {
    return (Resolve-Path -LiteralPath $Path -ErrorAction Stop).ProviderPath
  } catch {
    return [IO.Path]::GetFullPath($Path)
  }
}

function Initialize-ChezmoiSourceDir {
  if ($env:CHEZMOI_SOURCE_DIR) {
    Write-Info "CHEZMOI_SOURCE_DIR override: $($env:CHEZMOI_SOURCE_DIR)"
    return
  }

  if (-not (HaveCmd 'git')) { return }

  try {
    $exe = Get-CzExecutable
    $configuredSource = (& $exe source-path 2>$null | Out-String).Trim()
  } catch {
    return
  }

  if ([string]::IsNullOrEmpty($configuredSource)) { return }

  try {
    $configuredFull = Get-NormalizedDirectoryPath $configuredSource
    $rootFull = Get-NormalizedDirectoryPath $script:ROOT
  } catch {
    return
  }

  $cmp = [System.StringComparison]::OrdinalIgnoreCase
  if ([string]::Equals($configuredFull, $rootFull, $cmp)) { return }

  try {
    $inside = (& git -C $script:ROOT rev-parse --is-inside-work-tree 2>$null | Out-String).Trim()
  } catch {
    return
  }

  if ($inside -ne 'true') { return }

  $env:CHEZMOI_SOURCE_DIR = $script:ROOT
  Write-Info "CHEZMOI_SOURCE_DIR auto-detected: $($env:CHEZMOI_SOURCE_DIR)"
}

function Invoke-Cz([string[]]$CzArgs) {
  $exe = Get-CzExecutable
  # Optional override: set $env:CHEZMOI_SOURCE_DIR to point chezmoi at a
  # different source directory (e.g. a feature worktree). Without this,
  # chezmoi uses its configured source dir, so edits in branch worktrees
  # are invisible.
  if ($env:CHEZMOI_SOURCE_DIR) {
    $CzArgs = @('--source', $env:CHEZMOI_SOURCE_DIR) + $CzArgs
  }
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
  # Chezmoi scripts need source-level syntax checks even when chezmoi reports
  # their generated targets as managed.
  if ($relsrc -like '.chezmoiscripts/*') { return "chezmoiscript:$relsrc" }
  if (Test-ManagedSourceRel $relsrc) { return "managed:$((Get-TargetFromSourceRel $relsrc))" }

  switch -Wildcard ($relsrc) {
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
    Set-UnavailableExitCode 'shellcheck and docker/podman are unavailable'
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
    Set-UnavailableExitCode 'ansible-playbook and docker/podman are unavailable'
    return
  }

  $image = 'local/ansible-syntax:repo'
  $ready = Ensure-ContainerImage -Runtime $rt -Image $image -Dockerfile 'assets/Dockerfile.ansible-syntax'
  if (-not $ready) { return }
  & $rt run --rm -t -v "$($script:ROOT):/work" -w /work $image ansible-playbook -i localhost, --syntax-check $fileRel
}

function Invoke-AnsibleContainerLint([string]$fileRel) {
  $rt = Get-ContainerRuntime
  if (-not $rt) {
    Set-UnavailableExitCode 'ansible-lint and docker/podman are unavailable'
    return
  }

  $image = 'local/ansible-syntax:repo'
  $ready = Ensure-ContainerImage -Runtime $rt -Image $image -Dockerfile 'assets/Dockerfile.ansible-syntax'
  if (-not $ready) { return }
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

# Get-Command alone is not enough on native Windows: the Microsoft Store
# app-execution alias for python3 is a real executable in PATH, but running it
# prints "Python was not found; run without arguments to install from the
# Microsoft Store" and exits 49. Execute each candidate before accepting it.
function Test-PythonRuns([string]$Exe, [string[]]$PreArgs, [int]$MinMinor) {
  try {
    & $Exe @PreArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, $MinMinor) else 1)" *> $null
    return ($LASTEXITCODE -eq 0)
  } catch {
    # Reachable: $ErrorActionPreference = 'Stop' (top of file) turns a failed
    # launch into a throw. A non-zero exit does not throw by default
    # ($PSNativeCommandUseErrorActionPreference is False through PS 7.6), so
    # the $LASTEXITCODE check above is what rejects the WindowsApps alias.
    return $false
  }
}

# Candidates are whitespace-separated invocations so the Windows launcher can
# carry its version argument ("py -3"). Returns the split invocation, or $null.
function Resolve-PythonCmd([string[]]$Candidates, [int]$MinMinor) {
  foreach ($cand in $Candidates) {
    $parts = @($cand -split '\s+')
    if (-not (HaveCmd $parts[0])) { continue }
    $pre = @($parts | Select-Object -Skip 1)
    if (Test-PythonRuns $parts[0] $pre $MinMinor) { return ,$parts }
  }
  return $null
}

function Invoke-Python([string[]]$PyCmd, [string[]]$Arguments) {
  $exe = $PyCmd[0]
  $pre = @($PyCmd | Select-Object -Skip 1)
  & $exe @pre @Arguments
}

# The YAML snippet below needs nothing newer than 3; the floor is there to
# reject a Python 2 "python", which parses the probe and exits 1.
function Get-PythonCmd {
  return Resolve-PythonCmd @('python3', 'python', 'py -3') 7
}

# tomllib landed in 3.11.
function Get-PythonCmdForToml {
  return Resolve-PythonCmd @('python3.12', 'python3.11', 'python3', 'python', 'py -3') 11
}

function Invoke-YamlParse([string]$fileRel) {
  $py = Get-PythonCmd
  if (-not $py) {
    $script:AUDIT_OUT = 'python3/python not available; YAML validator unavailable'
    $script:AUDIT_RC = 127
    Invoke-AuditUnavailable -Check 'YAML' -Subject $fileRel -Advisory 1
    return
  }

  $pySrc = @'
import sys
try:
  import yaml
except Exception:
  print("PyYAML not installed; YAML validator unavailable", file=sys.stderr)
  sys.exit(127)
with open(sys.argv[1], "r", encoding="utf-8") as f:
  yaml.safe_load(f)
print("YAML OK")
'@

  Invoke-AuditCapture {
    Invoke-Python $py @('-c', $pySrc, $fileRel)
  }
  Invoke-AuditResult -Check 'YAML' -Subject $fileRel -Advisory 1
}

function Invoke-TomlParse([string]$fileRel) {
  $py = Get-PythonCmdForToml
  if (-not $py) {
    $fallback = Get-PythonCmd
    if ($fallback) {
      $script:AUDIT_OUT = "TOML validator unavailable (need Python 3.11+ for tomllib; found $fallback but version too old)"
    } else {
      $script:AUDIT_OUT = 'python3/python not available; TOML validator unavailable'
    }
    $script:AUDIT_RC = 127
    Invoke-AuditUnavailable -Check 'TOML' -Subject $fileRel -Advisory 1
    return
  }

  $pySrc = @'
import sys
try:
  import tomllib
except Exception:
  print("tomllib not available (need Python 3.11+); TOML validator unavailable", file=sys.stderr)
  sys.exit(127)
with open(sys.argv[1], "rb") as f:
  tomllib.load(f)
print("TOML OK")
'@

  Invoke-AuditCapture {
    Invoke-Python $py @('-c', $pySrc, $fileRel)
  }
  Invoke-AuditResult -Check 'TOML' -Subject $fileRel -Advisory 1
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
    $wslInput = $WindowsPath -replace '\\', '/'
    $out = & wsl wslpath -a $wslInput 2>$null
    return ($out | Out-String).Trim()
  } catch {
    return $null
  }
}

function Get-BashPath([string]$WindowsPath) {
  if (-not (HaveCmd 'bash')) { return $null }

  if (HaveCmd 'cygpath') {
    try {
      $out = & cygpath -u -- $WindowsPath 2>$null
      $path = ($out | Out-String).Trim()
      if (-not [string]::IsNullOrEmpty($path)) { return $path }
    } catch {
      # Fall through to bash -lc
    }
  }

  try {
    $escaped = $WindowsPath.Replace('\', '\\').Replace('"', '\"')
    $cmd = 'cygpath -u "' + $escaped + '"'
    $out = & bash -lc $cmd 2>$null
    $path = ($out | Out-String).Trim()
    if (-not [string]::IsNullOrEmpty($path)) { return $path }
  } catch {
    return $null
  }

  return $null
}

function Invoke-BashSyntaxCheckPath([string]$Path) {
  $full = [IO.Path]::GetFullPath($Path)

  if (-not $IsWindows) {
    if (-not (HaveCmd 'bash')) { throw 'bash unavailable; syntax check is required' }
    bash -n $full
    if ($LASTEXITCODE -ne 0) { throw "Bash syntax check failed for $Path" }
    return
  }

  if (HaveCmd 'bash') {
    $bashPath = Get-BashPath $full
    if (-not [string]::IsNullOrEmpty($bashPath)) {
      bash -n $bashPath
      if ($LASTEXITCODE -ne 0) { throw "Bash syntax check failed for $Path" }
      return
    }
  }

  if (HaveCmd 'wsl') {
    $wslPath = Get-WslPath $full
    if ([string]::IsNullOrEmpty($wslPath)) {
      throw 'WSL is available but wslpath failed; required bash syntax check could not run'
    }

    & wsl bash -n $wslPath
    if ($LASTEXITCODE -ne 0) { throw "Bash syntax check failed for $Path" }
    return
  }

  throw 'bash unavailable; syntax check is required'
}

function Check-ShellFileRel([string]$fileRel) {
  Invoke-BashSyntaxCheckPath $fileRel

  Invoke-AuditCapture { Invoke-ShellCheck $fileRel }
  Invoke-AuditResult -Check 'SHELLCHECK' -Subject $fileRel -Advisory 1
}

function Check-AnsibleFileRel([string]$fileRel) {
  Invoke-AuditCapture { Invoke-AnsibleSyntax $fileRel }
  Invoke-AuditResult -Check 'ANSIBLE_SYNTAX' -Subject $fileRel -Advisory 0

  Invoke-AuditCapture { Invoke-AnsibleLint $fileRel }
  Invoke-AuditResult -Check 'ANSIBLE_LINT' -Subject $fileRel -Advisory 1
}

function Check-ChezmoiConfig([string]$relsrc) {
  $abs = Join-Path (Get-SourceDir) $relsrc
  Write-Info "Validating chezmoi config file: $relsrc"

  if ($relsrc -like '*.tmpl') {
    $templateArgs = @('execute-template')
    if ($relsrc -eq '.chezmoi.toml.tmpl') { $templateArgs += '--init' }
    $templateArgs += @('-f', $abs)
    Invoke-Cz $templateArgs | Out-Null
    Write-Info "Template renders OK: $relsrc"
  }

  Invoke-AuditCapture { Invoke-Cz @('doctor') }
  Invoke-AuditHandle -Check 'CHEZMOI_DOCTOR' -Subject $relsrc -Advisory 1
}

function Check-PowerShellFileRel([string]$relsrc) {
  $sourceText = $null
  if ($relsrc -like '*.ps1.tmpl') {
    $abs = Join-Path (Get-SourceDir) $relsrc
    $sourceText = (Invoke-Cz @('execute-template', '-f', $abs) | Out-String)
  } elseif ($relsrc -like '*.ps1' -and (Test-Path -LiteralPath $relsrc -PathType Leaf)) {
    $sourceText = Get-Content -LiteralPath $relsrc -Raw
  } else {
    return
  }

  $tokens = $null
  $parseErrors = $null
  [void][System.Management.Automation.Language.Parser]::ParseInput(
    $sourceText,
    $relsrc,
    [ref]$tokens,
    [ref]$parseErrors
  )

  foreach ($parseError in $parseErrors) {
    $extent = $parseError.Extent
    [Console]::Error.WriteLine(
      'ERROR: {0}:{1}:{2}: {3}',
      $relsrc,
      $extent.StartLineNumber,
      $extent.StartColumnNumber,
      $parseError.Message
    )
  }
  if ($parseErrors.Count -gt 0) {
    throw "PowerShell parse failed for $relsrc"
  }

  Write-Info "PowerShell renders and parses OK: $relsrc"
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

    if ($relsrc -like '*.ps1' -or $relsrc -like '*.ps1.tmpl') {
      Check-PowerShellFileRel $relsrc
      return
    }

    Write-Info "No automated check for $relsrc. Review manually."
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
    } elseif ($relsrc -like '*.ps1' -or $relsrc -like '*.ps1.tmpl') {
      Check-PowerShellFileRel $relsrc
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
  Initialize-ChezmoiSourceDir

  switch ($Command) {
    'classify' { Classify $RelSrc | Write-Output }
    'dryrun-if-managed' { DryRun-IfManaged $RelSrc }
    'check' { Check $RelSrc }
  }
} finally {
  Pop-Location
}
