Describe 'ffpwa-refresh.ps1' {
  BeforeAll {
    . (Join-Path $PSScriptRoot 'PesterCompat.ps1')
    . (Join-Path (Split-Path (Split-Path $PSScriptRoot)) 'dot_local/executable_ffpwa-refresh.ps1')

    function Set-Ini([string]$Directory, [string]$Version) {
      New-Item -ItemType Directory -Force -Path $Directory | Out-Null
      Set-Content -LiteralPath (Join-Path $Directory 'application.ini') -Value @('[App]', 'Name=Firefox', "Version=$Version")
    }
  }

  BeforeEach {
    $env:FFPWA_REFRESH_USERDATA = Join-Path $TestDrive 'FirefoxPWA'
    $env:FFPWA_REFRESH_FIREFOX_DIR = Join-Path $TestDrive 'Mozilla Firefox'
    $script:RuntimeDir = Join-Path $env:FFPWA_REFRESH_USERDATA 'runtime'
    Remove-Item -LiteralPath $env:FFPWA_REFRESH_USERDATA, $env:FFPWA_REFRESH_FIREFOX_DIR -Recurse -Force -ErrorAction SilentlyContinue

    Mock Get-FirefoxpwaPath { 'C:\fixture\firefoxpwa.exe' }
    Mock Get-FirefoxpwaProfileList {
      @(
        'ID: 00000000000000000000000000',
        'Apps:',
        '- Toodledo: https://www.toodledo.com/signin.php (01JKR0YG7ESP2E7XHWQ8P92V3C)',
        '- BookFusion: https://www.bookfusion.com/bookshelf (01JKR2ZM8G31NPX0GJC8MRCMZT)',
        '- Wanderlog: https://wanderlog.com/home (01JKR3661HQ8222BBVWCNCYTDN)'
      )
    }
    Mock Invoke-Firefoxpwa { 0 }
    Mock Get-Process { @() }
    Mock Test-SevenZipInstalled { $true }
  }

  AfterEach {
    Remove-Item Env:\FFPWA_REFRESH_USERDATA, Env:\FFPWA_REFRESH_FIREFOX_DIR -ErrorAction SilentlyContinue
  }

  It 'compares versions numerically' {
    Assert-Equal (Test-VersionOlder '156.0.1' '157.0') $true
    Assert-Equal (Test-VersionOlder '9.0' '10.0') $true
    Assert-Equal (Test-VersionOlder '157.0' '157.0') $false
    Assert-Equal (Test-VersionOlder '157.0.1' '157.0') $false
  }

  It 'does nothing when the runtime matches Firefox' {
    Set-Ini $script:RuntimeDir '157.0'
    Set-Ini $env:FFPWA_REFRESH_FIREFOX_DIR '157.0'

    Assert-Equal (Invoke-Auto) 0

    Assert-MockCalled Invoke-Firefoxpwa -Times 0 -Exactly
  }

  It 'does nothing when the runtime is newer than Firefox' {
    Set-Ini $script:RuntimeDir '157.0.1'
    Set-Ini $env:FFPWA_REFRESH_FIREFOX_DIR '157.0'

    Assert-Equal (Invoke-Auto) 0

    Assert-MockCalled Invoke-Firefoxpwa -Times 0 -Exactly
  }

  It 'reinstalls the runtime and updates every web app when stale' {
    Set-Ini $script:RuntimeDir '156.0.1'
    Set-Ini $env:FFPWA_REFRESH_FIREFOX_DIR '157.0'

    Assert-Equal (Invoke-Auto) 0

    Assert-MockCalled Invoke-Firefoxpwa -Times 1 -Exactly -ParameterFilter { $Arguments[0] -eq 'runtime' -and $Arguments[1] -eq 'install' }
    Assert-MockCalled Invoke-Firefoxpwa -Times 3 -Exactly -ParameterFilter { $Arguments[0] -eq 'site' -and $Arguments[1] -eq 'update' }
    Assert-MockCalled Invoke-Firefoxpwa -Times 0 -Exactly -ParameterFilter { $Arguments -contains '00000000000000000000000000' }
  }

  It 'skips the refresh while web apps run' {
    Set-Ini $script:RuntimeDir '156.0.1'
    Set-Ini $env:FFPWA_REFRESH_FIREFOX_DIR '157.0'
    Mock Get-Process { [pscustomobject]@{ Path = (Join-Path $script:RuntimeDir 'firefox.exe') } }

    Assert-Equal (Invoke-Auto 3>$null) 0

    Assert-MockCalled Invoke-Firefoxpwa -Times 0 -Exactly
  }

  It 'skips the refresh when 7-Zip is missing' {
    Set-Ini $script:RuntimeDir '156.0.1'
    Set-Ini $env:FFPWA_REFRESH_FIREFOX_DIR '157.0'
    Mock Test-SevenZipInstalled { $false }

    Assert-Equal (Invoke-Auto 3>$null) 0

    Assert-MockCalled Invoke-Firefoxpwa -Times 0 -Exactly
  }

  It 'skips when the runtime is not installed' {
    Set-Ini $env:FFPWA_REFRESH_FIREFOX_DIR '157.0'

    Assert-Equal (Invoke-Auto) 0

    Assert-MockCalled Invoke-Firefoxpwa -Times 0 -Exactly
  }

  It 'fails without updating web apps when the runtime install fails' {
    Set-Ini $script:RuntimeDir '156.0.1'
    Set-Ini $env:FFPWA_REFRESH_FIREFOX_DIR '157.0'
    Mock Invoke-Firefoxpwa { 1 } -ParameterFilter { $Arguments[0] -eq 'runtime' }

    Assert-Equal (Invoke-Auto) 1

    Assert-MockCalled Invoke-Firefoxpwa -Times 0 -Exactly -ParameterFilter { $Arguments[0] -eq 'site' }
  }

  It 'keeps updating web apps past a failure and returns 1' {
    Mock Invoke-Firefoxpwa { 7 } -ParameterFilter { $Arguments[2] -eq '01JKR2ZM8G31NPX0GJC8MRCMZT' }

    Assert-Equal (Invoke-Sites 3>$null) 1

    Assert-MockCalled Invoke-Firefoxpwa -Times 3 -Exactly -ParameterFilter { $Arguments[0] -eq 'site' }
  }

  It 'refuses a runtime reinstall while web apps run' {
    Mock Get-Process { [pscustomobject]@{ Path = (Join-Path $script:RuntimeDir 'firefox.exe') } }

    Assert-Equal (Invoke-Runtime) 2

    Assert-MockCalled Invoke-Firefoxpwa -Times 0 -Exactly
  }

  It 'detects only firefox processes under the runtime directory' {
    $runtimeFirefox = Join-Path $script:RuntimeDir 'firefox.exe'
    $systemFirefox = Join-Path $env:FFPWA_REFRESH_FIREFOX_DIR 'firefox.exe'

    Mock Get-Process { [pscustomobject]@{ Path = $systemFirefox } }
    Assert-Equal (Test-RuntimeRunning) $false

    Mock Get-Process { @([pscustomobject]@{ Path = $systemFirefox }, [pscustomobject]@{ Path = $runtimeFirefox }) }
    Assert-Equal (Test-RuntimeRunning) $true
  }
}
