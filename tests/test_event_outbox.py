from blackfong_ecosystem.contracts.event_v1 import EventEnvelope
from blackfong_ecosystem.event_ingestion import EventV1Ingestor
from blackfong_ecosystem.event_queue import EventQueue
from blackfong_ecosystem.storage import EventStore
import pytest


def build(root):
    ingestor = EventV1Ingestor(
        EventStore(root / "events.db"), EventQueue(root / "queue.db")
    )
    ingestor.init_db()
    return ingestor


def test_duplicate_is_one_event_and_one_queue_item(tmp_path):
    service = build(tmp_path)
    envelope = EventEnvelope(
        event_type="earthglass.observations.updated",
        source="earthglass",
        payload={"count": 1},
    )
    first = service.ingest(envelope)
    second = service.ingest(envelope)
    assert first.legacy_event.id == second.legacy_event.id
    assert first.queue_id == second.queue_id
    assert len(service.event_store.list_events(limit=20)) == 1
    assert service.event_queue.dequeue(20) == [first.legacy_event.id]


def test_delivery_recovers_after_restart(tmp_path, monkeypatch):
    service = build(tmp_path)
    original = service.event_queue.enqueue_once
    monkeypatch.setattr(
        service.event_queue,
        "enqueue_once",
        lambda _: (_ for _ in ()).throw(OSError("offline")),
    )
    envelope = EventEnvelope(
        event_type="earthglass.observations.updated", source="earthglass", payload={}
    )
    first = service.ingest(envelope)
    assert first.queue_id is None
    assert service._outbox.status()["pending"] == 1
    restarted = build(tmp_path)
    second = restarted.ingest(envelope)
    assert first.legacy_event.id == second.legacy_event.id
    assert restarted._outbox.status()["pending"] == 0


def test_id_reuse_with_different_payload_rejected(tmp_path):
    service = build(tmp_path)
    first = EventEnvelope(
        event_type="earthglass.observations.updated", source="earthglass", payload={}
    )
    service.ingest(first)
    conflicting = {**first.to_dict(), "payload": {"changed": True}}
    with pytest.raises(ValueError, match="different contents"):
        service.ingest(conflicting)
