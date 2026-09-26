function New-CbmResult {
  param(
    [int] $ExitCode = 0,
    [string] $Stdout = '',
    [string] $Stderr = '',
    [switch] $TimedOut,
    [int] $ChildProcessId = 4242
  )

  $effectiveExitCode = $ExitCode
  if ($TimedOut) {
    $effectiveExitCode = $null
  }

  [pscustomobject]@{
    ExitCode     = $effectiveExitCode
    Stdout       = $Stdout
    Stderr       = $Stderr
    TimedOut     = [bool] $TimedOut
    StillRunning = $false
    ProcessId    = $ChildProcessId
  }
}

function Invoke-HookCatchingErrors {
  param([hashtable] $Parameters = @{})

  $arguments = @{ Path = $script:Binary }
  foreach ($key in $Parameters.Keys) {
    $arguments[$key] = $Parameters[$key]
  }

  $script:Failure = $null
  try {
    Invoke-CbmAutoIndexConfigure @arguments
  } catch {
    $script:Failure = $_.Exception.Message
  }
}

Describe 'CBM auto-index configuration hook' {
  BeforeAll {
    . (Join-Path $PSScriptRoot 'PesterCompat.ps1')
    . (Join-Path $env:DOTFILES_TEST_RENDER_DIR 'cbm-auto-index.ps1')
    $script:HookTimeoutSeconds = $CbmTimeoutSeconds
    $script:SlowCommand = @('-NoProfile', '-Command', 'Start-Sleep -Seconds 30')
    $script:PowerShellPath = (Get-Process -Id $PID).Path
  }

  BeforeEach {
    $script:Binary = Join-Path $TestDrive 'codebase-memory-mcp.exe'
    Set-Content -LiteralPath $script:Binary -Value '# fixture'
    $script:Calls = New-Object System.Collections.ArrayList
    $script:ObservedTimeout = 0
    Mock Write-Warning {}
  }

  It 'waits at least as long as CBM sequences its own startup deadlines' {
    # 10s version-cohort deadline plus the 120s startup-transition backstop.
    Assert-Equal ($script:HookTimeoutSeconds -ge 130) $true
    Assert-Equal ($script:HookTimeoutSeconds -le 300) $true
  }

  It 'reports a real timeout instead of throwing when failures are allowed' {
    $failure = $null
    $result = $null
    try {
      $result = Invoke-CbmNative -Path $script:PowerShellPath -Arguments $script:SlowCommand -TimeoutSeconds 1 -AllowFailure
    } catch {
      $failure = $_.Exception.Message
    }

    Assert-Equal $failure $null
    Assert-Equal $result.TimedOut $true
    Assert-Equal $result.StillRunning $false
  }

  It 'drains both streams and the exit code from a real command' {
    $command = 'Write-Output "on-stdout"; [Console]::Error.WriteLine("on-stderr"); exit 3'
    $result = Invoke-CbmNative -Path $script:PowerShellPath -Arguments @('-NoProfile', '-Command', $command) -TimeoutSeconds 20 -AllowFailure

    Assert-Equal $result.ExitCode 3
    Assert-Equal $result.Stdout 'on-stdout'
    Assert-Equal $result.Stderr 'on-stderr'
    Assert-Equal $result.TimedOut $false
  }

  It 'still throws on a real timeout when failures are not allowed' {
    $failure = $null
    try {
      [void] (Invoke-CbmNative -Path $script:PowerShellPath -Arguments $script:SlowCommand -TimeoutSeconds 1)
    } catch {
      $failure = $_.Exception.Message
    }

    Assert-Matches $failure 'Timed out running'
  }

  It 'passes the script-level budget through the entrypoint default' {
    Mock Invoke-CbmNative {
      $script:ObservedTimeout = $TimeoutSeconds
      New-CbmResult -Stdout 'true'
    }

    Invoke-HookCatchingErrors

    Assert-Equal $script:Failure $null
    Assert-Equal $script:ObservedTimeout $script:HookTimeoutSeconds
  }

  It 'skips without failing when a timeout coincides with a live CBM session' {
    Mock Invoke-CbmNative {
      [void] $script:Calls.Add(($Arguments -join ' '))
      New-CbmResult -TimedOut -ChildProcessId 4242
    }
    Mock Get-Process { [pscustomobject]@{ Id = 777; ProcessName = 'codebase-memory-mcp' } }

    Invoke-HookCatchingErrors -Parameters @{ TimeoutSeconds = 1 }

    Assert-Equal $script:Failure $null
    Assert-Equal $script:Calls.Count 1
    Assert-MockCalled Write-Warning -Times 1 -Exactly -Scope It -ParameterFilter {
      $Message -match 'auto_index was not verified this run' -and $Message -match 'no complete response within 1 seconds'
    }
  }

  It 'fails when a timeout has no live CBM session to explain it' {
    Mock Invoke-CbmNative { New-CbmResult -TimedOut }
    Mock Get-Process { @() }

    Invoke-HookCatchingErrors -Parameters @{ TimeoutSeconds = 1 }

    Assert-Matches $script:Failure 'did not finish within 1 seconds'
    Assert-Matches $script:Failure 'config get auto_index'
    Assert-MockCalled Write-Warning -Times 0 -Exactly -Scope It
  }

  It 'does not treat the timed-out child as the session that excuses the timeout' {
    Mock Invoke-CbmNative { New-CbmResult -TimedOut -ChildProcessId 4242 }
    Mock Get-Process { [pscustomobject]@{ Id = 4242; ProcessName = 'codebase-memory-mcp' } }

    Invoke-HookCatchingErrors -Parameters @{ TimeoutSeconds = 1 }

    Assert-Matches $script:Failure 'did not finish within 1 seconds'
  }

  It 'skips without failing when the CLI will not start while sessions are active' {
    Mock Invoke-CbmNative {
      New-CbmResult -ExitCode 1 -Stderr 'CBM CLI could not start because a pre-coordination generation is active'
    }
    Mock Get-Process { [pscustomobject]@{ Id = 777; ProcessName = 'codebase-memory-mcp' } }

    Invoke-HookCatchingErrors

    Assert-Equal $script:Failure $null
    Assert-MockCalled Write-Warning -Times 1 -Exactly -Scope It -ParameterFilter {
      $Message -match 'pre-coordination generation is active'
    }
  }

  It 'fails with the diagnostic when no live session explains a CLI failure' {
    Mock Invoke-CbmNative { New-CbmResult -ExitCode 1 -Stderr 'cache is unreadable' }
    Mock Get-Process { @() }

    Invoke-HookCatchingErrors

    Assert-Matches $script:Failure 'exited with code 1'
    Assert-Matches $script:Failure 'cache is unreadable'
    Assert-MockCalled Write-Warning -Times 0 -Exactly -Scope It
  }

  It 'leaves an already-enabled cache untouched' {
    Mock Invoke-CbmNative {
      [void] $script:Calls.Add(($Arguments -join ' '))
      New-CbmResult -Stdout 'true'
    }

    Invoke-HookCatchingErrors

    Assert-Equal $script:Failure $null
    Assert-Equal ($script:Calls -join '|') 'config get auto_index'
  }

  It 'sets then re-verifies a disabled cache' {
    $script:Replies = @('false', '', 'true')
    Mock Invoke-CbmNative {
      $reply = $script:Replies[$script:Calls.Count]
      [void] $script:Calls.Add(($Arguments -join ' '))
      New-CbmResult -Stdout $reply
    }

    Invoke-HookCatchingErrors

    Assert-Equal $script:Failure $null
    Assert-Equal ($script:Calls -join '|') 'config get auto_index|config set auto_index true|config get auto_index'
  }

  It 'reports a value that never becomes true as a failure' {
    Mock Invoke-CbmNative { New-CbmResult -Stdout 'false' }

    Invoke-HookCatchingErrors

    Assert-Matches $script:Failure 'auto_index verification failed'
  }

  It 'fails when the binary is missing' {
    Invoke-HookCatchingErrors -Parameters @{ Path = (Join-Path $TestDrive 'absent.exe') }

    Assert-Matches $script:Failure 'is not installed at'
  }
}
