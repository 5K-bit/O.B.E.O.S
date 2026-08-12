from __future__ import annotations
from pathlib import Path

from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError

from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.daise import DAISE
from blackfong_ecosystem.events import Event
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.storage import EventStore, NullVectorStore
from blackfong_ecosystem.tinyprogrammer_companion import TinyProgrammerCompanion


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


class FakeCodingAgent:
    def process(self, user_input, context=None):
        return {
            "response": "CODINGAGENT: Review the file workflow before applying changes.",
            "confidence": 0.91,
            "trace_event_id": "coding-trace",
            "tools_invoked": [],
            "provider": "ollama",
            "model": "deepseek-r1:8b",
            "backend_error": None,
            "backend_metadata": {},
            "agent": "coding",
            "task_type": "code_review",
        }


def test_daise_remains_visible_speaker_when_specialist_is_selected(tmp_path: Path):
    memory = build_memory(tmp_path)
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="Please review this code path carefully.",
        agents={"coding": FakeCodingAgent()},
        context={
            "request_id": "operator-hardening-1",
            "session_id": "session-1",
            "analysis_mode": "quick",
            "agent_override": "coding",
        },
    )

    assert result["agent"] == "daise"
    assert result["output_agent"] == "daise"
    assert result["selected_agent"] == "coding"
    assert "CODINGAGENT:" not in result["response"]
    assert result["response"].startswith("Review the file workflow")


def test_operator_action_routes_persist_notification_pin_and_cancel(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    client = dashboard.app.test_client()
    session_id = memory.create_session_id()

    query = memory.persist_user_turn(session_id=session_id, correlation_id=memory.create_correlation_id(), text="Remember this system note.")
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I will keep that note available for pinning.",
        confidence=0.93,
        parent_event_id=query.id,
    )

    notification = client.post(
        "/api/notifications/test",
        json={"desktop": False, "session_id": session_id, "request_id": "notif-1"},
    ).get_json()
    notification_id = notification["notification"]["id"]
    read_result = client.post(
        f"/api/notifications/{notification_id}/read",
        json={"session_id": session_id, "request_id": "notif-2"},
    ).get_json()
    pin_result = client.post(
        f"/api/session/{session_id}/pin",
        json={"request_id": "pin-1"},
    ).get_json()

    pending = dashboard.operator_actions.create_intent(
        action="tiny_launch",
        session_id=session_id,
        request_id="cancel-1",
        target="tinyprogrammer",
        human_reason="Pending Tiny launch.",
    )
    cancelled = client.post(
        f"/api/operator/actions/{pending['intent_id']}/cancel",
        json={"session_id": session_id, "request_id": "cancel-1"},
    ).get_json()
    actions = client.get(f"/api/operator/actions?session_id={session_id}").get_json()

    assert notification["action_status"] == "completed"
    assert read_result["action_status"] == "completed"
    assert pin_result["action_status"] == "completed"
    assert cancelled["status"] == "cancelled"
    assert any(action["action"] == "notification_test" for action in actions["actions"])
    assert any(action["action"] == "notification_read" for action in actions["actions"])
    assert any(action["action"] == "pin_session_memory" for action in actions["actions"])
    assert any(action["status"] == "cancelled" for action in actions["actions"])


