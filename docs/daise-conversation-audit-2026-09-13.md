# D.A.I.S.E conversation audit

Date: 2026-09-13

The audit classified every persisted `agent_response` event in the configured OBEOS event store. It did not call Ollama and did not alter conversation history.

Machine-readable detail: `artifacts/daise-conversation-audit-2026-09-13.json`

| Classification | Count | Meaning |
|---|---:|---|
| Model-generated | 108 | Provider recorded as Ollama or response path recorded as a live model |
| Deterministic evidence | 205 | Local runtime truth, Earth/system evidence, or explicitly grounded deterministic output |
| Failed generation | 97 | Failed response or recorded backend error |
| Heuristic/placeholder | 28 | Heuristic provider or fallback model metadata |
| Empty response | 1 | Successful response event with no response text |
| Unknown | 45 | Historical event lacks enough metadata to prove its response path |
| **Total** | **484** | All persisted agent response events available to the audit |

Only 108 of 484 historical responses, approximately 22.3%, are provably model-generated from stored metadata. The audit confirms that the concern about bypasses and placeholders was justified, although historical records with missing metadata cannot be classified with certainty.

## Current policy

- Ordinary conversation is model-led and must reach Ollama.
- Grounded memory and deterministic conversational follow-ups do not bypass the model by default.
- Explicit Earth/system truth may use deterministic evidence.
- Deterministic evidence is visibly prefixed with `Local evidence summary (not model-generated).`
- Model failure is surfaced as failure; the conversational lane no longer turns it into heuristic prose.
- Future response events carry a response path and backend metadata sufficient for the audit to distinguish model, deterministic, failed, empty, heuristic, and unknown outcomes.

Focused validation after the routing change: **51 tests passed** in `tests/test_model_routing.py` and `tests/test_agent_model_and_memory_profiles.py`.
