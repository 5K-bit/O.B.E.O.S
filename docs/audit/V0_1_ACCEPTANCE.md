# v0.1 Acceptance (Milestone 1)

Scope rule: v0.1 is read-only plus launching. It adds **no new executing actions** and does not widen authority (risks R1–R4).

## Automated (run in CI, Linux, `QT_QPA_PLATFORM=offscreen`)

| ID | Test | Decision |
|---|---|---|
| A1 | Workspace constructs offscreen and enters fullscreen on a launch flag; `closeEvent` leaves child processes running | 1 |
| A2 | `StartupCoordinator._overall_state` returns degraded/offline, not "ready", when any required subsystem is degraded/offline/unknown (regression for R5) | Honest telemetry |
| A3 | Health mapping: timeout → `unknown` or `offline`, malformed → `unknown`, stale (older than threshold) → `stale`, never `online` | 3 |
| A4 | Every service tile has a state label **and** a timestamp | 3 |
| A5 | Boot splash dismisses while a required subsystem is still checking; it does not block service startup | 2 |
| A6 | Terminal launcher builds an argument list (no `shell=True`), and quoting-injection inputs are inert | 10 |
| A7 | Feed is read-only: no handler in the feed can call an executing command | 6, 20 |
| A8 | `ImportError` for PySide6 preserves the underlying cause (R17) | diagnostics |
| A9 | Existing suite baseline: 516 pass / 32 skip; no new failures vs the recorded 23 | DoD |

## Manual on Blackcomputer (Windows 11), results go in the ledger

| ID | Check |
|---|---|
| M1 | Launch from shortcut: window goes fullscreen; Esc/close works |
| M2 | Tiles show real states for each service actually running, and `offline` for a service you stop |
| M3 | Stop the Event API: tile changes within one refresh interval and shows a timestamp; it never stays green |
| M4 | "Open terminal" starts a separate Windows Terminal; closing Workspace leaves it open |
| M5 | Close Workspace: Event API/HUD keep running (check Task Manager/ports) |
| M6 | Resource: idle CPU/RAM with Workspace open is recorded and acceptable on Blackcomputer's hardware |

v0.1 stays "Not verified" until M1–M6 are recorded.
