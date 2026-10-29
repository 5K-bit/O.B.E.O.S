"""Atomic canonical-envelope receipts and a durable queue-delivery outbox."""

from __future__ import annotations

import json
from sqlalchemy import text

from .storage import EventModel


class EventOutbox:
    def __init__(self, store):
        self.store = store
        with store.engine.begin() as conn:
            conn.execute(
                text("""CREATE TABLE IF NOT EXISTS event_receipts (
                envelope_id TEXT PRIMARY KEY, legacy_event_id TEXT NOT NULL UNIQUE,
                envelope_json TEXT NOT NULL)""")
            )
            conn.execute(
                text("""CREATE TABLE IF NOT EXISTS event_delivery_outbox (
                event_id TEXT PRIMARY KEY, delivered INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)""")
            )

    def persist(self, envelope, event):
        encoded = json.dumps(envelope.to_dict(), sort_keys=True, separators=(",", ":"))
        with self.store.engine.connect() as conn:
            conn.execute(text("BEGIN IMMEDIATE"))
            try:
                existing = conn.execute(
                    text(
                        "SELECT legacy_event_id,envelope_json FROM event_receipts WHERE envelope_id=:id"
                    ),
                    {"id": envelope.event_id},
                ).first()
                if existing:
                    if existing.envelope_json != encoded:
                        raise ValueError(
                            "Event ID already exists with different contents"
                        )
                    conn.commit()
                    return self.store.get_event_by_id(existing.legacy_event_id)
                metadata = event.metadata or {}
                conn.execute(
                    EventModel.__table__.insert().values(
                        id=event.id,
                        timestamp=event.timestamp,
                        source=event.source,
                        type=event.type,
                        payload=event.payload,
                        confidence=event.confidence,
                        event_metadata=metadata,
                        session_id=metadata.get("session_id"),
                        request_id=metadata.get("request_id"),
                        correlation_id=metadata.get("correlation_id"),
                        hash=event.hash,
                    )
                )
                conn.execute(
                    text("INSERT INTO event_receipts VALUES (:id,:legacy,:payload)"),
                    {"id": envelope.event_id, "legacy": event.id, "payload": encoded},
                )
                conn.execute(
                    text("INSERT INTO event_delivery_outbox(event_id) VALUES (:id)"),
                    {"id": event.id},
                )
                conn.commit()
                return event
            except Exception:
                conn.rollback()
                raise

    def flush(self, queue, limit=100):
        with self.store.engine.connect() as conn:
            pending = (
                conn.execute(
                    text(
                        "SELECT event_id FROM event_delivery_outbox WHERE delivered=0 ORDER BY rowid LIMIT :limit"
                    ),
                    {"limit": max(1, min(limit, 1000))},
                )
                .scalars()
                .all()
            )
        delivered = {}
        for event_id in pending:
            try:
                queue_id = (
                    queue.enqueue_once(event_id)
                    if hasattr(queue, "enqueue_once")
                    else queue.enqueue(event_id)
                )
            except Exception as exc:
                with self.store.engine.begin() as conn:
                    conn.execute(
                        text(
                            "UPDATE event_delivery_outbox SET attempts=attempts+1,last_error=:error WHERE event_id=:id"
                        ),
                        {"id": event_id, "error": type(exc).__name__},
                    )
                continue
            with self.store.engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE event_delivery_outbox SET delivered=1,attempts=attempts+1,last_error=NULL WHERE event_id=:id"
                    ),
                    {"id": event_id},
                )
            delivered[event_id] = queue_id
        return delivered

    def status(self):
        with self.store.engine.connect() as conn:
            return dict(
                conn.execute(
                    text(
                        "SELECT COUNT(*) AS total, COALESCE(SUM(delivered=0),0) AS pending FROM event_delivery_outbox"
                    )
                )
                .mappings()
                .one()
            )
