"""Classify every persisted D.A.I.S.E response without calling a model."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from blackfong_ecosystem.storage import EventModel, EventStore


def classify(payload: dict[str, Any]) -> str:
    response_path = str(payload.get("response_path") or "").strip().lower()
    provider = str(payload.get("provider") or "").strip().lower()
    model = str(payload.get("model") or "").strip().lower()
    response = str(payload.get("response") or payload.get("text") or "").strip()
    if not payload.get("success", True) or payload.get("backend_error"):
        return "failed_generation"
    if not response:
        return "empty_response"
    if response_path == "deterministic_evidence" or provider in {"local_runtime_truth", "conversation_memory", "knowledge_librarian"}:
        return "deterministic_evidence"
    if provider == "heuristic" or "fallback" in model or response_path in {"heuristic", "fallback"}:
        return "heuristic_or_placeholder"
    if provider == "ollama" or response_path in {"live_model", "model"}:
        return "model_generated"
    if response_path in {"grounded_memory", "grounded_knowledge", "grounded_conversation"}:
        return "retrieval_only"
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    store = EventStore(args.db) if args.db else EventStore()
    try:
        with store.session() as session:
            rows = (
                session.query(EventModel)
                .filter(EventModel.type == "agent_response")
                .order_by(EventModel.timestamp.asc())
                .all()
            )
        counts: Counter[str] = Counter()
        records: list[dict[str, Any]] = []
        for row in rows:
            payload = row.payload or {}
            category = classify(payload)
            counts[category] += 1
            records.append({
                "event_id": row.id,
                "request_id": row.request_id or payload.get("request_id"),
                "session_id": row.session_id,
                "timestamp": row.timestamp,
                "category": category,
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "response_path": payload.get("response_path") or (payload.get("backend_metadata") or {}).get("response_path"),
                "backend_error": payload.get("backend_error"),
                "response_chars": len(str(payload.get("response") or payload.get("text") or "")),
            })
        result = {
            "schema_version": "1.0",
            "scope": "all persisted agent_response events",
            "total": len(records),
            "counts": dict(sorted(counts.items())),
            "records": records,
        }
        rendered = json.dumps(result, indent=2, default=str) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
