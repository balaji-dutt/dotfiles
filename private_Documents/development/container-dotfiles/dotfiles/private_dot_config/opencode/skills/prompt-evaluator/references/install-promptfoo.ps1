#Requires -Version 7.0
<#
.SYNOPSIS
    Cross-platform promptfoo installer for Windows (PowerShell 7).

.DESCRIPTION
    Ensures promptfoo and @opencode-ai/sdk are available.
    promptfoo is installed globally via npm if missing.
    @opencode-ai/sdk is installed in the current project via npm if missing.
    Exit codes: 0 = installed/already present, 1 = install failed.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Test-OpenCodeSdk {
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCmd) { return $false }

    try {
        & node -e "require.resolve('@opencode-ai/sdk')" 2>$null | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

$promptfooReady = $false
$sdkReady = $false

# -------------------------------------------------------------------
# Check if promptfoo is already installed
# -------------------------------------------------------------------
$existing = Get-Command promptfoo -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "promptfoo is already installed: $($existing.Source)"
    & promptfoo --version 2>$null
    $promptfooReady = $true
}

# Check npx availability
$npxCmd = Get-Command npx -ErrorAction SilentlyContinue
if (-not $promptfooReady -and $npxCmd) {
    try {
        $null = & npx promptfoo@latest --version 2>$null
        Write-Host "promptfoo is available via npx"
        $promptfooReady = $true
    }
    catch {
        # npx check failed, continue to install
    }
}

if (Test-OpenCodeSdk) {
    Write-Host "@opencode-ai/sdk is already available in current project"
    $sdkReady = $true
}

if (-not $promptfooReady) {
    Write-Host "promptfoo not found. Attempting installation..."
}

# -------------------------------------------------------------------
# Install via npm
# -------------------------------------------------------------------
$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) {
    Write-Error @"
ERROR: npm not found. Install Node.js first:
  winget install OpenJS.NodeJS.LTS
  Or visit: https://nodejs.org/
"@
    exit 1
}

if (-not $promptfooReady) {
    Write-Host "Installing promptfoo via npm (global)..."
    & npm install -g promptfoo
    $promptfooReady = $true
}

if (-not $sdkReady) {
    Write-Host "Installing @opencode-ai/sdk via npm (project local)..."
    & npm install @opencode-ai/sdk
    if (Test-OpenCodeSdk) {
        $sdkReady = $true
    }
}

# -------------------------------------------------------------------
# Verify installation
# -------------------------------------------------------------------
# Refresh PATH for current session
$env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path', 'User')

$installed = Get-Command promptfoo -ErrorAction SilentlyContinue
if (-not $promptfooReady -and $installed) {
    $promptfooReady = $true
}

if ($promptfooReady -and $sdkReady) {
    if ($installed) {
        Write-Host "promptfoo ready: $(& promptfoo --version)"
    }
    else {
        Write-Host "promptfoo ready via npx"
    }
    Write-Host "@opencode-ai/sdk is installed"
    exit 0
}
else {
    if (-not $promptfooReady) {
        Write-Error "ERROR: promptfoo is not available after installation attempt."
    }
    if (-not $sdkReady) {
        Write-Error "ERROR: @opencode-ai/sdk is not available after installation attempt."
    }
    exit 1
}
