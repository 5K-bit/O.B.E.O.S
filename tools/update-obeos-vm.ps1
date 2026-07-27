param(
    [string]$VmName = "OBEOS-v1",
    [string]$Vhdx = "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)\data\vm\OBEOS-v1\OBEOS-v1.vhdx",
    [string]$Distro = "Ubuntu-24.04",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$bundleRoot = Join-Path $repoRoot "data\obeos-remaster"
$bundleZip = Join-Path $repoRoot "data\obeos-remaster.zip"

$principal = [Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window."
}

Push-Location $repoRoot
try {
    Write-Host "[1/4] Materializing current OBEOS bundle"
    python -m blackfong_ecosystem.cli obeos materialize --output "data\obeos-remaster" | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Bundle materialization failed."
    }

    Write-Host "[2/4] Refreshing bundle zip"
    if (Test-Path -LiteralPath $bundleZip) {
        Remove-Item -LiteralPath $bundleZip -Force
    }
    Compress-Archive -Path (Join-Path $bundleRoot "*") -DestinationPath $bundleZip -Force

    Write-Host "[3/4] Syncing bundle into existing VM disk and forcing desktop-first boot"
    $repairArgs = @{
        VmName = $VmName
        Vhdx = $Vhdx
        BundleRoot = $bundleRoot
        Distro = $Distro
    }
    if ($NoStart) {
        $repairArgs.NoStart = $true
    }
    & (Join-Path $PSScriptRoot "repair-obeos-vm-desktop-first.ps1") @repairArgs
    if ($LASTEXITCODE -ne 0) {
        throw "VM disk update failed."
    }

    Write-Host "[4/4] Host-side update complete"
    Write-Host "After the VM boots, open Konsole and run:"
    Write-Host "  sudo /opt/obeos/bin/bootstrap-kubuntu-guest.sh"
    Write-Host "That finalizes guest dependencies, including PySide6 for the Qt desktop workspace."
}
finally {
    Pop-Location
}
