from __future__ import annotations

import time
from pathlib import Path

from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.device_registry import DeviceRegistryService, LegionAuthService
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.storage import EventStore, NullVectorStore


class EchoLegionOrchestrator:
    def __init__(self, memory_service: ConversationMemoryService):
        self.memory_service = memory_service

    def process_input(self, user_input, modality, context=None):
        context = dict(context or {})
        request_id = str(context.get("request_id") or "legion-request")
        session_id = str(context.get("session_id") or self.memory_service.create_session_id())
        correlation_id = str(context.get("correlation_id") or self.memory_service.create_correlation_id())

        query_event = self.memory_service.persist_user_turn(
            session_id=session_id,
            correlation_id=correlation_id,
            text=user_input,
            modality="text",
            routed_agent="jarvis",
            analysis_mode=str(context.get("analysis_mode") or "standard"),
            agent_override=context.get("agent_override"),
            request_id=request_id,
        )

        self.memory_service.persist_agent_response(
            session_id=session_id,
            correlation_id=correlation_id,
            agent_name="jarvis",
            response_text=f"Echo: {user_input}",
            confidence=0.91,
            modality="text",
            parent_event_id=query_event.id,
            provider="heuristic",
            model="legion-test-model",
            execution_time_ms=80.0,
            success=True,
            extras={
                "selected_agent": "jarvis",
                "consulted_agents": ["jarvis"],
                "synthesized": False,
                "analysis_mode": str(context.get("analysis_mode") or "standard"),
                "task_type": "general_conversation",
                "transport": context.get("transport"),
                "input_origin": context.get("input_origin"),
            },
            request_id=request_id,
        )

        return {
            "response": f"Echo: {user_input}",
            "confidence": 0.91,
            "agent": "jarvis",
            "selected_agent": "jarvis",
            "consulted_agents": ["jarvis"],
            "synthesized": False,
            "provider": "heuristic",
            "model": "legion-test-model",
            "backend_error": None,
            "backend_metadata": {},
            "task_type": "general_conversation",
            "analysis_mode": str(context.get("analysis_mode") or "standard"),
            "request_id": request_id,
            "session_id": session_id,
            "correlation_id": correlation_id,
            "response_event_id": None,
            "event_payload": {
                "selected_agent": "jarvis",
                "provider": "heuristic",
                "model": "legion-test-model",
            },
            "timestamp": time.time(),
        }


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


def build_dashboard(tmp_path: Path, *, orchestrator_instance=None) -> WebDashboard:
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=orchestrator_instance)
    dashboard.device_registry = DeviceRegistryService(
        state_path=tmp_path / "device_registry.json",
        event_store=memory.event_store,
        history_limit=3,
        stale_after_seconds=60,
    )
    dashboard.legion_auth = LegionAuthService(
        dashboard.device_registry,
        token_path=tmp_path / "legion_pairing_token.txt",
        session_ttl_hours=1,
    )
    return dashboard


