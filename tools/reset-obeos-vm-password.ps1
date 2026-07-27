param(
    [string]$VmName = "OBEOS-v1",
    [string]$Vhdx = "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)\data\vm\OBEOS-v1\OBEOS-v1.vhdx",
    [string]$Distro = "Ubuntu-24.04",
    [string]$User = "obeos",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$repairScriptPath = $null

$principal = [Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window."
}

if (-not (Test-Path -LiteralPath $Vhdx)) {
    throw "VHDX not found: $Vhdx"
}

if ($User -notmatch '^[a-z_][a-z0-9_-]*[$]?$') {
    throw "Unsafe Linux username: $User"
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

$first = Read-Host "New password for Linux user '$User'" -AsSecureString
$second = Read-Host "Type it again" -AsSecureString
$bstr1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($first)
$bstr2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($second)
try {
    $plain1 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr1)
    $plain2 = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr2)
}
finally {
    if ($bstr1 -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr1) }
    if ($bstr2 -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr2) }
}

if ($plain1 -ne $plain2) {
    throw "Passwords did not match."
}
if ([string]::IsNullOrWhiteSpace($plain1)) {
    throw "Password cannot be empty."
}

$passwordBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($plain1))
$plain1 = $null
$plain2 = $null

$bash = @'
set -euo pipefail

mountpoint="/mnt/obeos-rescue"
mkdir -p "$mountpoint"
if mountpoint -q "$mountpoint"; then
  umount "$mountpoint"
fi

part=""
for candidate in /dev/disk/by-label/kubuntu_2404 /dev/sdd2; do
  if [[ -e "$candidate" ]] && [[ "$(lsblk -no FSTYPE "$candidate" 2>/dev/null | head -n 1)" == "ext4" ]]; then
    part="$candidate"
    break
  fi
done

if [[ -z "$part" ]]; then
  echo "Could not find the Kubuntu ext4 partition. Current block devices:" >&2
  lsblk -f >&2
  exit 2
fi

mount "$part" "$mountpoint"
trap 'umount "$mountpoint" >/dev/null 2>&1 || true' EXIT

root="$mountpoint"
if [[ ! -f "$root/etc/os-release" ]]; then
  echo "Mounted partition does not look like a Linux root filesystem: $part" >&2
  exit 3
fi

user="${OBEOS_RESET_USER:?missing user}"
password="$(printf '%s' "${OBEOS_PASSWORD_BASE64:?missing password}" | base64 -d)"

if ! grep -q "^${user}:" "$root/etc/passwd"; then
  echo "Creating Linux user $user"
  chroot "$root" /usr/sbin/useradd -m -s /bin/bash "$user"
fi

printf '%s:%s\n' "$user" "$password" | chroot "$root" /usr/sbin/chpasswd
chroot "$root" /usr/sbin/usermod -aG sudo "$user" || true
for group_name in audio video plugdev netdev obeos; do
  if chroot "$root" /usr/bin/getent group "$group_name" >/dev/null 2>&1; then
    chroot "$root" /usr/sbin/usermod -aG "$group_name" "$user" || true
  fi
done

sync
umount "$mountpoint"
trap - EXIT
echo "Password reset complete for Linux user $user."
'@

if (Get-Command Stop-VM -ErrorAction SilentlyContinue) {
    Stop-VM -Name $VmName -TurnOff -Force -ErrorAction SilentlyContinue
}

wsl --shutdown
$repairScriptPath = Join-Path ([System.IO.Path]::GetTempPath()) ("obeos-vm-password-" + [guid]::NewGuid().ToString("N") + ".sh")
[System.IO.File]::WriteAllText($repairScriptPath, $bash, [System.Text.UTF8Encoding]::new($false))
$repairScriptWsl = (wsl -d $Distro -- wslpath -a "$repairScriptPath").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($repairScriptWsl)) {
    throw "Could not translate repair script path for WSL: $repairScriptPath"
}

wsl --mount --vhd "$Vhdx" --bare
if ($LASTEXITCODE -ne 0) {
    wsl --unmount "$Vhdx" 2>$null | Out-Null
    wsl --mount --vhd "$Vhdx" --bare
}
if ($LASTEXITCODE -ne 0) {
    throw "Failed to mount VHDX through WSL: $Vhdx"
}

try {
    wsl -d $Distro -u root -- env "OBEOS_RESET_USER=$User" "OBEOS_PASSWORD_BASE64=$passwordBase64" bash "$repairScriptWsl"
    if ($LASTEXITCODE -ne 0) {
        throw "Password reset failed with exit code $LASTEXITCODE."
    }
}
finally {
    $passwordBase64 = $null
    wsl -d $Distro -u root -- bash -lc 'umount /mnt/obeos-rescue >/dev/null 2>&1 || true'
    wsl --unmount "$Vhdx"
    if ($LASTEXITCODE -ne 0) {
        wsl --unmount
    }
    if ($repairScriptPath -and (Test-Path -LiteralPath $repairScriptPath)) {
        Remove-Item -LiteralPath $repairScriptPath -Force
    }
}

if (-not $NoStart) {
    Start-Service vmms
    Ensure-ObeosVmRegistered
    Start-VM -Name $VmName
    Start-Process "$env:WINDIR\System32\vmconnect.exe" -Verb RunAs -ArgumentList "localhost", $VmName
}

Write-Host "Done. Use the new password for Linux user '$User' in the VM."
