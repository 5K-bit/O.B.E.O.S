# OBEOS-v1 VM Updater

Use this wrapper so VM updates stay boring.

Run from the repo root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\tools\obeos-vm.ps1 update
```

Common actions:

```powershell
.\tools\obeos-vm.ps1 status
.\tools\obeos-vm.ps1 migrate-v3
.\tools\obeos-vm.ps1 update
.\tools\obeos-vm.ps1 start
.\tools\obeos-vm.ps1 stop
.\tools\obeos-vm.ps1 repair
.\tools\obeos-vm.ps1 password
.\tools\obeos-vm.ps1 bundle
```

Notes:

- `update`, `repair`, `start`, `stop`, and `password` need Administrator PowerShell.
- `bundle` only refreshes `data\obeos-remaster` and `data\obeos-remaster.zip`.
- `migrate-v3` renames `OBEOS-v2-clean` to `OBEOSv3`, syncs the latest bundle (including branding), and opens VMConnect.
- `update` syncs the current bundle into the existing VM VHDX, keeps boot desktop-first, and opens VMConnect.
- If Hyper-V loses the VM registration, `update`, `repair`, `start`, and `password` try to re-register the local `.vmcx` automatically.
- After dependency changes, boot the VM and run:

```bash
sudo /opt/obeos/bin/bootstrap-kubuntu-guest.sh
sudo reboot
```

Quick validation inside the VM:

```bash
blackfong home
blackfong desktop status
systemctl get-default
systemctl is-enabled obeos-tui.service
systemctl is-active obeos-tui.service
```
