# Working agreements for O.B.E.O.S

## The operator is on Windows PowerShell — hand over PowerShell, not bash

Their checkout is `C:\Users\New User\Documents\Projects 2026\O.B.E.O.S (official blackfong
ecosystem operating system)` — note the spaces and parentheses, so any path that does get
written needs quoting. The shell is PowerShell 5.1, which rejects bash habits outright:

- **no `&&`** ("The token '&&' is not a valid statement separator in this version") — give
  separate lines instead, which also means each command reports its own result rather than the
  chain aborting silently;
- **no `\` line continuation** (PowerShell uses a backtick, but plain separate lines are
  better);
- **no `grep`**, `sed`, `awk`, `curl` as written — `Select-String` and `Invoke-WebRequest` are
  the equivalents, or drop the filter;
- **`python`, not `python3`** (fall back to `py`);
- backslash path separators in arguments: `tools\probe_daise_conversation.py`.

The agent container is Linux, so commands verified there are not automatically pasteable. Write
the hand-over block in PowerShell.

## Verification runs on the operator's machine, not in the agent's container

A cloud session has no model backend — nothing answers on `127.0.0.1:8091` (llama.cpp) or
`11434` (Ollama) — so it cannot judge whether D.A.I.S.E converses, whether the HUD renders, or
whether the globe behaves. Anything resting on a live runtime is unverified until the operator
runs it.

**The method: hand over a paste-ready block, get the output back.** When something needs the
operator's hardware, write a probe into `tools/`, verify it runs in the container first (so
they are not debugging the script), then give them one block to paste. They paste the output
back and that output is the evidence. Do not ask them to describe what they saw — a probe that
prints `provider`, `model` and latency settles questions that prose cannot.

Existing probes:

- `tools/probe_daise_conversation.py` — drives five turns through `OperatorChatService`
  and prints a PASS/FAIL verdict. Run from the repository root.
- `docs/test-daise-conversation.md` — the manual UI version of the same test, plus the
  update path.

## Never trust a green reply from a down backend

With llama-server stopped, a turn falls back to a heuristic backend. For greetings it returns a
*fixed* cheerful greeting, so two `hi`s produce two identical replies in ~45 ms — indis­tinguish­able
from the deterministic-template bug that was removed in this branch. Those fallback replies are
persisted as real `agent_response` events, so they also pollute conversation history.

Before reading any conversational result, check the model name:

- `gemma4-heretic` → the live model answered; the result means something.
- `fallback-heuristic`, `bounded-context-grounded` → llama-server was down. Invalid test.
- `local_runtime_truth` / `grounded-truth-v1` → a deterministic path still answers. Real finding.

## Container gotchas that waste time

- **Submodules.** Eight of them, EarthGlass included. A fresh container clones them
  uninitialised and a plain `git pull` leaves them stale, which silently runs the Earth globe
  and chat panel from old built assets. Always `git submodule update --init --recursive`.
- **`pip` may not be `python3`'s pip.** Containers have shipped with `pip` on 3.13 while
  `python3` is 3.11, so `pip install` lands where imports cannot see it. Use
  `python3 -m pip install`.
- **Dependencies do not survive a container recycle.** `requirements.txt` cannot be installed
  whole (the TTS/audio stack fails to build); install the subset the task needs.
- **`earthglass` needs its core deps**, not `--no-deps`: `numpy` is a hard requirement and
  `tests/test_earth_control.py` / `tests/test_earth_chat.py` cannot collect without it. The
  heavy weather extras (`xarray`, `cfgrib`, `eccodes`) are genuinely optional.
- **The test suite rewrites `docs/release-evidence-latest.md`**, which is tracked. Revert it
  before committing.

## Known-failing tests that are not yours

Ten fail on `agent/integration-stabilization-v0.9` independently of any recent work, verified
by stashing: background delivery (×4), memory chamber unified graph (×3), release evidence,
consolidation, and the Ollama stream fallback. They are deliberately outside the CI allowlist
so they do not become merge blockers. Do not treat them as a regression you caused.

## The event log is append-only

`data/events.db` has a trigger rejecting deletes ("events are immutable"). Probe turns written
there cannot be cleaned up, so prefer an isolated `EventStore(tmp_path / "events.db")` for
anything exploratory. Two synthetic `hi` turns from 2026-10-03 are in there permanently for
exactly this reason.
