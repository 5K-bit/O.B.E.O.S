# D.A.I.S.E. local conversation recovery

## Verification checkpoint: 2026-09-20

This is an implementation record, not a declaration of acceptance.

### Observed baseline

- Configured dedicated llama.cpp endpoint: localhost:8091; connection refused
  during the baseline probe.
- The observed llama-server process belonged to Ollama, not the dedicated
  OBEOS llama.cpp installation. Process presence does not establish readiness.
- Ollama reported llama3.2:3b resident. This does not establish quality or
  readiness of the configured D.A.I.S.E. lane.
- Hardware inventory reported Intel UHD Graphics and approximately 32 GiB RAM.
  Reported adapter memory is not a reliable dedicated inference VRAM budget.

### Implemented and tested

- Generation checks health and advertised model identity before sending prompts.
- Supervisor readiness uses the same identity check.
- Selecting llama.cpp no longer falls through to Ollama because local files are
  absent. Actual endpoint readiness is checked at invocation, not construction.
- D.A.I.S.E. Ollama routing no longer automatically tries other models. Other
  agents retain existing routing behavior.
- Streaming without a provider finish reason raises an incomplete-stream error.
- Delivery metadata includes first-token latency, completion flag and output-limit
  termination. UI propagation of this metadata still needs acceptance testing.
- Targeted readiness, delivery and routing tests: 17 passed in 3.54 seconds.

### Remaining acceptance work

- Shared ownership/locking, asynchronous warmup waiting and cancellation,
  continued monitoring and one bounded restart after failure.
- Verify all surface-specific routing, including Earth's explanation lane.
- Serial installed-model comparison; no primary-model selection made yet.
- Runtime grounding, memory quality, unintended agent routing and failed-history
  exclusion checks.
- Twenty real conversation turns per local surface and measured warm latency.
- Full operator UI and lifecycle regression testing.

No cloud deployment or acceptance commit was made at this checkpoint. These
checks do not start a missing model server or establish conversational recovery.

## Lifecycle continuation and real-model probe

- Added cross-process launch leasing, continuing health monitoring, a single
  restart after an owned process exits, and safe detachment from ready servers.
  Detachment deliberately leaves the model resident; consumer lease accounting
  and lifecycle handoff still require completion.
- Added cancellable request-worker warmup on the configured local lane.
- Lifecycle/readiness/delivery/routing checkpoint: 22 tests passed (before the
  additional warmup cancellation tests).
- Isolated live probe: Gemma startup 24,231.39 ms, first token 7,840.30 ms,
  completion 33,122.77 ms. Ollama reported zero resident models before startup.
- Quality failure: the reply described the intended answering persona instead
  of conversing with the user. This is NOT a passing model selection result.
- Probe bypassed OperatorChatService and durable memory/profile learning. It
  establishes model behavior only, not HUD/workspace end-to-end acceptance.
- Reproduce with `.venv\\Scripts\\python.exe -m tools.probe_daise_local`.
  The probe leaves a ready server resident so it cannot disrupt other consumers.

## Controlled comparison in progress

- Added `tools.compare_daise_models`: identical six-turn conversations, three
  warm rounds, 120 output tokens, temperature 0.2, no retrieval or profile writes.
  Evidence fixture recall here is not installed Spawn-source acceptance.
- Gemma comparison is being saved under
  `data/evaluations/daise-recovery/gemma4.json`; inspect completed records before
  deciding a primary model. An initial resident request is not a cold load.
- First warm round failed on turn one: answer-planning text and output limit.
  Second round also returned answer-planning text on its greeting turn.
- Some first-candidate measurements overlap a small regression test run; treat
  these as diagnostic, not clean hardware latency rankings. Rerun latency for
  any passing finalist without concurrent tests or other inference.
- Lilo training intent no longer matches generic topic/progress/certification
  mentions. Explicit training phrases still work; direct overrides are unchanged.
- Combined intent/lifecycle/readiness/delivery/routing tests: 37 passed, two
  existing Pydantic deprecation warnings.

## Retrieval and candidate continuation

