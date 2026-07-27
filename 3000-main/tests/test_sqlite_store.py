import json

from three_thousand.storage.sqlite_store import SQLiteStore


def test_add_and_list_events(tmp_path) -> None:
    db_path = tmp_path / "events.sqlite3"
    store = SQLiteStore(db_path)
    store.initialize()

    event_id = store.add_event(
        event_type="motion_detected",
        confidence=0.42,
        snapshot_path="data/snapshots/test.jpg",
        metadata={"camera_index": 0},
    )
    events = store.list_events(limit=5)

    assert event_id > 0
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "motion_detected"
    assert event.confidence == 0.42
    assert event.snapshot_path == "data/snapshots/test.jpg"
    assert json.loads(event.metadata_json)["camera_index"] == 0
