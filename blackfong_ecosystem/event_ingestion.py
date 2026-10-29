from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
import threading

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
        self._outbox = None
        self._delivery_stop = threading.Event()
        self._delivery_thread = None
        self.delivery_error = None

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
        if hasattr(self.event_store, "engine"):
            from .event_outbox import EventOutbox
            if self._outbox is None:
                self.init_db()
                self._outbox = EventOutbox(self.event_store)
            legacy_event = self._outbox.persist(envelope, legacy_event)
            delivered = self._outbox.flush(self.event_queue)
            queue_id = delivered.get(legacy_event.id)
            if queue_id is None and hasattr(self.event_queue, "enqueue_once"):
                # A previous successful delivery has already been acknowledged.
                with self.event_store.engine.connect() as conn:
                    from sqlalchemy import text
                    complete = conn.execute(text("SELECT delivered FROM event_delivery_outbox WHERE event_id=:id"), {"id": legacy_event.id}).scalar()
                if complete:
                    queue_id = self.event_queue.enqueue_once(legacy_event.id)
        else:
            # Compatibility for in-memory adapters; durable stores use the outbox.
            self.event_store.add_event(legacy_event)
            queue_id = self.event_queue.enqueue(legacy_event.id)
        return IngestedEvent(envelope=envelope, legacy_event=legacy_event, queue_id=queue_id)

    def start_delivery(self):
        if not hasattr(self.event_store, "engine"):
            return
        if self._delivery_thread and self._delivery_thread.is_alive():
            return
        from .event_outbox import EventOutbox
        self.init_db()
        if self._outbox is None:
            self._outbox = EventOutbox(self.event_store)
        self._delivery_stop.clear()
        def drain():
            while not self._delivery_stop.wait(1):
                try:
                    self._outbox.flush(self.event_queue)
                    self.delivery_error = None
                except Exception as exc:
                    self.delivery_error = type(exc).__name__
        self._delivery_thread = threading.Thread(target=drain, name="obeos-event-outbox", daemon=True)
        self._delivery_thread.start()

    def flush_delivery(self):
        """Drain persisted-but-undelivered events; a no-op when delivery never started."""
        if self._outbox is None:
            return 0
        return self._outbox.flush(self.event_queue)

    def stop_delivery(self):
        self._delivery_stop.set()
        if self._delivery_thread:
            self._delivery_thread.join(timeout=3)

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
            "earthglass": "earthglass",
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
            return "spatial_observation_batch"
        if normalized.startswith("blackforge."):
            return "operator_action_result"
        return "operator_action_result"
