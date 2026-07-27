from __future__ import annotations

import time
from pathlib import Path

from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.daise import DAISE
from blackfong_ecosystem.events import EVENT_TYPES, Event
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.policy_engine import PolicyEngine
from blackfong_ecosystem.storage import EventStore, NullVectorStore
from blackfong_ecosystem.system_awareness import SystemAwarenessService


class FakeTinyProgrammer:
    def get_status(self):
        return {"online": False, "mode": "offline_snapshot"}


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


def stabilize_awareness(service: SystemAwarenessService, monkeypatch):
    monkeypatch.setattr(
        service,
        "observe_backend",
        lambda *, backend_bootstrap=None: {
            "schema_version": "1.0",
            "provider": "ollama",
            "status": "available",
            "configured_model": "llama3.2:3b",
            "model_available": True,
            "bootstrap": backend_bootstrap or {},
            "provenance": {"source": "test", "origin_service": "system_awareness", "schema_version": "1.0"},
        },
    )
    monkeypatch.setattr(
        service,
        "_probe_audio_devices",
        lambda: {"status": "available", "input_count": 1, "output_count": 1, "capture_started": False},
    )
    monkeypatch.setattr(service, "_module_available", lambda name: name in {"sounddevice", "speech_recognition", "pyttsx3", "cv2"})


def test_phase57_event_types_registered():
    for event_type in {
        "awareness_snapshot",
        "performance_snapshot",
        "sensor_capability_snapshot",
        "queue_pressure_observation",
        "consistency_conflict",
        "request_outcome",
        "agent_trust_update",
        "policy_decision",
        "capability_snapshot",
    }:
        assert event_type in EVENT_TYPES


def test_policy_precedence_safety_beats_convenience():
    engine = PolicyEngine()
    decision = engine.evaluate(
        "request",
        {
            "text": "please turn on the webcam",
            "active_capture_requested": True,
            "approval_granted": True,
        },
    ).to_dict()

    assert decision["decision_state"] == "clarify"
    assert decision["winning_policy"]["category"] == "safety"
    assert "capture_requires_explicit_mode" in decision["risk_flags"]


def test_policy_unknown_and_schema_compatibility():
    engine = PolicyEngine()

    unknown = engine.evaluate("request", {"insufficient_evidence": True}).to_dict()
    assert unknown["decision_state"] == "unknown"
    assert unknown["approved"] is False

    supported = engine.check_schema_compatibility("awareness_snapshot", "1.0")
    incompatible = engine.check_schema_compatibility("awareness_snapshot", "2.0")
    missing = engine.check_schema_compatibility("awareness_snapshot", None)

    assert supported["compatibility_status"] == "supported"
    assert incompatible["compatibility_status"] == "incompatible"
    assert missing["compatibility_status"] == "unknown"


def test_awareness_snapshot_shape_sensors_and_events(tmp_path: Path, monkeypatch):
    memory = build_memory(tmp_path)
    service = SystemAwarenessService(
        memory_service=memory,
        tinyprogrammer=FakeTinyProgrammer(),
        emit_min_interval=0,
    )
    stabilize_awareness(service, monkeypatch)

    snapshot = service.build_snapshot(
        request_states={"r1": {"status": "pending", "started_at": time.time() - 1}},
        request_threads={},
        backend_bootstrap={"status": "ready"},
        voice_status={"active": False, "message": "idle"},
        emit=True,
    )

    assert snapshot["schema_version"] == "1.0"
    assert set(["observed", "inferred", "confidence", "freshness", "health", "provenance", "policy", "compatibility", "sensors", "diagnostic_summary"]).issubset(snapshot)
    assert snapshot["sensors"]["microphone"]["can_hear_state"] == "available_if_enabled"
    assert snapshot["sensors"]["camera"]["active_capture"] is False
    assert snapshot["observed"]["queue"]["pending_requests"] == 1
    assert snapshot["diagnostic_summary"]["top_bottleneck"] == "queue_pressure"

    assert memory.event_store.count_events(type="awareness_snapshot") == 1
    assert memory.event_store.count_events(type="sensor_capability_snapshot") == 1
    assert memory.event_store.count_events(type="queue_pressure_observation") == 1


def test_contradiction_detection_degrades_health(tmp_path: Path, monkeypatch):
    memory = build_memory(tmp_path)
    service = SystemAwarenessService(memory_service=memory, tinyprogrammer=FakeTinyProgrammer(), emit_min_interval=0)
    stabilize_awareness(service, monkeypatch)
    monkeypatch.setattr(
        service,
        "observe_backend",
        lambda *, backend_bootstrap=None: {
            "schema_version": "1.0",
            "provider": "ollama",
            "status": "unavailable",
            "configured_model": "llama3.2:3b",
            "model_available": False,
            "bootstrap": backend_bootstrap or {},
            "error": "connection refused",
        },
    )

    snapshot = service.build_snapshot(backend_bootstrap={"status": "ready"}, emit=True)

    assert snapshot["health"]["overall"] == "degraded"
    assert snapshot["health"]["contradictions"]
    assert memory.event_store.count_events(type="consistency_conflict") == 1


def test_hud_awareness_endpoints_and_commands(tmp_path: Path, monkeypatch):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(
        host="127.0.0.1",
        port=8123,
        memory_service=memory,
        orchestrator_instance=None,
    )
    dashboard.tinyprogrammer = FakeTinyProgrammer()
    dashboard.awareness_service.tinyprogrammer = dashboard.tinyprogrammer
    stabilize_awareness(dashboard.awareness_service, monkeypatch)
    client = dashboard.app.test_client()

    awareness = client.get("/api/awareness").get_json()
    sensors = client.get("/api/sensors").get_json()
    diagnose = client.get("/api/diagnose").get_json()
    capabilities = client.get("/api/capabilities").get_json()
    why = client.get("/api/why?topic=sensors").get_json()

    assert awareness["health"]["overall"] in {"healthy", "partial"}
    assert sensors["sensors"]["speaker"]["can_speak_state"] == "ready"
    assert "top_recommendation" in diagnose["diagnostic_summary"]
    assert capabilities["source_of_truth"] == "capability_registry"
    assert "capability/readiness" in why["answer"]

    command = client.post("/api/commands/execute", json={"command": "/diagnose"}).get_json()
    limits = client.post("/api/commands/execute", json={"command": "/limits"}).get_json()

    assert command["status"] == "ok"
    assert "diagnostic_summary" in command
    assert limits["policy"]["precedence"][0]["category"] == "safety"


def test_agent_trust_uses_confidence_decay(tmp_path: Path):
    memory = build_memory(tmp_path)
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        memory_service=memory,
    )
    old_ms = int((time.time() - 90 * 24 * 60 * 60) * 1000)
    now_ms = int(time.time() * 1000)

    for _ in range(10):
        memory.event_store.add_event(
            Event.new(
                source="daise",
                type="agent_selection",
                payload={"selected_agent": "jarvis", "task_type": "general_conversation", "outcome_success": False},
                confidence=0.5,
                timestamp_ms=old_ms,
            )
        )
    for _ in range(10):
        memory.event_store.add_event(
            Event.new(
                source="daise",
                type="agent_selection",
                payload={"selected_agent": "jarvis", "task_type": "general_conversation", "outcome_success": True},
                confidence=0.9,
                timestamp_ms=now_ms,
            )
        )

    trust = daise.get_agent_trust_score("jarvis", "general_conversation")

    assert trust > 0.7
