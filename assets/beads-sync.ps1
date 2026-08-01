param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('status', 'clean', 'pull', 'push', 'init')]
  [string] $Command,

  [switch] $DryRun,
  [switch] $Backup,

  # init only: issue prefix passed to bd init (default: the dolt_database
  # name; throwaway - the reset adopts the remote's counters anyway).
  [string] $Prefix
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
# init establishes the server itself; every other command needs one running.
if (-not $DbPort -and $Command -ne 'init') { Die "no Dolt port; run 'bd dolt start' first" }

if ($Prefix -and $Command -ne 'init') { Die '-Prefix is init-only' }

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

# The private sync remote URL. Never echo it unredacted - the repo is public.
# Falls through to BD_SYNC_REMOTE when the file is absent OR lacks the key,
# matching the two documented ways of providing the URL (docs/beads.md).
function Get-SyncRemoteUrl {
  if (Test-Path -LiteralPath '.beads/config.local.yaml' -PathType Leaf) {
    foreach ($line in (Get-Content -LiteralPath '.beads/config.local.yaml')) {
      if ($line -match '^\s*remote:\s*"?([^"]+?)"?\s*$') { return $Matches[1] }
    }
  }
  if ($env:BD_SYNC_REMOTE) { return $env:BD_SYNC_REMOTE }
  return $null
}

# Best-effort listener check. Returning $false when we cannot check is
# acceptable: bd init fails loudly on a genuine port collision anyway.
function Test-PortInUse([int]$Port) {
  try {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
  } catch {
    return $false
  }
}

# Return the ignored (safe to reset) dirty tables; exit non-zero if anything else
# is dirty.
#
# NOTE: every call to a function that can return @() MUST be wrapped in @().
# PowerShell unrolls an empty array on return, so a bare `$x = Get-Foo` leaves
# $x as $null, and $null.Count throws under Set-StrictMode -Version Latest.
# This bites only when the working set is CLEAN, which is the path least likely
# to be exercised during testing - it shipped broken for exactly that reason.
function Get-SafeResetList {
  $rows = @(Get-DirtyTables)
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
    # Out-Null: bd's stdout must not leak into this function's output stream,
    # or it contaminates the caller's return value. See Restart-DoltServer.
    & bd export --all -o $out | Out-Null
    if ($LASTEXITCODE -ne 0) { Die "bd export failed (exit $LASTEXITCODE)" }
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
  #
  # `| Out-Null` is NOT cosmetic. bd prints "Dolt server started (PID ...)" on
  # stdout; in PowerShell that lands in this function's output stream, flows up
  # into the caller's, and turns `return 0` into an Object[] that `exit` cannot
  # cast to int - silently exiting 0. Same failure mode as the Write-Output bug.
  # Out-Null discards stdout without disturbing $LASTEXITCODE.
  & bd dolt stop 2>$null | Out-Null   # may already be stopped; tolerated
  & bd dolt start | Out-Null
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
  $rows = @(Get-DirtyTables)   # @() required - see Get-SafeResetList note
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
  $tables = @(Get-SafeResetList)   # @() required - see Get-SafeResetList note
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
  # Consistent with pull/push: bash propagates this via set -e, so match it.
  if ($LASTEXITCODE -ne 0) { Die "dolt checkout failed (exit $LASTEXITCODE)" }
  Write-Info "reset: $($tables -join ' ')"
  return 0
}

function Invoke-Pull {
  Invoke-BackupIfAsked
  Restart-DoltServer

  $tables = @(Get-SafeResetList)   # @() required - see Get-SafeResetList note
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
  $pullOut = (Invoke-DoltSql -Query $sql | Out-String)
  $pullRc  = $LASTEXITCODE
  [Console]::Out.WriteLine((Redact $pullOut))
  # bash propagates a failed pull via pipefail + set -e; match that.
  if ($pullRc -ne 0) { Die "dolt pull failed (exit $pullRc)" }
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
  # Commit failure is tolerated ("nothing to commit" is normal); push failure is
  # not - bash propagates it via pipefail + set -e, so this must match.
  [Console]::Out.WriteLine((Redact ((& bd dolt commit 2>&1 | Out-String))))

  $pushOut = (& bd dolt push 2>&1 | Out-String)
  $pushRc  = $LASTEXITCODE
  [Console]::Out.WriteLine((Redact $pushOut))
  if ($pushRc -ne 0) { Die "bd dolt push failed (exit $pushRc)" }
  return 0
}

