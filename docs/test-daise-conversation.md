# Testing whether D.A.I.S.E actually converses

The deterministic-template layer that used to answer greetings, check-ins and reflections
before any model ran is now demoted to prompt evidence: the model writes every reply, and the
clock, service status and work ledger are handed to it as facts rather than returned as the
answer. That change is landed and gated by CI. What CI cannot test is whether it works against
a real model, because no automated check runs a model backend. This is that test.

## 1. Is your checkout already current?

```bash
cd ~/O.B.E.O.S          # your checkout
git fetch origin agent/integration-stabilization-v0.9
git log --oneline HEAD..origin/agent/integration-stabilization-v0.9
```

**Nothing printed → you already have it.** Skip to step 3. Branch head `f4a9ce7` is a merge
from 2026-10-24 that already contains both conversational commits, so a machine synced since
then is current.

## 2. Only if step 1 printed commits

```bash
git checkout agent/integration-stabilization-v0.9
git merge origin/agent/integration-stabilization-v0.9

# Eight submodule pins moved, EarthGlass among them. `git pull` alone leaves them
# behind, which would run the globe and chat panel from stale built assets.
git submodule update --init --recursive

# The Earth client is TypeScript; the panel you type into is a build artefact.
pnpm --dir EarthGlass/obeos-earth build
```

No reinstall needed — `requirements.txt` and `pyproject.toml` did not change.

## 3. The model backend has to be up — check this before anything else

```bash
curl -s http://127.0.0.1:8091/v1/models      # expect gemma4-heretic
```

**Do not skip this.** With llama-server down, the turn falls back to a heuristic backend that
answers with a fixed greeting, and two `hi`s produce two *identical* canned replies:

```
[assistant] Hello. D.A.I.S.E. is here with local O.B.E.O.S. memory, recent session
            context, and stored knowledge still available.
```

That is indistinguishable from the original bug. The panel does show a notice beside it —
"The local model backend (llama-server) is not running. Start it, then send again." — but if
you read the reply and not the notice you would reasonably conclude nothing was fixed.

Two ways to tell the cases apart for certain:

- the notice is present (outage) vs absent (real reply);
- in the log query in step 5, the failing case reads `llamacpp fallback-heuristic`, the working
  case reads `llamacpp gemma4-heretic`.

Note also that these fallback replies are persisted as real `agent_response` events, so testing
against a down backend writes canned turns into conversation history that later prompts will
read back. Another reason to start llama-server first.

## 4. The test

Open Ask DAISE on the globe screen and type **`hi`**. Then type **`hi`** again.

| Check | Pass | Fail means |
|---|---|---|
| The two replies differ | different wording | an interceptor still sits in front of the model |
| Each takes seconds | 1–10s | ~45 ms is a dictionary lookup, not inference |
| No label prefix | plain reply | "Local evidence summary (not model-generated)." = template |

Then two that exercise the rest of the change:

- **`what time is it`** — expect a natural sentence containing the correct local time. The fact
  is deterministic, the sentence is not; that is evidence injection working.
- Ask a real question, then a terse follow-up like **`why that one`**. The follow-up should
  resolve against the previous answer. Standard mode used to hand the model only two exchanges,
  which is why follow-ups used to restart from a generic summary.

## 5. Confirm from the data, not from feel

```bash
python3 -c "
import sqlite3, json
c = sqlite3.connect('data/events.db')
for ts, p in c.execute(\"select timestamp, payload from events \"
                       \"where type='agent_response' order by timestamp desc limit 5\"):
    d = json.loads(p)
    print(d.get('provider'), d.get('model'), round(d.get('execution_time_ms', 0)), 'ms')
"
```

Expect `llamacpp gemma4-heretic` with timings in the hundreds to thousands of milliseconds.

Any row reading `local_runtime_truth` / `grounded-truth-v1` means a deterministic path was
missed and is still answering — report it and it can be traced.

## What this does not cover

- `data/events.db` contains two synthetic `hi` turns from a diagnostic probe on 2026-10-03
  (11:56 and 11:57). The events table is append-only by trigger, so they could not be removed.
  They sit in the same session as the real test turns.
- Reply latency is not addressed by this change. Removing a 45 ms template means waiting on
  real inference, and there is no streaming on the Earth chat route yet, so a reply appears all
  at once after a pause. `send_message` already accepts a `stream_callback` and the llama.cpp
  backend supports streaming; wiring an SSE route is the obvious next step if the wait reads as
  a hang.
