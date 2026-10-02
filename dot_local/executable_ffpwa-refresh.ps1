Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:UlidPattern = '[0-9A-HJKMNP-TV-Z]{26}'

function Show-Usage {
    @'
usage: ffpwa-refresh.ps1 [sites|runtime|auto]

  sites    Update every FirefoxPWA web app (default). Rebuilds the shortcuts,
           names, and icons, the same as "Update web apps" in the extension.
  runtime  Reinstall and patch the FirefoxPWA runtime from Mozilla's latest
           Firefox, then update every web app. Refuses while web apps run.
  auto     Run "runtime" only when the runtime is older than the installed
           Firefox. Skips with a warning while web apps run.
'@ | Write-Host
}

function Write-Info([string]$Message) { Write-Host "INFO: $Message" }
function Write-ErrorLine([string]$Message) { [Console]::Error.WriteLine("ERROR: $Message") }

function Get-RuntimeDir {
    $userData = if ($env:FFPWA_REFRESH_USERDATA) { $env:FFPWA_REFRESH_USERDATA } else { Join-Path $env:APPDATA 'FirefoxPWA' }
    Join-Path $userData 'runtime'
}

function Get-FirefoxDir {
    if ($env:FFPWA_REFRESH_FIREFOX_DIR) { return $env:FFPWA_REFRESH_FIREFOX_DIR }

    foreach ($root in 'HKLM:\SOFTWARE\Mozilla\Mozilla Firefox', 'HKLM:\SOFTWARE\WOW6432Node\Mozilla\Mozilla Firefox') {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $version = Get-ItemPropertyValue -LiteralPath $root -Name 'CurrentVersion' -ErrorAction SilentlyContinue
        if (-not $version) { continue }
        $installDir = Get-ItemPropertyValue -LiteralPath (Join-Path $root "$version\Main") -Name 'Install Directory' -ErrorAction SilentlyContinue
        if ($installDir -and (Test-Path -LiteralPath $installDir -PathType Container)) { return $installDir }
    }

    Join-Path $env:ProgramFiles 'Mozilla Firefox'
}

function Get-IniVersion([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^Version=(.+)$') { return $Matches[1].Trim() }
    }
    $null
}

function Test-NumericVersion([string]$Version) {
    $Version -match '^\d+(\.\d+)*$'
}

function Test-VersionOlder([string]$Left, [string]$Right) {
    $leftParts = @($Left.Split('.') | ForEach-Object { [long]$_ })
    $rightParts = @($Right.Split('.') | ForEach-Object { [long]$_ })
    $count = [Math]::Max($leftParts.Count, $rightParts.Count)
    for ($index = 0; $index -lt $count; $index++) {
        $leftPart = if ($index -lt $leftParts.Count) { $leftParts[$index] } else { 0 }
        $rightPart = if ($index -lt $rightParts.Count) { $rightParts[$index] } else { 0 }
        if ($leftPart -lt $rightPart) { return $true }
        if ($leftPart -gt $rightPart) { return $false }
    }
    $false
}

function Get-FirefoxpwaPath {
    $command = Get-Command firefoxpwa -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { $command.Source } else { $null }
}

function Invoke-Firefoxpwa([string[]]$Arguments) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & (Get-FirefoxpwaPath) @Arguments | Out-Host
        $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Get-FirefoxpwaProfileList {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& (Get-FirefoxpwaPath) profile list)
        if ($LASTEXITCODE -ne 0) { throw "firefoxpwa profile list failed (exit $LASTEXITCODE)" }
        $output
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Get-WebApps {
    foreach ($line in Get-FirefoxpwaProfileList) {
        if ("$line".TrimEnd("`r") -match "^- (.+) \(($script:UlidPattern)\)$") {
            $label = $Matches[1]
            $separator = $label.IndexOf(': ')
            [pscustomobject]@{
                Id   = $Matches[2]
                Name = if ($separator -ge 0) { $label.Substring(0, $separator) } else { $label }
            }
        }
    }
}

