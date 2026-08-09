
# Load Claude Code Environment variables
$globalEnv = Join-Path $HOME ".config\powershell\claude-env.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Redirect the unsafe Beads/Dolt habit path through this repo's sync helper.
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
        if ($arg -eq '--') {
            $index++
            break
        }
        if ($arg -in @('-C', '--directory')) {
            if ($index + 1 -ge $bdArgs.Count) {
                & $bdCommand.Source @bdArgs
                return
            }
            $directory = [string]$bdArgs[$index + 1]
            $index += 2
            continue
        }
        if ($arg -match '^(?:-C|--directory)=(.*)$') {
            $directory = $Matches[1]
            $index++
            continue
        }
        if ($arg -in @('--db', '--actor', '--dolt-auto-commit')) {
            $unsupported = $true
            if ($index + 1 -ge $bdArgs.Count) { break }
            $index += 2
            continue
        }
        if ($arg.StartsWith('-')) {
            $unsupported = $true
            $index++
            continue
        }
        break
    }

    if ($index + 1 -ge $bdArgs.Count -or
        [string]$bdArgs[$index] -ne 'dolt' -or
        [string]$bdArgs[$index + 1] -notin @('pull', 'push')) {
        & $bdCommand.Source @bdArgs
        return
    }

    $action = [string]$bdArgs[$index + 1]
    if ($index + 2 -ne $bdArgs.Count) { $unsupported = $true }

    $gitCommand = Get-Command git -CommandType Application, ExternalScript -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $gitCommand) {
        & $bdCommand.Source @bdArgs
        return
    }

    $repoRoot = (& $gitCommand.Source -C $directory rev-parse --show-toplevel 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
        & $bdCommand.Source @bdArgs
        return
    }

    $syncScript = Join-Path $repoRoot 'assets/beads-sync.ps1'
    if (-not (Test-Path -LiteralPath $syncScript -PathType Leaf)) {
        & $bdCommand.Source @bdArgs
        return
    }

    if ($unsupported) {
        [Console]::Error.WriteLine(
            "bd: refusing redirected ``bd dolt $action`` with unsupported arguments; run ``pwsh -NoProfile -File '$syncScript' $action`` explicitly"
        )
        $global:LASTEXITCODE = 2
        return
    }

    $pwshCommand = Get-Command pwsh -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $pwshCommand) {
        [Console]::Error.WriteLine('bd: pwsh executable not found on PATH')
        $global:LASTEXITCODE = 127
        return
    }

    [Console]::Error.WriteLine(
        "bd: redirecting ``bd dolt $action`` to ``pwsh -NoProfile -File '$syncScript' $action``"
    )
    & $pwshCommand.Source -NoProfile -File $syncScript $action
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
