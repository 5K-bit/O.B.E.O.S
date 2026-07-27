import time
from pathlib import Path

from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.storage import EventStore, NullVectorStore


class SlowFakeOrchestrator:
    def __init__(self, memory_service: ConversationMemoryService):
        self.memory_service = memory_service

    def process_input(self, user_input, modality, context=None):
        context = dict(context or {})
        request_id = str(context.get("request_id") or "fake-request")
        session_id = str(context.get("session_id") or self.memory_service.create_session_id())
        correlation_id = str(context.get("correlation_id") or self.memory_service.create_correlation_id())
        progress_callback = context.get("progress_callback")
        report_callback = context.get("agent_report_callback")
        selection_callback = context.get("agent_selection_callback")

        time.sleep(0.15)
        query_event = self.memory_service.persist_user_turn(
            session_id=session_id,
            correlation_id=correlation_id,
            text=user_input,
            modality="text",
            routed_agent="jarvis",
            analysis_mode=str(context.get("analysis_mode") or "quick"),
            agent_override=context.get("agent_override"),
            request_id=request_id,
        )

        if progress_callback:
            progress_callback(
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "phase": "routing",
                    "agent": "jarvis",
                    "message": "Routing through jarvis.",
                }
            )

        self.memory_service.persist_workflow_event(
            source="daise",
            event_type="routing_decision",
            payload={
                "query": user_input,
                "suggested_agent": "jarvis",
                "candidates": [{"agent": "jarvis", "routing_relevance": 0.85}],
                "routing_confidence": 0.85,
                "complexity_score": 0.22,
                "multi_agent_mode": False,
            },
            confidence=0.85,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
        )

        if progress_callback:
            progress_callback(
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "phase": "agent",
                    "agent": "jarvis",
                    "message": "Consulting jarvis.",
                }
            )

        report = {
            "agent": "jarvis",
            "response": f"Echo: {user_input}",
            "confidence": 0.91,
            "trace_event_id": None,
            "tool_activity_summary": [],
            "provider": "heuristic",
            "model": "fake-jarvis",
            "evidence_summary": "Fake report for HUD test.",
            "task_type": "general_conversation",
            "backend_error": None,
            "backend_metadata": {},
            "routing_relevance": 0.85,
            "trust_score": 0.5,
            "score": 0.83,
        }
        self.memory_service.persist_workflow_event(
            source="daise",
            event_type="agent_report",
            payload={**report, "response_preview": report["response"]},
            confidence=0.91,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
        )
        if report_callback:
            report_callback({"request_id": request_id, "session_id": session_id, "report": report})

        if progress_callback:
            progress_callback(
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "phase": "evaluation",
                    "agent": "daise",
                    "message": "Evaluating specialist reports.",
                }
            )

        selection = {
            "selected_agent": "jarvis",
            "output_agent": "jarvis",
            "synthesized": False,
            "score_gap": 1.0,
            "provider": "heuristic",
            "model": "fake-jarvis",
            "reports": [report],
        }
        self.memory_service.persist_workflow_event(
            source="daise",
            event_type="agent_selection",
            payload=selection,
            confidence=0.91,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
        )
        if selection_callback:
            selection_callback({"request_id": request_id, "session_id": session_id, **selection})

        response_event = self.memory_service.persist_agent_response(
            session_id=session_id,
            correlation_id=correlation_id,
            agent_name="jarvis",
            response_text=f"Echo: {user_input}",
            confidence=0.91,
            modality="text",
            parent_event_id=query_event.id,
            provider="heuristic",
            model="fake-jarvis",
            execution_time_ms=180.0,
            success=True,
            extras={
                "selected_agent": "jarvis",
                "consulted_agents": ["jarvis"],
                "synthesized": False,
                "analysis_mode": str(context.get("analysis_mode") or "quick"),
                "task_type": "general_conversation",
            },
            request_id=request_id,
        )

        if progress_callback:
            progress_callback(
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "phase": "complete",
                    "agent": "jarvis",
                    "message": "Request complete.",
                }
            )

        return {
            "response": f"Echo: {user_input}",
            "confidence": 0.91,
            "agent": "jarvis",
            "selected_agent": "jarvis",
            "consulted_agents": ["jarvis"],
            "synthesized": False,
            "provider": "heuristic",
            "model": "fake-jarvis",
            "backend_error": None,
            "backend_metadata": {},
            "task_type": "general_conversation",
            "analysis_mode": str(context.get("analysis_mode") or "quick"),
            "request_id": request_id,
            "session_id": session_id,
            "correlation_id": correlation_id,
            "response_event_id": response_event.id,
            "event_payload": {
                "selected_agent": "jarvis",
                "provider": "heuristic",
                "model": "fake-jarvis",
            },
            "timestamp": time.time(),
        }

    def get_voice_loop_status(self):
        return {"active": False, "message": "Voice loop unavailable in fake orchestrator"}

    def start_voice_loop(self):
        return False

    def stop_voice_loop(self):
        return False


def build_dashboard(tmp_path: Path) -> WebDashboard:
    event_store = EventStore(tmp_path / "events.db")
    memory_service = ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())
    return WebDashboard(
        host="127.0.0.1",
        port=8099,
        memory_service=memory_service,
        orchestrator_instance=SlowFakeOrchestrator(memory_service),
    )


def test_background_chat_returns_immediate_acceptance_and_completes(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()

    response = client.post(
        "/api/chat",
        json={
            "message": "remember this test flow",
            "background": True,
            "request_id": "hud-test-1",
            "analysis_mode": "quick",
        },
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["status"] == "accepted"
    assert payload["phase"] == "accepted"

    initial_trace = client.get("/api/request/hud-test-1/trace").get_json()
    assert initial_trace["status"] == "pending"
    assert initial_trace["phase"] == "accepted"

    deadline = time.time() + 2.0
    final_trace = None
    while time.time() < deadline:
        final_trace = client.get("/api/request/hud-test-1/trace").get_json()
        if final_trace["status"] == "complete":
            break
        time.sleep(0.05)

    assert final_trace is not None
    assert final_trace["status"] == "complete"
    assert final_trace["selected_agent"] == "jarvis"
    assert any(event["event_type"] == "agent_selection" for event in final_trace["events"])


def test_completed_background_chat_refreshes_session_messages(tmp_path: Path):
    dashboard = build_dashboard(tmp_path)
    client = dashboard.app.test_client()

    accepted = client.post(
        "/api/chat",
        json={
            "message": "show me session replay",
            "background": True,
            "request_id": "hud-test-2",
        },
    ).get_json()

    session_id = accepted["session_id"]
    deadline = time.time() + 2.0
    while time.time() < deadline:
        trace = client.get("/api/request/hud-test-2/trace").get_json()
        if trace["status"] == "complete":
            break
        time.sleep(0.05)

    messages = client.get(f"/api/session/{session_id}/messages").get_json()
    assert messages["count"] == 2
    assert messages["messages"][0]["role"] == "user"
    assert messages["messages"][1]["role"] == "assistant"
    assert messages["messages"][1]["text"] == "Echo: show me session replay"
