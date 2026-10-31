# Blackfong Workspace: Step 0 Inventory

Audit date: 2026-10-17. Method: static read of cloned repos (depth 50), plus a baseline test run on Linux.

**Evidence levels**
- **[V]** I verified it directly (command output or file read in this session).
- **[A]** Reported by a read-only audit sub-agent with file:line citations. Not independently re-checked.
- **[U]** Unverified. Needs Windows/Blackcomputer or the deployed runtime.

No Windows, live-service or deployed-runtime evidence exists in this audit. Anything that depends on Blackcomputer is **[U]**.

## 1. Canonical repository

| Question | Answer | Evidence |
|---|---|---|
| Where is the PySide6 Workspace? | `5K-bit/O.B.E.O.S`, `desktop_shell.py` (2604 lines) plus `blackfong_ecosystem/` | [V] README "Current Product Truth": canonical operator shell is `Blackfong Workspace` (PySide6) |
| Is `blackfong-mainscreen` the Workspace? | **No.** It is a Next.js / Coinbase OnchainKit "BKFG Protocol" Base miniapp | [V] package.json, README |
| `obeos-cloud` vs O.B.E.O.S | Fork with shared history (merge-base 72297aa, 2026-08-13), ~10 commits ahead each. O.B.E.O.S is architecturally ahead (v0.9/v2 work, manifests, contracts). obeos-cloud is newer by date (2026-10-03) and has Termius/Telegram docs and model time-limit changes | [A] |
| Canonical Sentinel | `5K-bit/Sentinel` (private, newest, CI, superset of Sentinel-By-Blackfong). Port 3 extra tests and `pytest.ini` from Sentinel-By-Blackfong | [A] |
| Branch | `feat/workspace-v0.1` created locally in the O.B.E.O.S clone. **Not pushed** (the clone is read-only) | [V] |

Decision needed: merge the obeos-cloud-only commits into O.B.E.O.S, or keep both. Recommend merging into O.B.E.O.S and retiring the fork.

## 2. Repository inventory

| Repo | What it is | State |
|---|---|---|
| O.B.E.O.S | Monorepo: spine, DAISE, Workspace, HUD, TUI, event store, policy | Active, 35 commits |
| Sentinel | Docker/Kali practice lab with a vendored CLI. Not an OBEOS security service | Active, CI |
| Sentinel-By-Blackfong | Older standalone Sentinel CLI | Superseded |
| Earthglass | FastAPI layer engine plus separate React/Cesium client (`obeos-earth/`) | Active, 26 commits |
| Blackforge v0.3 | CLI scaffolder (`new`, `inspect`, `validate`, `doctor`) | Scaffolder only [V manifest] |
| PYBUS | Python/security learning platform | Docs and manifest only [V] |
| Blackrelay | README and two docs | No code [A] |
| Blackfong-Installer | Media/installer pipeline (`code_warden`, systemd unit) | Active [A] |
| OBEOSX | "Initial commit" 2025-10-22 | Stale |
| obeos-cloud | Fork of O.B.E.O.S, vendors Max-Payne, Night-watch-OS, 3000 | Active |
| blackfong-mainscreen | Unrelated web miniapp | Not part of OBEOS |

Corrections to README assumptions:
- PYBUS is **not** the event backbone.
- Blackforge has no repair, deploy or agent-runtime capability, so Milestone 7 is mostly net-new.
- No "Lilo/Stitch roles" exist in Blackforge. Lilo and Stitch are learning agents inside O.B.E.O.S (`agents/lilo_agent.py`, `stitch_agent.py`) [A]. The README requires these to be "verified if present".
- `Max-Payne`, the declared health aggregator, is an **empty submodule** in O.B.E.O.S [A]. Only `contracts/health_v1.py:aggregate_health` exists.

## 3. Runtime map [A unless noted]

