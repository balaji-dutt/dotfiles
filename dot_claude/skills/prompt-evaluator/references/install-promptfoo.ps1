#Requires -Version 7.0
<#
.SYNOPSIS
    Validates a managed Promptfoo runtime package root.

.DESCRIPTION
    The compatibility filename is retained for existing skill consumers. This
    script does not install packages. It verifies that Promptfoo and all supported
    provider SDKs resolve from one lockfile-managed node_modules tree.
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$RuntimeDir
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error 'ERROR: node is required to validate the Promptfoo runtime.'
    exit 1
}

$candidates = [System.Collections.Generic.List[string]]::new()
if ($RuntimeDir) { $candidates.Add($RuntimeDir) }
if ($env:PROMPTFOO_RUNTIME_DIR) { $candidates.Add($env:PROMPTFOO_RUNTIME_DIR) }
$candidates.Add((Get-Location).Path)
$candidates.Add((Join-Path $HOME '.local/share/promptfoo-runtime'))

function Test-PromptfooRuntime {
    param([Parameter(Mandatory)][string]$PackageRoot)

    $packageJson = Join-Path $PackageRoot 'package.json'
    $promptfooBin = if ($IsWindows) {
        Join-Path $PackageRoot 'node_modules/.bin/promptfoo.cmd'
    }
    else {
        Join-Path $PackageRoot 'node_modules/.bin/promptfoo'
    }

    if (-not (Test-Path -LiteralPath $packageJson -PathType Leaf) -or
        -not (Test-Path -LiteralPath $promptfooBin -PathType Leaf)) {
        return $false
    }

    $resolver = @'
const { createRequire } = require('node:module');
const path = require('node:path');
const root = path.resolve(process.argv[1]);
const requireFromRuntime = createRequire(path.join(root, 'package.json'));
for (const packageName of [
  'promptfoo',
  '@opencode-ai/sdk',
  '@anthropic-ai/claude-agent-sdk',
  '@anthropic-ai/sdk',
]) {
  requireFromRuntime.resolve(packageName);
}
'@

    & node -e $resolver $PackageRoot 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }

    & $promptfooBin --version 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

foreach ($candidate in $candidates | Select-Object -Unique) {
    if ($candidate -and (Test-PromptfooRuntime -PackageRoot $candidate)) {
        $resolvedRoot = (Resolve-Path -LiteralPath $candidate).Path
        Write-Host "Promptfoo runtime ready: $resolvedRoot"
        $promptfooBin = if ($IsWindows) {
            Join-Path $resolvedRoot 'node_modules/.bin/promptfoo.cmd'
        }
        else {
            Join-Path $resolvedRoot 'node_modules/.bin/promptfoo'
        }
        & $promptfooBin --version
        Write-Host 'Provider SDKs resolve from the same package root.'
        exit 0
    }
}

Write-Error @'
ERROR: no complete managed Promptfoo runtime was found.
Expected promptfoo, @opencode-ai/sdk, @anthropic-ai/claude-agent-sdk, and
@anthropic-ai/sdk in one node_modules tree. Install the project's lockfile or
set PROMPTFOO_RUNTIME_DIR to its package root, then run this verifier again.
'@
exit 1
