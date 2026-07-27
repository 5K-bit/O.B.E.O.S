param(
    [string]$TaskName = "O.B.E.O.S Legion HUD"
)

$ErrorActionPreference = "Stop"
$StartupLauncher = Join-Path ([Environment]::GetFolderPath("Startup")) "Start OBEOS Legion HUD.cmd"

$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task: $TaskName"
} else {
    Write-Host "Scheduled task not found: $TaskName"
}

if (Test-Path $StartupLauncher) {
    Remove-Item -LiteralPath $StartupLauncher -Force
    Write-Host "Removed Startup launcher: $StartupLauncher"
} else {
    Write-Host "Startup launcher not found: $StartupLauncher"
}
