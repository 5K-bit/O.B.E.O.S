# Next patch — outstanding fixes

Updated 2026-09-17 (third session the same day), on
`agent/integration-stabilization-v0.9`. The previous snapshot's "next thing
to pick up" is resolved, and resolving it exposed a contract conflict that
this session settled. P0/P1/P1b/P7 stay **[RESOLVED]** as recorded before and
are summarised only briefly here; the sections that changed are P1c (new),
P4 (now mostly done), P8 (new) and P9 (new).

**Note on this file**: it is untracked (`git status` shows
`?? docs/next-patch-readme.md`). That is exactly why the previous snapshot
listed a failure a later commit had already fixed — the doc was written
before `28f46aa` landed and never committed alongside it. Consider
`git add`ing it so the handoff travels with the code.

**Interpreter reminder** (unchanged, still true): bare `python`/`pip` on this
machine resolve to the system-wide
`C:\Users\New User\AppData\Local\Programs\Python\Python311\python.exe`, **not**
this project's `.venv`. Always invoke `./.venv/Scripts/python.exe`.

**Trust only a real summary line.** This session had a background run report
"completed, exit code 0" to the harness while its log ended mid-test with
`EXIT_CODE=127` and no `==== N failed, N passed ====` line — it had died at
397 of ~603 tests. Always confirm the summary line before believing a run.

**Latest `tests/ -m quick` run** (after all fixes in this doc):
**QUICKRUNRESULT**.

Reproduce with:

```bash
./.venv/Scripts/python.exe -m pytest tests/ -m quick -v > pytest-run.log 2>&1; echo "EXIT_CODE=$?" >> pytest-run.log
```

