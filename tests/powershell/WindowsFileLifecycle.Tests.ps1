Describe 'Windows file lifecycle' {
  BeforeAll {
    . (Join-Path $PSScriptRoot 'PesterCompat.ps1')
    . (Join-Path $env:DOTFILES_TEST_RENDER_DIR 'windows-sync.ps1')
    . (Join-Path $env:DOTFILES_TEST_RENDER_DIR 'windows-cleanup.ps1')
  }

  BeforeEach {
    $script:Dest = $TestDrive
  }

  It 'converts chezmoi component prefixes in relative paths' {
    Assert-Equal (Convert-RelativePath 'private_parent/dot_child/executable_tool.ps1') 'parent\.child\tool.ps1'
  }

  It 'renders templates as UTF-8 without a BOM' {
    function chezmoi { @('first', 'second') }
    $global:LASTEXITCODE = 0
    $sourceFile = Join-Path $TestDrive 'source.ps1.tmpl'
    $destination = Join-Path $TestDrive 'output.ps1.tmpl'
    Set-Content -LiteralPath $sourceFile -Value 'fixture'

    Render-Or-CopyFile -srcFile $sourceFile -dstFile $destination

    $written = Join-Path $TestDrive 'output.ps1'
    Assert-Equal ([System.IO.File]::ReadAllText($written)) "first`nsecond"
    $bytes = [System.IO.File]::ReadAllBytes($written)
    Assert-Equal (($bytes[0] -eq 0xEF) -and ($bytes[1] -eq 0xBB) -and ($bytes[2] -eq 0xBF)) $false
  }

  It 'propagates template command failures without writing output' {
    function chezmoi { $global:LASTEXITCODE = 17 }
    $sourceFile = Join-Path $TestDrive 'source with spaces.ps1.tmpl'
    $destination = Join-Path $TestDrive 'output with spaces.ps1.tmpl'
    Set-Content -LiteralPath $sourceFile -Value 'fixture'
    $threw = $false

    try { Render-Or-CopyFile -srcFile $sourceFile -dstFile $destination } catch { $threw = $true }

    Assert-Equal $threw $true
    Assert-Equal (Test-Path -LiteralPath (Join-Path $TestDrive 'output with spaces.ps1')) $false
  }

  It 'copies static files and creates parent directories' {
    $sourceFile = Join-Path $TestDrive 'source.txt'
    $destination = Join-Path $TestDrive 'nested\output.txt'
    Set-Content -LiteralPath $sourceFile -Value 'fixture' -NoNewline

    Render-Or-CopyFile -srcFile $sourceFile -dstFile $destination

    Assert-Equal (Get-Content -LiteralPath $destination -Raw) 'fixture'
  }

  It 'removes curated stale directories and preserves unrelated state' {
    $stale = Join-Path $TestDrive '.config\doom'
    $unrelated = Join-Path $TestDrive '.config\keep'
    New-Item -ItemType Directory -Path $stale | Out-Null
    New-Item -ItemType Directory -Path $unrelated | Out-Null

    Remove-StaleWindowsConfig -HomePath $TestDrive -RelativePaths @('.config\doom')
    Remove-StaleWindowsConfig -HomePath $TestDrive -RelativePaths @('.config\doom')

    Assert-Equal (Test-Path -LiteralPath $stale) $false
    Assert-Equal (Test-Path -LiteralPath $unrelated) $true
  }

  It 'rejects rooted and traversal cleanup paths' {
    $traversalThrew = $false
    try { Remove-RelPath '..\outside' } catch { $traversalThrew = $true }
    Assert-Equal $traversalThrew $true

    $rootedThrew = $false
    try { Remove-RelPath ([System.IO.Path]::GetPathRoot($TestDrive)) } catch { $rootedThrew = $true }
    Assert-Equal $rootedThrew $true
  }

  It 'removes only the requested wrong-apply path' {
    $target = Join-Path $TestDrive 'assets'
    $unrelated = Join-Path $TestDrive 'keep'
    New-Item -ItemType Directory -Path $target | Out-Null
    New-Item -ItemType Directory -Path $unrelated | Out-Null

    Remove-RelPath 'assets'

    Assert-Equal (Test-Path -LiteralPath $target) $false
    Assert-Equal (Test-Path -LiteralPath $unrelated) $true
  }

  It 'honors cleanup dry-run state' {
    $target = Join-Path $TestDrive 'assets'
    New-Item -ItemType Directory -Path $target | Out-Null
    $env:CHEZMOI_WRONG_APPLY_DRYRUN = '1'
    try {
      Remove-RelPath 'assets'
    } finally {
      Remove-Item Env:CHEZMOI_WRONG_APPLY_DRYRUN -ErrorAction SilentlyContinue
    }

    Assert-Equal (Test-Path -LiteralPath $target) $true
  }
}