def test_operator_actions_route_degrades_when_event_store_is_busy(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    client = dashboard.app.test_client()

    def busy_list_actions(*args, **kwargs):
        raise RuntimeError("database is locked")

    dashboard.operator_actions.list_actions = busy_list_actions
    response = client.get("/api/operator/actions")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["status"] == "degraded"
    assert payload["pending_actions"] == 0
    assert payload["reason_code"] == "event_store_temporarily_unavailable"


def test_operator_summary_materializes_expired_actions_without_persisting_new_events(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    client = dashboard.app.test_client()
    session_id = memory.create_session_id()

    expired = dashboard.operator_actions.create_intent(
        action="tiny_log",
        session_id=session_id,
        request_id="expired-summary-1",
        human_reason="This should expire quickly.",
        ttl_seconds=-1.0,
    )

    expired_events_before = len(memory.event_store.list_events_by_type("operator_intent_expired", limit=20))
    payload = client.get(f"/api/operator/summary?session_id={session_id}").get_json()
    expired_events_after = len(memory.event_store.list_events_by_type("operator_intent_expired", limit=20))

    assert expired_events_after == expired_events_before
    assert any(
        item["intent_id"] == expired["intent_id"] and item["status"] == "expired"
        for item in payload["actions"]["items"]
    )


def test_event_store_retries_locked_event_insert(tmp_path: Path, monkeypatch):
    store = EventStore(tmp_path / "events.db")
    store.init_db()
    attempts = {"count": 0}

    class FlakySession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add(self, model):
            self.model = model

        def commit(self):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise SQLAlchemyOperationalError("INSERT INTO events", {}, Exception("database is locked"))

    monkeypatch.setattr(store, "session", lambda: FlakySession())

    store.add_event(
        Event.new(
            source="blackfong",
            type="operator_intent_state",
            payload={"status": "testing"},
            request_id="retry-1",
        )
    )

    assert attempts["count"] == 2


def test_whatchanged_reports_recent_tiny_and_file_transitions(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    client = dashboard.app.test_client()
    session_id = memory.create_session_id()

    memory.persist_workflow_event(
        source="tinyprogrammer",
        event_type="tinyprogrammer_status",
        payload={"state": "offline", "online": False},
        session_id=session_id,
        confidence=0.9,
        tags=["tinyprogrammer"],
    )
    memory.persist_workflow_event(
        source="blackfong",
        event_type="file_workflow_state",
        payload={"state": "proposed", "path": str(tmp_path / "sample.txt")},
        session_id=session_id,
        confidence=0.9,
        tags=["file_workflow"],
    )

    response = client.get(f"/api/whatchanged?session_id={session_id}").get_json()

    assert response["counts"]["tinyprogrammer"] >= 1
    assert response["counts"]["file_workflow"] >= 1
    assert "Recent changes:" in response["summary_text"]


def test_tinyprogrammer_status_degrades_when_event_store_is_busy(tmp_path: Path):
    memory = build_memory(tmp_path)
    project = tmp_path / "TinyProgrammer-main"
    project.mkdir()
    companion = TinyProgrammerCompanion(
        memory_service=memory,
        base_url="http://127.0.0.1:8765",
        project_path=project,
        timeout=0.01,
    )

    def busy_sync(status):
        raise RuntimeError("database is locked")

    companion._sync_status_event = busy_sync
    status = companion.get_status(probe_live=False)

    assert status["state"] == "offline"
    assert status["status_sync"] == "deferred"
    assert status["status_sync_reason"] == "event_store_temporarily_unavailable"


def test_tinyprogrammer_bbs_reason_codes_and_stream_launch_env(monkeypatch, tmp_path: Path):
    import blackfong_ecosystem.tinyprogrammer_companion as tiny_module

    memory = build_memory(tmp_path)
    project = tmp_path / "TinyProgrammer-main"
    project.mkdir()
    (project / "main.py").write_text("print('tiny')\n", encoding="utf-8")
    companion = TinyProgrammerCompanion(
        memory_service=memory,
        base_url="http://127.0.0.1:8765",
        project_path=project,
        timeout=0.01,
    )
    companion.launch_timeout = 0.05

    bbs = companion.get_bbs(limit=5)
    popen_calls = []
    status_calls = {"count": 0}

    class FakePopen:
        pid = 5150

        def __init__(self, *args, **kwargs):
            popen_calls.append(kwargs["env"])

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    def fake_fetch_live_status():
        status_calls["count"] += 1
        if status_calls["count"] < 2:
            return None
        return {"state": "running", "online": True, "stream_enabled": True}

    monkeypatch.setattr(tiny_module.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(companion, "_fetch_live_status", fake_fetch_live_status)

    launched = companion.launch(consent={"approved": True, "level": 1}, open_browser=False, enable_stream=True)

    assert bbs["bbs_reason"] in {"unconfigured_bbs", "offline_unreachable", "brain_not_initialized"}
    assert launched["success"] is True
    assert popen_calls[0]["WEB_STREAM_ENABLED"] == "true"


def test_file_scan_route_returns_operator_ownership_metadata(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    dashboard.file_workflow.allowed_roots = [tmp_path.resolve()]
    dashboard.file_workflow.denied_roots = []
    client = dashboard.app.test_client()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("offline-first\n", encoding="utf-8")

    result = client.post(
        "/api/files/scan",
        json={"path": str(workspace), "session_id": "files-1", "request_id": "scan-1"},
    ).get_json()

    assert result["success"] is True
    assert result["action_status"] == "completed"
    assert result["owned_by"] == "daise"
    assert result["governed_by"] == "blackfong"
    assert any(entry["name"] == "notes.txt" for entry in result["entries"])
