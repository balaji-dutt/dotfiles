Describe 'Windows drive mapping lifecycle' {
  BeforeAll {
    . (Join-Path $PSScriptRoot 'PesterCompat.ps1')
    function global:Get-SmbMapping {}
    function global:New-SmbMapping {
      param([string]$LocalPath, [string]$RemotePath, [bool]$Persistent)
    }
    . (Join-Path $env:DOTFILES_TEST_RENDER_DIR 'MapDrives.ps1')
  }

  BeforeEach {
    $script:mappedCalls = @()
    Mock Get-SmbMapping { @() }
    Mock New-SmbMapping {
      $script:mappedCalls += [pscustomobject]@{ LocalPath = $LocalPath; RemotePath = $RemotePath }
    }
    Mock Start-Sleep {}
  }

  AfterAll {
    Remove-Item Function:\global:Get-SmbMapping,Function:\global:New-SmbMapping -ErrorAction SilentlyContinue
  }

  It 'does nothing when every mapping is available' {
    Invoke-MapDrives

    Assert-MockCalled Get-SmbMapping -Times 1 -Exactly -Scope It
    Assert-MockCalled New-SmbMapping -Times 0 -Exactly -Scope It
    Assert-MockCalled Start-Sleep -Times 0 -Exactly -Scope It
  }

  It 'remaps every unavailable drive once after success' {
    Mock Get-SmbMapping {
      @(
        [pscustomobject]@{ LocalPath = 'H:'; RemotePath = '\\server\home'; Status = 'Unavailable' },
        [pscustomobject]@{ LocalPath = 'S:'; RemotePath = '\\server\shared'; Status = 'Unavailable' }
      )
    }

    Invoke-MapDrives

    Assert-MockCalled New-SmbMapping -Times 2 -Exactly -Scope It
    Assert-Equal $script:mappedCalls[0].LocalPath 'H:'
    Assert-Equal $script:mappedCalls[0].RemotePath '\\server\home'
    Assert-Equal $script:mappedCalls[1].LocalPath 'S:'
    Assert-Equal $script:mappedCalls[1].RemotePath '\\server\shared'
    Assert-MockCalled Start-Sleep -Times 0 -Exactly -Scope It
  }

  It 'bounds repeated failures at three attempts with two sleeps' {
    Mock Get-SmbMapping {
      @([pscustomobject]@{ LocalPath = 'H:'; RemotePath = '\\server\home'; Status = 'Unavailable' })
    }
    Mock New-SmbMapping { throw 'mapping failed' }

    Invoke-MapDrives -RetryDelaySeconds 1

    Assert-MockCalled Get-SmbMapping -Times 3 -Exactly -Scope It
    Assert-MockCalled New-SmbMapping -Times 3 -Exactly -Scope It
    Assert-MockCalled Start-Sleep -Times 2 -Exactly -Scope It -ParameterFilter { $Seconds -eq 1 }
  }

  It 'retries only mappings that remain unavailable' {
    $script:round = 0
    Mock Get-SmbMapping {
      $script:round++
      if ($script:round -eq 1) {
        return @(
          [pscustomobject]@{ LocalPath = 'H:'; RemotePath = '\\server\home'; Status = 'Unavailable' },
          [pscustomobject]@{ LocalPath = 'S:'; RemotePath = '\\server\shared'; Status = 'Unavailable' }
        )
      }
      return @([pscustomobject]@{ LocalPath = 'S:'; RemotePath = '\\server\shared'; Status = 'Unavailable' })
    }
    Mock New-SmbMapping {
      $script:mappedCalls += [pscustomobject]@{ LocalPath = $LocalPath; RemotePath = $RemotePath }
      if ($LocalPath -eq 'S:') { throw 'mapping failed' }
    }

    Invoke-MapDrives -RetryDelaySeconds 1

    Assert-Equal @($script:mappedCalls | Where-Object LocalPath -EQ 'H:').Count 1
    Assert-Equal @($script:mappedCalls | Where-Object LocalPath -EQ 'S:').Count 3
    Assert-MockCalled Start-Sleep -Times 2 -Exactly -Scope It
  }

  It 'is a no-op on rerun after a successful mapping' {
    $script:queries = 0
    Mock Get-SmbMapping {
      $script:queries++
      if ($script:queries -eq 1) {
        return @([pscustomobject]@{ LocalPath = 'H:'; RemotePath = '\\server\home'; Status = 'Unavailable' })
      }
      return @()
    }

    Invoke-MapDrives
    Invoke-MapDrives

    Assert-MockCalled New-SmbMapping -Times 1 -Exactly -Scope It
    Assert-MockCalled Start-Sleep -Times 0 -Exactly -Scope It
  }
}
