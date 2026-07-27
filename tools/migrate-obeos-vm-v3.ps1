param(
    [string]$OldVmName = "OBEOS-v2-clean",
    [string]$NewVmName = "OBEOSv3",
    [string]$InstallVhdx = "",
    [string]$LegacyHyperVName = "OBEOS-v2",
    [string]$Distro = "Ubuntu-24.04",
    [switch]$NoStart,
    [switch]$SkipBundleUpdate
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$oldVmRoot = Join-Path $repoRoot "data\vm\$OldVmName"
$newVmRoot = Join-Path $repoRoot "data\vm\$NewVmName"
$oldVhdx = Join-Path $oldVmRoot "$OldVmName.vhdx"
$newVhdx = Join-Path $newVmRoot "$NewVmName.vhdx"
$bundleRoot = Join-Path $repoRoot "data\obeos-remaster"

$principal = [Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window."
}

Import-Module Hyper-V -ErrorAction Stop

function Resolve-VhdPath {
    param([string]$Preferred)
    if (Test-Path -LiteralPath $Preferred) {
        return (Resolve-Path -LiteralPath $Preferred).Path
    }
    $alt = Get-ChildItem -LiteralPath $oldVmRoot -Filter "*.vhdx" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($alt) {
        return $alt.FullName
    }
    throw "No VHDX found under $oldVmRoot"
}

Write-Host "=== OBEOS VM migration: $OldVmName -> $NewVmName ==="

$resolvedOldVhd = Resolve-VhdPath -Preferred $oldVhdx
if (-not (Test-Path -LiteralPath $oldVmRoot)) {
    throw "VM data folder not found: $oldVmRoot"
}

$vm = Get-VM -Name $OldVmName -ErrorAction SilentlyContinue
if (-not $vm) {
    $vm = Get-VM -Name $NewVmName -ErrorAction SilentlyContinue
    if ($vm) {
        Write-Host "VM already registered as $NewVmName"
    }
    else {
        $vmcx = Get-ChildItem -LiteralPath $oldVmRoot -Filter "*.vmcx" -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($vmcx) {
            Write-Host "Registering VM from $($vmcx.FullName)"
            Import-VM -Path $vmcx.FullName -Register | Out-Null
            $vm = Get-VM -Name $OldVmName -ErrorAction SilentlyContinue
        }
    }
}

if ($vm -and $vm.State.ToString() -ne "Off") {
    Write-Host "Stopping $($vm.Name) ..."
    try {
        Stop-VM -Name $vm.Name -TurnOff -Force -ErrorAction Stop
    }
    catch {
        $vm = Get-VM -Name $vm.Name -ErrorAction Stop
        if ($vm.State.ToString() -ne "Off") {
            throw
        }
    }
}

if ($vm -and $vm.Name -ne $NewVmName) {
    Write-Host "Renaming Hyper-V VM to $NewVmName"
    Rename-VM -Name $vm.Name -NewName $NewVmName
    $vm = Get-VM -Name $NewVmName -ErrorAction Stop
}

if (-not (Test-Path -LiteralPath $newVmRoot)) {
    Write-Host "Moving VM folder to $newVmRoot"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $newVmRoot) | Out-Null
    Move-Item -LiteralPath $oldVmRoot -Destination $newVmRoot
}
elseif ($oldVmRoot -ne $newVmRoot -and (Test-Path -LiteralPath $oldVmRoot)) {
    Write-Host "Target folder exists; merging VHD from old folder if needed"
    if ((Test-Path -LiteralPath $resolvedOldVhd) -and -not (Test-Path -LiteralPath $newVhdx)) {
        Move-Item -LiteralPath $resolvedOldVhd -Destination $newVhdx
    }
}

if (-not (Test-Path -LiteralPath $newVhdx)) {
    $candidate = Get-ChildItem -LiteralPath $newVmRoot -Filter "*.vhdx" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "$NewVmName.vhdx" } |
        Select-Object -First 1
    if ($candidate) {
        Write-Host "Renaming VHDX $($candidate.Name) -> $NewVmName.vhdx"
        Rename-Item -LiteralPath $candidate.FullName -NewName "$NewVmName.vhdx"
    }
}

if (-not (Test-Path -LiteralPath $newVhdx)) {
    throw "Expected VHDX at $newVhdx after migration."
}

$newVhdx = (Resolve-Path -LiteralPath $newVhdx).Path

if ($vm) {
    $drives = Get-VMHardDiskDrive -VMName $NewVmName -ErrorAction SilentlyContinue
    foreach ($drive in $drives) {
        if ($drive.Path -and ($drive.Path -ne $newVhdx)) {
            Write-Host "Pointing VM disk controller to $newVhdx"
            Set-VMHardDiskDrive -VMName $NewVmName -ControllerType $drive.ControllerType -ControllerNumber $drive.ControllerNumber -ControllerLocation $drive.ControllerLocation -Path $newVhdx
        }
    }
}

$statusPath = Join-Path $newVmRoot "boot_obeos_hyperv_status.json"
@{
    vmName = $NewVmName
    migratedFrom = $OldVmName
    vhdPath = $newVhdx
    vmRoot = $newVmRoot
    migratedAt = (Get-Date).ToString("o")
    generation = "v3"
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statusPath -Encoding UTF8

if (-not $SkipBundleUpdate) {
    Write-Host "Materializing and syncing OBEOS v3 bundle into guest disk"
    Push-Location $repoRoot
    try {
        python -m blackfong_ecosystem.cli obeos materialize --output "data\obeos-remaster" | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Bundle materialization failed."
        }
        $bundleZip = Join-Path $repoRoot "data\obeos-remaster.zip"
        if (Test-Path -LiteralPath $bundleZip) {
            Remove-Item -LiteralPath $bundleZip -Force
        }
        Compress-Archive -Path (Join-Path $bundleRoot "*") -DestinationPath $bundleZip -Force
    }
    finally {
        Pop-Location
    }

    $repairArgs = @{
        VmName = $NewVmName
        Vhdx = $newVhdx
        BundleRoot = $bundleRoot
        Distro = $Distro
    }
    if ($NoStart) {
        $repairArgs.NoStart = $true
    }
    & (Join-Path $PSScriptRoot "repair-obeos-vm-desktop-first.ps1") @repairArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Guest disk sync failed."
    }
}

if ($LegacyHyperVName) {
    $alignArgs = @{
        PrimaryVmName = $NewVmName
        LegacyVmName = $LegacyHyperVName
        NoRepair = $true
    }
    if ($InstallVhdx) {
        $alignArgs.InstallVhdx = $InstallVhdx
    }
    elseif (Test-Path -LiteralPath $newVhdx) {
        $alignArgs.InstallVhdx = $newVhdx
    }
    & (Join-Path $PSScriptRoot "align-obeosv3-primary.ps1") @alignArgs
}

Write-Host ""
Write-Host "Migration complete."
Write-Host "  VM name:  $NewVmName"
Write-Host "  VHDX:     $newVhdx"
Write-Host ""
Write-Host "After the VM boots to Plasma, open Konsole and run:"
Write-Host "  sudo /opt/obeos/bin/bootstrap-kubuntu-guest.sh"
Write-Host "  sudo /opt/obeos/bin/apply-obeos-branding.sh"
Write-Host "  sudo reboot"
Write-Host ""
Write-Host "Quick checks:"
Write-Host "  blackfong desktop workspace"
Write-Host "  blackfong hud --host 127.0.0.1 --port 8080 --no-open-browser"