**Still outstanding from the previous snapshot**: a real unrestricted
`pytest -v` (covering `tests/` *and* `blackfong_ecosystem/`, per
`pyproject.toml`'s `testpaths`) has still not been run. Every number in this
doc is `tests/ -m quick` scope and is not comparable to the older 698/18/4
figures.

## P0 — numpy/chromadb environment drift **[RESOLVED, was environment not code]**

Not a code bug. `.venv` has the correctly pinned `numpy==1.26.4`; the
`np.float_` failure only reproduces against the system-wide Python.

## P1 — DAISE grounded-truth response path **[RESOLVED]**

## P1b — jarvis_agent.py fallback-response fields **[RESOLVED]**

Three bugs in the backend-error return path: `error` never set, `confidence`
not forced to `0.0`, and `provider` reporting the *fallback* backend instead
of the primary one that failed. See P1c — bug #3 was only half-fixed.

## P1c — fallback `provider` contract **[RESOLVED this session]**

The previous snapshot named
`test_broad_grounded_conversation_still_drives_fallback_when_backend_misses`
as the next thing to pick up. It now passes — commit `28f46aa` fixed it.
Instead the quick suite showed **6 failures** in
`tests/test_agent_model_and_memory_profiles.py` (the file the snapshot
recorded as 44/44 green), all `test_timeout_fallback_*`.

Root cause was **not** a bug but a contract conflict between two test files:

- `tests/test_agent_model_and_memory_profiles.py` (uncommitted working-tree
  rewrite) asserts, 12 times, that "failed inference must not become a
  fabricated conversational response": `error` set, `confidence == 0.0`, and
  `provider != "heuristic"`.
- `tests/test_unified_retrieval_profiles.py` (clean, committed) asserted the
  exact opposite for the same code path: `provider == "heuristic"`.

Both exercise `_bounded_context_fallback_response`, which hardcoded
`provider="heuristic"`. P1b's bug #3 fix had only ever been applied to the
*generic* fallback path (guarded by `generic_fallback_used`), never to the
bounded-context path. The trade was verified directly: making `provider`
honest fixed all 6 and broke exactly that one test (264 passed / 1 failed).

**Resolved in favour of the honest provider**, matching P1b bug #3's
documented intent:

1. `agents/jarvis_agent.py` — `_bounded_context_fallback_response` takes a
   new `primary_provider` argument and reports the backend that actually
   failed. The fallback's own identity moved into metadata as
   `fallback_provider` / `fallback_model` (`bounded_context_fallback` and
   `response_path` were already there). Both call sites pass
   `primary_provider=getattr(chat_backend, "provider_name", None)`.
2. `daise/core.py` — `_grounded_conversation_override_for_generic_report`
   gated its override on `provider in {"heuristic", ""}`, which made the
   provider string **load-bearing for routing, not just display**. Once the
   provider became honest that gate would have silently stopped firing. It
   now keys on the explicit `fallback_mode` marker and falls back to the
   legacy provider/model name check only for reports carrying no fallback
   metadata, which keeps every stub-built report in the tests working.
3. `daise/reasoning_pipeline.py` — the `SimpleNamespace` it builds for that
   gate carried only provider/model/response, so the marker never reached
   it. It now passes `backend_metadata` through, and the gate reads it via
   `getattr(...)` so other duck-typed callers cannot raise `AttributeError`.
4. `tests/test_unified_retrieval_profiles.py` — the single stale assertion
   now expects `provider == "local"` (the backend that failed) plus
   `backend_metadata["fallback_provider"] == "heuristic"` and
   `response_path == "bounded_context_fallback"`.

Gate behaviour after the change, verified case by case:

| report | provider | admitted? |
| --- | --- | --- |
| legacy stub report | `heuristic` | yes (backward compatible) |
| honest bounded fallback | `local` | yes (**this is the fix**) |
| honest preemptive fallback | `ollama` | yes |
| real live-model answer | `ollama` | no (correctly blocked) |

**Note for whoever touches this next**: that gate is why a fallback answer's
`provider` cannot be changed casually. If you re-source or rename it again,
change `daise/core.py`'s override and the `fallback_mode` marker together.
The previous snapshot's warning still applies too — the confidence check must
test `backend_error` *before* `provider == "ollama"`.

## P2 — Follow-ups from the ApprovalGrants / MaxPayne wiring (unchanged, still open)

- No automated test for the `blackfong maxpayne serve` CLI command itself.
  Worth a CliRunner test that stubs uvicorn or hits a random port.
- `Max-Payne/docs/blackfong-authority.md` still reads as if no OBEOS-side
  caller exists — it should mention `blackfong maxpayne serve`.
- `obeos_ecosystem.toml`'s `maxpayne` `notes` field could mention the new
  serve path (its opt-in `launch_policy` is still correct as-is).

## P3 — Follow-ups from the Ollama → llama.cpp migration (low priority, unchanged)

- No UI status chip for llama.cpp readiness in the boot splash.
- No `llama-swap` equivalent if Ollama is ever fully retired.
- No log rotation for `data/logs/llamacpp-server.log`.

## P4 — Dependency hygiene **[pydantic reconciled; lock regeneration still blocked]**

The previous snapshot said the pin lived in two places. It lives in **five**
declared places plus the generated locks, and two of those are *generated*:

| file | role | state |
| --- | --- | --- |
| `pyproject.toml` | **source of truth** | **fixed → `pydantic>=2.10,<3`** |
| `requirements.txt` | hand-maintained | already `pydantic>=2.10,<3` |
| `requirements.docker-hud.txt` | tracked; Docker HUD deploy | **fixed → `pydantic>=2.10,<3`** |
| `requirements/desktop.in` | generated from pyproject | **regenerated** |
| `artifacts/tested-constraints.txt` | generated from the live venv | untouched — see P9 |
| `requirements/*.lock` (×2) | compiled, with hashes | still `2.6.4` — blocked on P9 |

`requirements.docker-hud.txt` was not in the previous snapshot's inventory
and is the one *tracked* file that was still stale. It runs the same
`blackfong_ecosystem` code in the HUD container, so leaving it on 2.6.4 while
everything else moved to `>=2.10` would have meant the container running a
different pydantic than anything is tested against. **Verify the HUD
container still builds** — that bump was not validated against a real Docker
build here.

Also still on `2.6.4`, deliberately left alone: the untracked remaster/VM
staging copies under `data/obeos-remaster/opt/blackfong/` (`pyproject.toml`,
`requirements-obeos-linux.txt`, `requirements.docker-hud.txt`) and the
generated `blackfong_ecosystem.egg-info/requires.txt` (refreshes on reinstall).

`tools/prepare_desktop_lock.py` is the generator the previous snapshot could
not name. It reads `pyproject.toml` + `EarthGlass/pyproject.toml` to write
`requirements/desktop.in`, and snapshots **whatever is installed in the
venv** into `artifacts/tested-constraints.txt`. The two `.lock` files are
then compiled from those with hashes — that compile step is *not* in the
repo, but `uv` 0.12.12 is available in `.venv`.

`./.venv/Scripts/python.exe -m pip check` is now clean, which retires the
previous snapshot's `exa-py` / `google-genai` / `hermes-agent` / `langchain` /
`ollama` complaints.

Regenerating `desktop.in` also showed it was stale against EarthGlass beyond
pydantic — missing `numpy>=1.26.0` and the whole `weather` extra (`pillow`,
`xarray`, `cfgrib`, `eccodes`). Those do belong in the desktop lock, because
`earth-desktop.yml` installs EarthGlass with `--no-deps`, so the lock is the
only place its dependencies can come from.

**Do NOT just re-run the generator** (see P9) — the venv it snapshots has
drifted. Correct order when picking this up:

1. Reconcile the venv against declared dependencies (P9).
2. `./.venv/Scripts/python.exe tools/prepare_desktop_lock.py`
3. Compile both locks with hashes (uv or pip-compile, needs network), for
   Windows *and* Linux (`--python-platform linux` for the latter).

Still unaddressed: the `torch>=2.0.0` vs `setuptools<82` conflict.

## P8 — `requirements/` is untracked but CI installs from it **(new this session)**

`.github/workflows/earth-desktop.yml` (itself untracked) runs:

```
python -m pip install --require-hashes -r requirements/desktop-${{ matrix.lock }}-py311.lock
```

but `git ls-files requirements/` returns **nothing** — the whole directory,
both lock files included, has never been committed. `artifacts/` is
gitignored outright (`.gitignore:58`), so `tested-constraints.txt` cannot be
committed as-is either.

Consequences, in priority order:

- If `earth-desktop.yml` is committed without `requirements/`, that job fails
  immediately on a fresh clone — the file it installs from will not exist.
- The locks are not reproducibly regenerable from a clean checkout, because
  their constraints input is gitignored.

Decide: commit `requirements/` (and un-ignore or relocate the constraints
file), or make the workflow generate what it needs before installing.

## P9 — `.venv` has drifted from the declared dependencies **(new this session)**

A dry run of the constraints regeneration compared the venv against the
recorded constraints: **131 recorded vs 231 installed**.

- **~100 packages would be added** that are not desktop-runtime deps — the
  voice/ML/vision stack (`TTS`, `torch==2.11.0`, `transformers`, `spacy`,
  `librosa`, `opencv-python`, `matplotlib`, `pandas`, `faster-whisper`,
  `pygame`, …) plus the local editable installs from the integration pass
  (`3000`, `blackforge`, `maxpayne`, `quietdrop`, `nightwatch-os-dashboard`).
- **Declared dependencies would be *removed*, because they are not installed
  at all**: `Flask-SocketIO` and `winotify` (both declared in
  `pyproject.toml`), plus `gtfs-realtime-bindings`, `editables` and
  Flask-SocketIO's transitive deps (`python-socketio`, `python-engineio`,
  `bidict`, `simple-websocket`, `wsproto`).

