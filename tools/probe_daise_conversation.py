"""Drive a real conversation through the operator chat path and print the evidence.

Exercises the same entry point the Earth panel and the HUD use
(OperatorChatService.send_message), so a pass here means the surfaces pass. Prints
provider, model and latency per turn, because those are what distinguish a genuine
model reply from a deterministic template or a heuristic fallback.

Writes real turns to the operator's session, which is the point: this is a live
conversation, not a simulation.

    python3 tools/probe_daise_conversation.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

# A model reply can carry any character. On a legacy Windows console stdout is
# cp1252, where printing one would raise UnicodeEncodeError and lose the whole
# run after the work was already done. Degrade the character, not the result.
try:  # pragma: no cover - depends on the host console
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TURNS = [
    ("hi", "greeting"),
    ("hi", "greeting repeated -- must differ from the first"),
    ("what time is it", "deterministic fact, generated sentence"),
    ("what should we work on next in obeos?", "sets up the follow-up"),
    ("why that one", "terse follow-up -- must resolve against the previous answer"),
]


def main() -> int:
    from blackfong_ecosystem.config import chat_settings, llamacpp_settings

    print("=" * 72)
    print("BACKEND")
    print("=" * 72)
    print(f"  daise_backend : {chat_settings.daise_backend}")
    print(f"  daise_model   : {chat_settings.daise_model}")
    print(f"  llamacpp url  : {llamacpp_settings.base_url}")

    reachable = False
    try:
        import httpx

        with httpx.Client(timeout=5.0, trust_env=False) as client:
            response = client.get(f"{llamacpp_settings.base_url.rstrip('/')}/v1/models")
            reachable = response.status_code == 200
            print(f"  /v1/models    : HTTP {response.status_code}")
            if reachable:
                names = [m.get("id") for m in (response.json().get("data") or [])]
                print(f"  served models : {names}")
    except Exception as exc:
        print(f"  /v1/models    : UNREACHABLE ({type(exc).__name__}: {exc})")

    if not reachable:
        print()
        print("  !! llama-server is not answering. Every turn below will fall back to")
        print("     a heuristic greeting, which looks exactly like the original bug.")
        print("     Start llama-server first or this probe proves nothing.")
    print()

    from blackfong_ecosystem.operator_chat import OperatorChatService

    service = OperatorChatService()
    session_id = None
    replies: list[str] = []
    models: list[str] = []
    errors: list[str] = []

    for index, (message, intent) in enumerate(TURNS, start=1):
        print("=" * 72)
        print(f"TURN {index}: {message!r}   [{intent}]")
        print("=" * 72)
        started = time.perf_counter()
        try:
            payload = service.send_message(
                message=message,
                session_id=session_id,
                analysis_mode="standard",
                include_messages=False,
                include_trace=False,
                context={"input_origin": "probe", "transport": "probe"},
            )
        except Exception as exc:
            print(f"  RAISED {type(exc).__name__}: {exc}")
            return 1
        wall_ms = (time.perf_counter() - started) * 1000
        session_id = payload.get("session_id") or session_id

        result = payload.get("result") or {}
        reply = str(result.get("response") or "").strip()
        replies.append(reply)
        models.append(str(result.get("model") or ""))
        if payload.get("status") == "error" or payload.get("error"):
            errors.append(str(payload.get("error") or "error"))

        print(f"  status        : {payload.get('status')}")
        if payload.get("error"):
            print(f"  error         : {payload.get('error')}")
        if payload.get("error_type"):
            print(f"  error_type    : {payload.get('error_type')}")
        print(f"  provider      : {result.get('provider')}")
        print(f"  model         : {result.get('model')}")
        print(f"  server ms     : {round(float(result.get('execution_time_ms') or 0))}")
        print(f"  wall ms       : {round(wall_ms)}")
        print(f"  response_path : {result.get('response_path')}")
        print("  reply         :")
        for line in (reply or "(empty)").splitlines() or ["(empty)"]:
            print(f"    {line}")
        print()

    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    first, second = (replies + ["", ""])[:2]
    # Model names that mean something other than the live model answered.
    NOT_THE_MODEL = ("fallback-heuristic", "heuristic", "grounded", "deterministic", "truth-v1")
    served = [str(m or "") for m in models]
    impostors = sorted({m for m in served if any(t in m.lower() for t in NOT_THE_MODEL)})

    checks = [
        ("every turn was answered by the live model", not impostors),
        ("the two 'hi' replies differ", bool(first) and first != second),
        ("no 'Local evidence summary' label", not any("Local evidence summary" in r for r in replies)),
        ("no turn reported an error", not errors),
    ]
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    print()
    if impostors:
        print(f"  Not a valid test: {', '.join(impostors)} answered instead of the model.")
        print("  Start llama-server and re-run; nothing below this line means anything yet.")
    elif all(ok for _, ok in checks):
        print("  The cutover holds: every reply was generated.")
    else:
        print("  A real finding -- paste this whole output back.")

    print()
    print("=" * 72)
    print("EVENT LOG (last 6 agent responses)")
    print("=" * 72)
    db = Path("data/events.db")
    if not db.exists():
        print(f"  {db} not found -- run from the repository root")
    else:
        connection = sqlite3.connect(str(db))
        rows = connection.execute(
            "select payload from events where type='agent_response' "
            "order by timestamp desc limit 6"
        ).fetchall()
        for (raw,) in rows:
            d = json.loads(raw)
            print(
                f"  {str(d.get('provider')):22s} {str(d.get('model')):26s} "
                f"{round(float(d.get('execution_time_ms') or 0)):>7} ms"
            )
        print()
        print("  Want: llamacpp / gemma4-heretic, hundreds-to-thousands of ms.")
        print("  fallback-heuristic  => llama-server was down; the probe proves nothing.")
        print("  local_runtime_truth => a deterministic path still answers; a real finding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
