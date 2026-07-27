from __future__ import annotations

import time
from functools import partial

from click.testing import CliRunner

from blackfong_ecosystem.agents.jarvis_agent import JARVISAgent
from blackfong_ecosystem.chat_backends import HeuristicChatBackend
from blackfong_ecosystem.cli import cli
from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.operational_interfaces import AdminControls, SessionExplorer, WebDashboard
from blackfong_ecosystem.storage import EventStore, NullVectorStore


def wait_for_request(client, request_id: str, timeout_s: float = 2.0):
    deadline = time.time() + timeout_s
    latest = None
    while time.time() < deadline:
        latest = client.get(f"/api/request/{request_id}/trace").get_json()
        if latest and latest.get("status") == "complete":
            return latest
        time.sleep(0.05)
    raise AssertionError(f"request {request_id} did not complete: {latest}")


class StubUnifiedOrchestrator:
    def __init__(self, memory_service: ConversationMemoryService):
        self.memory_service = memory_service
        self._voice_active = False

    def process_input(self, user_input: str, modality, context=None):
        context = dict(context or {})
        session_id = context["session_id"]
        correlation_id = context.get("correlation_id") or self.memory_service.create_correlation_id()
        query_event = self.memory_service.persist_user_turn(
            session_id=session_id,
            correlation_id=correlation_id,
            text=user_input,
            routed_agent=context.get("agent_override") or "jarvis",
            analysis_mode=context.get("analysis_mode", "quick"),
        )
        memory_context = self.memory_service.build_context(
            session_id=session_id,
            query=user_input,
            agent_name=context.get("agent_override") or "jarvis",
        )
        remembered_green = "green" in str(memory_context).lower()
        response = "Your favorite color is green." if "favorite color" in user_input.lower() and remembered_green else "I'll remember that your favorite color is green."
        result = {
            "agent": "jarvis",
            "selected_agent": "jarvis",
            "response": response,
            "confidence": 0.9,
            "provider": "heuristic",
            "model": "stub",
            "timestamp": time.time(),
        }
        response_event = self.memory_service.persist_agent_response(
            session_id=session_id,
            correlation_id=correlation_id,
            agent_name=result["agent"],
            response_text=result["response"],
            confidence=result["confidence"],
            parent_event_id=query_event.id,
            provider=result.get("provider"),
            model=result.get("model"),
        )
        summary_event = self.memory_service.maybe_create_session_summary(session_id, correlation_id=correlation_id, force=True)
        result["session_id"] = session_id
        result["correlation_id"] = correlation_id
        result["query_event_id"] = query_event.id
        result["response_event_id"] = response_event.id
        result["summary_event_id"] = summary_event.id if summary_event else None
        return result

    def start_voice_loop(self):
        self._voice_active = True
        return True

    def stop_voice_loop(self):
        self._voice_active = False
        return True

    def get_voice_loop_status(self):
        return {"active": self._voice_active, "message": "stubbed"}


def build_memory_service(tmp_path) -> ConversationMemoryService:
    return ConversationMemoryService(
        event_store=EventStore(tmp_path / "events.db"),
        vector_store=NullVectorStore(),
    )


def test_jarvis_recalls_session_memory_after_reopen(monkeypatch, tmp_path):
    service = build_memory_service(tmp_path)
    monkeypatch.setattr("blackfong_ecosystem.agent.EventStore", partial(EventStore, tmp_path / "agent-events.db"))

    session_id = service.create_session_id()
    first_corr = service.create_correlation_id()
    service.persist_user_turn(
        session_id=session_id,
        correlation_id=first_corr,
        text="My favorite color is green.",
    )
    service.persist_agent_response(
        session_id=session_id,
        correlation_id=first_corr,
        agent_name="jarvis",
        response_text="I'll remember that your favorite color is green.",
        confidence=0.9,
    )

    first_agent = JARVISAgent(chat_backend=HeuristicChatBackend(), fallback_backend=HeuristicChatBackend())
    first_result = first_agent.process(
        "What is my favorite color?",
        context={
            "session_id": session_id,
            "correlation_id": service.create_correlation_id(),
            "memory_context": service.build_context(
                session_id=session_id,
                query="What is my favorite color?",
                agent_name="jarvis",
            ),
        },
    )
    assert "green" in first_result["response"].lower()

    reopened_agent = JARVISAgent(chat_backend=HeuristicChatBackend(), fallback_backend=HeuristicChatBackend())
    reopened_result = reopened_agent.process(
        "What is my favorite color?",
        context={
            "session_id": session_id,
            "correlation_id": service.create_correlation_id(),
            "memory_context": service.build_context(
                session_id=session_id,
                query="What is my favorite color?",
                agent_name="jarvis",
            ),
        },
    )
    assert "green" in reopened_result["response"].lower()


def test_hud_chat_sessions_pin_and_commands(monkeypatch, tmp_path):
    service = build_memory_service(tmp_path)
    monkeypatch.setattr("blackfong_ecosystem.agent.EventStore", partial(EventStore, tmp_path / "agent-events.db"))
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=service, orchestrator_instance=StubUnifiedOrchestrator(service))
    SessionExplorer(dashboard)
    AdminControls(dashboard)
    client = dashboard.app.test_client()

    first = client.post("/api/chat", json={"message": "My favorite color is green.", "request_id": "hud-unify-1"})
    assert first.status_code == 202
    session_id = first.get_json()["session_id"]
    wait_for_request(client, "hud-unify-1")

    second = client.post("/api/chat", json={"session_id": session_id, "message": "What is my favorite color?", "request_id": "hud-unify-2"})
    assert second.status_code == 202
    second_trace = wait_for_request(client, "hud-unify-2")
    assert "green" in second_trace["result"]["response"].lower()

    sessions = client.get("/api/sessions").get_json()
    assert any(session["session_id"] == session_id for session in sessions["sessions"])

    messages = client.get(f"/api/session/{session_id}/messages").get_json()
    assert messages["count"] >= 4

    pinned = client.post(f"/api/session/{session_id}/pin", json={}).get_json()
    assert pinned["status"] == "pinned"

    memories = client.get("/api/memory").get_json()["memories"]
    assert any(memory["memory_type"] == "pinned_memory" for memory in memories)

    commands = client.get("/api/commands").get_json()["commands"]
    assert any(command["command"] == "/status" for command in commands)

    status = client.post("/api/commands/execute", json={"command": "/status", "session_id": session_id}).get_json()
    assert status["status"] == "ok"


def test_cli_help_exposes_hud_and_event_metadata_flags():
    runner = CliRunner()

    hud_help = runner.invoke(cli, ["hud", "--help"])
    assert hud_help.exit_code == 0
    assert "--open-browser / --no-open-browser" in hud_help.output

    event_help = runner.invoke(cli, ["event-log", "--help"])
    assert event_help.exit_code == 0
    assert "--text" in event_help.output
    assert "--payload-file" in event_help.output
    assert "--session-id" in event_help.output
    assert "--correlation-id" in event_help.output
    assert "--tag" in event_help.output
