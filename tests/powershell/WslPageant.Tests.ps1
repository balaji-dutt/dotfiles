Describe 'WSL Pageant helpers' {
  BeforeAll {
    . (Join-Path $PSScriptRoot 'PesterCompat.ps1')
    . (Join-Path $env:DOTFILES_TEST_RENDER_DIR 'Start-WslSshPageant.ps1')
  }

  It 'prefers an explicit executable path' {
    $preferred = Join-Path $TestDrive 'PAGEANT.EXE'
    Set-Content -LiteralPath $preferred -Value 'fixture'

    Assert-Equal (Resolve-Exe -Preferred $preferred -FallbackNames @('pageant.exe')) $preferred
  }

  It 'reports a missing fallback executable' {
    Mock Get-Command { }
    Assert-Equal (Resolve-Exe -Preferred '' -FallbackNames @('pageant.exe')) $null
  }

  It 'uses PATH order for distinct fallback executables' {
    Mock Get-Command {
      @(
        [pscustomobject]@{ Source = 'C:\one\pageant.exe' },
        [pscustomobject]@{ Source = 'C:\two\pageant.exe' }
      )
    }

    Assert-Equal (Resolve-Exe -Preferred '' -FallbackNames @('pageant.exe')) 'C:\one\pageant.exe'
  }

  It 'accepts duplicate resolutions of the same executable' {
    Mock Get-Command {
      @(
        [pscustomobject]@{ Source = 'C:\tools\pageant.exe' },
        [pscustomobject]@{ Source = 'C:\tools\pageant.exe' }
      )
    }

    Assert-Equal (Resolve-Exe -Preferred '' -FallbackNames @('pageant.exe')) 'C:\tools\pageant.exe'
  }

  It 'quotes detached executable paths and arguments for cmd' {
    $exe = Join-Path $TestDrive 'tool with spaces.exe'
    Set-Content -LiteralPath $exe -Value 'fixture'
    Mock Start-Process {}

    Start-DetachedProcess -ExePath $exe -Arguments @('plain', 'value with spaces', 'quote"value')

    Assert-MockCalled Start-Process -Times 1 -Exactly -Scope It -ParameterFilter {
      $FilePath -eq 'cmd.exe' -and
      $ArgumentList[0] -eq '/c' -and
      $ArgumentList[1] -match 'tool with spaces\.exe' -and
      $ArgumentList[1] -match 'value with spaces' -and
      $ArgumentList[1] -match 'quote""value'
    }
  }

  It 'writes logs beneath disposable local app data' {
    $previous = $env:LOCALAPPDATA
    $env:LOCALAPPDATA = $TestDrive
    try {
      Write-Log 'fixture message'
      $log = Join-Path (Join-Path $TestDrive 'wsl-ssh-pageant') 'Start-WslSshPageant.log'
      Assert-Matches (Get-Content -LiteralPath $log -Raw) 'fixture message'
    } finally {
      $env:LOCALAPPDATA = $previous
    }
  }
}

