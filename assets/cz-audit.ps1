Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

param(
  [Parameter(Mandatory=$true)]
  [ValidateSet('classify','dryrun-if-managed','check')]
  [string] $Command,

  [Parameter(Mandatory=$true)]
  [string] $RelSrc
)

function HaveCmd($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Get-ContainerRuntime {
  if (HaveCmd 'docker') { return 'docker' }
  if (HaveCmd 'podman') { return 'podman' }
  return $null
}

function Invoke-Cz([string[]]$Args) {
  $exe = (Get-Command cz -ErrorAction SilentlyContinue)
  if (-not $exe) { $exe = (Get-Command chezmoi -ErrorAction SilentlyContinue) }
  if (-not $exe) { throw "Neither 'cz' nor 'chezmoi' found in PATH" }
  & $exe @Args
}

function Get-SourceDir { (Invoke-Cz @('source-path')).Trim() }
function Get-DestDir   { (Invoke-Cz @('target-path')).Trim() }

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

  $target.Substring($dest.Length).TrimStart('\','/')
}

function Test-ManagedSourceRel([string]$relsrc) {
  $rel = Get-TargetRelFromSourceRel $relsrc
  $managed = Invoke-Cz @('managed')
  return ($managed -split "`r?`n" | Where-Object { $_ -ceq $rel } | Measure-Object).Count -gt 0
}

function Test-ChezmoiConfigFile([string]$relsrc) {
  return $relsrc -match '^(?:\.chezmoiignore(?:\.tmpl)?|\.chezmoiremove(?:\.tmpl)?|\.chezmoi\.toml(?:\.tmpl)?|\.chezmoidata\..+|\.chezmoiroot)$'
}

function Classify([string]$relsrc) {
  if (Test-ChezmoiConfigFile $relsrc) { return "chezmoi-config:$relsrc" }
  if (Test-ManagedSourceRel $relsrc)  { return "managed:$((Get-TargetFromSourceRel $relsrc))" }

  switch -Wildcard ($relsrc) {
    '.chezmoiscripts/*' { return "chezmoiscript:$relsrc" }
    'ansible/*'         { return "ansible:$relsrc" }
    'assets/*'          { return "assets:$relsrc" }
    'configs/*'         { return "configs:$relsrc" }
    'docs/*'            { return "docs:$relsrc" }
    'CLAUDE.md'         { return "docs:$relsrc" }
    'AGENTS.md'         { return "docs:$relsrc" }
    'README.md'         { return "docs:$relsrc" }
    'TODO.md'           { return "docs:$relsrc" }
    'bootstrap-wsl.sh'  { return "bootstrap:$relsrc" }
    default             { return "repo:$relsrc" }
  }
}

function DryRun-IfManaged([string]$relsrc) {
  if (Test-ChezmoiConfigFile $relsrc) {
    Write-Host "chezmoi-config file; skipping apply/diff: $relsrc"
    Write-Host "Run: pwsh ./assets/cz-audit.ps1 check $relsrc"
    return
  }

  if (-not (Test-ManagedSourceRel $relsrc)) {
    Write-Host "Not a managed chezmoi target: $relsrc"
    Write-Host "Classification: $(Classify $relsrc)"
    return
  }

  $t = Get-TargetFromSourceRel $relsrc
  Invoke-Cz @('diff','--use-builtin-diff','--no-pager','--verbose', $t) | Write-Output
  Invoke-Cz @('apply','--use-builtin-diff','--no-pager','--dry-run','--verbose', $t) | Write-Output
}

function Run-ShellCheck([string]$fileRel) {
  if (HaveCmd 'shellcheck') {
    shellcheck $fileRel | Write-Output
    return
  }

  $rt = Get-ContainerRuntime
  if (-not $rt) {
    Write-Host "shellcheck not available (no shellcheck, no docker/podman). Skipped."
    return
  }

  & $rt run --rm -v "${PWD}:/work" -w /work koalaman/shellcheck:stable shellcheck $fileRel | Write-Output
}

function Run-AnsibleSyntax([string]$fileRel) {
  if (HaveCmd 'ansible-playbook') {
    ansible-playbook --syntax-check $fileRel | Write-Output
    return
  }

  $rt = Get-ContainerRuntime
  if (-not $rt) {
    Write-Host "ansible-playbook not available (no ansible, no docker/podman). Skipped."
    return
  }

  & $rt run --rm -t -v "${PWD}:/work" -w /work quay.io/ansible/ansible-runner:stable `
    ansible-playbook --syntax-check $fileRel | Write-Output
}

function Check-ChezmoiConfig([string]$relsrc) {
  $abs = Join-Path (Get-SourceDir) $relsrc
  Write-Host "Validating chezmoi config file: $relsrc"

  if ($relsrc -like '*.tmpl') {
    Invoke-Cz @('execute-template','-f', $abs) | Out-Null
    Write-Host "Template renders OK: $relsrc"
  }

  Invoke-Cz @('doctor') | Write-Output
}

function Check([string]$relsrc) {
  $kind = Classify $relsrc
  Write-Host "Classification: $kind"

  if ($kind.StartsWith('chezmoi-config:')) {
    Check-ChezmoiConfig $relsrc
    return
  }

  if ($kind.StartsWith('managed:')) {
    DryRun-IfManaged $relsrc
    return
  }

  # Repo-only checks on Windows
  if ($relsrc -like '*.ps1' -or $relsrc -like '*.ps1.tmpl') {
    $path = Join-Path (Get-SourceDir) $relsrc
    if (Test-Path -LiteralPath $path) {
      $null = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$null, [ref]$null)
      Write-Host "PowerShell parse OK: $relsrc"
    }
    return
  }

  if ($relsrc -like '*.sh' -or $relsrc -like '*.sh.tmpl' -or $relsrc -eq 'bootstrap-wsl.sh') {
    Run-ShellCheck $relsrc
    return
  }

  if ($relsrc -like 'ansible/*') {
    if (Test-Path -LiteralPath $relsrc) {
      Run-AnsibleSyntax $relsrc
    } else {
      Write-Host "ansible path isn't a file; run against a playbook (e.g. ansible/site.yml)"
    }
    return
  }

  Write-Host "Repo-only file; no chezmoi apply/diff required."
}

switch ($Command) {
  'classify'          { Classify $RelSrc | Write-Output }
  'dryrun-if-managed' { DryRun-IfManaged $RelSrc }
  'check'             { Check $RelSrc }
}
