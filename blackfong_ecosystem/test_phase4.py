"""
Phase 4 verification tests for the O.B.E.O.S orchestration engine.
"""

from __future__ import annotations

import threading
import time
import uuid

from blackfong_ecosystem.event_api import create_app
from blackfong_ecosystem.orchestrator import Orchestrator


def test_orchestrator_initialization(tmp_path):
    """Test that orchestrator initializes correctly."""
    chroma_dir = tmp_path / "chroma-init"
    orchestrator = Orchestrator(chroma_persist_dir=str(chroma_dir))

    assert "jarvis" in orchestrator.agents
    assert "lilo" in orchestrator.agents
    assert "stitch" in orchestrator.agents
    assert orchestrator.semantic_search is not None
    assert orchestrator.semantic_search.collection is not None


def test_agent_routing():
    """Test that requests are routed to appropriate agents."""
    orchestrator = Orchestrator()
    test_cases = [
        ("How can you help me?", "jarvis"),
        ("I need help with Python certification", "lilo"),
        ("Show me a coding exercise", "stitch"),
        ("What's the system status?", "jarvis"),
        ("Create a quiz for me", "lilo"),
        ("Debug this Python function", "stitch"),
    ]

    for user_input, expected_agent in test_cases:
        routed_agent = orchestrator._route_request(user_input)
        assert routed_agent == expected_agent, f"Expected {expected_agent}, got {routed_agent} for: {user_input}"


def test_direct_request_processing(tmp_path):
    """Test processing user requests directly through orchestrator."""
    orchestrator = Orchestrator(chroma_persist_dir=str(tmp_path / "chroma-direct"))
    result = orchestrator.process_user_request("Hello, how are you?")

    assert "response" in result
    assert "agent" in result
    assert "correlation_id" in result
    assert result["agent"] == "jarvis"
    assert isinstance(result["correlation_id"], str)


def test_semantic_search(tmp_path):
    """Test semantic search functionality."""
    orchestrator = Orchestrator(chroma_persist_dir=str(tmp_path / "chroma-semantic"))

    trace_data_1 = {
        "agent_name": "jarvis",
        "user_input": "How do I check system status?",
        "timestamp": int(time.time() * 1000),
        "steps": [
            {"step_id": "parse", "action": "parse_input", "confidence": 0.95},
            {"step_id": "check", "action": "check_system_status", "confidence": 0.90},
        ],
        "final_decision": "System status: All components online",
        "overall_confidence": 0.92,
        "alternative_actions": ["query_logs", "run_diagnostics"],
    }
    trace_data_2 = {
        "agent_name": "lilo",
        "user_input": "Create a Python certification quiz",
        "timestamp": int(time.time() * 1000),
        "steps": [
            {"step_id": "parse", "action": "parse_quiz_request", "confidence": 0.88},
            {"step_id": "generate", "action": "generate_questions", "confidence": 0.85},
        ],
        "final_decision": "Generated 10-question PCEP quiz",
        "overall_confidence": 0.86,
        "alternative_actions": ["show_study_guide", "practice_mode"],
    }

    orchestrator.semantic_search.store_reasoning_trace(str(uuid.uuid4()), trace_data_1)
    orchestrator.semantic_search.store_reasoning_trace(str(uuid.uuid4()), trace_data_2)
    time.sleep(0.1)

    status_results = orchestrator.search_reasoning_history("system status", limit=5)
    quiz_results = orchestrator.search_reasoning_history("python quiz", limit=5)

    assert status_results
    assert any(result["agent_name"] == "jarvis" for result in status_results)
    assert quiz_results
    assert any(result["agent_name"] == "lilo" for result in quiz_results)


def test_workflow_tracking(tmp_path):
    """Test workflow tracking with correlation IDs."""
    orchestrator = Orchestrator(chroma_persist_dir=str(tmp_path / "chroma-workflow"))
    correlation_id = str(uuid.uuid4())

    initial_event = {
        "id": str(uuid.uuid4()),
        "event_type": "agent_query",
        "payload": {"user_input": "Help me with Python"},
        "correlation_id": correlation_id,
    }
    orchestrator.workflow_tracker.start_workflow(correlation_id, initial_event)

    agent_event = {
        "id": str(uuid.uuid4()),
        "source": "stitch",
        "event_type": "reasoning_trace",
        "correlation_id": correlation_id,
    }
    orchestrator.workflow_tracker.add_workflow_event(correlation_id, agent_event)

    status = orchestrator.get_workflow_status(correlation_id)
    assert status is not None
    assert status["correlation_id"] == correlation_id
    assert status["status"] == "active"
    assert "stitch" in status["agents_involved"]
    assert status["event_count"] == 2

    orchestrator.workflow_tracker.complete_workflow(correlation_id)
    completed = orchestrator.get_workflow_status(correlation_id)
    assert completed["status"] == "completed"


def test_full_integration(tmp_path):
    """Test full integration with a locally started event API."""
    event_api_url = "http://127.0.0.1:18000"
    orchestrator = Orchestrator(event_api_url=event_api_url, chroma_persist_dir=str(tmp_path / "chroma-full"))
    event_app = create_app()

    def run_event_api():
        event_app.run(host="127.0.0.1", port=18000, debug=False, use_reloader=False)

    api_thread = threading.Thread(target=run_event_api, daemon=True)
    api_thread.start()
    time.sleep(1.5)

    response = orchestrator.event_client.publish_event(
        source="user",
        event_type="agent_query",
        payload={"user_input": "Test integration"},
    )
    assert "event_id" in response

    result = orchestrator.process_user_request("Show me available tools")
    assert "response" in result
    assert result["agent"] == "jarvis"


def main() -> bool:
    """Run the Phase 4 tests as a manual script."""
    try:
        test_orchestrator_initialization()
        test_agent_routing()
        test_direct_request_processing()
        test_semantic_search()
        test_workflow_tracking()
        test_full_integration()
    except Exception:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
