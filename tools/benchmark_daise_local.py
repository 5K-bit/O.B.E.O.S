"""Synthetic local-only latency probe; never reads or records private conversations."""
import json
import time
from pathlib import Path
from threading import Timer
from blackfong_ecosystem.cancellation import CancellationToken, cancellation_scope
from blackfong_ecosystem.model_routing import build_routed_chat_backend, backend_options_for_context

results = []
for state in ("first_request", "warm_request"):
    token = CancellationToken()
    timer = Timer(120, token.cancel)
    backend = build_routed_chat_backend("daise", "standard")
    start = time.perf_counter()
    first = []
    def receive(text):
        token.raise_if_cancelled()
        if text and not first:
            first.append(time.perf_counter() - start)
    timer.start()
    try:
        with cancellation_scope(token):
            result = backend.generate_stream([
                {"role": "system", "content": "You are D.A.I.S.E., the local OBEOS companion. Respond naturally. Do not invent prior history."},
                {"role": "user", "content": "Hi DAISE, I am back to work on OBEOS Earth."},
            ], on_token=receive, **backend_options_for_context({}, "daise"))
        results.append({"state": state, "status": "ok", "model": result.model,
                        "first_token_s": first[0] if first else None,
                        "total_s": time.perf_counter() - start, "characters": len(result.text),
                        "metadata": result.metadata})
    except Exception as exc:
        results.append({"state": state, "status": "error", "error": str(exc),
                        "first_token_s": first[0] if first else None,
                        "total_s": time.perf_counter() - start})
    finally:
        timer.cancel()
    print(json.dumps(results[-1], default=str), flush=True)
output = Path(__file__).resolve().parents[1] / "artifacts/consolidation-gemma-latency.json"
output.write_text(json.dumps({"scope": "Synthetic generation only; retrieval and Earth contention not included", "runs": results}, indent=2, default=str), encoding="utf-8")
