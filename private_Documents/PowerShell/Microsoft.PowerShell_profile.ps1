
# Load Claude Code Environment variables
$globalEnv = Join-Path $HOME ".config\powershell\claude-env.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Return true only for command forms known to write issue data.
function global:Test-BdMutation([object[]] $BdArgs) {
    $index = 0
    while ($index -lt $BdArgs.Count) {
        $arg = [string]$BdArgs[$index]
        if ($arg -eq '--') { $index++; break }
        if ($arg -in @('-q', '--quiet', '-v', '--verbose', '--json', '--profile', '--readonly', '--sandbox', '--global')) {
            $index++; continue
        }
        if ($arg -in @('-C', '--directory', '--db', '--actor', '--dolt-auto-commit')) {
            if ($index + 1 -ge $BdArgs.Count) { return $false }
            $index += 2; continue
        }
        if ($arg -match '^(?:-C|--directory|--db|--actor|--dolt-auto-commit)=') { $index++; continue }
        if ($arg -in @('-h', '--help', '--version', '-V')) { return $false }
        if ($arg.StartsWith('-')) { $index++; continue }
        break
    }
    if ($index -ge $BdArgs.Count) { return $false }
    $primary = [string]$BdArgs[$index]
    $index++
    $alwaysWrite = @(
        'assign', 'batch', 'close', 'comment', 'create', 'new', 'create-form',
        'defer', 'delete', 'duplicate', 'edit', 'forget', 'import', 'link',
        'note', 'priority', 'promote', 'q', 'remember', 'rename', 'reopen',
        'set-state', 'supersede', 'tag', 'undefer', 'update'
    )
    if ($primary -in $alwaysWrite) { return $true }
    $subcommand = if ($index -lt $BdArgs.Count) { [string]$BdArgs[$index] } else { '' }
    switch ($primary) {
        'comments' { return $subcommand -eq 'add' }
        'dep' { return $subcommand -in @('add', 'remove', 'relate', 'unrelate') }
        'label' { return $subcommand -in @('add', 'remove', 'propagate') }
        'epic' { return $subcommand -eq 'close-eligible' }
        'gate' { return $subcommand -in @('add-waiter', 'check', 'create', 'resolve') }
        'merge-slot' { return $subcommand -in @('acquire', 'create', 'release') }
        'todo' { return $subcommand -in @('add', 'done') }
        'restore' { return @($BdArgs | Where-Object { [string]$_ -eq '--apply' }).Count -gt 0 }
    }
    return $false
}

# Redirect unsafe Dolt sync habits and snapshot successful interactive writes.
function global:bd {
    $bdArgs = @($args)
    $bdCommand = Get-Command bd -CommandType Application, ExternalScript -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $bdCommand) {
        [Console]::Error.WriteLine('bd: executable not found on PATH')
        $global:LASTEXITCODE = 127
        return
    }

    $directory = (Get-Location).ProviderPath
    $unsupported = $false
    $index = 0
    while ($index -lt $bdArgs.Count) {
        $arg = [string]$bdArgs[$index]
        if ($arg -eq '--') { $index++; break }
        if ($arg -in @('-C', '--directory')) {
            if ($index + 1 -ge $bdArgs.Count) { break }
            $directory = [string]$bdArgs[$index + 1]
            $index += 2; continue
        }
        if ($arg -match '^(?:-C|--directory)=(.*)$') {
            $directory = $Matches[1]
            $index++; continue
        }
        if ($arg -in @('--db', '--actor', '--dolt-auto-commit')) {
            $unsupported = $true
            if ($index + 1 -ge $bdArgs.Count) { break }
            $index += 2; continue
        }
        if ($arg.StartsWith('-')) {
            $unsupported = $true
            $index++; continue
        }
        break
    }

    $gitCommand = Get-Command git -CommandType Application, ExternalScript -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $repoRoot = $null
    if ($gitCommand) {
        $repoRoot = (& $gitCommand.Source -C $directory rev-parse --show-toplevel 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { $repoRoot = $null }
    }
    $syncScript = if ($repoRoot) { Join-Path $repoRoot 'assets/beads-sync.ps1' } else { $null }

    $isSync = $index + 1 -lt $bdArgs.Count -and
        [string]$bdArgs[$index] -eq 'dolt' -and
        [string]$bdArgs[$index + 1] -in @('pull', 'push')
    if ($isSync -and $syncScript -and (Test-Path -LiteralPath $syncScript -PathType Leaf)) {
        $action = [string]$bdArgs[$index + 1]
        if ($index + 2 -ne $bdArgs.Count) { $unsupported = $true }
        if ($unsupported) {
            [Console]::Error.WriteLine(
                "bd: refusing redirected ``bd dolt $action`` with unsupported arguments; run ``pwsh -NoProfile -File '$syncScript' $action`` explicitly"
            )
            $global:LASTEXITCODE = 2
            return
        }
        $pwshCommand = Get-Command pwsh -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $pwshCommand) {
            [Console]::Error.WriteLine('bd: pwsh executable not found on PATH')
            $global:LASTEXITCODE = 127
            return
        }
        [Console]::Error.WriteLine(
            "bd: redirecting ``bd dolt $action`` to ``pwsh -NoProfile -File '$syncScript' $action``"
        )
        & $pwshCommand.Source -NoProfile -File $syncScript $action
        return
    }

    & $bdCommand.Source @bdArgs
    $nativeExit = $global:LASTEXITCODE
    if ($nativeExit -eq 0 -and (Test-BdMutation $bdArgs) -and -not $env:BD_GIT_HOOK -and
        $env:BD_AUTO_SNAPSHOT -notin @('0', 'false', 'FALSE', 'no', 'NO', 'off', 'OFF') -and
        $repoRoot -and $syncScript -and (Test-Path -LiteralPath $syncScript -PathType Leaf)) {
        $metadata = Join-Path $repoRoot '.beads/metadata.json'
        if (Test-Path -LiteralPath $metadata -PathType Leaf) {
            try { $database = (Get-Content -Raw -LiteralPath $metadata | ConvertFrom-Json).dolt_database } catch { $database = $null }
            if ($database -eq 'dots') {
                $pwshCommand = Get-Command pwsh -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($pwshCommand) {
                    & $pwshCommand.Source -NoProfile -File $syncScript snapshot -IfDue
                    if ($global:LASTEXITCODE -ne 0) {
                        [Console]::Error.WriteLine('bd: warning: automatic Beads JSONL snapshot failed')
                    }
                }
            }
        }
    }
    $global:LASTEXITCODE = $nativeExit
}

# Load OpenCode Environment variables
$globalEnv = Join-Path $HOME ".config\powershell\opencode-env.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load Fix for programs that do not load Registry Path correctly (like VSCode)
$globalEnv = Join-Path $HOME ".config\powershell\repair-path.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load PowerShell aliases
$globalEnv = Join-Path $HOME ".config\powershell\aliases.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load git helpers
$globalEnv = Join-Path $HOME ".config\powershell\git.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load package managers
$globalEnv = Join-Path $HOME ".config\powershell\packages.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load prompt configuration
$globalEnv = Join-Path $HOME ".config\powershell\prompt.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }
