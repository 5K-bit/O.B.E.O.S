param(
    [string]$VmName = "OBEOSv3",
    [string]$Vhdx = "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)\data\vm\OBEOSv3\OBEOSv3.vhdx",
    [switch]$Start
)

$ErrorActionPreference = "Stop"
$principal = [Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run from elevated PowerShell."
}

Import-Module Hyper-V
$vm = Get-VM -Name $VmName -ErrorAction Stop

if ($vm.State.ToString() -ne "Off") {
    try { Stop-VM -Name $VmName -TurnOff -Force } catch { }
    Start-Sleep -Seconds 2
}

foreach ($snap in @(Get-VMSnapshot -VMName $VmName -ErrorAction SilentlyContinue)) {
    Write-Host "Removing checkpoint: $($snap.Name)"
    Remove-VMSnapshot -VMName $VmName -Name $snap.Name -Confirm:$false
}

$drive = Get-VMHardDiskDrive -VMName $VmName | Select-Object -First 1
if (-not $drive) {
    if (-not (Test-Path -LiteralPath $Vhdx)) {
        throw "No disk on VM and VHDX not found: $Vhdx"
    }
    Write-Host "Attaching $Vhdx"
    Add-VMHardDiskDrive -VMName $VmName -Path $Vhdx -ControllerType SCSI -ControllerNumber 0 -ControllerLocation 0
    $drive = Get-VMHardDiskDrive -VMName $VmName | Select-Object -First 1
}

if ($drive.Path -like "*.avhdx") {
    $parent = (Get-VHD -Path $drive.Path).ParentPath
    if ($parent -and (Test-Path -LiteralPath $parent)) {
        Write-Host "Replacing differencing disk with parent: $parent"
        Remove-VMHardDiskDrive -VMName $VmName `
            -ControllerType $drive.ControllerType `
            -ControllerNumber $drive.ControllerNumber `
            -ControllerLocation $drive.ControllerLocation
        Add-VMHardDiskDrive -VMName $VmName -Path $parent `
            -ControllerType SCSI -ControllerNumber 0 -ControllerLocation 0
        $drive = Get-VMHardDiskDrive -VMName $VmName | Select-Object -First 1
    }
}

try {
    Set-VMFirmware -VMName $VmName -EnableSecureBoot Off -ErrorAction SilentlyContinue
}
catch { }

Set-VMFirmware -VMName $VmName -FirstBootDevice $drive
$fw = Get-VMFirmware -VMName $VmName
$hd = $fw.BootOrder | Where-Object { $_.BootType -eq "Drive" } | Select-Object -First 1
$net = $fw.BootOrder | Where-Object { $_.BootType -eq "Network" } | Select-Object -First 1
if ($hd) {
    if ($net) {
        Set-VMFirmware -VMName $VmName -BootOrder $hd, $net
    }
    else {
        Set-VMFirmware -VMName $VmName -BootOrder $hd
    }
}

Write-Host ""
Write-Host "Boot fix applied for $VmName"
Write-Host "  Disk: $($drive.Path)"
Write-Host "  Boot order: Drive first, then Network (if present)"
Write-Host "  Secure Boot: Off"

if ($Start) {
    Start-VM -Name $VmName
    Start-Process "$env:WINDIR\System32\vmconnect.exe" -ArgumentList "localhost", $VmName
}
