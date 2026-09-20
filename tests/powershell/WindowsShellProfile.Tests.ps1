Describe 'Windows PowerShell profile loading' {
  BeforeAll {
    . (Join-Path $PSScriptRoot 'PesterCompat.ps1')
    $script:ProfileSource = Join-Path $env:DOTFILES_TEST_RENDER_DIR 'Microsoft.PowerShell_profile.ps1'
    $script:OriginalHome = $HOME

    function New-ProfileFragment([string]$Name, [string]$Content) {
      $path = Join-Path $HOME ".config\powershell\$Name.ps1"
      $parent = Split-Path -Parent $path
      if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
      }
      Set-Content -LiteralPath $path -Value $Content
    }
  }

  BeforeEach {
    Set-Variable HOME -Scope Global -Value $TestDrive -Force
    Remove-Item -LiteralPath (Join-Path $TestDrive '.config') -Recurse -Force -ErrorAction SilentlyContinue
    $global:ProfileLoadOrder = @()
    $global:LASTEXITCODE = 0
  }

  AfterEach {
    Set-Variable HOME -Scope Global -Value $script:OriginalHome -Force
    foreach ($name in @(
      'bd', 'Test-BdClientMode', 'Test-BdMutation', 'Get-BeadsWslHint',
      'Test-BeadsServerPort', 'Get-BeadsWslPort', 'Invoke-BeadsWslSync'
    )) {
      Remove-Item -Path "Function:\global:$name" -ErrorAction SilentlyContinue
    }
    Remove-Variable ProfileLoadOrder -Scope Global -ErrorAction SilentlyContinue
    Remove-Item Env:BEADS_DOLT_SERVER_PORT -ErrorAction SilentlyContinue
  }

  It 'loads optional fragments in profile order' {
    $names = @('claude-env', 'beads-env', 'opencode-env', 'repair-path', 'aliases', 'git', 'packages', 'prompt')
    foreach ($name in $names) {
      New-ProfileFragment -Name $name -Content "`$global:ProfileLoadOrder += '$name'"
    }

    . $script:ProfileSource

    Assert-Equal ($global:ProfileLoadOrder -join ',') ($names -join ',')
  }

  It 'loads silently when every optional fragment is absent' {
    $threw = $false
    try { . $script:ProfileSource } catch { $threw = $true }

    Assert-Equal $threw $false
    Assert-Equal (Test-Path Function:\global:bd) $true
  }

  It 'propagates malformed fragment failures' {
    New-ProfileFragment -Name 'aliases' -Content "throw 'malformed fragment'"
    $threw = $false

    try { . $script:ProfileSource } catch { $threw = $true }

    Assert-Equal $threw $true
  }

  It 'fails closed when the native bd executable is missing' {
    . $script:ProfileSource
    Mock Get-Command { $null } -ParameterFilter { $Name -eq 'bd' }

    bd list

    Assert-Equal $global:LASTEXITCODE 127
  }

  It 'refreshes a stale client port before invoking native bd' {
    $repo = Join-Path $TestDrive 'repo with spaces'
    New-Item -ItemType Directory -Path (Join-Path $repo '.beads') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $repo '.beads/metadata.json') -Value '{"dolt_database":"dots"}'
    $bdLog = Join-Path $TestDrive 'bd.log'
    Remove-Item -LiteralPath $bdLog -Force -ErrorAction SilentlyContinue
    $bdScript = Join-Path $TestDrive 'bd.ps1'
    $gitScript = Join-Path $TestDrive 'git.ps1'
    Set-Content -LiteralPath $bdScript -Value "Add-Content -LiteralPath '$bdLog' -Value (`$args -join ' '); exit 0"
    Set-Content -LiteralPath $gitScript -Value "Write-Output '$repo'; exit 0"
    function global:Get-BeadsWslHint { param([string]$Command) return "hint:$Command" }
    function global:Get-BeadsWslPort { return 4444 }
    function global:Test-BeadsServerPort { param([int]$Port) return $Port -eq 4444 }
    $env:BEADS_DOLT_SERVER_PORT = '1111'
    . $script:ProfileSource
    Mock Get-Command { [pscustomobject]@{ Source = $bdScript } } -ParameterFilter { $Name -eq 'bd' }
    Mock Get-Command { [pscustomobject]@{ Source = $gitScript } } -ParameterFilter { $Name -eq 'git' }

    bd list

    Assert-Equal $global:LASTEXITCODE 0
    Assert-Equal $env:BEADS_DOLT_SERVER_PORT '4444'
    Assert-Equal (Get-Content -LiteralPath $bdLog -Raw).Trim() 'list'
  }

  It 'refuses native bd when client mode remains unreachable' {
    $repo = Join-Path $TestDrive 'repo'
    New-Item -ItemType Directory -Path (Join-Path $repo '.beads') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $repo '.beads/metadata.json') -Value '{"dolt_database":"dots"}'
    $bdLog = Join-Path $TestDrive 'bd.log'
    Remove-Item -LiteralPath $bdLog -Force -ErrorAction SilentlyContinue
    $bdScript = Join-Path $TestDrive 'bd.ps1'
    $gitScript = Join-Path $TestDrive 'git.ps1'
    Set-Content -LiteralPath $bdScript -Value "Set-Content -LiteralPath '$bdLog' -Value called; exit 0"
    Set-Content -LiteralPath $gitScript -Value "Write-Output '$repo'; exit 0"
    function global:Get-BeadsWslHint { param([string]$Command) return "hint:$Command" }
    function global:Get-BeadsWslPort { return 0 }
    function global:Test-BeadsServerPort { return $false }
    . $script:ProfileSource
    Mock Get-Command { [pscustomobject]@{ Source = $bdScript } } -ParameterFilter { $Name -eq 'bd' }
    Mock Get-Command { [pscustomobject]@{ Source = $gitScript } } -ParameterFilter { $Name -eq 'git' }

    bd list

    Assert-Equal $global:LASTEXITCODE 3
    Assert-Equal (Test-Path -LiteralPath $bdLog) $false
  }
}