function Test-RuntimeRunning {
    $prefix = (Get-RuntimeDir).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    foreach ($process in @(Get-Process -Name firefox -ErrorAction SilentlyContinue)) {
        $processPath = try { $process.Path } catch { $null }
        if ($processPath -and $processPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    $false
}

function Test-SevenZipInstalled {
    $uninstallKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\7-Zip'
    if (Get-ItemPropertyValue -LiteralPath $uninstallKey -Name 'DisplayVersion' -ErrorAction SilentlyContinue) { return $true }
    [bool](Get-Command 7z.exe -CommandType Application -ErrorAction SilentlyContinue)
}

function Invoke-Sites {
    if (-not (Get-FirefoxpwaPath)) {
        Write-ErrorLine 'firefoxpwa is not on PATH'
        return 1
    }

    try {
        $apps = @(Get-WebApps)
    } catch {
        Write-ErrorLine $_.Exception.Message
        return 1
    }
    if ($apps.Count -eq 0) {
        Write-Info 'No web apps installed; nothing to update'
        return 0
    }

    $failed = [System.Collections.Generic.List[string]]::new()
    foreach ($app in $apps) {
        Write-Info "Updating $($app.Name) ($($app.Id))"
        $exitCode = Invoke-Firefoxpwa @('site', 'update', $app.Id)
        if ($exitCode -ne 0) {
            Write-Warning "Updating $($app.Name) failed (exit $exitCode)"
            $failed.Add($app.Name)
        }
    }

    Write-Info "Updated $($apps.Count - $failed.Count) of $($apps.Count) web apps"
    if ($failed.Count -gt 0) {
        Write-ErrorLine "Failed to update: $($failed -join ', ')"
        return 1
    }
    0
}

function Invoke-Runtime {
    if (-not (Get-FirefoxpwaPath)) {
        Write-ErrorLine 'firefoxpwa is not on PATH'
        return 1
    }
    if (Test-RuntimeRunning) {
        Write-ErrorLine 'FirefoxPWA web apps are running; quit them and run ffpwa-refresh.ps1 runtime again'
        return 2
    }

    Write-Info "Reinstalling the FirefoxPWA runtime from the latest Mozilla Firefox"
    $exitCode = Invoke-Firefoxpwa @('runtime', 'install')
    if ($exitCode -ne 0) {
        Write-ErrorLine "firefoxpwa runtime install failed (exit $exitCode)"
        return 1
    }
    $version = Get-IniVersion (Join-Path (Get-RuntimeDir) 'application.ini')
    if ($version) { Write-Info "FirefoxPWA runtime is now Firefox $version" }

    Invoke-Sites
}

function Invoke-Auto {
    if (-not (Get-FirefoxpwaPath)) {
        Write-Info 'firefoxpwa is not installed; skipping the runtime check'
        return 0
    }
    $firefoxDir = Get-FirefoxDir
    $firefoxVersion = Get-IniVersion (Join-Path $firefoxDir 'application.ini')
    if (-not $firefoxVersion) {
        Write-Info "Firefox not found at $firefoxDir; skipping the runtime check"
        return 0
    }
    $runtimeVersion = Get-IniVersion (Join-Path (Get-RuntimeDir) 'application.ini')
    if (-not $runtimeVersion) {
        Write-Info 'FirefoxPWA runtime is not installed; skipping the runtime check'
        return 0
    }
    if (-not ((Test-NumericVersion $runtimeVersion) -and (Test-NumericVersion $firefoxVersion))) {
        Write-Info "Cannot compare runtime $runtimeVersion with Firefox $firefoxVersion; skipping the runtime check"
        return 0
    }
    if (-not (Test-VersionOlder $runtimeVersion $firefoxVersion)) {
        Write-Info "FirefoxPWA runtime is up to date (runtime $runtimeVersion, Firefox $firefoxVersion)"
        return 0
    }

    Write-Info "FirefoxPWA runtime $runtimeVersion is older than Firefox $firefoxVersion"
    if (Test-RuntimeRunning) {
        Write-Warning 'FirefoxPWA web apps are running; skipping the runtime refresh. Quit them and run ffpwa-refresh.ps1 runtime.'
        return 0
    }
    if (-not (Test-SevenZipInstalled)) {
        Write-Warning '7-Zip is not installed, so firefoxpwa would prompt for elevation to install it; skipping the runtime refresh. Install 7-Zip or run ffpwa-refresh.ps1 runtime.'
        return 0
    }

    Invoke-Runtime
}

function Invoke-FfpwaRefresh([string[]]$Arguments) {
    $command = if (@($Arguments).Count -gt 0) { $Arguments[0] } else { 'sites' }
    switch ($command) {
        'sites' { return Invoke-Sites }
        'runtime' { return Invoke-Runtime }
        'auto' { return Invoke-Auto }
        { $_ -in @('-h', '--help', 'help') } { Show-Usage; return 0 }
        default { Show-Usage; return 64 }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-FfpwaRefresh $args)
}
