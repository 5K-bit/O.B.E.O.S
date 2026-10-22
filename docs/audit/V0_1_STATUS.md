# v0.1 Status

Branch: `feat/workspace-v0.1`. Evidence level: Linux, offscreen Qt only.
**v0.1 is not verified until M1-M6 in V0_1_ACCEPTANCE.md pass on Blackcomputer (Windows 11).** Everything that can be done and tested without Windows is done.

## Test evidence
`pytest tests -q` (Python 3.12, `QT_QPA_PLATFORM=offscreen`): **595 passed, 0 failed, 33 skipped, 3 xfailed.**
Baseline before this work: 516 passed, 23 failed, 32 skipped.
New: `tests/test_workspace_v01.py`, 60 tests.

## Done
| Item | What changed | Tests |
|---|---|---|
| B01 | `requires-python >=3.11,<3.13`; `numpy<2` pinned | n/a |
| B02 | PySide6 import error now includes the underlying cause | A8 (subprocess with a fake failing PySide6) |
| B03 | CLI test no longer rewrites tracked `docs/release-evidence-latest.md` (it was `test_cli_exposes_desktop_and_obeos_contract_commands`, not the release-evidence test) | verified `git status` clean after full run |
| B04 | 23 failures triaged, see below | full suite |
| B05 | Port-8000 collision (EarthGlass in `obeos_ecosystem.toml` vs Event API) is **detected and shown** in Diagnostics. **Not resolved**: choosing which service moves is an owner decision | port-conflict tests |
| B07/B11 | `workspace_health.py`: online/degraded/offline/unknown/stale with timestamps; non-raising HTTP probe; **Service Health** dock | A2, A3, A4 |
| B10 | Fullscreen by default (`--windowed` / `BLACKFONG_WINDOWED=1` opt out), F11 toggle, Ctrl+Q | A1 |
| B13 | `terminal_launcher.py`: detached `wt.exe` (PowerShell fallback), argument list, no shell; Ctrl+Shift+T and button | A6 |
| B14 | `workspace_feed.py`: read-only feed with severity, time, id, unread marker, truncation notice, and a **Raw Detail** toggle | A7 |
| B15 | `workspace_diagnostics.py` + **Diagnostics** button: versions, Event API URL, last health, port conflicts | diagnostics tests |

## B04 triage of the original 23 failures
| Count | Cause | Resolution |
|---|---|---|
| 17 | DAISE identity matcher claimed any "what/who ... you ... are/really" question (e.g. "what are you still carrying forward"), preempting memory recall; "work" as a noun triggered the memory-system explainer | **Code fix** in `daise/core.py` (`_IDENTITY_EXCLUDED_TOKENS`, narrower `explanation_shape`). All 264 tests in `test_unified_retrieval_profiles.py` pass |
| 1 | Release-evidence test needs the external installer checkout at a machine-specific path | Skipped with reason when absent |
| 3 | Tests lagging intentional changes: surface renamed CORTEX to THOUGHTGLASS; default agent is `daise`; librarian message reworded | Test expectations updated |
| 3 | Unclear whether code or test is right | `xfail(strict=False)` with reasons, **need owner decision**: (1) chat file proposal now deferred pending consent, (2) gesture select of node `tinyprogrammer`, (3) no `memory_retrieval` event for a request routed to singular DAISE |

The DAISE matcher change alters runtime behavior; it is covered by the 264-test file but should be reviewed by the owner.

## Not done
| Item | State |
|---|---|
| B12 | Boot splash left as is: it already polls the coordinator and offers "continue in degraded mode" after 30 s. No new test (A5) |
| Mark All Read | Existing button in the Notifications pane writes read-state events. Left unchanged; the feed itself is read-only |
| M1-M6 | Require Windows |
| Port 8000 | Collision reported, not resolved |

## Known limitations
- HUD (127.0.0.1:8080) is not probed: its health route is unconfirmed.
- Probes run in the refresh worker with a 2 s timeout each.
- Offscreen Qt cannot prove real fullscreen, `wt.exe` behavior, or detachment on Windows.
- Risk register items R1 to R4 (hard-coded consent, unauthenticated HUD/Event API routes, substring policy matching) are untouched; v0.1 adds no executing actions.