| Service | Bind / port | Auth |
|---|---|---|
| Event API (Flask) | `localhost:8000` | **None** |
| HUD / Legion (`WebDashboard`) | `127.0.0.1:8080` by default. The autostart scripts default to **0.0.0.0:8080**. Docker CMD is 0.0.0.0, published to 127.0.0.1 | Pairing token + cookie, but only ~6 of 112 routes enforce it |
| QuietDrop | 127.0.0.1:8844 | None found |
| EarthGlass | declared 127.0.0.1:8000, which **collides with the Event API** | Presence API: bearer tokens/ACL; core layer API: CORS only |
| TinyProgrammer | 127.0.0.1:5000 | Unknown |

Data stores [A]: `data/events.db` (SQLite WAL), `data/event_queue.db`, `data/chroma/`, `device_registry.json`, `operator_session.json`, `legion_pairing_token.txt`, plus per-project DBs. Note: `data/events.db` in this clone was created by my test run [V], so it does not describe the deployed system.

## 4. Workspace shell vs Decisions 1–13

| # | Decision | Status | Evidence |
|---|---|---|---|
| 1 | Fullscreen shell | **Missing** | [V] no `showFullScreen`/`showMaximized` in desktop_shell.py. `resize(1680,1040)` at :1688 [A]. Manual launch only |
| 2 | Boot sequence | Implemented | `workspace_boot_splash.py`, polls `StartupCoordinator` every 400 ms [A]. Progress = share of subsystems in a terminal state. `_overall_state` reports "ready" even when degraded [A] |
| 3 | Command Center | Partial | Home tab is a cognition graph. Status chips, notifications pane, launchers exist. No terminal, no music [A] |
| 4 | Dark/cyan UI | Partial | Dark QSS exists; accent is teal/gold on the splash, main accent unconfirmed [A] |
| 5 | Dock/detach/layout | Implemented | `workspace_window_manager.py`, QSettings persistence, Ctrl+1..5 presets [A] |
| 6 | Severity notifications | Partial | `level`/`category` stored; UI dumps JSON. No severity routing, colouring or dedup [A] |
| 7 | Resource management | Partial | Duplicate-process handling for Tiny only. All panes built eagerly. 15 s refresh timer [A] |
| 8 | Offline music | **Missing** | [V] no mpv / QMediaPlayer in any .py |
| 9 | Hybrid console | Partial | Registered slash-command console only; no PowerShell passthrough [A] |
| 10 | Windows Terminal / ConPTY | **Missing** | [V] no wt.exe / ConPTY |
| 11 | Permission-gated AI | Partial, weak | Consent levels 1–3 in `policy_engine.py` [A]. UI hard-codes `consent={"approved": True,...}` [V desktop_shell.py:232, 1026, 1056, 1672; ui_commands.py:446, 453, 593] |
| 12 | Approval Center | **Missing as UI** | No UI. `OperatorActionService` logs intents only. Real revalidation exists only in `FileWorkflow.apply` (file_workflow.py:253-300) [A] |
| 13 | Session restore | Implemented (layout, session id) | Persists geometry/state/UUID only; nothing replays commands [A] |

Window close [A]: `closeEvent` (desktop_shell.py:2522) stops the SSE thread, floating panels and the Tiny stream view, and saves layout. It does **not** stop Tiny, Docker stacks or other children. This is compatible with "core services survive GUI exit", but nothing supervises or restarts them. Workspace reads health mostly in-process (`build_workspace_snapshot`), with HTTP for Tiny and an SSE feed from the HUD at a hard-coded `127.0.0.1:8080`.

## 5. Events, storage, recovery vs Decisions 14–39