- Corrected transcript queries to select the newest bounded set, then restore
  chronological display order. Previously an ascending limited query selected
  the oldest events in long sessions.
- Failed, cancelled, explicitly incorrect and incomplete assistant events remain
  in history but receive `prompt_eligible=false`; context and new summaries skip
  these events. Historical derived summaries have not yet been revalidated.
- Incomplete generation now sets an agent error while preserving partial text;
  it does not count as successful recovery.
- Added cross-process single-turn admission per session to OperatorChatService;
  busy sessions return an explicit error, other sessions remain independent.
  Generated request IDs retain the full UUID rather than eight characters.
- Llama 3.2 3B: 18/18 warm turns completed; mean first token 4,725.92 ms
  (618.58-6,404.33), mean completion 14,268.59 ms (8,916.74-22,359.77).
  Correct official expansion and supplied fixture citation; one internally
  contradictory follow-up answer. Not yet accepted or configured as primary.
- Gemma 3 1B comparison underway: much faster, but observed invented citation
  identifier A-001 and failure to expand the assistant's name. Speed alone
  does not satisfy acceptance.
- Memory eligibility and training intent test checkpoint: 21 passed. New
  session-admission and incomplete-agent-response tests await execution after
  the serial model comparison to avoid distorting timing.

## Shared delivery audit

- Found hardcoded success in both D.A.I.S.E. orchestration paths, despite
  backend-error metadata. Response persistence, request outcomes and returned
  error fields now agree on failure; incomplete output is retained but not
  reported as successful recovery.
- HUD chat previously bypassed OperatorChatService. It now uses that shared
  entry point with its existing token/progress/report callbacks preserved.
- Active pipeline conversation normalization also excludes ineligible history.
- Mistral comparison remains in progress. First-round follow-up first-token
  latencies are tens of seconds; no primary candidate has been accepted.
- Delivery/HUD regression tests were started during the diagnostic Mistral run.
  As with Gemma, clean latency ranking requires an idle-machine finalist rerun.
  These regression tests do not call the live model.

## Candidate comparison checkpoint

| Candidate | Warm turns | Mean first token | Mean completion | Quality observation |
| --- | ---: | ---: | ---: | --- |
| Gemma 4 heretic | 13 | 10.26 s | 40.31 s | Analysis instead of answers; first round stopped on output limit |
| Llama 3.2 3B | 18 | 4.73 s | 14.27 s | Conversational; one internally contradictory sequence answer |
| Gemma 3 1B QAT | 18 | 1.87 s | 3.34 s | Invented source identifier twice; failed name expansion |
| Mistral latest | 18 | 37.79 s | 58.10 s | Conversational but slow; contradictory sequence statements |

These are diagnostic measurements, not clean finalist rankings. No candidate
has passed the full correctness/reliability acceptance suite. All installed
models remain intact; evaluation-loaded candidates were unloaded sequentially.
The supplied Spawn fixture is not evidence of installed-source retrieval.

## Startup and regression checkpoint

- Removed global orchestrator re-import on injected service requests. The
  unchanged two-second HUD acceptance test subsequently passed.
- Startup now warms only the selected D.A.I.S.E. backend, not an unrelated
  fast-model chain plus a dedicated llama.cpp server simultaneously.
- Earth and HUD use the same selected-backend warmup as the classic workspace.
- HUD readiness now includes the actual model warmup result rather than ignoring
  failure. Ollama warmup uses the configured conversation context size to avoid
  an immediate context-size reload on first generation.
- Combined regression suite: 55 passed, two Pydantic deprecation warnings.
  Pytest reported 3877.15 seconds elapsed for this run, an anomalously long
  duration requiring investigation; this is not evidence of startup performance.
- Still outstanding: cross-surface live acceptance, full ownership handoff and
  recovery verification, clean finalist evaluation, source-backed Spawn/Tiny
  checks, complete lifecycle trace/UI wiring, and related-only commit.

## Active reasoning-path audit

- Centralized identity/action-truth instructions in `daise_identity.py` and used
  them in both agent and active pipeline prompts. Active pipeline previously
  lacked the official expansion.
