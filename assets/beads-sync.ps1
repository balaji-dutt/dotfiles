param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('status', 'clean', 'pull', 'push')]
  [string] $Command,

  [switch] $DryRun,
  [switch] $Backup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Pass native-command arguments POSIX-style so `--password ''` survives as a real
# empty argument. Default is 'Windows' on PS 7.3+, which is fine for dolt, but
# Legacy mode on older PS7 can drop empty strings and shift `--no-tls` into the
# password slot. Note dolt does NOT accept the `--password=` equals form - it
# swallows the following flag - so the empty argument is the only option.
$PSNativeCommandArgumentPassing = 'Standard'

# beads-sync - Beads/Dolt sync helper for this repo (Windows / PowerShell 7).
# POSIX sibling: assets/beads-sync.sh
#
# WHY THIS EXISTS
# ---------------
# `bd dolt pull` cannot succeed in this repo. During its own pull path, bd writes
# rows into `ignored_schema_migrations` immediately before calling dolt merge.
# That table matches a `dolt_ignore` pattern, so:
#
#   - dolt refuses to stage it   -> `bd dolt commit` reports "nothing to commit"
#   - dolt merge refuses to run against ANY dirty working set, ignored or not
#
# bd therefore deadlocks itself. Verified 2026-07-25 on a provably clean working
# set: `bd dolt pull` still failed with "cannot merge with uncommitted changes"
# and left the table dirty afterwards.
#
# *** DO NOT "SIMPLIFY" THE pull PATH TO CALL `bd dolt pull`. ***
#
# Cleaning the table first does not help - bd re-dirties it inside the same
# invocation. That exact approach was tried on this machine and failed. The pull
# must run as ONE dolt SQL session (reset the ignored tables, then dolt_pull)
# with no bd process in between.
#
# SCOPE: only `pull` is affected. Every other bd command works normally. `push`
# is not broken either - it is wrapped here only to bundle the server restart
# that gives the server a live ssh-agent.

function Write-Info([string]$Message) {
  [Console]::Error.WriteLine("INFO: $Message")
}

function Die([string]$Message) {
  [Console]::Error.WriteLine("ERROR: $Message")
  exit 2
}

