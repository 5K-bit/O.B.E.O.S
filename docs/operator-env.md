# Operator environment variables

O.B.E.O.S reads tuning knobs from `BLACKFONG_*` environment variables. Defaults are defined in `blackfong_ecosystem/config.py`.

## Ollama and response modes

| Variable | Purpose | Default |
|----------|---------|---------|
| `BLACKFONG_OLLAMA_URL` | Ollama base URL | `http://localhost:11434` |
| `BLACKFONG_OLLAMA_MODEL` | Legacy default model tag | `llama3.2:3b` |
| `BLACKFONG_FAST_MODEL` | Fast chat model | `llama3.2:3b` |
| `BLACKFONG_FAST_MODEL_FALLBACKS` | Comma-separated fast fallbacks | `llama3.2:3b` |
| `BLACKFONG_STANDARD_MODEL` | Standard chat model | same as fast |
| `BLACKFONG_DEEP_MODEL` | Deep reasoning model | `deepseek-r1:8b` |
| `BLACKFONG_CODING_MODEL` | Coding agent model | `deepseek-coder:6.7b` |
| `BLACKFONG_FAST_TIMEOUT` | Fast mode wall timeout (seconds) | `60` |
| `BLACKFONG_STANDARD_TIMEOUT` | Standard mode timeout | `90` |
| `BLACKFONG_DEEP_TIMEOUT` | Deep mode timeout | `180` |
| `BLACKFONG_FAST_NUM_PREDICT` | Max tokens for fast replies | `128` |
| `BLACKFONG_FAST_PROMPT_BUDGET_TOKENS` | Fast context budget | `600` |

Only models that Ollama reports as installed are used; missing tags are skipped quickly instead of timing out.

## HUD

| Variable | Purpose | Default |
|----------|---------|---------|
| `BLACKFONG_HUD_HOST` | Bind address | `127.0.0.1` |
| `BLACKFONG_HUD_PORT` | Port | `8080` |
| `BLACKFONG_HUD_OPEN_BROWSER` | Open browser on launch | `true` |

HUD `/api/chat` defaults to `background: true` so the UI stays responsive while the brain runs.

## TinyProgrammer

| Variable | Purpose | Default |
|----------|---------|---------|
| `BLACKFONG_TINYPROGRAMMER_ENABLED` | Enable companion | `true` |
| `BLACKFONG_TINYPROGRAMMER_URL` | Companion URL | `http://127.0.0.1:5000` |
| `BLACKFONG_TINYPROGRAMMER_AUTO_START` | Launch on boot | `false` |
| `BLACKFONG_TINYPROGRAMMER_AUTO_STREAM` | Require live stream | `true` |

## TUI

| Variable | Purpose | Default |
|----------|---------|---------|
| `BLACKFONG_TUI_HEARTBEAT_INTERVAL` | Status heartbeat (seconds) | `0.6` |
| `BLACKFONG_TUI_STATUS_REFRESH_INTERVAL` | Panel refresh interval | `2.5` |

Press **M** in the TUI to cycle Fast → Standard → Deep response modes.

## Surfaces

| Entry point | Command |
|-------------|---------|
| Desktop workspace | `python desktop_shell.py` |
| Thoughtglass HUD | `python -m blackfong_ecosystem.operational_interfaces` |
| Terminal UI | `python -m blackfong_ecosystem.ui.blackcomputer_tui` |

All three chat surfaces route through `OperatorChatService` and share session memory when configured.

## O.B.E.O.S branding (Linux guest / remaster)

After materializing the deployment bundle:

```bash
blackfong obeos materialize --output data/obeos-remaster
```

On the Kubuntu guest (as root), after bootstrap:

```bash
/opt/obeos/bin/apply-obeos-branding.sh
```

This applies the O.B.E.O.S identity across GRUB, Plymouth, SDDM, Plasma color scheme, lock screen wallpaper, and login issue text. TinyProgrammer autostart remains off unless you set `BLACKFONG_TINYPROGRAMMER_AUTO_START=true`.
