param(
    [string]$VmName = "OBEOSv3",
    [string]$Vhdx = "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)\data\vm\OBEOSv3\OBEOSv3.vhdx",
    [string]$BundleRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) "data\obeos-remaster"),
    [string]$Distro = "",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$repairScriptPath = $null

function Resolve-ObeosWslDistro {
    param([string]$Preferred)
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($Preferred)) {
        $candidates += $Preferred.Trim()
    }
    $candidates += @("Ubuntu-24.04", "Ubuntu", "docker-desktop")
    $listed = @()
    try {
        $raw = (wsl -l -q 2>$null)
        if ($raw) {
            $listed = @($raw | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        }
    }
    catch { }
    foreach ($name in $listed) {
        if ($candidates -notcontains $name) {
            $candidates += $name
        }
    }
    foreach ($name in $candidates) {
        if ([string]::IsNullOrWhiteSpace($name)) {
            continue
        }
        wsl -d $name -u root -- sh -c "exit 0" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return $name
        }
    }
    throw "No usable WSL distro found. Install Ubuntu 24.04 or ensure docker-desktop WSL is available."
}

function Convert-ToWslLinuxPath {
    param(
        [Parameter(Mandatory = $true)][string]$WindowsPath,
        [string]$DistroName = ""
    )
    $resolved = (Resolve-Path -LiteralPath $WindowsPath).Path
    if ($resolved -match '^([A-Za-z]):\\(.*)$') {
        $drive = $Matches[1].ToLowerInvariant()
        $rest = $Matches[2] -replace '\\', '/'
        if ($DistroName -eq "docker-desktop") {
            return "/mnt/host/$drive/$rest"
        }
        return "/mnt/$drive/$rest"
    }
    throw "Cannot convert Windows path for WSL: $WindowsPath"
}

$principal = [Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window."
}

if (-not (Test-Path -LiteralPath $Vhdx)) {
    throw "VHDX not found: $Vhdx"
}
if (-not (Test-Path -LiteralPath $BundleRoot)) {
    throw "Bundle root not found: $BundleRoot"
}

$Distro = Resolve-ObeosWslDistro -Preferred $Distro
Write-Host "Using WSL distro: $Distro"

function Test-VmState {
    param(
        [Parameter(Mandatory = $true)]
        $Vm,
        [Parameter(Mandatory = $true)]
        [string]$Expected
    )
    return ($Vm.State.ToString() -eq $Expected)
}

function Stop-ObeosVmIfNeeded {
    param([string]$Name)
    if (-not (Get-Command Get-VM -ErrorAction SilentlyContinue)) {
        return
    }
    $vm = Get-VM -Name $Name -ErrorAction SilentlyContinue
    if (-not $vm) {
        return
    }
    if (Test-VmState -Vm $vm -Expected "Off") {
        return
    }
    try {
        Stop-VM -Name $Name -TurnOff -Force -ErrorAction Stop
    }
    catch {
        $vm = Get-VM -Name $Name -ErrorAction SilentlyContinue
        if ($vm -and -not (Test-VmState -Vm $vm -Expected "Off")) {
            throw
        }
    }
}

