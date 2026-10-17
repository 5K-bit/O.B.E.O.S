# Dependency Map (Step 0)

Evidence tags as in STEP0_INVENTORY.md. Arrows mean "depends on / reads from".

```
Workspace (desktop_shell.py, PySide6)
  ├─ in-process ─> awareness_service, StartupCoordinator, integration registry, EventStore (SQLite)   [A]
  ├─ HTTP ───────> TinyProgrammer 127.0.0.1:5000 (/api/status)                                       [A]
  ├─ HTTP ───────> Ollama /api/chat (backend warm-up)                                                [A]
  ├─ SSE ────────> HUD 127.0.0.1:8080 /api/live/stream (hard-coded, optional)                        [A]
  └─ Popen ──────> Tiny, Docker stacks, external integrations (arg lists, fixed whitelist)           [A]

HUD / Legion (WebDashboard, Flask-SocketIO) ─> same EventStore, UIEventBus, device_registry
Event API (Flask :8000) ─> EventStore (data/events.db, WAL), event_queue.db
EventStore ─> optional Chroma VectorStore (data/chroma)
DAISE (daise/core.py) ─> policy_engine, file_workflow, memory chambers, model_routing -> Ollama
Spine adapters ─> Jarvis, Lilo (lilo.db), Stitch, SecurityPlus (sqlite), 3000, TinyProgrammer
EarthGlass (FastAPI) ─> HTTP only; Cesium client is a separate TS app; NOT on the event backbone
Sentinel (/home/user/sentinel) ─> standalone; OBEOS only has sentinel.py (detection events + Pi agent)
Blackforge ─> manifest/scaffold CLI; no runtime link to OBEOS services
Max-Payne ─> declared aggregator; empty submodule in O.B.E.O.S, vendored only in obeos-cloud
```

## Build-order dependencies for v0.1

1. Health contract (`contracts/health_v1.py`, exists [A]) → service registry config → Workspace tiles.
2. Fix `_overall_state` before any UI shows "ready" (R5).
3. Service registry must come from manifests/`obeos_ecosystem.toml`, not the in-memory dict (R12), and ports must be de-conflicted first (R11).
4. Fullscreen and terminal launcher are independent of the services and can be built in parallel.
5. The feed needs the Event API query fix (R7) to be trustworthy; v0.1 can show a "may be truncated" notice until then.

## Cross-repo blockers

| Needed for | Blocker |
|---|---|
| Decision 8 (music) | Nothing exists; choose engine (mpv candidate) |
| Decisions 12, 15 | Approval Center must precede any remote or executing feature |
| Decisions 23–32 | A second storage destination must be provisioned by the operator |
| Decisions 36, 37 | Windows service/supervisor design decision needed (Task Scheduler vs service wrapper) |
| Decisions 37–39 | Blackforge needs an agent/repair runtime; today it is a scaffolder |
