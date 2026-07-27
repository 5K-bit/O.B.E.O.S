param(
    [string]$NewVmName = "OBEOSv3",
    [string]$Distro = "Ubuntu-24.04",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$newVmRoot = Join-Path $repoRoot "data\vm\$NewVmName"
$newVhdx = Join-Path $newVmRoot "$NewVmName.vhdx"
$oldVhdx = Join-Path $newVmRoot "OBEOS-v2-clean.vhdx"
$bundleRoot = Join-Path $repoRoot "data\obeos-remaster"
$legacyRoot = Join-Path $repoRoot "data\vm\OBEOS-v2-clean"

Import-Module Hyper-V -ErrorAction Stop

if (Test-Path -LiteralPath $oldVhdx) {
    if (Test-Path -LiteralPath $newVhdx) {
        Remove-Item -LiteralPath $newVhdx -Force
    }
    Rename-Item -LiteralPath $oldVhdx -NewName "$NewVmName.vhdx"
    Write-Host "Renamed VHDX to $newVhdx"
}

if (-not (Test-Path -LiteralPath $newVhdx)) {
    throw "VHDX not found at $newVhdx"
}
$newVhdx = (Resolve-Path -LiteralPath $newVhdx).Path

$vm = Get-VM -Name $NewVmName -ErrorAction Stop
if ($vm.State.ToString() -ne "Off") {
    try {
        Stop-VM -Name $NewVmName -TurnOff -Force -ErrorAction Stop
    }
    catch {
        $vm = Get-VM -Name $NewVmName -ErrorAction Stop
        if ($vm.State.ToString() -ne "Off") {
            throw
        }
    }
}

foreach ($drive in Get-VMHardDiskDrive -VMName $NewVmName) {
    if ($drive.Path -ne $newVhdx) {
        Set-VMHardDiskDrive -VMName $NewVmName `
            -ControllerType $drive.ControllerType `
            -ControllerNumber $drive.ControllerNumber `
            -ControllerLocation $drive.ControllerLocation `
            -Path $newVhdx
        Write-Host "Updated VM disk path to $newVhdx"
    }
}

@{
    vmName = $NewVmName
    migratedFrom = "OBEOS-v2-clean"
    vhdPath = $newVhdx
    vmRoot = $newVmRoot
    migratedAt = (Get-Date).ToString("o")
    generation = "v3"
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $newVmRoot "boot_obeos_hyperv_status.json") -Encoding UTF8

Push-Location $repoRoot
try {
    python -m blackfong_ecosystem.cli obeos materialize --output "data\obeos-remaster" | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "materialize failed" }
    $zip = Join-Path $repoRoot "data\obeos-remaster.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path (Join-Path $bundleRoot "*") -DestinationPath $zip -Force
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
if ($NoStart) { $repairArgs.NoStart = $true }
& (Join-Path $PSScriptRoot "repair-obeos-vm-desktop-first.ps1") @repairArgs

if (Test-Path -LiteralPath $legacyRoot) {
    Remove-Item -LiteralPath $legacyRoot -Recurse -Force
    Write-Host "Removed empty legacy folder $legacyRoot"
}

Write-Host "OBEOSv3 ready. VM: $NewVmName  VHDX: $newVhdx"
