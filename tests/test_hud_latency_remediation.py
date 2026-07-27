from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from blackfong_ecosystem.chat_backends import ChatBackendError, OllamaChatBackend
from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.daise import DAISE
from blackfong_ecosystem.storage import EventStore, NullVectorStore


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


def test_ollama_quick_read_timeout_does_not_retry(monkeypatch):
    calls = {"count": 0}

    class TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            calls["count"] += 1
            raise httpx.ReadTimeout("still generating")

    monkeypatch.setattr("blackfong_ecosystem.chat_backends.httpx.Client", TimeoutClient)
    backend = OllamaChatBackend(model="llama3.2:3b", timeout=180)

    with pytest.raises(ChatBackendError) as exc_info:
        backend.generate(
            [{"role": "user", "content": "hello"}],
            timeout=75,
            max_attempts=2,
            retry_read_timeout=False,
            mode="quick",
        )

    assert calls["count"] == 1
    assert exc_info.value.metadata["ollama_attempts"] == 1
    assert exc_info.value.metadata["timeout_reason"] == "read_timeout_after_75s"
    assert exc_info.value.metadata["retry_read_timeout"] is False


def test_prompt_budget_compacts_quick_jarvis_context(tmp_path: Path):
    memory = build_memory(tmp_path)
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    for index in range(20):
        memory.persist_user_turn(
            session_id=session_id,
            correlation_id=correlation_id,
            text=f"Long user message {index} " + ("memory detail " * 30),
        )
        memory.persist_agent_response(
            session_id=session_id,
            correlation_id=correlation_id,
            agent_name="jarvis",
            response_text=f"Long assistant response {index} " + ("system detail " * 30),
            confidence=0.9,
        )
    for index in range(6):
        memory.pin_session_memory(
            session_id,
            title=f"Memory {index}",
            text=f"Important durable fact {index}: " + ("creative system preference " * 20),
        )

    quick = memory.build_context(
        session_id=session_id,
        query="What do you remember?",
        agent_name="jarvis",
        analysis_mode="quick",
    )
    deep = memory.build_context(
        session_id=session_id,
        query="What do you remember?",
        agent_name="jarvis",
        analysis_mode="deep",
    )

    assert quick["prompt_mode"] == "quick_compact"
    assert len(quick["session_messages"]) <= 8
    assert len(quick["global_memories"]) <= 3
    assert len(quick["recent_events"]) <= 3
    assert quick["prompt_metrics"]["prompt_context_tokens_estimate"] <= quick["prompt_metrics"]["prompt_budget_tokens"]
    assert deep["memory_profile"]["transcript_window"] > quick["memory_profile"]["transcript_window"]


def test_simple_chat_stays_single_agent_and_code_routes_to_coding(tmp_path: Path):
    memory = build_memory(tmp_path)
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        memory_service=memory,
    )
    agents = ["jarvis", "coding", "voice"]

    simple_scores = daise._route_agent_scores(
        user_input="hello, how are you?",
        modality="text",
        agent_names=agents,
        routing_keywords={},
        agent_override=None,
    )
    simple_multi = daise._should_use_multi_agent(
        analysis_mode="quick",
        complexity_score=0.18,
        routing_confidence=simple_scores["jarvis"],
        critical_request=False,
        route_scores=simple_scores,
        agent_override=None,
    )

    code_scores = daise._route_agent_scores(
        user_input="review this code for bugs and tests",
        modality="text",
        agent_names=agents,
        routing_keywords={},
        agent_override=None,
    )

    assert simple_scores["jarvis"] > simple_scores["coding"]
    assert simple_scores["voice"] == 0
    assert simple_multi is False
    assert code_scores["coding"] > code_scores["jarvis"]


class FakeJarvisAgent:
    def process(self, user_input, context=None):
        return {
            "response": "Fake Jarvis response",
            "confidence": 0.91,
            "tools_invoked": [],
            "trace_event_id": "trace-1",
            "task_type": "general_conversation",
            "provider": "ollama",
            "model": "llama3.2:3b",
            "backend_error": None,
            "backend_metadata": {
                "ollama_attempts": 1,
                "wall_time_ms": 1234,
                "ollama_total_duration_ms": 1100,
                "load_duration_ms": 10,
                "prompt_eval_count": 640,
                "eval_count": 32,
                "prompt_metrics": {"approx_prompt_tokens": 700, "prompt_mode": "quick_compact"},
            },
            "agent": "jarvis",
        }


def test_daise_persists_latency_observation_for_trace(tmp_path: Path):
    memory = build_memory(tmp_path)
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="hello there",
        agents={"jarvis": FakeJarvisAgent()},
        context={"request_id": "latency-trace-1", "analysis_mode": "quick"},
    )
    trace = memory.get_request_trace("latency-trace-1")

    assert result["selected_agent"] == "jarvis"
    assert any(event["event_type"] == "latency_observation" for event in trace["events"])
    assert trace["latency_observation"]["prompt_eval_count"] == 640
    assert trace["latency_observation"]["ollama_attempts"] == 1


def test_why_slow_uses_latency_provenance(tmp_path: Path):
    memory = build_memory(tmp_path)
    memory.persist_workflow_event(
        source="daise",
        event_type="latency_observation",
        payload={
            "schema_version": "1.0",
            "request_id": "slow-1",
            "provider": "heuristic",
            "model": "fallback-heuristic",
            "execution_time_ms": 75001,
            "prompt_eval_count": 1300,
            "ollama_attempts": 1,
            "timeout_reason": "read_timeout_after_75s",
            "fallback_reason": "Ollama request timed out after 75s for model llama3.2:3b",
        },
        request_id="slow-1",
    )

    awareness = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        memory_service=memory,
    )
    explanation = awareness.conversation_memory
    from blackfong_ecosystem.system_awareness import SystemAwarenessService

    why = SystemAwarenessService(memory_service=explanation).explain_why(
        "why was the response slow?",
        request_id="slow-1",
    )

    assert "75.0s" in why["answer"]
    assert "prompt tokens=1300" in why["answer"]
    assert "Fallback reason" in why["answer"]
