from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class Event:
    id: int
    timestamp: str
    event_type: str
    confidence: float
    snapshot_path: str
    metadata_json: str


def utc_timestamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    return metadata or {}
