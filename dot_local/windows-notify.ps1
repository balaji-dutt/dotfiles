param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Message
)

$ErrorActionPreference = 'Stop'

try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null

    $app = Get-StartApps | Where-Object {
        $_.AppID -match '\\WindowsPowerShell\\v1\.0\\powershell\.exe$'
    } | Select-Object -First 1
    if ($null -eq $app) {
        throw 'Windows PowerShell notification identity is not registered'
    }

    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
        [Windows.UI.Notifications.ToastTemplateType]::ToastText02
    )
    $text = $xml.GetElementsByTagName('text')
    $text.Item(0).InnerText = $Title
    $text.Item(1).InnerText = $Message
    $audio = $xml.CreateElement('audio')
    $audio.SetAttribute('silent', 'true')
    $null = $xml.DocumentElement.AppendChild($audio)

    $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app.AppID).Show($toast)
} catch {
    [Console]::Error.WriteLine("windows-notify: $($_.Exception.Message)")
    exit 1
}
