from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

EVENT_CONTRACT_VERSION = "1.0"
_EVENT_TYPE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)+$")


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_type: str
    source: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    correlation_id: str | None = None
    parent_event_id: str | None = None
    contract_version: str = EVENT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if self.contract_version != EVENT_CONTRACT_VERSION:
            raise ValueError(f"event contract must be {EVENT_CONTRACT_VERSION}")
        if not _EVENT_TYPE.fullmatch(self.event_type):
            raise ValueError("event_type must be namespaced, e.g. system.health.degraded")
        if not self.source.strip():
            raise ValueError("event source is required")
        if not isinstance(self.payload, dict):
            raise ValueError("event payload must be an object")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventEnvelope":
        return cls(
            event_type=str(data["event_type"]),
            source=str(data["source"]),
            payload=dict(data.get("payload") or {}),
            event_id=str(data.get("event_id") or uuid.uuid4()),
            timestamp=str(data.get("timestamp") or datetime.now(timezone.utc).isoformat()),
            correlation_id=data.get("correlation_id"),
            parent_event_id=data.get("parent_event_id"),
            contract_version=str(data.get("contract_version") or EVENT_CONTRACT_VERSION),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "parent_event_id": self.parent_event_id,
            "payload": self.payload,
        }
