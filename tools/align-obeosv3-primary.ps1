param(
    [string]$PrimaryVmName = "OBEOSv3",
    [string]$LegacyVmName = "OBEOS-v2",
    [string]$InstallVhdx = "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)\data\vm\OBEOSv3\OBEOSv3.vhdx",
    [switch]$NoRepair
)

$ErrorActionPreference = "Stop"
$principal = [Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run from elevated PowerShell."
}

Import-Module Hyper-V
if (-not (Test-Path -LiteralPath $InstallVhdx)) {
    throw "Install VHDX not found: $InstallVhdx"
}
$InstallVhdx = (Resolve-Path -LiteralPath $InstallVhdx).Path

function Stop-IfRunning([string]$Name) {
    $vm = Get-VM -Name $Name -ErrorAction SilentlyContinue
    if (-not $vm) { return }
    if ($vm.State.ToString() -ne "Off") {
        try { Stop-VM -Name $Name -TurnOff -Force -ErrorAction Stop }
        catch {
            $vm = Get-VM -Name $Name
            if ($vm.State.ToString() -ne "Off") { throw }
        }
    }
}

function Clear-Disks([string]$Name) {
    foreach ($d in @(Get-VMHardDiskDrive -VMName $Name -ErrorAction SilentlyContinue)) {
        Remove-VMHardDiskDrive -VMName $Name `
            -ControllerType $d.ControllerType `
            -ControllerNumber $d.ControllerNumber `
            -ControllerLocation $d.ControllerLocation `
            -ErrorAction Stop
    }
}

function Attach-InstallDisk([string]$Name) {
    $existing = Get-VMHardDiskDrive -VMName $Name -ErrorAction SilentlyContinue
    if ($existing | Where-Object { $_.Path -eq $InstallVhdx }) {
        return
    }
    Clear-Disks -Name $Name
    Add-VMHardDiskDrive -VMName $Name -Path $InstallVhdx -ControllerType SCSI -ControllerNumber 0 -ControllerLocation 0
}

$legacy = Get-VM -Name $LegacyVmName -ErrorAction SilentlyContinue
$primary = Get-VM -Name $PrimaryVmName -ErrorAction SilentlyContinue

Stop-IfRunning -Name $LegacyVmName
Stop-IfRunning -Name $PrimaryVmName

if ($legacy -and $primary -and $legacy.Name -ne $primary.Name) {
    Write-Host "Removing duplicate VM registration '$PrimaryVmName' (keeping VHDX on disk)."
    Clear-Disks -Name $PrimaryVmName
    Remove-VM -Name $PrimaryVmName -Force
    $primary = $null
}

if ($legacy) {
    Write-Host "Pointing '$LegacyVmName' at install disk and renaming to '$PrimaryVmName'."
    Attach-InstallDisk -Name $LegacyVmName
    if ($legacy.Name -ne $PrimaryVmName) {
        Rename-VM -Name $LegacyVmName -NewName $PrimaryVmName
    }
}
elseif (-not $primary) {
    throw "Neither '$LegacyVmName' nor '$PrimaryVmName' is registered in Hyper-V."
}
else {
    Write-Host "Attaching install disk to existing '$PrimaryVmName'."
    Attach-InstallDisk -Name $PrimaryVmName
}

$statusPath = Join-Path (Split-Path -Parent $InstallVhdx) "boot_obeos_hyperv_status.json"
@{
    vmName = $PrimaryVmName
    vhdPath = $InstallVhdx
    alignedFrom = $LegacyVmName
    alignedAt = (Get-Date).ToString("o")
    generation = "v3"
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statusPath -Encoding UTF8

Write-Host ""
Write-Host "Hyper-V VM '$PrimaryVmName' now uses:"
Write-Host "  $InstallVhdx"
Write-Host ""
Write-Host "The old OBEOS-v2 differencing disk (107 MB) is no longer attached."
Write-Host "Use VM '$PrimaryVmName' in Hyper-V Manager (not the old OBEOS-v2 differencing disk)."

if (-not $NoRepair) {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    & (Join-Path $PSScriptRoot "repair-obeos-vm-desktop-first.ps1") `
        -VmName $PrimaryVmName `
        -Vhdx $InstallVhdx `
        -BundleRoot (Join-Path $repoRoot "data\obeos-remaster") `
        -NoStart
}
