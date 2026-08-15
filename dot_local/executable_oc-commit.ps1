$ErrorActionPreference = 'Stop'

if ($args.Count -eq 1 -and $args[0] -in @('-h', '--help', 'help')) {
    @'
usage: oc-commit <git commit args>

Direct git commit replacement that records OpenCode as author and committer.
Pass normal git commit arguments directly; no wrapper-specific flags are needed.

Examples:
  oc-commit -m "subject"
  oc-commit -m "subject" -m "body"
  oc-commit -F C:\path\to\message
'@
    exit 0
}

$gitCommand = @(Get-Command git -CommandType Application -ErrorAction Stop)[0]
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $gitCommand.Source
$startInfo.UseShellExecute = $false
$startInfo.WorkingDirectory = $PWD.ProviderPath
[void]$startInfo.ArgumentList.Add('commit')
foreach ($argument in $args) {
    [void]$startInfo.ArgumentList.Add($argument)
}
$startInfo.Environment['GIT_AUTHOR_NAME'] = 'OpenCode'
$startInfo.Environment['GIT_AUTHOR_EMAIL'] = 'noreply@opencode.ai'
$startInfo.Environment['GIT_COMMITTER_NAME'] = 'OpenCode'
$startInfo.Environment['GIT_COMMITTER_EMAIL'] = 'noreply@opencode.ai'

$process = [System.Diagnostics.Process]::Start($startInfo)
try {
    $process.WaitForExit()
    exit $process.ExitCode
}
finally {
    $process.Dispose()
}
