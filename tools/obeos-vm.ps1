param(
    [ValidateSet("update", "bundle", "repair", "fix-boot", "fix-grub", "apply-desktop", "start", "stop", "status", "password", "migrate-v3")]
    [string]$Action = "update",
    [string]$VmName = "OBEOSv3",
    [string]$Vhdx = "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)\data\vm\OBEOSv3\OBEOSv3.vhdx",
    [string]$Distro = "",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$bundleRoot = Join-Path $repoRoot "data\obeos-remaster"
$bundleZip = Join-Path $repoRoot "data\obeos-remaster.zip"

function Test-IsAdmin {
    $principal = [Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-Admin {
    if (-not (Test-IsAdmin)) {
        throw "Run this action from an elevated PowerShell window."
    }
}

function Ensure-ObeosVmRegistered {
    if (-not (Get-Command Get-VM -ErrorAction SilentlyContinue)) {
        return
    }
    try {
        Get-VM -Name $VmName -ErrorAction Stop | Out-Null
        return
    }
    catch {
        $vmDataRoot = Split-Path -Parent $Vhdx
        $vmcx = Get-ChildItem -LiteralPath $vmDataRoot -Filter "*.vmcx" -Recurse -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -eq $vmcx) {
            throw "Hyper-V VM '$VmName' is not registered, and no .vmcx was found under $vmDataRoot."
        }
        Write-Host "Registering Hyper-V VM from $($vmcx.FullName)"
        Import-VM -Path $vmcx.FullName -Register | Out-Null
    }
}

function Invoke-BundleRefresh {
    Push-Location $repoRoot
    try {
        Write-Host "[bundle] Materializing current OBEOS bundle"
        python -m blackfong_ecosystem.cli obeos materialize --output "data\obeos-remaster" | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Bundle materialization failed."
        }
        Write-Host "[bundle] Refreshing data\obeos-remaster.zip"
        if (Test-Path -LiteralPath $bundleZip) {
            Remove-Item -LiteralPath $bundleZip -Force
        }
        Compress-Archive -Path (Join-Path $bundleRoot "*") -DestinationPath $bundleZip -Force
        $zip = Get-Item -LiteralPath $bundleZip
        Write-Host ("[bundle] Ready: {0} bytes" -f $zip.Length)
    }
    finally {
        Pop-Location
    }
}

function Start-ObeosVm {
    Assert-Admin
    Start-Service vmms
    Ensure-ObeosVmRegistered
    $vm = Get-VM -Name $VmName -ErrorAction Stop
    if ($vm.State.ToString() -ne "Running") {
        Start-VM -Name $VmName
    }
    Start-Process "$env:WINDIR\System32\vmconnect.exe" -Verb RunAs -ArgumentList "localhost", $VmName
}

function Stop-ObeosVm {
    Assert-Admin
    Ensure-ObeosVmRegistered
    $vm = Get-VM -Name $VmName -ErrorAction Stop
    if ($vm.State.ToString() -eq "Off") {
        Write-Host "$VmName is already off."
        return
    }
    Stop-VM -Name $VmName -Shutdown
    Write-Host "Shutdown requested for $VmName."
}

function Show-ObeosStatus {
    $vmState = "unknown"
    $vmFound = $false
    if (Get-Command Get-VM -ErrorAction SilentlyContinue) {
        try {
            $vm = Get-VM -Name $VmName -ErrorAction Stop
            $vmState = [string]$vm.State
            $vmFound = $true
        }
        catch {
            $vmState = "not registered or not accessible"
        }
    }
    [pscustomobject]@{
        VmName = $VmName
        VmFound = $vmFound
        VmState = $vmState
        Vhdx = $Vhdx
        VhdxExists = Test-Path -LiteralPath $Vhdx
        BundleRoot = $bundleRoot
        BundleExists = Test-Path -LiteralPath $bundleRoot
        BundleZip = $bundleZip
        BundleZipExists = Test-Path -LiteralPath $bundleZip
        Distro = $Distro
        Admin = Test-IsAdmin
        NextUpdateCommand = ".\tools\obeos-vm.ps1 update"
    } | Format-List
}

switch ($Action) {
    "bundle" {
        Invoke-BundleRefresh
    }
    "update" {
        Assert-Admin
        & (Join-Path $PSScriptRoot "update-obeos-vm.ps1") -VmName $VmName -Vhdx $Vhdx -Distro $Distro -NoStart:$NoStart
    }
    "repair" {
        Assert-Admin
        & (Join-Path $PSScriptRoot "repair-obeos-vm-desktop-first.ps1") -VmName $VmName -Vhdx $Vhdx -BundleRoot $bundleRoot -Distro $Distro -NoStart:$NoStart
    }
    "fix-boot" {
        Assert-Admin
        & (Join-Path $PSScriptRoot "fix-obeos-vm-boot.ps1") -VmName $VmName -Vhdx $Vhdx -Start:(-not $NoStart)
    }
    "fix-grub" {
        Assert-Admin
        & (Join-Path $PSScriptRoot "fix-obeos-vm-grub-root.ps1") -VmName $VmName -Vhdx $Vhdx -Start:(-not $NoStart)
    }
    "apply-desktop" {
        Assert-Admin
        & (Join-Path $PSScriptRoot "apply-obeos-desktop-on-disk.ps1") -VmName $VmName -Vhdx $Vhdx -Start:(-not $NoStart)
    }
    "start" {
        Start-ObeosVm
    }
    "stop" {
        Stop-ObeosVm
    }
    "status" {
        Show-ObeosStatus
    }
    "password" {
        Assert-Admin
        & (Join-Path $PSScriptRoot "reset-obeos-vm-password.ps1") -VmName $VmName -Vhdx $Vhdx -Distro $Distro -NoStart:$NoStart
    }
    "migrate-v3" {
        Assert-Admin
        & (Join-Path $PSScriptRoot "migrate-obeos-vm-v3.ps1") -OldVmName "OBEOS-v2-clean" -NewVmName "OBEOSv3" -Distro $Distro -NoStart:$NoStart
    }
}
