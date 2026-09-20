$script:PesterMajor = @(Get-Module Pester | ForEach-Object { $_.Version.Major } | Sort-Object -Descending)[0]

function Assert-Equal($Actual, $Expected) {
  if ($script:PesterMajor -ge 5) {
    $Actual | Should -Be $Expected
  } else {
    $Actual | Should Be $Expected
  }
}

function Assert-Matches($Actual, [string]$Pattern) {
  if ($script:PesterMajor -ge 5) {
    $Actual | Should -Match $Pattern
  } else {
    $Actual | Should Match $Pattern
  }
}
