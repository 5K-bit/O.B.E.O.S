from blackfong_ecosystem.events import Event
from blackfong_ecosystem.storage import EventStore


def _store(tmp_path):
    store = EventStore(db_path=tmp_path / "events.db")
    store.init_db()
    return store


def test_list_recent_sessions_summarizes_messages_agents_and_preview(tmp_path):
    store = _store(tmp_path)
    store.add_event(Event.new(source="user", type="agent_query", payload={"text": "hello there"}, session_id="s1", timestamp_ms=1000))
    store.add_event(Event.new(source="daise", type="agent_response", payload={"response": "hi back"}, session_id="s1", timestamp_ms=2000))
    store.add_event(Event.new(source="user", type="agent_query", payload={"text": "x" * 300}, session_id="s2", timestamp_ms=500))

    sessions = {item["session_id"]: item for item in store.list_recent_sessions()}

    assert set(sessions) == {"s1", "s2"}
    first = sessions["s1"]
    assert first["event_count"] == 2
    assert first["message_count"] == 2
    assert "daise" in first["agents"]
    assert first["preview"] == "hi back"  # newest message first
    assert first["start_time"] == 1.0 and first["last_activity"] == 2.0
    assert len(sessions["s2"]["preview"]) == 120


def test_list_recent_sessions_orders_by_last_activity_and_respects_limit(tmp_path):
    store = _store(tmp_path)
    store.add_event(Event.new(source="user", type="agent_query", payload={"text": "old"}, session_id="old", timestamp_ms=1000))
    store.add_event(Event.new(source="user", type="agent_query", payload={"text": "new"}, session_id="new", timestamp_ms=9000))

    assert [item["session_id"] for item in store.list_recent_sessions()] == ["new", "old"]
    assert [item["session_id"] for item in store.list_recent_sessions(limit=1)] == ["new"]


def test_init_db_runs_full_migration_once_per_database_file(tmp_path, monkeypatch):
    store = _store(tmp_path)
    calls = []
    monkeypatch.setattr(EventStore, "_init_db_full", lambda self: calls.append(1))

    store.init_db()
    store.init_db()

    assert calls == []  # already initialized in this process


def test_init_db_reinitializes_when_database_file_is_gone(tmp_path):
    store = _store(tmp_path)
    store.engine.dispose()
    (tmp_path / "events.db").unlink()
    (tmp_path / "events.db-wal").unlink(missing_ok=True)
    (tmp_path / "events.db-shm").unlink(missing_ok=True)

    store.init_db()

    assert (tmp_path / "events.db").exists()
    store.add_event(Event.new(source="user", type="agent_query", payload={"text": "works"}, session_id="s", timestamp_ms=1))
    assert store.list_recent_sessions()[0]["session_id"] == "s"