- Active pipeline now uses streaming when supplied, propagates cancellation,
  and avoids repeating the current user turn after hydrated transcript history.
- Applied explicit-training gating to the active pipeline selector too; the
  earlier legacy-selector fix alone was insufficient.
- Targeted regression: 29 passed in 13.31 seconds; per-test bodies under 5 ms.
  This does not reproduce the earlier anomalous hour-long suite duration.
- New eight-turn Llama follow-up screen failed explicit fact retention, name
  expansion and source recall. No primary change is justified by that result.
- Inspected installed Gemma Ollama configuration. Its short custom TEMPLATE
  is not by itself proof of broken chat: Modelfile also specifies RENDERER gemma4
  and PARSER gemma4; upstream uses a native renderer. No template was rewritten.
  References: https://registry.ollama.com/library/gemma4 and
  https://github.com/ollama/ollama/blob/main/model/renderers/gemma4.go
- Started an additional isolated follow-up screen of already-installed
  qwen2.5-coder:3b (approximately 1.93 GB download size), after unloading Llama.
  This is not a selection or a new download. Larger 26B candidate has not been
  loaded because its 18.6 GB weights exceed the earlier measured free RAM.

## Adapter parity and completion contract

- Qwen 3B follow-up screen retained some later facts but mishandled the initial
  sequence. A direct HTTP request and OBEOS Ollama adapter with the same canonical
  system prompt both returned the same incorrect sentence. No adapter text
  rewriting explains that observed failure.
- Ollama adapter now forwards explicit temperature, exposes done_reason as
  finish_reason, distinguishes output-limit termination, rejects streams missing
  provider completion, and records first-token latency.
- Adapter/routing/completion tests: 13 passed in 23.11 seconds. Slowest test was
  model-chain discovery (5.41 seconds), not the mocked delivery tests.
- A terminal launch encountered `wait for runner spawn_ready`; a subsequent
  read succeeded. Existing probe process completed. No duplicate run launched.
- Fresh hardware check: 18,857,748 KiB free of 33,266,520 KiB total RAM, no
  resident Ollama models. Started isolated Qwen 2.5 Coder 7B follow-up screen;
  installed weights approximately 4.68 GB. Default model remains unchanged.

## Bounded recovery and larger-candidate screen

- Supervisor now tolerates transient endpoint outages with a bounded recovery
  window, then restores ready only after a verified healthy model endpoint.
  Externally owned processes remain untouched. Subscribers refresh their deadline
  after healthy observations instead of retaining an expired startup deadline.
- Lifecycle tests: eight passed in 3.38 seconds, including external transient
  recovery, no external restart and exactly one owned-process restart.
- Qwen 7B eight-turn screen kept explicit chronology, accepted correction,
  answered general knowledge and identity, and denied unperformed file access.
  The source-time question elicited an unnecessary real-time disclaimer but
  retained route-17 and 18:45; further source-grounding checks remain necessary.
- Qwen 7B full three-round comparison is running. Early responses are coherent;
  first-token latency around 10-13 seconds exceeds the five-second target.
  This is not yet a primary selection or live-surface acceptance result.

## Completed Qwen 7B comparison and identity guard

- Three comparison rounds completed: 18/18 responses ended with provider `stop`.
  Mean first-token latency was 10,495.93 ms (maximum 14,337.03 ms); mean
  completion was 17,647.59 ms (maximum 24,216.47 ms). The five-second warm
  first-token target is not met. Runtime evidence is in
  `data/evaluations/daise-recovery/qwen25-7b-comparison.json`.
- Responses retained chronology, supplied the official expansion, declined
  unavailable runtime checks, and cited the supplied synthetic Spawn fixture.
  This does not verify retrieval from the installed Spawn collection or actual
  Tiny status, and is not the required 20-turn-per-surface acceptance.
- Production primary remains unchanged pending full-stack acceptance. Gemma
  remains installed; no two-model reasoning/answering chain was introduced.
- Ollama now rejects a different reported model before delivering that chunk's
  text. Omitted default `:latest` tags are treated equivalently. Missing model
  fields remain compatible with existing adapters, not proof of identity.
