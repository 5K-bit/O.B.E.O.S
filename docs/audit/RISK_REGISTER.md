# Risk Register (Step 0)

Severity: H = could cause data loss, unauthorized action or a false "healthy" display. Evidence tags as in STEP0_INVENTORY.md.

| ID | Risk | Sev | Decisions | Evidence | Mitigation |
|---|---|---|---|---|---|
| R1 | Consent is hard-coded `approved: True` in UI and slash handlers, so "approval" is a click or a typed command. `/docker up` and `/desktop` run with `execute=True` and no consent argument | H | 11, 12 | [V] desktop_shell.py:232,1026,1056,1672; ui_commands.py:446,453,593 | Build the Approval Center before adding any new action (Milestone 2). v0.1 must add **no** new executing actions |
| R2 | Several HUD routes are unauthenticated, including `/api/files/*` (apply/rollback), `/api/admin/reset`, `/api/chat`. SocketIO `cors_allowed_origins="*"`. Autostart scripts default to `0.0.0.0` | H | 14, 15 | [A] operational_interfaces.py:265, 1650-1821, 3306-3340; tools/start-legion-hub.ps1:2 | Default-deny auth middleware; bind 127.0.0.1 unless Tailscale interface configured |
| R3 | Event API has no auth and accepts publish/register from any local process | H | 11, 14 | [A] event_api.py:274 | Token on write routes before remote clients exist |
| R4 | Policy engine matches keywords by substring (`in lower_text`), so it is bypassable and over-matches | H | 11 | [A] policy_engine.py:416 | Replace with structured action IDs, not text matching |
| R5 | `_overall_state` returns "ready" once nothing is warming, even if subsystems are degraded/offline | H | Honest telemetry | [A] startup_coordinator.py:368-381 | Fix before v0.1 displays state; add test |
| R6 | Event publish is not idempotent (server-side uuid4; client IDs ignored). Retries create duplicates; no cursor sync | M | 17 | [A] events.py:270, event_api.py:108-151 | Add client idempotency key and `since_id`/cursor |
| R7 | `/events/query` applies metadata filters **after** `limit`, so results can be silently incomplete | M | 17, 20 | [A] event_api.py:108-151 | Push filters into SQL |
| R8 | No backup, restore, encryption, retention or restore test. Only an unencrypted daily SQLite copy in the obeos-cloud fork | H | 23–30 | [A] | Milestone 4. Until then the Workspace must not imply recoverability |
| R9 | Windows autostart is logon-triggered and needs an interactive token; no Windows service; Event API has no autostart | H | 36, boundary rule | [A] tools/legion-hub-task.xml; install-legion-autostart.ps1 | Service/supervisor design in Milestone 6 |
| R10 | GUI close does not stop children, but nothing restarts them. Survives exit, yet orphaned after crash | M | 7 | [A] desktop_shell.py:2522 | Document; supervise in Milestone 6 |
| R11 | Port 8000 declared for both Event API and EarthGlass | M | 4 | [A] obeos_ecosystem.toml; event_api.py:274 | Assign unique ports in service registry |
| R12 | Service registry is an in-memory dict, lost on restart | M | 7 | [A] service_registry.py:81-95 | Derive from manifests/toml at start |
| R13 | Max-Payne (declared health aggregator) is an empty submodule in O.B.E.O.S; "wire MaxPayne" is unchecked | M | 3, honest telemetry | [A] .gitmodules; docs/obeos-integration-stabilization-phase.md:25 | v0.1 uses `contracts/health_v1.aggregate_health` directly |
| R14 | obeos-cloud fork diverged (~10 commits each) | M | all | [A] | Merge cloud-only commits into O.B.E.O.S |
| R15 | Unpinnable dependency set: pydantic pin fails on Py3.13; chromadb vs NumPy 2 | M | DoD | [V] | Constrain Python to 3.11–3.12 and add `numpy<2` |
| R16 | Tests mutate tracked docs and repo `data/` | L | DoD | [V] | Redirect to tmp dir |
| R17 | Real ImportError hidden behind a generic PySide6 message | L | diagnostics | [V] desktop_shell.py:35 | Chain the exception |
| R18 | Committed Coinbase/OnchainKit API key in blackfong-mainscreen docs | M | Security | [A] | Rotate; scrub |
| R19 | Agent token falls back to Legion pairing token (coupling) | M | 11 | [A] device_registry.py:657-658 | Require distinct token |
| R20 | 22 of 23 test failures undiagnosed; may hide regressions in DAISE routing | M | DoD | [V] | Triage next |
| R21 | All Windows-specific behaviour untested from this environment | H | 1, 10, 36 | [U] | Manual checklist on Blackcomputer |
