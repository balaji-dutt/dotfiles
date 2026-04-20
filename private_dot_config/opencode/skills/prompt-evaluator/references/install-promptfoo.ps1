#Requires -Version 7.0
<#
.SYNOPSIS
    Cross-platform promptfoo installer for Windows (PowerShell 7).

.DESCRIPTION
    Checks for promptfoo presence and installs via npm if not found.
    Exit codes: 0 = installed/already present, 1 = install failed.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# -------------------------------------------------------------------
# Check if promptfoo is already installed
# -------------------------------------------------------------------
$existing = Get-Command promptfoo -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "promptfoo is already installed: $($existing.Source)"
    & promptfoo --version 2>$null
    exit 0
}

# Check npx availability
$npxCmd = Get-Command npx -ErrorAction SilentlyContinue
if ($npxCmd) {
    try {
        $null = & npx promptfoo@latest --version 2>$null
        Write-Host "promptfoo is available via npx"
        exit 0
    }
    catch {
        # npx check failed, continue to install
    }
}

Write-Host "promptfoo not found. Attempting installation..."

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

Write-Host "Installing promptfoo via npm (global)..."
& npm install -g promptfoo

# -------------------------------------------------------------------
# Verify installation
# -------------------------------------------------------------------
# Refresh PATH for current session
$env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path', 'User')

$installed = Get-Command promptfoo -ErrorAction SilentlyContinue
if ($installed) {
    Write-Host "promptfoo installed successfully: $(& promptfoo --version)"
    exit 0
}
else {
    Write-Error "ERROR: Installation completed but promptfoo not found in PATH."
    exit 1
}