| Decisions | Status | Evidence |
|---|---|---|
| 14 remote access | Partial | Legion HUD PWA + pairing token; mostly unauthenticated routes [A] |
| 15, 16 | Missing | No capability-scoped remote ops; no offline cache/reconnect contract |
| 17 incremental sync | **Missing** | `/events/query` has no cursor/since-id; metadata filters applied after `limit`; SSE has no replay [A] |
| 17 dedup / IDs | Weak | uuid4 server-side; client IDs ignored, so a retried POST duplicates. `contracts/EventEnvelope` accepts caller IDs but isn't wired to storage [A] |
| 18 unified notification state | Missing | Single `level/category`; no read/dismissed/ack/resolved [A] |
| 19 auto-resolve | Not found | `alert_engine.py` exists, behaviour unverified |
| 20 activity feed + raw inspector | Partial | Event query with correlation_id filter [A] |
| 21 tiered archive | Partial | SQLite events with immutable triggers; blackbox daily capsules with SHA-256 [A] |
| 22 configurable storage | Partial | `config.py` data dir [A], tiers absent |
| 23–28 backups, retention, encryption, restore tests | **Missing** | Only `Night-watch-OS/nightwatch/backup.py` (daily SQLite copy, **obeos-cloud only**, no encryption/retention/restore) [A] |
| 29–32 guided recovery / merge | Missing; `state_reconciliation.py` repairs index/vector drift [A] |
| 33–36 maintenance, updates, restarts | **Missing** | No auto-update. Windows autostart is a logon-triggered Scheduled Task (InteractiveToken); **no Windows service**; Event API has no autostart [A] |
| 37 self-healing | Minimal | Bounded retries in `StartupCoordinator` and `event_queue`; Max-Payne `heal.py` only in obeos-cloud, dry-run default [A] |
| 38, 39 | Missing | Blackforge is a scaffolder; no diff/commit/deploy/rollback UI |

## 6. Baseline tests [V]

Environment: Linux, Python 3.12, `QT_QPA_PLATFORM=offscreen`, `numpy<2`. Command: `pytest tests -q`.
**Result: 516 passed, 23 failed, 32 skipped (118 s).**

| Failures | Count | Assessment |
|---|---|---|
| `test_unified_retrieval_profiles` (DAISE recall/identity phrasing → `local_runtime_truth` instead of `conversation_memory`, `daise` vs `jarvis`) | 17 | Not diagnosed. Likely depend on local data/models or brittle routing |
| `test_daise_file_chat_and_cognition` | 2 | Not diagnosed |
| `test_blackfong_cortex_thoughtglass` (gesture route) | 1 | Not diagnosed |
| `test_knowledge_intel` (librarian "No results" wording) | 1 | Not diagnosed (collections absent in this env) |
| `test_memory_chamber_unified_graph` | 1 | Not diagnosed |
| `test_verification_and_integrations::release_evidence` | 1 | Environmental: Linux path, installer validation False |

Skips: knowledge collections (`spawn`, `security_plus`, `python`, `obeos`) are absent in this environment.

Tooling findings:
1. `pydantic==2.6.4` (pinned) **fails to build on Python 3.13**. Use 3.11/3.12. [V]
2. `chromadb<0.5` plus the default NumPy 2 breaks on import (`np.float_` removed). Needs `numpy<2` pinned. [V]
3. `desktop_shell.py:35` turns the real `ImportError` (here a missing `libEGL.so.1`) into a generic "PySide6 is required" message. Hides diagnosis. [V]
4. `test_release_evidence_writes_json_and_markdown_reports` **overwrites the tracked `docs/release-evidence-latest.md`** (reverted here). Tests should write to a temp dir. [V]
5. Tests create `data/` state inside the repo (gitignored). [V]
6. No Windows test run was possible. Every Windows-specific path is **[U]**.

## 7. Secrets [A]

No live credentials found in OBEOS repos. In `blackfong-mainscreen`, a Coinbase/OnchainKit API key is committed in `README.md`, `QUICK_START.md`, `INTEGRATION_GUIDE.md:80,86` and `INTEGRATION_SUMMARY.md:128`. **Rotate and scrub history.** Sentinel's lab has a documented fixed password (loopback-only).

## 8. Exit criterion

An evidence-backed inventory exists for the repos and the code paths above. Step 0 is **not closed**: live service ports/health, Windows startup behaviour, and the root causes of 22 of the 23 test failures remain unverified.
