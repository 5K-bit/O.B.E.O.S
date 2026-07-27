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
if ((Get-VM -Name $VmName -ErrorAction Stop).State.ToString() -ne "Off") {
    Stop-VM -Name $VmName -TurnOff -Force
    Start-Sleep -Seconds 2
}

$sh = @'
#!/bin/sh
set -eu
mp=/mnt/obeos-root
mkdir -p "$mp"
part=""
for candidate in /dev/sdd2 /dev/sda2; do
  if [ -b "$candidate" ] && [ "$(lsblk -no FSTYPE "$candidate" 2>/dev/null | head -n1)" = ext4 ]; then
    part="$candidate"
    mount "$candidate" "$mp"
    break
  fi
done
[ -f "$mp/etc/os-release" ] || { echo "no root"; exit 1; }

for efi in /dev/sdd1 /dev/sda1; do
  if [ -b "$efi" ] && [ "$(lsblk -no FSTYPE "$efi" 2>/dev/null | head -n1)" = vfat ]; then
    mkdir -p "$mp/boot/efi"
    mount "$efi" "$mp/boot/efi" 2>/dev/null || true
    break
  fi
done

echo "Re-applying system branding..."
if [ -x "$mp/opt/obeos/bin/apply-obeos-branding.sh" ]; then
  mount --bind /dev "$mp/dev"
  mount -t proc proc "$mp/proc"
  mount -t sysfs sysfs "$mp/sys"
  mount -t devpts devpts "$mp/dev/pts" 2>/dev/null || true
  chroot "$mp" /bin/bash -lc '/opt/obeos/bin/apply-obeos-branding.sh'
  umount "$mp/dev/pts" "$mp/sys" "$mp/proc" "$mp/dev" 2>/dev/null || true
fi

wallpaper="/usr/share/wallpapers/O.B.E.O.S/contents/images/3840x2160.svg"
for home in "$mp/home/obeos" "$mp/root"; do
  [ -d "$home" ] || continue
  cfg="$home/.config"
  mkdir -p "$cfg"
  cat > "$cfg/kdeglobals" <<'EOF'
[KDE]
LookAndFeelPackage=org.kde.breezedark.desktop

[General]
ColorScheme=OBEOS
Name=OBEOS
EOF
  cat > "$cfg/kscreenlockerrc" <<EOF
[Daemon]
Image=$wallpaper
EOF
  mkdir -p "$cfg/autostart"
  cat > "$cfg/autostart/obeos-plasma-apply.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=OBEOS Plasma Theme
Exec=/opt/obeos/bin/apply-obeos-plasma-session.sh
X-KDE-autostart-after=panel
X-KDE-StartupNotify=false
OnlyShowIn=KDE;
EOF
  uid="$(stat -c '%u' "$home" 2>/dev/null || echo 0)"
  gid="$(stat -c '%g' "$home" 2>/dev/null || echo 0)"
  chown -R "$uid:$gid" "$cfg" 2>/dev/null || true
  echo "Updated desktop config for $home"
done

cat > "$mp/opt/obeos/bin/apply-obeos-plasma-session.sh" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
FLAG="${XDG_CONFIG_HOME:-$HOME/.config}/obeos-plasma-applied"
[ -f "$FLAG" ] && exit 0
if command -v plasma-apply-colorscheme >/dev/null 2>&1; then
  plasma-apply-colorscheme OBEOS || true
fi
WP="/usr/share/wallpapers/O.B.E.O.S/contents/images/3840x2160.svg"
if command -v qdbus >/dev/null 2>&1; then
  qdbus org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript "
    var allDesktops = desktops();
    for (var i = 0; i < allDesktops.length; i++) {
      allDesktops[i].wallpaperPlugin = 'org.kde.image';
      allDesktops[i].currentConfigGroup = Array('Wallpaper', 'org.kde.image', 'General');
      allDesktops[i].writeConfig('Image', 'file://$WP');
    }
  " 2>/dev/null || true
fi
touch "$FLAG"
EOS
chmod 0755 "$mp/opt/obeos/bin/apply-obeos-plasma-session.sh"

root_uuid="$(blkid -s UUID -o value "$part" 2>/dev/null || true)"
if [ -n "$root_uuid" ] && [ -f "$mp/boot/grub/grub.cfg" ]; then
  sed -i "s|root=/dev/sd[a-z][0-9]*|root=UUID=$root_uuid|g" "$mp/boot/grub/grub.cfg"
fi

if [ -x "$mp/bin/bash" ]; then
  mount --bind /dev "$mp/dev"
  mount -t proc proc "$mp/proc"
  mount -t sysfs sysfs "$mp/sys"
  chroot "$mp" /bin/bash -lc '
    if command -v plymouth-set-default-theme >/dev/null 2>&1; then
      plymouth-set-default-theme -R obeos 2>/dev/null || plymouth-set-default-theme obeos 2>/dev/null || true
    fi
    if command -v update-initramfs >/dev/null 2>&1; then
      update-initramfs -u 2>/dev/null || true
    fi
  ' || true
  umount "$mp/sys" "$mp/proc" "$mp/dev" 2>/dev/null || true
fi

umount "$mp/boot/efi" 2>/dev/null || true
umount "$mp"
echo "Desktop branding pushed to obeos user. Reboot the VM to see splash + SDDM + Plasma colors."
'@

$shPath = Join-Path $env:TEMP ("obeos-desktop-" + [guid]::NewGuid().ToString("N") + ".sh")
[System.IO.File]::WriteAllText($shPath, $sh.Replace("`r`n", "`n"), [Text.UTF8Encoding]::new($false))
$shWsl = "/mnt/host/c/" + ($shPath.Substring(2) -replace '\\', '/')

wsl --shutdown | Out-Null
wsl --mount --vhd $Vhdx --bare
if ($LASTEXITCODE -ne 0) { throw "Mount failed. Turn off the VM first." }
try {
    wsl -d docker-desktop -u root -- sh $shWsl
    if ($LASTEXITCODE -ne 0) { throw "Apply failed with exit $LASTEXITCODE" }
}
finally {
    wsl -d docker-desktop -u root -- sh -c "umount /mnt/obeos-root >/dev/null 2>&1 || true"
    wsl --unmount $Vhdx 2>$null | Out-Null
    Remove-Item $shPath -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Done. Start OBEOSv3 and log in as obeos."
Write-Host "You should see: dark OBEOS colors, OBEOS wallpaper, branded SDDM, Plymouth on boot."

if ($Start) {
    Start-VM -Name $VmName
    Start-Process "$env:WINDIR\System32\vmconnect.exe" -ArgumentList "localhost", $VmName
}