Describe 'WSL Pageant lifecycle' {
  BeforeAll {
    function New-PageantFixture([string]$Root) {
      $keysDir = Join-Path $Root 'keys with spaces'
      New-Item -ItemType Directory -Path $keysDir -Force | Out-Null
      $first = Join-Path $keysDir 'first key.ppk'
      $second = Join-Path $keysDir 'second.ppk'
      Set-Content -LiteralPath $first -Value 'first'
      Set-Content -LiteralPath $second -Value 'second'
      $allowlist = Join-Path $Root 'allow list.txt'
      Set-Content -LiteralPath $allowlist -Value @('# comment', '', '"first key.ppk"', $second)
      return [pscustomobject]@{
        KeysDir = $keysDir
        Allowlist = $allowlist
        First = $first
        Second = $second
        Socket = Join-Path (Join-Path $Root 'socket dir') 'agent.sock'
      }
    }
  }

  BeforeEach {
    $script:pageantExe = 'C:\Program Files\PuTTY\PAGEANT.EXE'
    $script:bridgeExe = 'C:\Program Files\PuTTY\wsl-ssh-pageant-amd64-gui.exe'
    Mock Resolve-Exe {
      if ($Preferred -match 'PAGEANT\.EXE$') { return $script:pageantExe }
      return $script:bridgeExe
    }
    Mock Get-Process { $null }
    Mock Start-DetachedProcess {}
    Mock Start-Process { [pscustomobject]@{ ExitCode = 0 } }
    Mock Start-Sleep {}
    Mock Stop-Process {}
    Mock Test-ReadableFile { $true }
    Mock Write-Log {}
  }

  It 'rejects absent empty and comment-only allowlists' {
    foreach ($allowlist in @('', (Join-Path $TestDrive 'missing.txt'))) {
      $threw = $false
      try {
        Invoke-WslSshPageant -AllowlistPath $allowlist -AgentSocket 'socket' -KeysDir $TestDrive
      } catch { $threw = $true }
      Assert-Equal $threw $true
    }

    $comments = Join-Path $TestDrive 'comments.txt'
    Set-Content -LiteralPath $comments -Value @('# one', ' ', '# two')
    $threw = $false
    try {
      Invoke-WslSshPageant -AllowlistPath $comments -AgentSocket 'socket' -KeysDir $TestDrive
    } catch { $threw = $true }
    Assert-Equal $threw $true
  }

  It 'rejects missing unreadable and relative keys without a key directory' {
    $missing = Join-Path $TestDrive 'missing-list.txt'
    Set-Content -LiteralPath $missing -Value 'missing.ppk'
    $threw = $false
    try {
      Invoke-WslSshPageant -AllowlistPath $missing -AgentSocket 'socket' -KeysDir $TestDrive
    } catch { $threw = $true }
    Assert-Equal $threw $true

    $relative = Join-Path $TestDrive 'relative-list.txt'
    Set-Content -LiteralPath $relative -Value 'relative.ppk'
    $threw = $false
    try {
      Invoke-WslSshPageant -AllowlistPath $relative -AgentSocket 'socket' -KeysDir ''
    } catch { $threw = $true }
    Assert-Equal $threw $true

    $fixture = New-PageantFixture -Root $TestDrive
    Mock Test-ReadableFile { $false }
    $threw = $false
    try {
      Invoke-WslSshPageant -AllowlistPath $fixture.Allowlist -AgentSocket $fixture.Socket -KeysDir $fixture.KeysDir
    } catch { $threw = $true }
    Assert-Equal $threw $true
  }

  It 'starts Pageant and the bridge and batches quoted keys' {
    $fixture = New-PageantFixture -Root $TestDrive

    Invoke-WslSshPageant `
      -PuttyDir 'C:\Program Files\PuTTY' `
      -WslSshPageantExe $script:bridgeExe `
      -AgentSocket $fixture.Socket `
      -KeysDir $fixture.KeysDir `
      -AllowlistPath $fixture.Allowlist

    Assert-Equal (Test-Path -LiteralPath (Split-Path -Parent $fixture.Socket)) $true
    Assert-MockCalled Start-DetachedProcess -Times 1 -Exactly -Scope It -ParameterFilter { $ExePath -eq $script:pageantExe }
    Assert-MockCalled Start-DetachedProcess -Times 1 -Exactly -Scope It -ParameterFilter {
      $ExePath -eq $script:bridgeExe -and $Arguments -contains $fixture.Socket
    }
    Assert-MockCalled Start-Process -Times 1 -Exactly -Scope It -ParameterFilter {
      $FilePath -eq $script:pageantExe -and $ArgumentList -match 'first key\.ppk' -and $ArgumentList -match 'second\.ppk'
    }
  }

  It 'leaves running processes alone unless restart is requested' {
    $fixture = New-PageantFixture -Root $TestDrive
    Mock Get-Process { [pscustomobject]@{ Name = $Name } }

    Invoke-WslSshPageant -AgentSocket $fixture.Socket -KeysDir $fixture.KeysDir -AllowlistPath $fixture.Allowlist
    Assert-MockCalled Stop-Process -Times 0 -Exactly -Scope It
    Assert-MockCalled Start-DetachedProcess -Times 0 -Exactly -Scope It

    Invoke-WslSshPageant -AgentSocket $fixture.Socket -KeysDir $fixture.KeysDir -AllowlistPath $fixture.Allowlist -RestartPageant -RestartBridge
    Assert-MockCalled Stop-Process -Times 2 -Exactly -Scope It
    Assert-MockCalled Start-DetachedProcess -Times 2 -Exactly -Scope It
  }

  It 'loads keys individually and logs child failures without aborting' {
    $fixture = New-PageantFixture -Root $TestDrive
    Mock Start-Process { [pscustomobject]@{ ExitCode = 9 } }

    Invoke-WslSshPageant `
      -AgentSocket $fixture.Socket `
      -KeysDir $fixture.KeysDir `
      -AllowlistPath $fixture.Allowlist `
      -AddKeysIndividually

    Assert-MockCalled Start-Process -Times 2 -Exactly -Scope It
    Assert-MockCalled Write-Log -Times 2 -Scope It -ParameterFilter { $Message -match 'exit code 9' }
  }

  It 'fails before process work when executables cannot be resolved' {
    $fixture = New-PageantFixture -Root $TestDrive
    Mock Resolve-Exe { $null }
    $threw = $false

    try {
      Invoke-WslSshPageant -AgentSocket $fixture.Socket -KeysDir $fixture.KeysDir -AllowlistPath $fixture.Allowlist
    } catch { $threw = $true }

    Assert-Equal $threw $true
    Assert-MockCalled Start-DetachedProcess -Times 0 -Exactly -Scope It
    Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It
  }
}
