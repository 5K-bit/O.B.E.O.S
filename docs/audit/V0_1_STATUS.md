# v0.1 Status

Branch: `feat/workspace-v0.1`. Evidence level: Linux, offscreen Qt only. **v0.1 is not verified until M1-M6 in V0_1_ACCEPTANCE.md pass on Blackcomputer.**

## Test evidence
`pytest tests -q`: 559 passed, 23 failed, 32 skipped. Baseline was 516 / 23 / 32. The 23 failures are the same set as the baseline (no new failures, none fixed).
New: `tests/test_workspace_v01.py`, 43 tests.

## Done
| Item | What changed | Tests |
|---|---|---|
| B01 | `requires-python >=3.11,<3.13`; `numpy<2` pinned | n/a |
| B02 | PySide6 import error now includes the underlying cause | n/a |
| B07/B11 | `blackfong_ecosystem/workspace_health.py`: states online/degraded/offline/unknown/stale, timestamps, staleness, non-raising HTTP probe. Wired into `build_workspace_snapshot` and a new **Service Health** dock | A2, A3, A4 |
| B10 | Fullscreen by default; `--windowed` or `BLACKFONG_WINDOWED=1` opts out; F11 toggles; Ctrl+Q quits | A1 (partial) |
| B13 | `blackfong_ecosystem/terminal_launcher.py`: `wt.exe` (PowerShell fallback), argument list, detached, no shell; Ctrl+Shift+T and an "Open Terminal" button | A6 |

## Not done
| Item | State |
|---|---|
| B03 | Release-evidence test still rewrites a tracked doc |
| B04 | 23 baseline failures not triaged |
| B05 | Port 8000 collision and registry-from-toml not addressed; only the Event API is probed |
| B12 | Splash already polls the coordinator and does not block startup; not changed |
| B14 | Read-only feed: existing Notifications pane unchanged (raw JSON), no truncation notice |
| B15 | No dedicated diagnostics view beyond the Service Health dock and existing System pane |
| A7, A8 | Feed-read-only test and ImportError-cause test not written |
| M1-M6 | Require Windows |

## Known limitations
- HUD (127.0.0.1:8080) is not probed: its health route is unconfirmed, and the registry lists only confirmed endpoints.
- Probes run in the refresh worker thread with a 2 s timeout each, so a hung service delays the refresh by at most that long.
- Offscreen Qt cannot prove real fullscreen on Windows.
