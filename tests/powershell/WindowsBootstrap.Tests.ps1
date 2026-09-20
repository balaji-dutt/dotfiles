Describe 'Windows bootstrap lifecycle' {
  BeforeAll {
    . (Join-Path $PSScriptRoot 'PesterCompat.ps1')
    . (Join-Path $env:DOTFILES_TEST_RENDER_DIR 'windows-bootstrap.ps1')
  }

  It 'deduplicates paths case-insensitively' {
    $existing = Join-Path $TestDrive 'Tools'
    New-Item -ItemType Directory -Path $existing | Out-Null
    $entry = [pscustomobject]@{ Path = $existing.ToUpperInvariant(); Create = $false; Position = 'Prepend' }

    $result = Update-PathString -CurrentPath $existing -Entries @($entry)

    Assert-Equal $result $existing
  }

  It 'preserves prepend and append order' {
    $current = Join-Path $TestDrive 'Current'
    $prepend = Join-Path $TestDrive 'Prepend'
    $append = Join-Path $TestDrive 'Append'
    @($current, $prepend, $append) | ForEach-Object {
      New-Item -ItemType Directory -Path $_ | Out-Null
    }
    $entries = @(
      [pscustomobject]@{ Path = $prepend; Create = $false; Position = 'Prepend' },
      [pscustomobject]@{ Path = $append; Create = $false; Position = 'Append' }
    )

    $result = Update-PathString -CurrentPath $current -Entries $entries

    Assert-Equal $result "$prepend;$current;$append"
  }

  It 'creates requested directories and skips missing optional paths' {
    $created = Join-Path $TestDrive 'Created'
    $missing = Join-Path $TestDrive 'Missing'
    $entries = @(
      [pscustomobject]@{ Path = $created; Create = $true; Position = 'Append' },
      [pscustomobject]@{ Path = $missing; Create = $false; Position = 'Append' }
    )

    $result = Update-PathString -CurrentPath '' -Entries $entries -SkipMissing -WarningAction SilentlyContinue

    Assert-Equal (Test-Path -LiteralPath $created) $true
    Assert-Equal $result $created
  }

  It 'throws for missing required paths and invalid positions' {
    $missing = [pscustomobject]@{ Path = (Join-Path $TestDrive 'Missing'); Create = $false; Position = 'Append' }
    $missingThrew = $false
    try { Update-PathString -CurrentPath '' -Entries @($missing) } catch { $missingThrew = $true }
    Assert-Equal $missingThrew $true

    $existing = Join-Path $TestDrive 'Existing'
    New-Item -ItemType Directory -Path $existing | Out-Null
    $invalid = [pscustomobject]@{ Path = $existing; Create = $false; Position = 'Middle' }
    $invalidThrew = $false
    try { Update-PathString -CurrentPath '' -Entries @($invalid) } catch { $invalidThrew = $true }
    Assert-Equal $invalidThrew $true
  }

  It 'updates user and process scopes through isolated persistence boundaries' {
    $entryPath = Join-Path $TestDrive 'Scoped Path'
    New-Item -ItemType Directory -Path $entryPath | Out-Null
    $entries = @([pscustomobject]@{ Path = $entryPath; Create = $false; Position = 'Append' })
    Mock Get-EnvironmentPath { '' }
    Mock Set-EnvironmentPath {}

    Add-ToEnvPath -Scope User -Entries $entries

    Assert-MockCalled Set-EnvironmentPath -Times 1 -Exactly -ParameterFilter { $Scope -eq 'User' }
    Assert-MockCalled Set-EnvironmentPath -Times 1 -Exactly -ParameterFilter { $Scope -eq 'Process' }
  }

  It 'creates the profile and inserts its snippet once' {
    $profileFile = Join-Path $TestDrive 'PowerShell Profile\profile.ps1'

    Update-PowerShellProfile -ProfilePath $profileFile
    Update-PowerShellProfile -ProfilePath $profileFile

    $content = Get-Content -LiteralPath $profileFile -Raw
    Assert-Equal ([regex]::Matches($content, 'claude-env\.ps1')).Count 1
    Assert-Matches $content 'claude-env\.local\.ps1'
  }
}
