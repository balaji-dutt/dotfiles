$ErrorActionPreference = 'Stop'

if ($args.Count -eq 1 -and $args[0] -in @('-h', '--help', 'help')) {
    @'
usage: cc-commit <git commit args>

Direct git commit replacement that records Claude as author and committer.
Pass normal git commit arguments directly; no wrapper-specific flags are needed.

Examples:
  cc-commit -m "subject"
  cc-commit -m "subject" -m "body"
  cc-commit -F C:\path\to\message
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
$startInfo.Environment['GIT_AUTHOR_NAME'] = 'Claude'
$startInfo.Environment['GIT_AUTHOR_EMAIL'] = 'noreply@anthropic.com'
$startInfo.Environment['GIT_COMMITTER_NAME'] = 'Claude'
$startInfo.Environment['GIT_COMMITTER_EMAIL'] = 'noreply@anthropic.com'

$process = [System.Diagnostics.Process]::Start($startInfo)
try {
    $process.WaitForExit()
    exit $process.ExitCode
}
finally {
    $process.Dispose()
}