def pair_client(client, dashboard: WebDashboard):
    token = dashboard.legion_auth.current_pairing_token(create=True)
    response = client.post("/api/legion/pair", json={"token": token, "label": "Test Legion"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "paired"
    return payload


def test_legion_routes_and_assets_render(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()

    html = client.get("/legion").get_data(as_text=True)
    manifest = client.get("/legion/manifest.webmanifest")
    service_worker = client.get("/legion/service-worker.js").get_data(as_text=True)
    css = client.get("/legion-static/app.css").get_data(as_text=True)
    js = client.get("/legion-static/app.js").get_data(as_text=True)

    assert "Legion Cockpit" in html
    assert "/legion-static/app.css" in html
    assert manifest.status_code == 200
    assert "fullscreen" in manifest.get_data(as_text=True)
    assert "CACHE_NAME" in service_worker
    assert "cockpit-grid" in css
    assert "[hidden]" in css
    assert "overflow-wrap" in css
    assert "/api/devices" in js
    assert "/api/legion/session" in js
    assert "hiddenSensorNames" in js
    assert "messages-panel" in html
    combined = "\n".join([html, css]).lower()
    blocked_v1_terms = ["screen streaming", "remote desktop", "vnc", "rdp", "webrtc", "moonlight"]
    assert not any(term in combined for term in blocked_v1_terms)


def test_legion_pairing_protects_device_apis(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()

    denied = client.get("/api/devices")
    assert denied.status_code == 401

    bad_pair = client.post("/api/legion/pair", json={"token": "wrong"})
    assert bad_pair.status_code == 401

    pair_client(client, dashboard)
    devices = client.get("/api/devices").get_json()

    assert devices["schema_version"] == "1.0"
    ids = {device["id"] for device in devices["devices"]}
    assert {"blackcomputer", "sentinel"}.issubset(ids)
    blackcomputer = next(device for device in devices["devices"] if device["id"] == "blackcomputer")
    assert blackcomputer["status"] == "online"


def test_legion_session_endpoint_links_shared_operator_session(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()
    pair_client(client, dashboard)

    payload = client.get("/api/legion/session").get_json()

    assert payload["transport"] == "legion"
    assert payload["surface"] == "legion"
    assert payload["session_id"]
    assert "legion" in payload["linked_surfaces"]


def test_legion_chat_uses_shared_session_and_legion_transport(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(
        host="127.0.0.1",
        port=8099,
        memory_service=memory,
        orchestrator_instance=EchoLegionOrchestrator(memory),
    )
    dashboard.device_registry = DeviceRegistryService(
        state_path=tmp_path / "device_registry.json",
        event_store=memory.event_store,
        history_limit=3,
        stale_after_seconds=60,
    )
    dashboard.legion_auth = LegionAuthService(
        dashboard.device_registry,
        token_path=tmp_path / "legion_pairing_token.txt",
        session_ttl_hours=1,
    )
    client = dashboard.app.test_client()
    pair_client(client, dashboard)

    session_payload = client.get("/api/legion/session").get_json()
    response = client.post(
        "/api/chat",
        json={
            "message": "Carry this through Legion.",
            "background": False,
            "analysis_mode": "standard",
            "transport": "legion",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["transport"] == "legion"
    assert payload["session_id"] == session_payload["session_id"]
    assert payload["request_id"]
    assert payload["messages"][0]["role"] == "user"
    assert payload["messages"][1]["role"] == "assistant"


def test_legion_device_list_uses_cached_blackcomputer_state(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()
    pair_client(client, dashboard)

    def fail_sync():
        raise AssertionError("device list should not block on heavy Blackcomputer sync")

    dashboard._sync_blackcomputer_device = fail_sync  # type: ignore[method-assign]
    response = client.get("/api/devices")

    assert response.status_code == 200
    devices = response.get_json()["devices"]
    blackcomputer = next(device for device in devices if device["id"] == "blackcomputer")
    assert blackcomputer["status"] == "online"


def test_sentinel_report_ingestion_and_rolling_history(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()
    token = dashboard.legion_auth.current_pairing_token(create=True)

    for index in range(5):
        response = client.post(
            "/api/devices/sentinel/report",
            headers={"X-Sentinel-Agent-Token": token},
            json={
                "label": "Sentinel",
                "role": "raspberry_pi_sentinel",
                "status": "online",
                "health": {"overall": "healthy", "cpu_percent": index, "ram_percent": 20 + index},
                "sensors": {"camera": {"available": True, "active": False}},
                "capabilities": ["system_health", "sensor_readiness"],
            },
        )
        assert response.status_code == 200

    pair_client(client, dashboard)
    sentinel = client.get("/api/devices/sentinel").get_json()

    assert sentinel["status"] == "online"
    assert sentinel["health"]["cpu_percent"] == 4
    assert len(sentinel["history"]) == 3
    assert dashboard.event_store.count_events(type="environment_snapshot") >= 5
    assert dashboard.event_store.count_events(type="sensor_capability_snapshot") >= 5


def test_sensor_controls_require_consent_and_emit_audit_events(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()
    pair_client(client, dashboard)

    blocked = client.post("/api/devices/sentinel/sensors/start", json={"mode": "live_vision"})
    assert blocked.status_code == 409
    assert blocked.get_json()["status"] == "requires_consent"

    started = client.post(
        "/api/devices/sentinel/sensors/start",
        json={"mode": "live_vision", "consent": {"approved": True, "level": 1}},
    )
    stopped = client.post(
        "/api/devices/sentinel/sensors/stop",
        json={"mode": "off", "consent": {"approved": True, "level": 1}},
    )

    assert started.status_code == 200
    assert started.get_json()["device"]["sensors"]["camera"]["active"] is True
    assert stopped.status_code == 200
    assert stopped.get_json()["device"]["sensor_mode"] == "off"
    assert dashboard.event_store.count_events(type="operator_consent") >= 2
    assert dashboard.event_store.count_events(type="operator_action_result") >= 2
    assert dashboard.event_store.count_events(type="sentinel_detection_event") >= 2


def test_device_report_rejects_missing_agent_token(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()

    response = client.post("/api/devices/sentinel/report", json={"status": "online"})

    assert response.status_code == 401
    assert response.get_json()["reason_code"] == "device_report_token_required"


def test_frame_device_defaults_and_agent_sensor_control(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()
    token = dashboard.legion_auth.current_pairing_token(create=True)

    pair_client(client, dashboard)
    devices = client.get("/api/devices").get_json()
    frame = next(device for device in devices["devices"] if device["id"] == "frame")

    assert frame["role"] == "wearable_frame"
    assert frame["sensors"]["display"]["available"] is True
    assert "look_and_ask" in frame["capabilities"]

    started = client.post(
        "/api/devices/frame/sensors/start",
        headers={"X-Blackfong-Agent-Token": token},
        json={"mode": "tap_to_talk", "consent": {"approved": True, "level": 1}},
    )
    stopped = client.post(
        "/api/devices/frame/sensors/stop",
        headers={"X-Blackfong-Agent-Token": token},
        json={"mode": "tap_to_talk", "consent": {"approved": True, "level": 1}},
    )

    assert started.status_code == 200
    assert started.get_json()["device"]["sensors"]["microphone"]["active"] is True
    assert stopped.status_code == 200
    assert stopped.get_json()["device"]["sensor_mode"] == "off"
    assert dashboard.event_store.count_events(type="operator_consent") >= 2


def test_frame_event_ingest_requires_token_and_publishes_canonical_event(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()
    token = dashboard.legion_auth.current_pairing_token(create=True)

    denied = client.post("/api/frame/events", json={"event_type": "frame_tap_event", "payload": {"tap_count": 1}})
    accepted = client.post(
        "/api/frame/events",
        headers={"X-Blackfong-Agent-Token": token},
        json={
            "event_type": "frame_tap_event",
            "payload": {"tap_count": 1, "intent": "status_card"},
            "session_id": "frame-session-test",
        },
    )
    rejected = client.post(
        "/api/frame/events",
        headers={"X-Blackfong-Agent-Token": token},
        json={"event_type": "unknown_frame_event", "payload": {}},
    )

    assert denied.status_code == 401
    assert accepted.status_code == 200
    payload = accepted.get_json()
    assert payload["event"]["source"] == "frame"
    assert payload["event"]["type"] == "frame_tap_event"
    assert payload["event"]["metadata"]["session_id"] == "frame-session-test"
    assert dashboard.event_store.count_events(type="frame_tap_event", source="frame") == 1
    assert rejected.status_code == 400


def test_frame_chat_route_uses_operator_chat_with_frame_context(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()
    token = dashboard.legion_auth.current_pairing_token(create=True)

    class StubOperatorChat:
        def __init__(self) -> None:
            self.kwargs = {}

        def send_message(self, **kwargs):
            self.kwargs = kwargs
            return {"status": "complete", "result": {"response": "Frame response"}}

    stub = StubOperatorChat()
    dashboard.operator_chat = stub

    response = client.post(
        "/api/frame/chat",
        headers={"X-Blackfong-Agent-Token": token},
        json={"message": "What am I looking at?", "modality": "vision", "context": {"session_id": "frame-session"}},
    )

    assert response.status_code == 200
    assert response.get_json()["result"]["response"] == "Frame response"
    assert stub.kwargs["analysis_mode"] == "standard"
    assert stub.kwargs["modality"] == "vision"
    assert stub.kwargs["context"]["input_origin"] == "frame"
    assert stub.kwargs["context"]["device_id"] == "frame"
