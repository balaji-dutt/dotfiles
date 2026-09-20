Describe 'Windows startup task lifecycle' {
  BeforeAll {
    . (Join-Path $PSScriptRoot 'PesterCompat.ps1')
    . (Join-Path $env:DOTFILES_TEST_RENDER_DIR 'windows-startup-task.ps1')
  }

  BeforeEach {
    Mock New-StartupTaskDefinition { 'task' }
    Mock Get-StartupTask { $null }
    Mock Register-StartupTask {}
  }

  It 'returns without task commands when the startup script is missing' {
    $missing = Join-Path $TestDrive 'missing.ps1'

    Invoke-StartupTaskRegistration -ScriptPath $missing -StartupDir $TestDrive -CurrentUser 'fixture\user' -WarningAction SilentlyContinue

    Assert-MockCalled New-StartupTaskDefinition -Times 0 -Exactly
    Assert-MockCalled Register-StartupTask -Times 0 -Exactly
  }

  It 'registers a missing task and removes stale fallback files' {
    $scriptPath = Join-Path $TestDrive 'Start Wsl Pageant.ps1'
    Set-Content -LiteralPath $scriptPath -Value '# fixture'
    Set-Content -LiteralPath (Join-Path $TestDrive 'Start-WslSshPageant.cmd') -Value 'fixture'
    Set-Content -LiteralPath (Join-Path $TestDrive 'Start-WslSshPageant.vbs') -Value 'fixture'

    Invoke-StartupTaskRegistration -ScriptPath $scriptPath -StartupDir $TestDrive -CurrentUser 'fixture\user'

    Assert-MockCalled Register-StartupTask -Times 1 -Exactly
    Assert-Equal (Test-Path -LiteralPath (Join-Path $TestDrive 'Start-WslSshPageant.cmd')) $false
    Assert-Equal (Test-Path -LiteralPath (Join-Path $TestDrive 'Start-WslSshPageant.vbs')) $false
  }

  It 'forces replacement when the task already exists' {
    $scriptPath = Join-Path $TestDrive 'Start-WslSshPageant.ps1'
    Set-Content -LiteralPath $scriptPath -Value '# fixture'
    Mock Get-StartupTask { 'existing' }

    Invoke-StartupTaskRegistration -ScriptPath $scriptPath -StartupDir $TestDrive -CurrentUser 'fixture\user'

    Assert-MockCalled Register-StartupTask -Times 1 -Exactly -ParameterFilter { $Force }
  }

  It 'writes a quoted VBS fallback on access denied' {
    $scriptPath = Join-Path $TestDrive 'Start Wsl Pageant.ps1'
    Set-Content -LiteralPath $scriptPath -Value '# fixture'
    Mock Register-StartupTask { throw 'Access is denied' }
    Mock Get-Command { [pscustomobject]@{ Source = 'C:\Program Files\PowerShell\7\pwsh.exe' } } -ParameterFilter { $Name -eq 'pwsh.exe' }

    Invoke-StartupTaskRegistration -ScriptPath $scriptPath -StartupDir $TestDrive -CurrentUser 'fixture\user' -WarningAction SilentlyContinue

    $content = Get-Content -LiteralPath (Join-Path $TestDrive 'Start-WslSshPageant.vbs') -Raw
    Assert-Matches $content 'C:\\Program Files\\PowerShell\\7\\pwsh\.exe'
    Assert-Matches $content 'Start Wsl Pageant\.ps1'
    Assert-Matches $content 'RestartPageant'
  }

  It 'uses Windows PowerShell when pwsh is unavailable' {
    Mock Get-Command { $null } -ParameterFilter { $Name -eq 'pwsh.exe' }
    $scriptPath = Join-Path $TestDrive 'Start-WslSshPageant.ps1'

    Write-StartupVbs -TargetScript $scriptPath -StartupDir $TestDrive -WarningAction SilentlyContinue

    Assert-Matches (Get-Content -LiteralPath (Join-Path $TestDrive 'Start-WslSshPageant.vbs') -Raw) 'powershell\.exe'
  }

  It 'propagates non-access-denied registration failures' {
    $scriptPath = Join-Path $TestDrive 'Start-WslSshPageant.ps1'
    Set-Content -LiteralPath $scriptPath -Value '# fixture'
    Remove-StartupFallback -StartupDir $TestDrive
    Mock Register-StartupTask { throw 'service unavailable' }

    $threw = $false
    try {
      Invoke-StartupTaskRegistration -ScriptPath $scriptPath -StartupDir $TestDrive -CurrentUser 'fixture\user'
    } catch {
      $threw = $true
    }
    Assert-Equal $threw $true
    Assert-Equal (Test-Path -LiteralPath (Join-Path $TestDrive 'Start-WslSshPageant.vbs')) $false
  }

  It 'passes the rendered script path through the production entrypoint' {
    Mock Invoke-StartupTaskRegistration {}

    Invoke-WindowsStartupTaskEntrypoint

    Assert-MockCalled Invoke-StartupTaskRegistration -Times 1 -Exactly -ParameterFilter { $ScriptPath -eq $script:scriptPath }
  }
}
