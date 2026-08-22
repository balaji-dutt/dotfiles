param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]] $RunnerArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runner = Join-Path $repoRoot 'assets/run-tests.py'

if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3 $runner @RunnerArgs
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
  & python3 $runner @RunnerArgs
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  & python $runner @RunnerArgs
} else {
  [Console]::Error.WriteLine('ERROR: Python 3 was not found')
  exit 2
}

exit $LASTEXITCODE