Describe 'Windows PowerShell fragments' {
  BeforeAll {
    $script:RenderRoot = $env:DOTFILES_TEST_RENDER_DIR
  }

  It 'resolves external executables deterministically and restores scoped environment' {
    . (Join-Path $script:RenderRoot 'claude-env.ps1')
    Mock Get-Command {
      @(
        [pscustomobject]@{ Source = 'C:\tools\claude.ps1' },
        [pscustomobject]@{ Source = 'C:\tools\claude.exe' }
      )
    } -ParameterFilter { $Name -eq 'claude' }

    Assert-Equal (Resolve-ExternalCommandSource -Name 'claude') 'C:\tools\claude.exe'
    $env:PROFILE_SCOPE_TEST = 'before'
    Invoke-WithScopedEnv -Env @{ PROFILE_SCOPE_TEST = 'during'; PROFILE_NEW_VALUE = 'set' } -ScriptBlock {
      Assert-Equal $env:PROFILE_SCOPE_TEST 'during'
      Assert-Equal $env:PROFILE_NEW_VALUE 'set'
    }
    Assert-Equal $env:PROFILE_SCOPE_TEST 'before'
    Assert-Equal (Test-Path Env:PROFILE_NEW_VALUE) $false
  }

  It 'defines aliases package fallbacks and the guarded git helper' {
    Mock Get-Command { $null } -ParameterFilter { $Name -in @('chezmoi', 'winget', 'choco') }
    . (Join-Path $script:RenderRoot 'aliases.ps1')
    . (Join-Path $script:RenderRoot 'packages.ps1')
    . (Join-Path $script:RenderRoot 'git.ps1')
    $Error.Clear()

    winget list -ErrorAction SilentlyContinue

    Assert-Equal (Test-Path Function:\global:czp) $true
    Assert-Equal (Test-Path Function:\global:winget) $true
    Assert-Equal (Test-Path Alias:\gpls) $true
    Assert-Equal ($Error.Count -gt 0) $true
  }

  It 'deduplicates repaired paths case-insensitively in stable order' {
    $previousPath = $env:Path
    try {
      . (Join-Path $script:RenderRoot 'repair-path.ps1')
      $merged = Merge-PowerShellPath -MachinePath 'C:\Windows;C:\Tools' -UserPath 'c:\tools;C:\Users\fixture\bin'
      Assert-Equal $merged 'C:\Windows;C:\Tools;C:\Users\fixture\bin'
    } finally {
      $env:Path = $previousPath
    }
  }

  It 'sets non-secret OpenCode defaults without replacing explicit values' {
    $previousHome = $HOME
    Set-Variable HOME -Scope Global -Value $TestDrive -Force
    Remove-Item Env:ANTHROPIC_SYSTEM_PROMPT_PATH,Env:OPENCODE_DISABLE_CLAUDE_CODE_PROMPT,Env:OPENCODE_DISABLE_CLAUDE_CODE_SKILLS -ErrorAction SilentlyContinue
    $env:OPENCODE_PROFILES = 'defaults'
    try {
      . (Join-Path $script:RenderRoot 'opencode-env.ps1')
      Assert-Equal $env:ANTHROPIC_SYSTEM_PROMPT_PATH 'NUL'
      Assert-Equal $env:OPENCODE_DISABLE_CLAUDE_CODE_PROMPT '1'
      Assert-Equal $env:OPENCODE_DISABLE_CLAUDE_CODE_SKILLS '1'
      Assert-Matches $env:OPENCODE_ENV_FILE 'opencode[\\/]opencode\.env$'
    } finally {
      Set-Variable HOME -Scope Global -Value $previousHome -Force
    }
  }
}
