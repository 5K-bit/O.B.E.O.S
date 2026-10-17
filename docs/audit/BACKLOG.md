# Implementation Backlog

Order = dependency order. `D#` = architecture Decision. Risk IDs refer to RISK_REGISTER.md.

## Milestone 0: contracts and hygiene

| # | Item | D | Risk |
|---|---|---|---|
| B01 | Pin Python 3.11–3.12 and `numpy<2`; document install | DoD | R15 |
| B02 | Chain the underlying `ImportError` in `desktop_shell.py:35` | diag | R17 |
| B03 | Redirect release-evidence test and test `data/` to a temp dir | DoD | R16 |
| B04 | Triage the 23 failing tests: fix, mark env-dependent, or quarantine with reason | DoD | R20 |
| B05 | De-conflict ports (EarthGlass vs Event API); one canonical service registry from `obeos_ecosystem.toml` | 7 | R11, R12 |
| B06 | Fix `_overall_state` so degraded/offline never reports "ready" | honest telemetry | R5 |
| B07 | Define Workspace health result (`online/degraded/offline/unknown/stale` + timestamp) over `contracts/health_v1.py` | 3 | R13 |
| B08 | Merge obeos-cloud-only commits into O.B.E.O.S | all | R14 |
| B09 | Rotate and scrub the key in blackfong-mainscreen docs | security | R18 |

## Milestone 1: v0.1

| # | Item | D |
|---|---|---|
| B10 | Fullscreen launch (flag + default), clean exit | 1 |
| B11 | Service tiles with state + timestamp from B07 | 3 |
| B12 | Boot splash progress tied to real checks; non-blocking | 2 |
| B13 | "Open terminal" via `wt.exe` as an arg-list subprocess, detached | 10 (phase 1) |
| B14 | Read-only feed with raw-detail links; truncation notice until B20 | 6, 20 |
| B15 | Diagnostics view (versions, ports, last check results) | 3 |
| B16 | Automated tests A1–A9; manual checklist M1–M6 on Blackcomputer | DoD |

## Milestone 2 and later (not started; ordered by blocking risk)

| # | Item | D | Risk |
|---|---|---|---|
| B17 | Replace hard-coded consent with a real Approval Center and structured action IDs | 11, 12 | R1, R4 |
| B18 | Default-deny auth on HUD routes; restrict bind; remove `cors_allowed_origins="*"` | 14, 15 | R2 |
| B19 | Token auth on Event API write routes; distinct Sentinel agent token | 11, 14 | R3, R19 |
| B20 | Event idempotency key, `since_id` cursor, SQL-side filters | 17 | R6, R7 |
| B21 | Notification states (read/dismissed/ack/resolved) and severity routing | 6, 18 | |
| B22 | Offline music service | 8 | |
| B23 | Lazy panel loading; resource gates | 7 | R10 |
| B24 | Embedded ConPTY PowerShell | 10 | |
| B25 | Session restore audit test (never replays) | 13 | |
| B26 | Backup, encryption, retention, restore-test harness | 23–28 | R8 |
| B27 | Windows service/supervisor, boot without login | 36, 37 | R9 |
| B28 | Maintenance/update intake | 33–35 | |
| B29 | Blackforge agent/repair runtime, Engineering Control Center | 37–39 | |
