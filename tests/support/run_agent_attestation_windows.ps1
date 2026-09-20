param([Parameter(Mandatory)][string]$SourceRoot)

$ErrorActionPreference = 'Stop'
$parent = [IO.Path]::GetTempPath()
if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'Temporary parent is unavailable' }
$stage = Join-Path $parent ('agent-attestation-' + [Guid]::NewGuid().ToString('N'))
$rc = 1
try {
    New-Item -ItemType Directory -Path $stage | Out-Null
    $entries = @(
        'dot_claude/skills/agent-attestation',
        'private_dot_config/opencode/attestation',
        'private_dot_config/opencode/plugins/opencode-agent-attestation.js',
        'dot_local/executable_cc-commit.ps1',
        'dot_local/executable_oc-commit.ps1',
        'tests/support/test_agent_attestation.mjs'
    )
    foreach ($entry in $entries) {
        $target = Join-Path $stage $entry
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $SourceRoot $entry) -Destination $target -Recurse
    }
    & node --test (Join-Path $stage 'tests/support/test_agent_attestation.mjs')
    $rc = $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}
exit $rc
