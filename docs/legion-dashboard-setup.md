# Legion O.B.E.O.S Dashboard Setup

## What This Adds

The Legion dashboard is a local-only fullscreen PWA served by the Blackcomputer HUD.

It shows:

- Blackcomputer health, diagnostics, events, notifications, and D.A.I.S.E quick chat
- Sentinel Raspberry Pi 5 health, temperature, network, disk, RAM, and sensor readiness
- gated Sentinel sensor start/stop controls

It does not stream screens or remote desktops.

## 1. Start Blackcomputer Hub

From the O.B.E.O.S project root on Blackcomputer:

```powershell
.\tools\start-legion-hub.ps1
```

This prints the local pairing token and starts the HUD on:

```text
http://<blackcomputer-lan-address>:8080/legion
```

If you already know the LAN address, open that address from the Legion. If local name resolution works on your network, try:

```text
http://blackcomputer.local:8080/legion
http://blackcomputer:8080/legion
```

If the Legion cannot reach the page but Blackcomputer can open it locally, allow the HUD through Windows Firewall from Administrator PowerShell:

```powershell
New-NetFirewallRule `
  -DisplayName "O.B.E.O.S Legion HUD 8080" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 8080 `
  -Profile Private
```

You can also start manually:

```powershell
$env:BLACKFONG_HUD_HOST="0.0.0.0"
$env:BLACKFONG_HUD_PORT="8080"
python -m blackfong_ecosystem.cli hud --host 0.0.0.0 --port 8080 --no-open-browser
```

## 2. Pair The Legion

Open `/legion` from the Legion browser and enter the pairing token shown by:

```powershell
python -m blackfong_ecosystem.cli legion token
```

After pairing, the Legion stores a local session cookie.

## 3. Add SteamOS Gaming Mode Shortcut

On the Legion in SteamOS Desktop Mode:

1. Open Steam.
2. Add a non-Steam game.
3. Choose the browser you want to use.
4. Set the launch target to the browser executable.
5. Add a fullscreen/kiosk URL argument for your Blackcomputer hub, for example:

```text
--kiosk http://blackcomputer.local:8080/legion
```

If that browser does not support `--kiosk`, use its fullscreen app/window option and point it at the same URL.

## 4. Start Sentinel Agent On Raspberry Pi 5

On Sentinel, install the project requirements or at minimum `httpx` and `psutil`.

Set the token printed by `blackfong legion token`:

```bash
export BLACKFONG_SENTINEL_AGENT_TOKEN="paste-token-here"
export BLACKFONG_HUB_URL="http://blackcomputer.local:8080"
```

Run:

```bash
python -m blackfong_ecosystem.cli sentinel agent --hub "$BLACKFONG_HUB_URL" --token "$BLACKFONG_SENTINEL_AGENT_TOKEN"
```

To test one report and exit:

```bash
python -m blackfong_ecosystem.cli sentinel agent --hub "$BLACKFONG_HUB_URL" --token "$BLACKFONG_SENTINEL_AGENT_TOKEN" --once
```

## 5. Optional Autostart

Templates are included but not installed automatically.

Install Blackcomputer login autostart:

```powershell
powershell -ExecutionPolicy Bypass -File ".\tools\install-legion-autostart.ps1"
```

The installer tries Task Scheduler first. If Windows denies that, it installs a current-user Startup launcher instead:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Start OBEOS Legion HUD.cmd
```

Remove Blackcomputer login autostart:

```powershell
powershell -ExecutionPolicy Bypass -File ".\tools\uninstall-legion-autostart.ps1"
```

Blackcomputer Windows Task Scheduler template:

```text
tools/legion-hub-task.xml
```

Edit the placeholder paths before importing it into Task Scheduler.

Sentinel systemd template:

```text
tools/blackfong-sentinel.service
```

Edit `WorkingDirectory`, `BLACKFONG_HUB_URL`, and `BLACKFONG_SENTINEL_AGENT_TOKEN`, then install on the Pi:

```bash
sudo cp tools/blackfong-sentinel.service /etc/systemd/system/blackfong-sentinel.service
sudo systemctl daemon-reload
sudo systemctl enable --now blackfong-sentinel.service
```

## Useful Commands

```powershell
python -m blackfong_ecosystem.cli legion status
python -m blackfong_ecosystem.cli legion paired
python -m blackfong_ecosystem.cli sentinel status
```

On the Pi:

```bash
python -m blackfong_ecosystem.cli sentinel status --local
```