function Start-ObeosVmIfNeeded {
    param([string]$Name)
    if (-not (Get-Command Get-VM -ErrorAction SilentlyContinue)) {
        return
    }
    $vm = Get-VM -Name $Name -ErrorAction Stop
    if (Test-VmState -Vm $vm -Expected "Running") {
        return
    }
    try {
        Start-VM -Name $Name -ErrorAction Stop
    }
    catch {
        $vm = Get-VM -Name $Name -ErrorAction Stop
        if (-not (Test-VmState -Vm $vm -Expected "Running")) {
            throw
        }
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

function Restore-ObeosVmHardDrives {
    param(
        [string]$Name,
        [string]$DiskPath,
        [array]$SavedDrives
    )
    if (-not (Get-Command Add-VMHardDiskDrive -ErrorAction SilentlyContinue)) {
        return
    }
    $vhdxResolved = (Resolve-Path -LiteralPath $DiskPath).Path
    $attached = Get-VMHardDiskDrive -VMName $Name -ErrorAction SilentlyContinue
    if ($SavedDrives.Count -gt 0) {
        foreach ($drive in $SavedDrives) {
            $path = if ($drive.Path) { $drive.Path } else { $vhdxResolved }
            if ($path -ne $vhdxResolved) {
                $path = $vhdxResolved
            }
            $existing = $attached | Where-Object { $_.Path -eq $path }
            if ($existing) {
                continue
            }
            Add-VMHardDiskDrive -VMName $Name `
                -Path $path `
                -ControllerType $drive.ControllerType `
                -ControllerNumber $drive.ControllerNumber `
                -ControllerLocation $drive.ControllerLocation `
                -ErrorAction Stop
        }
        return
    }
    if ($attached | Where-Object { $_.Path -eq $vhdxResolved }) {
        return
    }
    Write-Host "Re-attaching VHDX to $Name at SCSI 0:0:0"
    Add-VMHardDiskDrive -VMName $Name -Path $vhdxResolved -ControllerType SCSI -ControllerNumber 0 -ControllerLocation 0
}

$repairSh = @'
#!/bin/sh
set -eu

mountpoint="/mnt/obeos-rescue"
mkdir -p "$mountpoint"
if mountpoint -q "$mountpoint" 2>/dev/null; then
  umount "$mountpoint"
fi

part=""
for candidate in /dev/disk/by-label/kubuntu_2404 /dev/sdd2; do
  if [ -e "$candidate" ] && [ "$(lsblk -no FSTYPE "$candidate" 2>/dev/null | head -n 1)" = "ext4" ]; then
    part="$candidate"
    break
  fi
done

if [ -z "$part" ]; then
  echo "Could not find the Kubuntu ext4 partition. Current block devices:" >&2
  lsblk -f >&2
  exit 2
fi

mount "$part" "$mountpoint"
trap 'umount "$mountpoint" >/dev/null 2>&1 || true' EXIT

root="$mountpoint"
if [ ! -f "$root/etc/os-release" ]; then
  echo "Mounted partition does not look like a Linux root filesystem: $part" >&2
  exit 3
fi

echo "Repairing OBEOS boot contract on $part"
mkdir -p "$root/etc/systemd/system"

if [ -n "${OBEOS_BUNDLE_ROOT:-}" ] && [ -d "$OBEOS_BUNDLE_ROOT" ]; then
  echo "Syncing current OBEOS bundle from $OBEOS_BUNDLE_ROOT"
  mkdir -p "$root/opt/obeos" "$root/opt/blackfong" "$root/opt/daise" "$root/etc/obeos" "$root/usr/share/applications"
  cp -a "$OBEOS_BUNDLE_ROOT/opt/obeos/." "$root/opt/obeos/"
  cp -a "$OBEOS_BUNDLE_ROOT/opt/blackfong/." "$root/opt/blackfong/"
  cp -a "$OBEOS_BUNDLE_ROOT/opt/daise/." "$root/opt/daise/"
  cp -a "$OBEOS_BUNDLE_ROOT/etc/obeos/." "$root/etc/obeos/"
  cp -a "$OBEOS_BUNDLE_ROOT/etc/systemd/system/." "$root/etc/systemd/system/"
  cp -a "$OBEOS_BUNDLE_ROOT/usr/share/applications/." "$root/usr/share/applications/"
else
  echo "Warning: OBEOS_BUNDLE_ROOT was not available; only boot target repair will be applied." >&2
fi

graphical_target="/lib/systemd/system/graphical.target"
if [ -f "$root/usr/lib/systemd/system/graphical.target" ]; then
  graphical_target="/usr/lib/systemd/system/graphical.target"
fi
ln -sfn "$graphical_target" "$root/etc/systemd/system/default.target"

for wants in \
  "$root/etc/systemd/system/multi-user.target.wants" \
  "$root/etc/systemd/system/graphical.target.wants" \
  "$root/etc/systemd/system/default.target.wants"; do
  rm -f "$wants/obeos-tui.service"
done

sddm_target=""
for candidate in /usr/lib/systemd/system/sddm.service /lib/systemd/system/sddm.service; do
  if [ -f "$root$candidate" ]; then
    sddm_target="$candidate"
    break
  fi
done

if [ -n "$sddm_target" ]; then
  ln -sfn "$sddm_target" "$root/etc/systemd/system/display-manager.service"
  mkdir -p "$root/etc/systemd/system/graphical.target.wants"
  ln -sfn "$sddm_target" "$root/etc/systemd/system/graphical.target.wants/sddm.service"
else
  echo "Warning: sddm.service was not found in the guest. Plasma may need package repair after boot." >&2
fi

if [ -f "$root/etc/systemd/system/obeos-tui.service" ]; then
  sed -i \
    -e 's/^Description=.*/Description=OBEOS Blackcomputer TUI (manual safe mode)/' \
    -e 's/^RestartSec=.*/RestartSec=10/' \
    "$root/etc/systemd/system/obeos-tui.service"
fi

root_uuid="$(blkid -s UUID -o value "$part" 2>/dev/null || true)"
if [ -f "$root/boot/grub/grub.cfg" ] && [ -n "$root_uuid" ]; then
  echo "Fixing GRUB root= to UUID=$root_uuid (WSL device names break Hyper-V boot)"
  sed -i "s|root=/dev/sd[a-z][0-9]*|root=UUID=$root_uuid|g" "$root/boot/grub/grub.cfg"
  sed -i "s|root=UUID=UUID=|root=UUID=|g" "$root/boot/grub/grub.cfg"
fi
if [ -f "$root/etc/default/grub" ]; then
  if grep -q '^GRUB_DISABLE_LINUX_UUID=' "$root/etc/default/grub"; then
    sed -i 's/^GRUB_DISABLE_LINUX_UUID=.*/GRUB_DISABLE_LINUX_UUID=false/' "$root/etc/default/grub"
  else
    echo 'GRUB_DISABLE_LINUX_UUID=false' >> "$root/etc/default/grub"
  fi
fi

if [ -x "$root/opt/obeos/bin/apply-obeos-branding.sh" ] && [ -x "$root/bin/bash" ]; then
  echo "Applying OBEOS branding (GRUB, Plymouth, SDDM, Plasma) via chroot"
  mount --bind /dev "$root/dev" 2>/dev/null || true
  mount -t proc proc "$root/proc" 2>/dev/null || true
  mount -t sysfs sysfs "$root/sys" 2>/dev/null || true
  mount -t devpts devpts "$root/dev/pts" 2>/dev/null || true
  chroot "$root" /bin/bash -lc '/opt/obeos/bin/apply-obeos-branding.sh' || echo "Warning: branding apply in chroot reported errors." >&2
  umount "$root/dev/pts" "$root/sys" "$root/proc" "$root/dev" 2>/dev/null || true
  if [ -n "$root_uuid" ] && [ -f "$root/boot/grub/grub.cfg" ]; then
    sed -i "s|root=/dev/sd[a-z][0-9]*|root=UUID=$root_uuid|g" "$root/boot/grub/grub.cfg"
    sed -i "s|root=UUID=UUID=|root=UUID=|g" "$root/boot/grub/grub.cfg"
  fi
fi

if [ -d "$root/opt/obeos/bin" ]; then
  cat > "$root/opt/obeos/bin/return-to-tui.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat <<'MESSAGE'
OBEOS TUI safe mode is now manual.

The Plasma desktop remains the default home because it keeps mouse, keyboard,
HUD, and the Blackfong Qt workspace usable.

To intentionally start the Blackcomputer TUI on tty1, run:
  sudo systemctl start obeos-tui.service

To return to Plasma after using a TTY, try Ctrl+Alt+F2 or Ctrl+Alt+F7.
MESSAGE
EOF
  chmod 0755 "$root/opt/obeos/bin/return-to-tui.sh"
fi

obeos_uid="$(awk -F: '$1 == "obeos" {print $3}' "$root/etc/passwd" 2>/dev/null || true)"
obeos_gid="$(awk -F: '$1 == "obeos" {print $4}' "$root/etc/passwd" 2>/dev/null || true)"
if [ -n "$obeos_uid" ] && [ -n "$obeos_gid" ]; then
  mkdir -p "$root/opt/blackfong/data" "$root/var/lib/obeos" "$root/etc/obeos"
  chown -R "$obeos_uid:$obeos_gid" "$root/opt/blackfong" "$root/opt/daise" "$root/var/lib/obeos" "$root/etc/obeos" || true
  chmod 2775 "$root/opt/blackfong/data" "$root/var/lib/obeos" "$root/etc/obeos" || true
  chmod -R g+rwX "$root/opt/blackfong/data" "$root/var/lib/obeos" "$root/etc/obeos" || true
fi

sync
umount "$mountpoint"
trap - EXIT
echo "OBEOS desktop-first repair complete."
'@

$savedDrives = @()
if (Get-Command Get-VM -ErrorAction SilentlyContinue) {
    Ensure-ObeosVmRegistered
    Stop-ObeosVmIfNeeded -Name $VmName
    foreach ($drive in Get-VMHardDiskDrive -VMName $VmName -ErrorAction SilentlyContinue) {
        $savedDrives += [pscustomobject]@{
            ControllerType = $drive.ControllerType
            ControllerNumber = $drive.ControllerNumber
            ControllerLocation = $drive.ControllerLocation
            Path = $drive.Path
        }
        Remove-VMHardDiskDrive -VMName $VmName `
            -ControllerType $drive.ControllerType `
            -ControllerNumber $drive.ControllerNumber `
            -ControllerLocation $drive.ControllerLocation `
            -ErrorAction Stop
    }
    if ($savedDrives.Count -eq 0) {
        $savedDrives += [pscustomobject]@{
            ControllerType = "SCSI"
            ControllerNumber = 0
            ControllerLocation = 0
            Path = (Resolve-Path -LiteralPath $Vhdx).Path
        }
    }
}

wsl --shutdown
$BundleRoot = (Resolve-Path -LiteralPath $BundleRoot).Path
$bundleRootWsl = Convert-ToWslLinuxPath -WindowsPath $BundleRoot -DistroName $Distro
$repairScriptPath = Join-Path ([System.IO.Path]::GetTempPath()) ("obeos-vm-repair-" + [guid]::NewGuid().ToString("N") + ".sh")
[System.IO.File]::WriteAllText($repairScriptPath, $repairSh, [System.Text.UTF8Encoding]::new($false))
$repairScriptWsl = Convert-ToWslLinuxPath -WindowsPath $repairScriptPath -DistroName $Distro
wsl --mount --vhd "$Vhdx" --bare
if ($LASTEXITCODE -ne 0) {
    wsl --unmount "$Vhdx" 2>$null | Out-Null
    wsl --mount --vhd "$Vhdx" --bare
}
if ($LASTEXITCODE -ne 0) {
    throw "Failed to mount VHDX through WSL: $Vhdx"
}
try {
    wsl -d $Distro -u root -- env "OBEOS_BUNDLE_ROOT=$bundleRootWsl" sh "$repairScriptWsl"
    if ($LASTEXITCODE -ne 0) {
        throw "WSL guest repair failed with exit code $LASTEXITCODE."
    }
}
finally {
    wsl -d $Distro -u root -- sh -c 'umount /mnt/obeos-rescue >/dev/null 2>&1 || true'
    wsl --unmount "$Vhdx" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        wsl --unmount 2>$null | Out-Null
    }
    if ($repairScriptPath -and (Test-Path -LiteralPath $repairScriptPath)) {
        Remove-Item -LiteralPath $repairScriptPath -Force
    }
    Restore-ObeosVmHardDrives -Name $VmName -DiskPath $Vhdx -SavedDrives $savedDrives
}

if (-not $NoStart) {
    Start-Service vmms -ErrorAction SilentlyContinue
    Ensure-ObeosVmRegistered
    Restore-ObeosVmHardDrives -Name $VmName -DiskPath $Vhdx -SavedDrives @()
    Start-ObeosVmIfNeeded -Name $VmName
    Start-Process "$env:WINDIR\System32\vmconnect.exe" -Verb RunAs -ArgumentList "localhost", $VmName
}

& (Join-Path $PSScriptRoot "fix-obeos-vm-boot.ps1") -VmName $VmName -Vhdx $Vhdx

Write-Host "Done. The VM should now boot to the Kubuntu/Plasma desktop path."
