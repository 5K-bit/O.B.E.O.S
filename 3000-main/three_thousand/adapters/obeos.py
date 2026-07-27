from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict


def publish_motion_spike(
    *,
    confidence: float,
    snapshot_path: str,
    metadata: Dict[str, Any] | None = None,
    local_event_id: int | None = None,
) -> Dict[str, Any]:
    """Best-effort OBEOS event publication for detected motion."""
    if os.getenv("THREE_THOUSAND_OBEOS_EVENTS", "1").strip().lower() in {"0", "false", "no"}:
        return {"published": False, "reason": "disabled"}
    try:
        from blackfong_ecosystem.events import Event
        from blackfong_ecosystem.storage import EventStore

        db_override = os.getenv("OBEOS_EVENTS_DB")
        store = EventStore(Path(db_override).expanduser() if db_override else None)
        store.init_db()
        event = Event.new(
            source="project3000",
            type="motion_spike",
            payload={
                "description": "3000 motion spike detected",
                "snapshot_path": snapshot_path,
                "local_event_id": local_event_id,
                "metadata": metadata or {},
            },
            confidence=confidence,
            tags=["3000", "motion", "ecosystem"],
        )
        store.add_event(event)
        return {"published": True, "event_id": event.id}
    except Exception as exc:
        return {"published": False, "reason": str(exc)}