function HaveCmd($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Get-RepoRoot {
  $scriptDir = [IO.Path]::GetFullPath($PSScriptRoot)
  return [IO.Path]::GetFullPath((Join-Path $scriptDir '..'))
}

# Strip the private remote URL out of anything we echo. The repo is public.
function Redact([string]$Text) {
  return ($Text -replace '(git\+ssh://|ssh://|https://)[^\s"]*', '$1<REDACTED>')
}

Set-Location (Get-RepoRoot)

# dolt is not always on PATH on Windows; fall back to the default install path.
$DoltExe = 'dolt'
if (-not (HaveCmd 'dolt')) {
  $fallback = 'C:\Program Files\Dolt\bin\dolt.exe'
  if (Test-Path -LiteralPath $fallback -PathType Leaf) {
    $DoltExe = $fallback
  } else {
    Die "dolt not found on PATH and not at $fallback"
  }
}
if (-not (HaveCmd 'bd')) { Die 'bd not found on PATH' }

if (-not (Test-Path -LiteralPath '.beads/metadata.json' -PathType Leaf)) {
  Die '.beads/metadata.json not found; is this a Beads repo?'
}

# Connection details come from metadata.json so this also works for other Beads
# databases (e.g. the devcontainer's hliac), not just dots.
$meta   = Get-Content -Raw -LiteralPath '.beads/metadata.json' | ConvertFrom-Json
$DbName = if ($meta.PSObject.Properties['dolt_database'])    { $meta.dolt_database }    else { 'beads' }
$DbHost = if ($meta.PSObject.Properties['dolt_server_host']) { $meta.dolt_server_host } else { '127.0.0.1' }
$DbUser = if ($meta.PSObject.Properties['dolt_server_user']) { $meta.dolt_server_user } else { 'root' }

# Override the target database. Used to exercise the refusal path against a
# scratch database without touching real issue data.
if ($env:BEADS_SYNC_DB) { $DbName = $env:BEADS_SYNC_DB }

$DbPort = $env:BEADS_DOLT_SERVER_PORT
if (-not $DbPort -and (Test-Path -LiteralPath '.beads/dolt-server.port' -PathType Leaf)) {
  $DbPort = (Get-Content -Raw -LiteralPath '.beads/dolt-server.port').Trim()
}
if (-not $DbPort) { Die "no Dolt port; run 'bd dolt start' first" }

function Invoke-DoltSql {
  # -Quiet drops stderr instead of merging it, matching `2>/dev/null` in the .sh.
  # The CSV callers must not have warnings folded into their parse input.
  param([string]$Query, [switch]$Csv, [switch]$Quiet)
  $doltArgs = @(
    '--host', $DbHost, '--port', $DbPort, '--user', $DbUser,
    '--password', '', '--no-tls', '--use-db', $DbName, 'sql'
  )
  if ($Csv) { $doltArgs += @('-r', 'csv') }
  $doltArgs += @('-q', $Query)
  if ($Quiet) { return (& $DoltExe @doltArgs 2>$null) }
  return (& $DoltExe @doltArgs 2>&1)
}

# One query does the classification: every dirty table, flagged with whether it
# matches a dolt_ignore pattern. LIKE matching happens in SQL so we never have to
# reimplement pattern globbing (dolt_ignore uses patterns such as "wisp_%").
function Get-DirtyTables {
  $query = @'
select s.table_name,
       (select count(*) from dolt_ignore i
         where i.ignored = 1 and s.table_name like i.pattern) as is_ignored
  from dolt_status s;
'@
  $raw = Invoke-DoltSql -Query $query -Csv -Quiet
  if (-not $raw) { return @() }
  $text = ($raw | Out-String).Trim()
  if (-not $text) { return @() }
  return @($text | ConvertFrom-Csv)
}

function Get-RemoteName {
  $raw = Invoke-DoltSql -Query 'select name from dolt_remotes limit 1;' -Csv -Quiet
  $rows = @(($raw | Out-String).Trim() | ConvertFrom-Csv)
  if ($rows.Count -eq 0) { return $null }
  return $rows[0].name
}

# Return the ignored (safe to reset) dirty tables; exit non-zero if anything else
# is dirty.
function Get-SafeResetList {
  $rows = Get-DirtyTables
  if ($rows.Count -eq 0) { return @() }

  $unsafe = @($rows | Where-Object { $_.is_ignored -ne '1' })
  if ($unsafe.Count -gt 0) {
    [Console]::Error.WriteLine("ERROR: refusing to reset - these dirty tables are NOT dolt_ignore'd:")
    foreach ($row in $unsafe) { [Console]::Error.WriteLine("ERROR:   $($row.table_name)") }
    [Console]::Error.WriteLine("ERROR: that is real data. Run 'bd dolt commit' first.")
    exit 2
  }
  return @($rows | ForEach-Object { $_.table_name })
}

function Get-CheckoutSql([string[]]$Tables) {
  $sql = ''
  foreach ($table in $Tables) {
    if (-not $table) { continue }
    $sql += "call dolt_checkout('HEAD', '--', '$table'); "
  }
  return $sql
}

function Invoke-BackupIfAsked {
  if (-not $Backup) { return }
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $out   = Join-Path $HOME "$DbName-backup-$stamp.jsonl"
  if ($DryRun) {
    Write-Info "[dry-run] would run: bd export --all -o $out"
  } else {
    & bd export --all -o $out
    Write-Info "backup written: $out"
  }
}

function Restart-DoltServer {
  if ($DryRun) {
    Write-Info '[dry-run] would restart the Dolt server (live ssh-agent for the fetch)'
    return
  }
  Write-Info 'restarting Dolt server so it inherits this session ssh-agent'
  # Native exit codes do not trip $ErrorActionPreference, so check explicitly.
  # A failed start would otherwise leave us reading a stale port file and every
  # later dolt call would fail with a confusing connection error.
  & bd dolt stop 2>$null   # may already be stopped; tolerated
  & bd dolt start
  if ($LASTEXITCODE -ne 0) { Die "bd dolt start failed (exit $LASTEXITCODE)" }
  $script:DbPort = (Get-Content -Raw -LiteralPath '.beads/dolt-server.port').Trim()
}

# NOTE: display text goes through [Console]::Out.WriteLine, NOT Write-Output.
# Write-Output shares the output stream with `return`, so the strings would be
# folded into the return value; `exit` then receives an Object[] it cannot cast
# to int and silently exits 0 - bypassing the refusal guardrail entirely.
# Keep the output stream carrying the exit code and nothing else.
function Invoke-Status {
  [Console]::Out.WriteLine("Dolt server: ${DbHost}:${DbPort}  (database: ${DbName})")
  $rows = Get-DirtyTables
  if ($rows.Count -eq 0) {
    [Console]::Out.WriteLine('Working set clean - sync is safe.')
    return 0
  }
  [Console]::Out.WriteLine('Dirty tables:')
  $anyUnsafe = $false
  foreach ($row in $rows) {
    if ($row.is_ignored -eq '1') {
      [Console]::Out.WriteLine("  $($row.table_name)  (ignored by dolt_ignore - safe to reset)")
    } else {
      [Console]::Out.WriteLine("  $($row.table_name)  (NOT ignored - real data)")
      $anyUnsafe = $true
    }
  }
  if ($anyUnsafe) {
    [Console]::Out.WriteLine("Real uncommitted data present. Run 'bd dolt commit' before syncing.")
    return 1
  }
  [Console]::Out.WriteLine('Only bookkeeping tables dirty - safe to clean.')
  return 0
}

function Invoke-Clean {
  $tables = Get-SafeResetList
  if ($tables.Count -eq 0) {
    Write-Info 'nothing to clean; working set has no dirty ignored tables'
    return 0
  }
  $sql = Get-CheckoutSql $tables
  if ($DryRun) {
    Write-Info "[dry-run] would run: $sql"
    return 0
  }
  Invoke-DoltSql -Query $sql | Out-Null
  Write-Info "reset: $($tables -join ' ')"
  return 0
}

function Invoke-Pull {
  Invoke-BackupIfAsked
  Restart-DoltServer

  $tables = Get-SafeResetList
  $remote = Get-RemoteName
  if (-not $remote) { Die 'no Dolt remote configured; see docs/beads.md' }

  # THE WHOLE POINT: the reset and the merge run in ONE dolt session. Do not
  # split these, and do not call `bd dolt pull` instead - bd dirties the working
  # set inside its own pull and then fails to merge on its own dirt.
  $sql = (Get-CheckoutSql $tables) + "call dolt_pull('$remote');"

  if ($DryRun) {
    Write-Info "[dry-run] would run: $sql"
    return 0
  }

  Write-Info "pulling from '$remote' (reset + merge in one session)"
  [Console]::Out.WriteLine((Redact ((Invoke-DoltSql -Query $sql | Out-String))))
  return 0
}

function Invoke-Push {
  Invoke-BackupIfAsked
  Restart-DoltServer
  if ($DryRun) {
    Write-Info '[dry-run] would run: bd dolt commit && bd dolt push'
    return 0
  }
  # push does not merge, so the deadlock does not apply and bd is fine here.
  [Console]::Out.WriteLine((Redact ((& bd dolt commit 2>&1 | Out-String))))
  [Console]::Out.WriteLine((Redact ((& bd dolt push   2>&1 | Out-String))))
  return 0
}

switch ($Command) {
  'status' { exit (Invoke-Status) }
  'clean'  { exit (Invoke-Clean) }
  'pull'   { exit (Invoke-Pull) }
  'push'   { exit (Invoke-Push) }
}
