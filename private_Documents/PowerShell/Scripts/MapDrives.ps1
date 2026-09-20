function Invoke-MapDrives {
    param(
        [ValidateRange(1, 10)][int]$MaxAttempts = 3,
        [ValidateRange(0, 3600)][int]$RetryDelaySeconds = 30
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $failures = 0
        $mappedDrives = @(
            Get-SmbMapping |
                Where-Object -Property Status -Value Unavailable -EQ |
                Select-Object LocalPath, RemotePath
        )

        foreach ($mappedDrive in $mappedDrives) {
            try {
                New-SmbMapping -LocalPath $mappedDrive.LocalPath -RemotePath $mappedDrive.RemotePath -Persistent $true -ErrorAction Stop
            } catch {
                $failures++
                Write-Host "There was an error mapping $($mappedDrive.RemotePath) to $($mappedDrive.LocalPath)"
            }
        }

        if ($failures -eq 0 -or $attempt -eq $MaxAttempts) {
            return
        }

        Start-Sleep -Seconds $RetryDelaySeconds
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-MapDrives
}
