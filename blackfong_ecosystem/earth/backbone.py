"""Spatial batches reference authoritative history instead of duplicating every track in memory."""

from __future__ import annotations
import hashlib
import json
from ..contracts.event_v1 import EventEnvelope


class EarthBackbone:
    def __init__(self, *, event_store=None, event_queue=None):
        from ..event_ingestion import EventV1Ingestor

        self.ingestor = EventV1Ingestor(event_store, event_queue)
        self.ingestor.start_delivery()

    def publish(self, changes):
        evidence = [
            {
                "entity_id": c["observation"]["entity_id"],
                "sequence": c["sequence"],
                "layer": c["observation"]["layer"],
                "observed_at": c["observation"]["observed_at"],
                "provider": c["observation"]["provenance"]["provider"],
            }
            for c in changes
        ]
        if not evidence:
            return
        ident = hashlib.sha256(
            json.dumps(evidence, sort_keys=True).encode()
        ).hexdigest()
        envelope = EventEnvelope(
            event_type="earthglass.observations.updated",
            source="earthglass",
            event_id="earth-" + ident,
            timestamp=max(e["observed_at"] for e in evidence),
            payload={
                "schema_version": "1.0",
                "evidence_references": evidence,
                "count": len(evidence),
                "coalesced": True,
                "history_owner": "EarthGlass",
            },
        )
        self.ingestor.ingest(envelope)

    def publish_resync(self, snapshot):
        from datetime import datetime, timezone

        sequence = snapshot["sequence"]
        items = snapshot["observations"]
        timestamp = (
            items[0]["received_at"] if items else datetime.now(timezone.utc).isoformat()
        )
        envelope = EventEnvelope(
            event_type="earthglass.snapshot.required",
            source="earthglass",
            event_id="earth-resync-"
            + hashlib.sha256((str(sequence) + timestamp).encode()).hexdigest(),
            timestamp=timestamp,
            payload={
                "schema_version": "1.0",
                "sequence": sequence,
                "history_owner": "EarthGlass",
                "reason": "bounded delivery backlog overflow",
                "snapshot_query": "/api/v2/observations",
            },
        )
        self.ingestor.ingest(envelope)

    def flush(self):
        self.ingestor.flush_delivery()

    def related(self, entity_id, limit):
        from sqlalchemy import text

        store = self.ingestor.event_store
        if not hasattr(store, "engine"):
            return []
        with store.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT envelope_id,envelope_json FROM event_receipts ORDER BY rowid DESC LIMIT 500"
                )
            ).fetchall()
        result = []
        for ident, payload in rows:
            data = json.loads(payload)
            refs = data.get("payload", {}).get("evidence_references", [])
            if any(ref.get("entity_id") == entity_id for ref in refs):
                result.append(
                    {
                        "event_id": ident,
                        "event_type": data["event_type"],
                        "timestamp": data["timestamp"],
                    }
                )
                if len(result) >= limit:
                    break
        return result

    def perception_events(self, limit):
        from sqlalchemy import text

        with self.ingestor.event_store.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT envelope_json FROM event_receipts WHERE json_extract(envelope_json,'$.event_type')='project3000.motion.detected' ORDER BY rowid DESC LIMIT :limit"
                ),
                {"limit": max(1, min(limit, 1000))},
            ).fetchall()
        return [
            data
            for (value,) in rows
            if (data := json.loads(value)).get("event_type")
            == "project3000.motion.detected"
        ]

    def memory_references(self, entity_id, limit):
        result = []
        for kind in ("knowledge_ingested", "session_summary"):
            for event in self.ingestor.event_store.list_events(type=kind, limit=100):
                text_value = json.dumps(event.payload, ensure_ascii=False)
                if entity_id.lower() in text_value.lower():
                    result.append(
                        {
                            "event_id": event.id,
                            "type": event.type,
                            "excerpt": text_value[:240],
                        }
                    )
                    if len(result) >= limit:
                        return result
        return result

    def close(self):
        self.ingestor.stop_delivery()
        self.flush()
        for owner in (self.ingestor.event_store, self.ingestor.event_queue):
            if hasattr(owner, "engine"):
                owner.engine.dispose()
