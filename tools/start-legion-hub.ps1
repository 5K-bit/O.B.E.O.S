param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8080,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

function Get-LegionLanAddress {
    try {
        $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.IPAddress -match '^(10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|192\.168\.)' -and
                $_.IPAddress -notmatch '^169\.254\.' -and
                $_.InterfaceAlias -notlike '*vEthernet*' -and
                $_.InterfaceAlias -notlike '*Tailscale*'
            } |
            Sort-Object InterfaceMetric |
            Select-Object -ExpandProperty IPAddress -Unique
        return @($addresses)
    } catch {
        return @()
    }
}

if (-not $Python) {
    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $Python = $VenvPython
    } else {
        $Python = "python"
    }
}

$env:BLACKFONG_HUD_HOST = $HostAddress
$env:BLACKFONG_HUD_PORT = [string]$Port
$env:BLACKFONG_HUD_OPEN_BROWSER = "false"

Write-Host "Legion pairing token:"
& $Python -m blackfong_ecosystem.cli legion token

Write-Host ""
Write-Host "Starting Blackcomputer Legion hub on $HostAddress`:$Port"
$LanAddresses = Get-LegionLanAddress
if ($LanAddresses.Count -gt 0) {
    Write-Host "Open one of these from the Legion:"
    foreach ($Address in $LanAddresses) {
        Write-Host "  http://$Address`:$Port/legion"
    }
} else {
    Write-Host "Open from the Legion using Blackcomputer's Wi-Fi IPv4 address:"
    Write-Host "  http://<blackcomputer-ip>:$Port/legion"
}
& $Python -m blackfong_ecosystem.cli hud --host $HostAddress --port $Port --no-open-browser
