param(
    [string]$TaskName = "O.B.E.O.S Legion HUD",
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8080,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$StartScript = Join-Path $ProjectRoot "tools\start-legion-hub.ps1"
$StartupLauncherName = "Start OBEOS Legion HUD.cmd"

if (-not (Test-Path $StartScript)) {
    throw "Missing start script: $StartScript"
}

$Arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-WindowStyle", "Hidden",
    "-File", "`"$StartScript`"",
    "-HostAddress", $HostAddress,
    "-Port", [string]$Port
)

if ($Python) {
    $Arguments += @("-Python", "`"$Python`"")
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ($Arguments -join " ") `
    -WorkingDirectory $ProjectRoot

$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "Starts the local O.B.E.O.S Legion dashboard HUD for LAN access." `
        -Force | Out-Null

    Write-Host "Installed scheduled task: $TaskName"
    Write-Host "It will start the Legion HUD on login at http://<blackcomputer-ip>:$Port/legion"
    Write-Host "To start it now, run:"
    Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`""
} catch {
    $StartupDir = [Environment]::GetFolderPath("Startup")
    $StartupLauncher = Join-Path $StartupDir $StartupLauncherName
    $CommandLine = 'start "" powershell.exe ' + ($Arguments -join " ")
    Set-Content -LiteralPath $StartupLauncher -Encoding ASCII -Value @(
        "@echo off",
        "cd /d `"$ProjectRoot`"",
        $CommandLine
    )

    Write-Host "Scheduled task install was unavailable: $($_.Exception.Message)"
    Write-Host "Installed current-user Startup launcher instead:"
    Write-Host "  $StartupLauncher"
    Write-Host "It will start the Legion HUD after this Windows user logs in."
}
