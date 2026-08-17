from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .contracts.event_v1 import EventEnvelope
from .event_queue import EventQueue
from .events import Event
from .storage import EventStore


@dataclass(frozen=True, slots=True)
class IngestedEvent:
    envelope: EventEnvelope
    legacy_event: Event
    queue_id: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.envelope.contract_version,
            "event_id": self.envelope.event_id,
            "event_type": self.envelope.event_type,
            "source": self.envelope.source,
            "legacy_event_id": self.legacy_event.id,
            "legacy_event_type": self.legacy_event.type,
            "queue_id": self.queue_id,
        }


class EventV1Ingestor:
    """Bridge namespaced Event Contract v1 envelopes into the existing event spine.

    The canonical v1 envelope is preserved verbatim inside the legacy event payload.
    This keeps Thoughtglass, EventStore, and existing consumers operational while new
    external components publish namespaced events.
    """

    def __init__(self, event_store: EventStore | None = None, event_queue: EventQueue | None = None) -> None:
        self.event_store = event_store or EventStore()
        self.event_queue = event_queue or EventQueue()

    def init_db(self) -> None:
        self.event_store.init_db()
        self.event_queue.init_db()

    def ingest(self, envelope_or_data: EventEnvelope | dict[str, Any]) -> IngestedEvent:
        envelope = (
            envelope_or_data
            if isinstance(envelope_or_data, EventEnvelope)
            else EventEnvelope.from_dict(dict(envelope_or_data))
        )
        timestamp_ms = self._timestamp_ms(envelope.timestamp)
        legacy_event = Event.new(
            source=self._legacy_source(envelope.source),
            type=self._legacy_type(envelope.event_type),
            payload={
                "obeos_event_v1": envelope.to_dict(),
                "component_payload": dict(envelope.payload),
            },
            confidence=1.0,
            timestamp_ms=timestamp_ms,
            parent_event=envelope.parent_event_id,
            correlation_id=envelope.correlation_id,
            tags=["obeos-event-v1", envelope.event_type, f"source:{envelope.source}"],
        )
        self.event_store.add_event(legacy_event)
        queue_id = self.event_queue.enqueue(legacy_event.id)
        return IngestedEvent(envelope=envelope, legacy_event=legacy_event, queue_id=queue_id)

    @staticmethod
    def _timestamp_ms(value: str) -> int | None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(parsed.timestamp() * 1000)
        except Exception:
            return None

    @staticmethod
    def _legacy_source(source: str) -> str:
        normalized = source.strip().lower().replace("-", "_")
        source_map = {
            "daise": "daise",
            "blackfong": "blackfong",
            "system": "system",
            "sentinel": "sentinel",
            "project3000": "project3000",
            "3000": "project3000",
            "telegram": "telegram",
            "frame": "frame",
            "memory_chamber": "memory_chamber",
            "tinyprogrammer": "tinyprogrammer",
            "installer": "installer",
        }
        return source_map.get(normalized, "blackfong")

    @staticmethod
    def _legacy_type(event_type: str) -> str:
        normalized = event_type.lower()
        if normalized.startswith("system.health."):
            return "backend_health_observation"
        if normalized.startswith("maxpayne."):
            return "adapter_health_observation"
        if normalized.startswith("project3000.") or normalized.startswith("sentinel."):
            return "sentinel_detection_event"
        if normalized.startswith("daise."):
            if normalized.endswith("response"):
                return "agent_response"
            if normalized.endswith("request"):
                return "agent_query"
            return "reasoning_trace"
        if normalized.startswith("memory.") or normalized.startswith("assistant.memory."):
            return "memory_artifact_created"
        if normalized.startswith("nightwatch."):
            return "operator_action_result"
        if normalized.startswith("assistant."):
            return "operator_action_result"
        if normalized.startswith("earthglass."):
            return "adapter_health_observation"
        if normalized.startswith("blackforge."):
            return "operator_action_result"
        return "operator_action_result"