# Rebuild this peer from the sync remote without cloning. Proven 2026-08-01:
# a fresh `bd init` creates every dolt_ignore'd local table with an honest
# migration cursor, and `dolt reset --hard` to the fetched remote head adopts
# the tracked tables/history while PRESERVING the ignored tables (dolt treats
# them like git untracked files). Cloning cannot reach this state - clones
# inherit the tracked migration cursor with none of the clone-local tables.
#
# Sequencing rule (docs/beads.md): push from a current peer FIRST so the
# adopted cursor is fresh and no ignored migrations re-run on this machine.
function Invoke-Init {
  if ($Backup) { Die 'init does not take -Backup: there is no database to export yet' }

  # The destructive step stays human: this command never deletes data.
  if (Test-Path -LiteralPath '.beads/dolt') {
    Die ".beads/dolt already exists; init only rebuilds a dropped data dir. Export first (bd export --all -o `$HOME/$DbName-backup.jsonl), move .beads/dolt aside yourself, then re-run."
  }

  $url = Get-SyncRemoteUrl
  if (-not $url) { Die 'no sync remote: set sync.remote in .beads/config.local.yaml or set BD_SYNC_REMOTE (see docs/beads.md)' }

  $initPrefix = if ($Prefix) { $Prefix } else { $DbName }

  if ($DryRun) {
    Write-Info '[dry-run] would: bd dolt stop; assert BEADS_DOLT_SERVER_PORT (if set) has no other listener'
    Write-Info "[dry-run] would: bd init --server --non-interactive --skip-agents --skip-hooks --prefix $initPrefix"
    Write-Info '[dry-run] would: assert new server serves the database init just created (project id match)'
    Write-Info '[dry-run] would: replace any auto-derived dolt remote with the private sync remote'
    Write-Info "[dry-run] would: call dolt_fetch + dolt_reset('--hard','origin/main') in ONE session"
    Write-Info '[dry-run] would: patch .beads/metadata.json project_id from the adopted database'
    return 0
  }

  # Trap 1: wrong-server attach. WSL2 distros share 127.0.0.1 with this host,
  # and bd init happily adopts whatever answers on an advertised port
  # (2026-08-01: that stamped a live database with a scratch identity). Stop
  # this checkout's server, then refuse if the port still has a listener.
  & bd dolt stop 2>$null | Out-Null   # may already be stopped; tolerated
  if ($env:BEADS_DOLT_SERVER_PORT -and (Test-PortInUse ([int]$env:BEADS_DOLT_SERVER_PORT))) {
    Die "port $($env:BEADS_DOLT_SERVER_PORT) still has a listener after 'bd dolt stop'; another checkout or distro owns it. Stop that server or unset BEADS_DOLT_SERVER_PORT."
  }
  # Never trust an inherited data-dir override from another checkout's shell.
  $env:BEADS_DOLT_CLI_DIR = Join-Path (Get-RepoRoot) '.beads/dolt'

  $headBefore = (& git rev-parse HEAD | Out-String).Trim()

  # Force the from-zero LOCAL init path. If bd init can see a git 'origin' or
  # a configured sync remote it prints "initialized from git remote!" and
  # takes the clone-ish path instead - inheriting the poisoned migration
  # cursor with NO wisp tables (verified 2026-08-01 in a test clone; the wisp
  # assertion below caught it). Hide origin, config.local.yaml and
  # BD_SYNC_REMOTE while bd init runs; the finally block restores them even
  # when a later Die exits the script. $url is already in memory.
  $holdCfg = Test-Path -LiteralPath '.beads/config.local.yaml' -PathType Leaf
  $hadOrigin = $false
  & git remote get-url origin 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) {
    & git remote get-url beads-init-hold 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Die "git remote 'beads-init-hold' already exists; resolve that first" }
    $hadOrigin = $true
  }
  $savedSyncRemote = $env:BD_SYNC_REMOTE
  $initRc = 1
  try {
    if ($holdCfg) {
      Move-Item -LiteralPath '.beads/config.local.yaml' -Destination '.beads/config.local.yaml.init-hold' -Force
    }
    if ($hadOrigin) {
      Write-Info "temporarily renaming git remote 'origin' so bd init cannot derive from it"
      & git remote rename origin beads-init-hold | Out-Null
      if ($LASTEXITCODE -ne 0) {
        # The rename never happened; stop finally from "renaming back".
        $hadOrigin = $false
        Die 'failed to rename git remote origin'
      }
    }
    $env:BD_SYNC_REMOTE = $null

    Write-Info "running bd init (fresh local database, prefix '$initPrefix')"
    & bd init --server --non-interactive --skip-agents --skip-hooks --prefix $initPrefix | Out-Null
    $initRc = $LASTEXITCODE
  } finally {
    $env:BD_SYNC_REMOTE = $savedSyncRemote
    if ($hadOrigin) {
      & git remote rename beads-init-hold origin 2>$null | Out-Null
      if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("WARNING: could not rename git remote 'beads-init-hold' back to 'origin' - fix manually.")
      }
    }
    # The sentinel file IS the state (matches the .sh); safe to run twice.
    if (Test-Path -LiteralPath '.beads/config.local.yaml.init-hold' -PathType Leaf) {
      Move-Item -LiteralPath '.beads/config.local.yaml.init-hold' -Destination '.beads/config.local.yaml' -Force
    }
  }
  if ($initRc -ne 0) { Die "bd init failed (exit $initRc)" }

  # bd init rewrote metadata.json and started a server; re-read both. The
  # BEADS_SYNC_DB override still wins, matching the top of this script.
  $newMeta = Get-Content -Raw -LiteralPath '.beads/metadata.json' | ConvertFrom-Json
  if (-not $env:BEADS_SYNC_DB) {
    $script:DbName = if ($newMeta.PSObject.Properties['dolt_database']) { $newMeta.dolt_database } else { 'beads' }
  }
  $script:DbPort = (Get-Content -Raw -LiteralPath '.beads/dolt-server.port').Trim()
  if (-not (Test-Path -LiteralPath (Join-Path '.beads/dolt' $DbName))) {
    Die "bd init did not create .beads/dolt/$DbName in this checkout - wrong server?"
  }

  # The assertion the 2026-08-01 accident lacked: the server we are talking to
  # must be serving the database init just created.
  $projectIdQuery = 'select value from metadata where `key`=''_project_id'';'
  $dbPidRows = @((Invoke-DoltSql -Query $projectIdQuery -Csv -Quiet | Out-String).Trim() | ConvertFrom-Csv)
  $dbPid = if ($dbPidRows.Count -gt 0) { $dbPidRows[0].value } else { $null }
  if (-not $dbPid -or $dbPid -ne $newMeta.project_id) {
    Die "project id mismatch: metadata.json has $($newMeta.project_id), server database has $(if ($dbPid) { $dbPid } else { 'nothing' }). Refusing - this looks like another checkout's server."
  }

  # Trap 2: bd init auto-derives a Dolt remote from the git origin. On this
  # satellite the git remote is deliberately renamed so nothing derives, but
  # run the hygiene pass anyway: remove every remote that is not the private
  # sync remote before anything can push to it.
  $remoteRows = @((Invoke-DoltSql -Query 'select name, url from dolt_remotes;' -Csv -Quiet | Out-String).Trim() | ConvertFrom-Csv)
  foreach ($row in $remoteRows) {
    if ($row.url -ne $url) {
      Write-Info "removing auto-derived dolt remote '$($row.name)' (URL does not match sync.remote)"
      Invoke-DoltSql -Query "call dolt_remote('remove','$($row.name)');" | Out-Null
      if ($LASTEXITCODE -ne 0) { Die "failed to remove dolt remote '$($row.name)' (exit $LASTEXITCODE)" }
    }
  }
  $remote = Get-RemoteName
  if (-not $remote) {
    Invoke-DoltSql -Query "call dolt_remote('add','origin','$url');" | Out-Null
    if ($LASTEXITCODE -ne 0) { Die "failed to add dolt remote (exit $LASTEXITCODE)" }
    $remote = 'origin'
  }

  # bd init is also known to leak sync.remote into tracked .beads/config.yaml.
  $configDirty = (& git status --porcelain -- .beads/config.yaml | Out-String).Trim()
  if ($configDirty) {
    Write-Info 'bd init modified tracked .beads/config.yaml (sync.remote leak) - restoring from HEAD'
    & git restore --source=HEAD --worktree -- .beads/config.yaml
    if ($LASTEXITCODE -ne 0) { Die "git restore of .beads/config.yaml failed (exit $LASTEXITCODE)" }
  }
  $headAfter = (& git rev-parse HEAD | Out-String).Trim()
  if ($headAfter -ne $headBefore) {
    [Console]::Error.WriteLine("WARNING: bd init created git commits ($headBefore -> $headAfter).")
    [Console]::Error.WriteLine("WARNING: review 'git log $headBefore..HEAD'; reset ONLY after reviewing:")
    [Console]::Error.WriteLine("WARNING:   git reset --hard $headBefore")
  }

  # Fetch + hard reset in ONE dolt session, same rule as pull: no bd process
  # in between to dirty the working set.
  Write-Info "fetching from '$remote' and hard-resetting to $remote/main (one session)"
  $resetOut = (Invoke-DoltSql -Query "call dolt_fetch('$remote'); call dolt_reset('--hard','$remote/main');" | Out-String)
  $resetRc = $LASTEXITCODE
  [Console]::Out.WriteLine((Redact $resetOut))
  if ($resetRc -ne 0) { Die "dolt fetch/reset failed (exit $resetRc)" }

  $issueRows = @((Invoke-DoltSql -Query 'select count(*) as n from issues;' -Csv -Quiet | Out-String).Trim() | ConvertFrom-Csv)
  $issues = if ($issueRows.Count -gt 0) { [int]$issueRows[0].n } else { 0 }
  if ($issues -le 0) { Die 'issues table is empty after the reset - remote adoption failed' }

  $wispRaw = (Invoke-DoltSql -Query "show tables like 'wisp%';" -Csv -Quiet | Out-String).Trim()
  $wisps = @($wispRaw -split "`n" | Select-Object -Skip 1 | Where-Object { $_.Trim() })
  if ($wisps.Count -ne 6) {
    Die "expected 6 wisp tables after the reset, found $($wisps.Count). Dolt no longer preserves dolt_ignore'd tables across reset - STOP; see docs/beads.md before retrying."
  }

  # Trap 3: the tracked `metadata` table rode in with the reset, so the DB now
  # carries the shared project identity. Point metadata.json at it or bd
  # refuses to connect (PROJECT IDENTITY MISMATCH).
  $dbPidRows = @((Invoke-DoltSql -Query $projectIdQuery -Csv -Quiet | Out-String).Trim() | ConvertFrom-Csv)
  $dbPid = if ($dbPidRows.Count -gt 0) { $dbPidRows[0].value } else { $null }
  if (-not $dbPid) { Die 'could not read _project_id from the database after the reset' }
  # Swap only the UUID string so bd's own formatting (indentation, trailing
  # newline or lack of it) survives byte-for-byte - the file is git-tracked
  # and reformatting it would churn every peer.
  $metaText = Get-Content -Raw -LiteralPath '.beads/metadata.json'
  $currentId = ($metaText | ConvertFrom-Json).project_id
  Set-Content -LiteralPath '.beads/metadata.json' -Value $metaText.Replace($currentId, $dbPid) -NoNewline -Encoding utf8NoBOM
  Write-Info 'patched .beads/metadata.json project_id to the adopted database identity'

  # Trap 4: if the adopted migration cursor trails this bd version, the first
  # bd command re-runs the missing ignored migrations. Windows is where that
  # machinery has failed historically - push from a current peer before
  # running init so there is nothing to re-run.
  & bd list --limit 1 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine("WARNING: 'bd list' failed after init; run 'bd doctor' before using this checkout.")
  }

  [Console]::Out.WriteLine("init complete: $issues issues adopted from the sync remote.")
  [Console]::Out.WriteLine("Next: run 'bd doctor'. If it reports a Repo Fingerprint error, do NOT run")
  [Console]::Out.WriteLine("'bd migrate --update-repo-id' without reading docs/beads.md - repo_id is a")
  [Console]::Out.WriteLine('tracked value shared by every peer.')
  return 0
}

switch ($Command) {
  'status' { exit (Invoke-Status) }
  'clean'  { exit (Invoke-Clean) }
  'pull'   { exit (Invoke-Pull) }
  'push'   { exit (Invoke-Push) }
  'init'   { exit (Invoke-Init) }
}
