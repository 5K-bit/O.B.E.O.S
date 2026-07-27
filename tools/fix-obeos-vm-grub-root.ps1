param(
    [string]$VmName = "OBEOSv3",
    [string]$Vhdx = "C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong ecosystem operating system)\data\vm\OBEOSv3\OBEOSv3.vhdx",
    [string]$RootUuid = "27e30f51-bdab-4747-8925-764d71d23d1a",
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

$repairDir = Split-Path -Parent $PSScriptRoot
$fixBoot = Join-Path $PSScriptRoot "fix-obeos-vm-boot.ps1"
if (Test-Path -LiteralPath $fixBoot) {
    & $fixBoot -VmName $VmName -Vhdx $Vhdx
}

$sh = @"
#!/bin/sh
set -eu
ROOT_UUID='$RootUuid'
mp=/mnt/obeos-root
mkdir -p "`$mp"
part=""
for candidate in /dev/sdd2 /dev/sde2 /dev/sda2; do
  if [ -b "`$candidate" ] && [ "`$(lsblk -no FSTYPE "`$candidate" 2>/dev/null | head -n1)" = ext4 ]; then
    part="`$candidate"
    break
  fi
done
if [ -z "`$part" ]; then
  echo "ERROR: ext4 root not found" >&2
  lsblk -f >&2
  exit 1
fi
mount "`$part" "`$mp"
efipart=""
for efi in /dev/sdd1 /dev/sda1; do
  if [ -b "`$efi" ] && [ "`$(lsblk -no FSTYPE "`$efi" 2>/dev/null | head -n1)" = vfat ]; then
    efipart="`$efi"
    break
  fi
done
mkdir -p "`$mp/boot/efi"
if [ -n "`$efipart" ]; then
  mount "`$efipart" "`$mp/boot/efi" || true
fi

grub="`$mp/boot/grub/grub.cfg"
if [ -f "`$grub" ]; then
  sed -i "s|root=/dev/sd[a-z][0-9]*|root=UUID=`$ROOT_UUID|g" "`$grub"
  sed -i "s|root=UUID=UUID=|root=UUID=|g" "`$grub"
  echo "Patched `$grub"
  grep -E 'linux.*root=' "`$grub" | head -3
fi

def="`$mp/etc/default/grub"
if [ -f "`$def" ]; then
  if grep -q '^GRUB_DISABLE_LINUX_UUID=' "`$def"; then
    sed -i 's/^GRUB_DISABLE_LINUX_UUID=.*/GRUB_DISABLE_LINUX_UUID=false/' "`$def"
  else
    echo 'GRUB_DISABLE_LINUX_UUID=false' >> "`$def"
  fi
fi

# Do not run update-grub from WSL: it writes root=/dev/sdd2 and breaks Hyper-V boot.

umount "`$mp/boot/efi" 2>/dev/null || true
umount "`$mp"
echo "GRUB root UUID fix complete."
"@

$shPath = Join-Path $env:TEMP ("obeos-grub-fix-" + [guid]::NewGuid().ToString("N") + ".sh")
[System.IO.File]::WriteAllText($shPath, $sh.Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
$shWsl = "/mnt/host/c/" + ($shPath.Substring(2) -replace '\\', '/')

$distro = "docker-desktop"
wsl -d $distro -u root -- sh -c "exit 0" 2>$null
if ($LASTEXITCODE -ne 0) { throw "WSL distro '$distro' not available." }

wsl --shutdown | Out-Null
wsl --mount --vhd $Vhdx --bare
if ($LASTEXITCODE -ne 0) { throw "Could not mount VHDX (is the VM still running?): $Vhdx" }
try {
    wsl -d $distro -u root -- sh $shWsl
    if ($LASTEXITCODE -ne 0) { throw "GRUB fix script failed with exit code $LASTEXITCODE." }
}
finally {
    wsl -d $distro -u root -- sh -c "umount /mnt/obeos-root >/dev/null 2>&1 || true"
    wsl --unmount $Vhdx 2>$null | Out-Null
    Remove-Item -LiteralPath $shPath -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Fixed GRUB: kernel now boots with root=UUID=$RootUuid (not /dev/sdd2 from WSL)."
Write-Host "Start the VM and you should get Kubuntu/Plasma, not BusyBox."

if ($Start) {
    Start-VM -Name $VmName
    Start-Process "$env:WINDIR\System32\vmconnect.exe" -ArgumentList "localhost", $VmName
}