Confirmed runtime impact — both call sites are guarded, so this degrades
silently rather than crashing:

- `operational_interfaces.py:21` sets `HAS_SOCKETIO = False`, so the HUD's
  live socket features are off in this venv.
- `notification_service.py:145` cannot deliver Windows toast notifications.

This is why P4 step 2 must not run first: it would bake the drift in,
dropping two declared dependencies and adding a hundred unrelated ones.

## P5 — 9-piece integration pass (unchanged, still open)

**Found but NOT fixed**:

- `3000 status` and `nightwatch status` hang past their own 5s health-check
  timeout (correctly reported `degraded` by `blackfong obeos health`).
  Suspect a lingering background thread; 3000's own output points at an
  `event_delivery` queue.
- **Event spine, Phase 3 (not started)**: two live, non-reconciled write
  paths — `/events/v1/publish` (outbox-backed, canonical) and legacy
  `/events/publish` (`event_api.py:64`, bypasses the outbox). Bigger gap:
  **nothing calls `EventQueue.dequeue()` in production** — the queue is a
  ledger, not a working bus. It needs a real consumer.
- **Branding/visual cohesion, Phase 4 (not started)** — recommend scoping
  separately as a design initiative, not a bug fix.
- The per-component `obeos.manifest.json` + `component_registry.py`
  discovery system has **zero production callers**; the live path is
  `ecosystem_manifest.py` reading `obeos_ecosystem.toml`. Wire it up or
  remove it.
- No `display_name`/`brand` field in any manifest schema.
- "Jarvis" is removed from user-facing text only by a runtime sanitizer in
  `daise/core.py` (~line 1648-1665); any surface bypassing it leaks the name.

## P6 — Code-quality items deliberately left alone (unchanged)

`ruff` reports 227 issues across just the three files touched this session;
~4,305 repo-wide. The old-style `List[X]`/`Optional[X]` hints and import
ordering (~84%) stay deliberately untouched — new code added this session
matches the surrounding `Optional[X]` style on purpose.

- ~211 `BLE001` (blind `except Exception`) sites still only spot-checked.
- Harmless dead unused-variable extractions in `observability.py`,
  `memory_lifecycle_management.py`, `startup_coordinator.py`,
  `decision_quality_control.py`, `daise/core.py`.

## P7 — orphaned code **[RESOLVED]**

`approval_grants.py`'s `ApprovalGrants` was built out for real (see P2).
Don't re-flag this file as dead code.