- Mocked delivery regression: eight passed in 1.00 seconds, including streaming
  and non-streaming wrong-model rejection. `git diff --check` passed.

## Endpoint reuse and launch safety

- Moved local binary/GGUF availability checks into the background launch path,
  after verified endpoint reuse. An existing correct server no longer requires
  the client surface to have local launch files available.
- Model identity mismatch now propagates out of the readiness probe and records
  `failed` / `model_identity_mismatch`; it cannot be mistaken for an absent
  server and trigger a competing process launch. External processes are untouched.
- Lifecycle/readiness/selected-warmup regression: 20 passed in 1.33 seconds.
  These isolated tests do not replace real multi-process crash/recovery acceptance.

## Real shared-stack smoke test

- Added `python -m tools.probe_daise_stack`: separate process, scratch working
  directory and config paths established before importing runtime services.
  Uses the actual OperatorChatService and UnifiedOrchestrator, not a fake agent.
- Run `stack-49c196ba5d7e4080b9a22d3c39a49c14` completed all three turns with
  Qwen 7B, standard mode, unique request IDs, live Ollama responses and `stop`.
  Replies were natural, retained bus-before-rain order and accepted train correction.
- First-token times: 47.49 s, 29.75 s, 42.02 s. Turn completion: 52.72 s,
  34.60 s, 44.87 s. Latency remains unacceptable against the five-second target.
  Ollama reported the requested model and 2048 context; model load per turn was
  only 16-20 ms. Empty evaluation storage does not test installed Spawn evidence.
- Added provider prompt-evaluation/generation timings and effective context/output
  limits to stream metadata to localize latency. Eight delivery tests passed in
  0.49 s. These fields were added after this run and require a fresh live probe.
- This was shared-service acceptance preparation, not the required native/HUD
  20-turn UI acceptance. Production defaults and cloud remain unchanged.

## Prompt-processing latency diagnosis

- Instrumented run `stack-05631384736e4815a2434642665ec306` completed all three
  live turns coherently. Provider prompt evaluation consumed 23.64, 36.43 and
  50.33 seconds; generation consumed approximately 5-6 seconds. This isolates
  a major cost inside model prompt processing rather than memory retrieval alone.
- Removed redundant instructions already present in canonical identity/truth
  guidance. Placed stable transcript ahead of changing retrieval evidence and
  retained the current user turn exactly once at the end. No evidence, identity
  rule or explicit correction rule was removed.
- Prompt-order and incomplete-delivery tests: two passed in 4.38 seconds, with
  two existing Pydantic deprecation warnings. Fresh isolated live probe started;
  latency improvement is not yet verified.

## Native long-conversation evaluation started

- Optimized three-turn probe completed with coherent answers, but no reliable
  latency gain: first turn used 253 input tokens instead of 326; third-turn
  prompt processing still took 49.46 seconds. Prefix reordering is not evidence
  that provider caching is effective on this machine.
- `tools.probe_daise_stack --native` now exercises 20 turns through the actual
  ChatPane and ChatWorker with live Ollama, isolated authorities and offscreen
  Qt rendering. It records full-text rendering checks and event-loop gaps.
  Includes unfamiliar follow-ups, correction retention, identity, factual
  uncertainty and unintended-training checks. Run started under
  `stack-7e30fdcd099a4f15a1529ca8d355fabc`; results are pending.
- Offscreen checks do not substitute for visible workspace layout review or
  browser HUD acceptance. Installed knowledge/status checks remain separate.

## HUD error propagation audit

- HUD request execution now normalizes backend_error into error before updating
  request/action lifecycle. The response builder also recognizes backend_error.
  This prevents a backend-failed turn from being recorded as a completed action.
- Added a regression where the orchestrator returns backend_error without error;
  HUD regression process is still running, so this patch is not yet verified.
- Native long-run remains active; first four turns returned live responses.
  First turn rendered fully, with a measured 250 ms maximum event-loop gap.
  Full 20-turn results and quality assessment remain pending.
