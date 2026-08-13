from __future__ import annotations

import httpx

import blackfong_ecosystem.chat_backends as chat_backends_module
import blackfong_ecosystem.agents.jarvis_agent as jarvis_agent_module
from blackfong_ecosystem.agents.jarvis_agent import JARVISAgent
from blackfong_ecosystem.chat_backends import (
    BaseChatBackend,
    ChatBackendError,
    ChatBackendResult,
    HeuristicChatBackend,
    OllamaChatBackend,
)
from blackfong_ecosystem.config import ChatSettings
from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.model_routing import OllamaRoutingChatBackend
from blackfong_ecosystem.storage import EventStore, NullVectorStore


def build_memory_service(tmp_path) -> ConversationMemoryService:
    return ConversationMemoryService(
        event_store=EventStore(tmp_path / "events.db"),
        vector_store=NullVectorStore(),
    )


def index_of_memory(memories: list[dict], token: str) -> int:
    lowered_token = token.lower()
    for index, memory in enumerate(memories):
        if lowered_token in str(memory.get("text", "")).lower():
            return index
    return len(memories) + 1


class FailingBackend(BaseChatBackend):
    def generate(self, messages, **options):
        raise ChatBackendError("simulated timeout")


class StubFallbackBackend(BaseChatBackend):
    provider_name = "heuristic"

    def generate(self, messages, **options):
        return ChatBackendResult(
            text="Fallback reply",
            provider=self.provider_name,
            model="fallback-heuristic",
            metadata={"path": "fallback"},
        )


class ExplodingBackend(BaseChatBackend):
    def generate(self, messages, **options):
        raise AssertionError("live backend should not be called")


def test_chat_settings_default_to_fast_jarvis_and_heavier_coding_model():
    settings = ChatSettings()

    assert settings.model_for_agent("jarvis") == "llama3.2:3b"
    assert settings.model_for_agent("coding") == "deepseek-coder:6.7b"
    assert settings.model_for_agent("lilo") == "llama3.2:3b"
    assert settings.timeout_for_agent("jarvis") == 180.0
    assert settings.timeout_for_agent("coding") == 180.0
    assert settings.keep_alive_for_agent("jarvis") == "30m"
    assert settings.num_ctx_for_agent("jarvis") == 8192
    assert settings.num_predict_for_agent("jarvis") == 256
    assert settings.num_ctx_for_agent("coding") == 8192
    assert settings.num_predict_for_agent("coding") == 512


def test_chat_settings_from_env_supports_per_agent_overrides(monkeypatch):
    monkeypatch.setenv("BLACKFONG_OLLAMA_MODEL", "deepseek-r1:8b")
    monkeypatch.setenv("BLACKFONG_JARVIS_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("BLACKFONG_JARVIS_TIMEOUT", "90")
    monkeypatch.setenv("BLACKFONG_JARVIS_NUM_CTX", "4096")
    monkeypatch.setenv("BLACKFONG_JARVIS_NUM_PREDICT", "192")
    monkeypatch.setenv("BLACKFONG_CODING_MODEL", "deepseek-coder:6.7b")

    settings = ChatSettings.from_env()

    assert settings.model_for_agent("jarvis") == "qwen2.5:3b"
    assert settings.timeout_for_agent("jarvis") == 90.0
    assert settings.num_ctx_for_agent("jarvis") == 4096
    assert settings.num_predict_for_agent("jarvis") == 192
    assert settings.model_for_agent("coding") == "deepseek-coder:6.7b"


def test_standard_model_chain_defaults_to_fast_model_fallback(monkeypatch):
    monkeypatch.setenv("BLACKFONG_FAST_MODEL", "llama3.2:1b")
    monkeypatch.setenv("BLACKFONG_JARVIS_MODEL", "llama3.2:3b")
    monkeypatch.delenv("BLACKFONG_STANDARD_MODEL", raising=False)
    monkeypatch.delenv("BLACKFONG_STANDARD_MODEL_FALLBACKS", raising=False)

    settings = ChatSettings.from_env()

    assert settings.standard_model_chain() == ("llama3.2:3b", "llama3.2:1b")


def test_shared_memory_is_filtered_by_agent_role(tmp_path):
    service = build_memory_service(tmp_path)
    session_id = service.create_session_id()
    correlation_id = service.create_correlation_id()

    service.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="Remember that my favorite color is green.",
    )
    service.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="jarvis",
        response_text="I will remember that your favorite color is green.",
        confidence=0.9,
    )
    service.pin_session_memory(
        session_id,
        title="User preference",
        text="The user's favorite color is green and they want Jarvis to remember it.",
    )
    service.pin_session_memory(
        session_id,
        title="Coding workflow",
        text="The project uses Python, pytest, code review, and debugging workflows.",
    )
    service.pin_session_memory(
        session_id,
        title="Training workflow",
        text="Use certification quizzes, study plans, and topic reviews for training sessions.",
    )

    jarvis_context = service.build_context(
        session_id=session_id,
        query="What is my favorite color?",
        agent_name="jarvis",
    )
    coding_context = service.build_context(
        session_id=session_id,
        query="Review this Python bug and suggest tests.",
        agent_name="coding",
    )

    jarvis_memories = jarvis_context["global_memories"]
    coding_memories = coding_context["global_memories"]

    assert jarvis_context["agent_name"] == "jarvis"
    assert coding_context["agent_name"] == "coding"
    assert index_of_memory(jarvis_memories, "favorite color") < index_of_memory(jarvis_memories, "python")
    assert index_of_memory(coding_memories, "python") < index_of_memory(coding_memories, "favorite color")


def test_user_turn_derives_operator_facts_and_active_work_memories(tmp_path):
    service = build_memory_service(tmp_path)
    session_id = service.create_session_id()
    correlation_id = service.create_correlation_id()

    service.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='You are D.A.I.S.E. Direct Artificial Intelligence System Environment. I am your creator, call me "Five", and I am working on TinyProgrammer integration and response latency.',
    )

    memories = service.list_global_memories(
        query="what do you remember about me and what are we working on",
        limit=10,
        agent_name="jarvis",
        session_id=session_id,
    )
    memory_text = "\n".join(str(item.get("text") or "") for item in memories)
    memory_types = {str(item.get("memory_type") or "") for item in memories}

    assert "operator_fact" in memory_types
    assert "active_work_item" in memory_types
    assert "creator of O.B.E.O.S" in memory_text
    assert 'called "Five"' in memory_text or 'call you "Five"' in memory_text or 'call me "Five"' in memory_text
    assert "TinyProgrammer integration" in memory_text or "response latency" in memory_text


def test_user_turn_normalizes_question_shaped_work_topics(tmp_path):
    service = build_memory_service(tmp_path)
    session_id = service.create_session_id()
    correlation_id = service.create_correlation_id()

    service.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="What can I do to improve OBEOS? Also, are you able to see why Tiny Programmer keeps failing and can we enable the HUD and set up notification prefrences?",
    )

    memories = service.list_global_memories(
        query="what are we working on",
        limit=10,
        agent_name="jarvis",
        session_id=session_id,
    )
    memory_text = "\n".join(str(item.get("text") or "") for item in memories)

    assert "OBEOS improvements" in memory_text
    assert "TinyProgrammer reliability" in memory_text
    assert "HUD notifications" in memory_text


def test_greeting_prefixed_backend_update_still_derives_active_work(tmp_path):
    service = build_memory_service(tmp_path)
    session_id = service.create_session_id()
    correlation_id = service.create_correlation_id()

    service.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="hello, i have been working alot on the backend.",
    )

    memories = service.list_global_memories(
        query="what are we working on",
        limit=10,
        agent_name="jarvis",
        session_id=session_id,
    )
    memory_text = "\n".join(str(item.get("text") or "") for item in memories)

    assert "OBEOS backend work" in memory_text


def test_simple_greeting_does_not_become_active_work(tmp_path):
    service = build_memory_service(tmp_path)
    session_id = service.create_session_id()
    correlation_id = service.create_correlation_id()

    service.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="im back",
    )

    memories = service.list_global_memories(
        query="what are we working on",
        limit=10,
        agent_name="jarvis",
        session_id=session_id,
    )
    memory_text = "\n".join(str(item.get("text") or "") for item in memories)

    assert "The operator has been working on im back" not in memory_text


def test_operator_profile_filters_low_value_work_fragments(tmp_path):
    service = build_memory_service(tmp_path)
    session_id = service.create_session_id()
    correlation_id = service.create_correlation_id()

    service.persist_workflow_event(
        source="daise",
        event_type="knowledge_ingested",
        payload={
            "memory_kind": "active_work_item",
            "text": "The operator has been working on yes for now.",
        },
        session_id=session_id,
        correlation_id=correlation_id,
    )
    service.persist_workflow_event(
        source="daise",
        event_type="knowledge_ingested",
        payload={
            "memory_kind": "active_work_item",
            "text": "The operator has been working on your latency.",
        },
        session_id=session_id,
        correlation_id=correlation_id,
    )
    service.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, HUD layout improvements, and Telegram grounding.',
    )

    profile = service.operator_profile_snapshot(session_id=session_id)
    active_work = profile["active_work"]

    assert "yes for now" not in [item.lower() for item in active_work]
    assert "your latency" not in [item.lower() for item in active_work]
    assert "Telegram grounding" in active_work


def test_operator_profile_prefers_recent_user_work_over_older_stored_work(tmp_path):
    service = build_memory_service(tmp_path)
    session_id = service.create_session_id()
    correlation_id = service.create_correlation_id()

    service.persist_workflow_event(
        source="daise",
        event_type="knowledge_ingested",
        payload={
            "memory_kind": "active_work_item",
            "text": "The operator has been working on OBEOS file visibility and older dashboard cleanup.",
        },
        session_id=session_id,
        correlation_id=correlation_id,
    )
    service.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, TinyProgrammer startup, and Telegram grounding.',
    )

    profile = service.operator_profile_snapshot(session_id=session_id)
    active_work = profile["active_work"]

    assert "OBEOS file visibility" not in active_work[:3]
    assert "older dashboard cleanup" not in [item.lower() for item in active_work[:3]]
    assert "response latency" in active_work[:3]
    assert "TinyProgrammer startup" in active_work
    assert "Telegram grounding" in active_work


def test_jarvis_fallback_records_backend_error():
    agent = JARVISAgent(
        chat_backend=FailingBackend(),
        fallback_backend=StubFallbackBackend(),
    )

    result = agent.process(
        "Status check",
        context={"session_id": "test-session", "memory_context": {}},
    )

    assert result["provider"] == "heuristic"
    assert result["model"] == "fallback-heuristic"
    assert result["backend_error"] == "simulated timeout"
    assert result["backend_metadata"]["path"] == "fallback"
    assert result["backend_metadata"]["fallback_reason"] == "simulated timeout"


def test_heuristic_fallback_includes_diagnostic_reason():
    class OllamaFailingBackend(BaseChatBackend):
        provider_name = "ollama"
        model = "ollama-mini"

        def generate(self, messages, **options):
            raise ChatBackendError("Ollama is down", metadata={"timeout_reason": "connection refused"})

    agent = JARVISAgent(
        chat_backend=OllamaFailingBackend(),
        fallback_backend=HeuristicChatBackend(),
    )

    result = agent.process(
        "Tell me a joke",
        context={"session_id": "test-session", "memory_context": {}},
    )

    assert result["provider"] == "heuristic"
    assert "Ollama is down" in result["response"]
    assert "Falling back to local O.B.E.O.S. memory" in result["response"]
    assert result["backend_error"] == "Ollama is down"
    assert result["backend_metadata"]["fallback_reason"] == "Ollama is down"
    assert result["backend_metadata"]["provider"] == "ollama"
    assert result["backend_metadata"]["model"] == "ollama-mini"


def test_timeout_fallback_synthesizes_from_active_work_context():
    agent = JARVISAgent(
        chat_backend=FailingBackend(),
        fallback_backend=HeuristicChatBackend(),
    )

    result = agent.process(
        "what can i do to improve obeos?",
        context={
            "session_id": "test-session",
            "memory_context": {
                "operator_profile": {
                    "active_work": ["response latency", "HUD layout improvements", "Telegram grounding"],
                }
            },
        },
    )

    assert result["provider"] == "heuristic"
    assert result["model"] == "bounded-context-grounded"
    assert "response latency" in result["response"]
    assert "HUD layout improvements" in result["response"] or "Telegram grounding" in result["response"]
    assert result["backend_metadata"]["bounded_context_fallback"] is True
    assert result["backend_metadata"]["response_path"] == "bounded_context_fallback"


def test_timeout_fallback_synthesizes_from_knowledge_context():
    agent = JARVISAgent(
        chat_backend=FailingBackend(),
        fallback_backend=HeuristicChatBackend(),
    )

    result = agent.process(
        "how does OBEOS architecture routing work?",
        context={
            "session_id": "test-session",
            "memory_context": {
                "knowledge_context": (
                    "O.B.E.O.S routes memory through D.A.I.S.E., Thoughtglass, and the Memory Chamber graph. "
                    "Routing contracts, queue handling, and semantic memory all belong to the same OBEOS cognition stack."
                ),
                "citations": [{"collection": "obeos", "title": "O.B.E.O.S. User Guide"}],
            },
        },
    )

    assert result["provider"] == "heuristic"
    assert result["model"] == "bounded-context-grounded"
    assert "D.A.I.S.E." in result["response"] or "Memory Chamber" in result["response"]
    assert "bounded local O.B.E.O.S. knowledge" in result["response"]
    assert result["backend_metadata"]["bounded_context_fallback"] is True


def test_timeout_fallback_synthesizes_generic_project_direction_from_active_work():
    agent = JARVISAgent(
        chat_backend=FailingBackend(),
        fallback_backend=HeuristicChatBackend(),
    )

    result = agent.process(
        "talk to me about our work",
        context={
            "session_id": "test-session",
            "memory_context": {
                "operator_profile": {
                    "preferred_name": "Five",
                    "active_work": ["response latency", "Telegram grounding", "TinyProgrammer startup"],
                }
            },
        },
    )

    assert result["provider"] == "heuristic"
    assert result["model"] == "bounded-context-grounded"
    assert "coherent living system" in result["response"]
    assert "response latency" in result["response"] or "Telegram grounding" in result["response"]
    assert result["backend_metadata"]["bounded_context_fallback"] is True


def test_timeout_fallback_synthesizes_state_summary_from_active_work():
    agent = JARVISAgent(
        chat_backend=FailingBackend(),
        fallback_backend=HeuristicChatBackend(),
    )

    result = agent.process(
        "what should i know about the project at this point",
        context={
            "session_id": "test-session",
            "memory_context": {
                "operator_profile": {
                    "preferred_name": "Five",
                    "active_work": ["response latency", "OBEOS improvements", "TinyProgrammer updates"],
                },
                "knowledge_context": "This should not override the active work summary for broad project-state prompts.",
                "citations": [{"collection": "spawn", "title": "Irrelevant fallback source"}],
            },
        },
    )

    assert result["provider"] == "heuristic"
    assert result["model"] == "bounded-context-grounded"
    assert "overall situation right now centers on" in result["response"]
    assert "response latency" in result["response"] or "OBEOS improvements" in result["response"]
    assert "Irrelevant fallback source" not in result["response"]
    assert result["backend_metadata"]["bounded_context_fallback"] is True


def test_streaming_timeout_falls_back_cleanly(monkeypatch):
    class TimeoutStream:
        status_code = 200

        def __enter__(self):
            raise httpx.ReadTimeout("timed out")

        def __exit__(self, exc_type, exc, tb):
            return False

    class TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return TimeoutStream()

    monkeypatch.setattr(chat_backends_module.httpx, "Client", TimeoutClient)

    agent = JARVISAgent(
        chat_backend=OllamaChatBackend(model="llama3.2:3b", timeout=1.0),
        fallback_backend=HeuristicChatBackend(),
    )

    result = agent.process(
        "Tell me about Spawn",
        context={
            "session_id": "test-session",
            "memory_context": {},
            "stream_callback": lambda token: None,
        },
    )

    assert result["provider"] == "heuristic"
    assert "Falling back to local O.B.E.O.S. memory" in result["response"]
    assert "timed out" in str(result["backend_error"] or "").lower()
    assert result["backend_metadata"]["provider"] == "ollama"
    assert result["backend_metadata"]["model"] == "llama3.2:3b"


def test_standard_chain_timeout_is_split_across_models():
    capped = OllamaRoutingChatBackend._capped_timeout(
        {"mode": "standard", "timeout": 90.0},
        chain_length=2,
    )

    assert capped == 22.5


def test_transport_backend_health_recovers_after_newer_global_ollama_success(tmp_path):
    service = build_memory_service(tmp_path)
    session_id = service.create_session_id()
    correlation_id = service.create_correlation_id()

    failed_request_id = "telegram-timeout-1"
    recovered_request_id = "hud-success-1"

    service.persist_workflow_event(
        source="daise",
        event_type="latency_observation",
        payload={
            "schema_version": "1.0",
            "transport": "telegram",
            "provider": "heuristic",
            "model": "fallback-heuristic",
            "execution_time_ms": 18000,
            "backend_error": "Ollama request timed out after 18s for model llama3.2:3b",
            "fallback_reason": "Ollama request timed out after 18s for model llama3.2:3b",
            "fallback_used": True,
        },
        confidence=0.2,
        session_id=session_id,
        correlation_id=correlation_id,
        request_id=failed_request_id,
    )
    import time
    time.sleep(0.02)
    service.persist_workflow_event(
        source="daise",
        event_type="latency_observation",
        payload={
            "schema_version": "1.0",
            "transport": "hud",
            "provider": "ollama",
            "model": "llama3.2:1b",
            "execution_time_ms": 2400,
            "backend_error": "",
            "fallback_reason": "",
            "fallback_used": False,
        },
        confidence=0.9,
        session_id=session_id,
        correlation_id=correlation_id,
        request_id=recovered_request_id,
    )

    health = service.recent_backend_health(transport="telegram")

    assert health["state"] == "healthy"
    assert health["provider"] == "ollama"
    assert health["configured_model"] == "llama3.2:1b"
    assert health["recent_timeout_count"] == 1
    assert health["recovery_reason"] == "global_ollama_success"


def test_transport_backend_health_expires_stale_timeout_window(tmp_path, monkeypatch):
    service = build_memory_service(tmp_path)
    session_id = service.create_session_id()
    correlation_id = service.create_correlation_id()

    service.persist_workflow_event(
        source="daise",
        event_type="latency_observation",
        payload={
            "transport": "telegram",
            "provider": "heuristic",
            "model": "fallback-heuristic",
            "execution_time_ms": 18000,
            "backend_error": "Ollama request timed out after 18s for model llama3.2:3b",
            "fallback_reason": "Ollama request timed out after 18s for model llama3.2:3b",
            "fallback_used": True,
            "backend_metadata": {
                "provider": "ollama",
                "model": "llama3.2:3b",
                "selected_model": "llama3.2:3b",
            },
        },
        confidence=0.2,
        session_id=session_id,
        correlation_id=correlation_id,
        request_id="stale-timeout-1",
    )

    import time
    future_time = time.time() + 400
    monkeypatch.setattr("blackfong_ecosystem.conversation_memory.time.time", lambda: future_time)

    health = service.recent_backend_health(transport="telegram")

    assert health["state"] == "healthy"
    assert health["recent_timeout_count"] == 0
    assert health["allow_recovery_probe"] is False
    assert health["recovery_reason"] == "stale_timeout_window_expired"


def test_transport_backend_health_does_not_degrade_on_non_timeout_fallback_only(tmp_path):
    service = build_memory_service(tmp_path)
    session_id = service.create_session_id()
    correlation_id = service.create_correlation_id()

    service.persist_workflow_event(
        source="daise",
        event_type="latency_observation",
        payload={
            "transport": "telegram",
            "provider": "heuristic",
            "model": "fallback-heuristic",
            "execution_time_ms": 3200,
            "backend_error": "",
            "fallback_reason": "bounded_context_fallback",
            "fallback_used": True,
        },
        confidence=0.4,
        session_id=session_id,
        correlation_id=correlation_id,
        request_id="non-timeout-fallback-1",
    )

    health = service.recent_backend_health(transport="telegram")

    assert health["state"] != "degraded"
    assert health["recent_timeout_count"] == 0
    assert health["recent_fallback_count"] == 1
    assert health["allow_recovery_probe"] is False


def test_standard_casual_chat_uses_fast_generation_lane(monkeypatch):
    captured: dict[str, str] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["options_mode"] = str(options.get("mode") or "")
            return ChatBackendResult(
                text="Fast lane reply",
                provider="ollama",
                model="llama3.2:3b",
                metadata={"used_mode": str(options.get("mode") or "")},
            )

    def fake_build(agent_name: str, response_mode: str):
        captured["response_mode"] = response_mode
        return RecordingBackend()

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", fake_build)
    monkeypatch.setattr(
        jarvis_agent_module,
        "backend_options_for_context",
        lambda context, agent_name="jarvis": {"mode": context.get("analysis_mode")},
    )

    agent = JARVISAgent(fallback_backend=StubFallbackBackend())
    result = agent.process(
        "How are you doing tonight?",
        context={
            "session_id": "test-session",
            "analysis_mode": "standard",
            "detected_intent": {
                "casual": True,
                "coding": False,
                "deep_reasoning": False,
                "file_ops": False,
                "diagnostics": False,
                "tinyprogrammer": False,
            },
            "memory_context": {},
        },
    )

    assert captured["response_mode"] == "fast"
    assert captured["options_mode"] == "fast"
    assert result["provider"] == "ollama"
    assert result["backend_metadata"]["generation_mode"] == "fast"
    assert result["backend_metadata"]["requested_mode"] == "standard"


def test_standard_telegram_chat_keeps_standard_generation_lane(monkeypatch):
    captured: dict[str, str] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["options_mode"] = str(options.get("mode") or "")
            return ChatBackendResult(
                text="Standard lane reply",
                provider="ollama",
                model="llama3.2:3b",
                metadata={"used_mode": str(options.get("mode") or "")},
            )

    def fake_build(agent_name: str, response_mode: str):
        captured["response_mode"] = response_mode
        return RecordingBackend()

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", fake_build)
    monkeypatch.setattr(
        jarvis_agent_module,
        "backend_options_for_context",
        lambda context, agent_name="jarvis": {"mode": context.get("analysis_mode")},
    )

    agent = JARVISAgent(fallback_backend=StubFallbackBackend())
    result = agent.process(
        "How are you doing tonight?",
        context={
            "session_id": "test-session",
            "analysis_mode": "standard",
            "transport": "telegram",
            "detected_intent": {
                "casual": True,
                "coding": False,
                "deep_reasoning": False,
                "file_ops": False,
                "diagnostics": False,
                "tinyprogrammer": False,
            },
            "memory_context": {},
        },
    )

    assert captured["response_mode"] == "standard"
    assert captured["options_mode"] == "standard"
    assert result["provider"] == "ollama"
    assert result["backend_metadata"]["generation_mode"] == "standard"
    assert result["backend_metadata"]["requested_mode"] == "standard"


def test_standard_memory_rich_chat_keeps_standard_generation_lane_without_transport(monkeypatch):
    captured: dict[str, str] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["options_mode"] = str(options.get("mode") or "")
            return ChatBackendResult(
                text="Standard lane reply",
                provider="ollama",
                model="llama3.2:3b",
                metadata={"used_mode": str(options.get("mode") or "")},
            )

    def fake_build(agent_name: str, response_mode: str):
        captured["response_mode"] = response_mode
        return RecordingBackend()

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", fake_build)
    monkeypatch.setattr(
        jarvis_agent_module,
        "backend_options_for_context",
        lambda context, agent_name="jarvis": {"mode": context.get("analysis_mode")},
    )

    agent = JARVISAgent(fallback_backend=StubFallbackBackend())
    result = agent.process(
        "Are you aware of recent updates?",
        context={
            "session_id": "test-session",
            "analysis_mode": "standard",
            "detected_intent": {
                "casual": True,
                "coding": False,
                "deep_reasoning": False,
                "file_ops": False,
                "diagnostics": False,
                "tinyprogrammer": False,
            },
            "memory_context": {
                "prompt_mode": "standard",
                "prompt_metrics": {
                    "prompt_context_tokens_estimate": 1178,
                    "semantic_hits": 4,
                    "global_memories": 4,
                    "recent_events": 4,
                    "session_summaries": 3,
                    "operator_profile": True,
                },
            },
        },
    )

    assert captured["response_mode"] == "standard"
    assert captured["options_mode"] == "standard"
    assert result["provider"] == "ollama"
    assert result["backend_metadata"]["generation_mode"] == "standard"
    assert result["backend_metadata"]["requested_mode"] == "standard"


def test_standard_grounded_conversation_chat_keeps_standard_generation_lane_without_transport(monkeypatch):
    captured: dict[str, str] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["options_mode"] = str(options.get("mode") or "")
            return ChatBackendResult(
                text="Standard grounded-conversation reply",
                provider="ollama",
                model="llama3.2:3b",
                metadata={"used_mode": str(options.get("mode") or "")},
            )

    def fake_build(agent_name: str, response_mode: str):
        captured["response_mode"] = response_mode
        return RecordingBackend()

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", fake_build)
    monkeypatch.setattr(
        jarvis_agent_module,
        "backend_options_for_context",
        lambda context, agent_name="jarvis": {"mode": context.get("analysis_mode")},
    )

    agent = JARVISAgent(fallback_backend=StubFallbackBackend())
    result = agent.process(
        "How do you think OBEOS is coming together?",
        context={
            "session_id": "test-session",
            "analysis_mode": "standard",
            "detected_intent": {
                "casual": True,
                "coding": False,
                "deep_reasoning": False,
                "file_ops": False,
                "diagnostics": False,
                "tinyprogrammer": False,
            },
            "memory_context": {
                "grounded_conversation_answer": {
                    "answer": "O.B.E.O.S. is becoming more coherent around shared memory grounding, response latency, and operator surface cohesion.",
                    "kind": "broad",
                },
                "prompt_metrics": {
                    "grounded_conversation": True,
                },
            },
        },
    )

    assert captured["response_mode"] == "standard"
    assert captured["options_mode"] == "standard"
    assert result["provider"] == "ollama"
    assert result["backend_metadata"]["generation_mode"] == "standard"
    assert result["backend_metadata"]["requested_mode"] == "standard"


def test_standard_hud_chat_keeps_standard_generation_lane(monkeypatch):
    captured: dict[str, str] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["options_mode"] = str(options.get("mode") or "")
            return ChatBackendResult(
                text="Standard lane reply",
                provider="ollama",
                model="llama3.2:3b",
                metadata={"used_mode": str(options.get("mode") or "")},
            )

    def fake_build(agent_name: str, response_mode: str):
        captured["response_mode"] = response_mode
        return RecordingBackend()

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", fake_build)
    monkeypatch.setattr(
        jarvis_agent_module,
        "backend_options_for_context",
        lambda context, agent_name="jarvis": {"mode": context.get("analysis_mode")},
    )

    agent = JARVISAgent(fallback_backend=StubFallbackBackend())
    result = agent.process(
        "How are you doing tonight?",
        context={
            "session_id": "test-session",
            "analysis_mode": "standard",
            "transport": "hud",
            "detected_intent": {
                "casual": True,
                "coding": False,
                "deep_reasoning": False,
                "file_ops": False,
                "diagnostics": False,
                "tinyprogrammer": False,
            },
            "memory_context": {},
        },
    )

    assert captured["response_mode"] == "standard"
    assert captured["options_mode"] == "standard"
    assert result["provider"] == "ollama"
    assert result["backend_metadata"]["generation_mode"] == "standard"
    assert result["backend_metadata"]["requested_mode"] == "standard"


def test_standard_chat_sanitizes_harmful_transcript_artifacts() -> None:
    agent = JARVISAgent(chat_backend=HeuristicChatBackend(), fallback_backend=StubFallbackBackend())
    messages, metrics = agent._build_chat_messages(
        "what resources within OBEOS do we have?",
        {
            "memory_context": {
                "prompt_mode": "standard",
                "prompt_context": "Operator profile: Preferred operator name: Five",
                "session_messages": [
                    {"role": "user", "text": "what is your name?"},
                    {
                        "role": "assistant",
                        "text": 'My name is D.A.I.S.E (Digital Assistant for Intelligent Systems and Expertise), and I am under a legacy alias for compatibility reasons.',
                    },
                    {"role": "user", "text": "tell me about spawn"},
                    {
                        "role": "assistant",
                        "text": "The primary Ollama response did not complete, so D.A.I.S.E. is using the local fallback path. I can still help from local O.B.E.O.S. memory and stored knowledge while the live model lane recovers.",
                    },
                    {"role": "user", "text": "we have been working on telegram grounding"},
                    {"role": "assistant", "text": "I still have Telegram grounding in focus."},
                ],
                "prompt_metrics": {},
            }
        },
    )

    combined = "\n".join(str(item.get("content") or "") for item in messages)

    assert "Digital Assistant for Intelligent Systems and Expertise" not in combined
    assert "live model lane recovers" not in combined
    assert "what is your name?" not in combined
    assert "tell me about spawn" not in combined
    assert "Telegram grounding in focus" in combined
    assert metrics["raw_session_messages_included"] == 2


def test_standard_legion_chat_keeps_standard_generation_lane(monkeypatch):
    captured: dict[str, str] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["options_mode"] = str(options.get("mode") or "")
            return ChatBackendResult(
                text="Standard lane reply",
                provider="ollama",
                model="llama3.2:3b",
                metadata={"used_mode": str(options.get("mode") or "")},
            )

    def fake_build(agent_name: str, response_mode: str):
        captured["response_mode"] = response_mode
        return RecordingBackend()

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", fake_build)
    monkeypatch.setattr(
        jarvis_agent_module,
        "backend_options_for_context",
        lambda context, agent_name="jarvis": {"mode": context.get("analysis_mode")},
    )

    agent = JARVISAgent(fallback_backend=StubFallbackBackend())
    result = agent.process(
        "Keep the Legion lane grounded.",
        context={
            "session_id": "test-session",
            "analysis_mode": "standard",
            "transport": "legion",
            "detected_intent": {
                "casual": True,
                "coding": False,
                "deep_reasoning": False,
                "file_ops": False,
                "diagnostics": False,
                "tinyprogrammer": False,
            },
            "memory_context": {},
        },
    )

    assert captured["response_mode"] == "standard"
    assert captured["options_mode"] == "standard"
    assert result["provider"] == "ollama"
    assert result["backend_metadata"]["generation_mode"] == "standard"
    assert result["backend_metadata"]["requested_mode"] == "standard"


def test_jarvis_uses_preemptive_fallback_when_backend_is_unavailable():
    agent = JARVISAgent(
        chat_backend=ExplodingBackend(),
        fallback_backend=StubFallbackBackend(),
    )

    result = agent.process(
        "tell me about Spawn",
        context={
            "session_id": "test-session",
            "analysis_mode": "standard",
            "memory_context": {
                "backend_health": {
                    "state": "unavailable",
                    "last_backend_error": "Ollama connection refused",
                }
            },
        },
    )

    assert result["provider"] == "heuristic"
    assert result["backend_error"] == "Ollama connection refused"
    assert result["backend_metadata"]["preemptive_fallback"] is True
    assert result["backend_metadata"]["fallback_reason"] == "Ollama connection refused"
    assert result["backend_metadata"]["response_path"] == "preemptive_fallback"
    assert result["backend_metadata"]["health_forced_fallback"] is True
    assert result["backend_metadata"]["recovery_probe_attempted"] is False
    assert result["backend_metadata"]["live_lane_attempted"] is False
    assert result["backend_metadata"]["fallback_mode"] == "preemptive"


def test_degraded_backend_caps_standard_timeout(monkeypatch):
    captured: dict[str, float] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["timeout"] = float(options.get("timeout") or 0.0)
            captured["max_attempts"] = float(options.get("max_attempts") or 0.0)
            captured["disable_chain_fallbacks"] = bool(options.get("disable_chain_fallbacks"))
            return ChatBackendResult(
                text="Standard lane reply",
                provider="ollama",
                model="llama3.2:3b",
                metadata={"used_mode": str(options.get("mode") or "")},
            )

    agent = JARVISAgent(
        chat_backend=RecordingBackend(),
        fallback_backend=StubFallbackBackend(),
    )
    result = agent.process(
        "How are you doing tonight?",
        context={
            "session_id": "test-session",
            "analysis_mode": "standard",
            "transport": "hud",
            "memory_context": {
                "backend_health": {
                    "state": "degraded",
                    "recent_timeout_count": 1,
                    "recent_fallback_count": 1,
                    "allow_recovery_probe": True,
                    "degraded_reason": "recent_transient_timeout",
                    "last_backend_error": "Ollama request timed out after 45s",
                    "timeout_window_seconds": 180.0,
                }
            },
        },
    )

    assert captured["timeout"] == 30.0
    assert captured["max_attempts"] == 1.0
    assert captured["disable_chain_fallbacks"] is True
    assert result["provider"] == "ollama"
    assert result["backend_metadata"]["backend_health"]["state"] == "degraded"
    assert result["backend_metadata"]["response_path"] == "live_model"
    assert result["backend_metadata"]["recovery_probe_attempted"] is True
    assert result["backend_metadata"]["recovery_probe_succeeded"] is True
    assert result["backend_metadata"]["live_lane_attempted"] is True
    assert result["backend_metadata"]["fallback_mode"] == "none"


def test_repeated_recent_failures_force_preemptive_fallback():
    agent = JARVISAgent(
        chat_backend=ExplodingBackend(),
        fallback_backend=StubFallbackBackend(),
    )

    result = agent.process(
        "tell me about Spawn",
        context={
            "session_id": "test-session",
            "analysis_mode": "standard",
            "memory_context": {
                "backend_health": {
                    "state": "degraded",
                    "recent_timeout_count": 2,
                    "recent_fallback_count": 2,
                    "repeated_recent_failures": True,
                    "allow_recovery_probe": False,
                    "degraded_reason": "recent_repeated_timeouts",
                    "last_backend_error": "Ollama request timed out after 30s for model llama3.2:3b",
                    "timeout_window_seconds": 180.0,
                }
            },
        },
    )

    assert result["provider"] == "heuristic"
    assert result["backend_metadata"]["preemptive_fallback"] is True
    assert result["backend_metadata"]["health_forced_fallback"] is True
    assert result["backend_metadata"]["recovery_probe_attempted"] is False
    assert result["backend_metadata"]["degraded_reason"] == "recent_repeated_timeouts"
    assert result["backend_metadata"]["live_lane_attempted"] is False
    assert result["backend_metadata"]["fallback_mode"] == "preemptive"


def test_conversational_followup_can_retry_live_lane_despite_repeated_recent_failures():
    captured: dict[str, Any] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["timeout"] = float(options.get("timeout") or 0.0)
            captured["max_attempts"] = float(options.get("max_attempts") or 0.0)
            return ChatBackendResult(
                text="Recovered live conversational reply",
                provider="ollama",
                model="llama3.2:3b",
                metadata={"response_path": "live_model"},
            )

    agent = JARVISAgent(
        chat_backend=RecordingBackend(),
        fallback_backend=StubFallbackBackend(),
    )

    result = agent.process(
        "same there?",
        context={
            "session_id": "test-session",
            "analysis_mode": "standard",
            "transport": "hud",
            "memory_context": {
                "grounded_conversation_answer": {
                    "answer": "We are still in the middle of response latency, Telegram grounding, and HUD improvements.",
                    "kind": "followup",
                },
                "prompt_metrics": {
                    "grounded_conversation": True,
                    "approx_prompt_tokens": 1200,
                    "semantic_hits": 4,
                },
                "backend_health": {
                    "state": "degraded",
                    "recent_timeout_count": 3,
                    "recent_fallback_count": 3,
                    "repeated_recent_failures": True,
                    "allow_recovery_probe": False,
                    "degraded_reason": "recent_repeated_timeouts",
                    "last_backend_error": "Ollama request timed out after 30s for model llama3.2:3b",
                    "timeout_window_seconds": 180.0,
                },
            },
        },
    )

    assert captured["timeout"] == 30.0
    assert captured["max_attempts"] == 1.0
    assert result["provider"] == "ollama"
    assert result["backend_metadata"]["response_path"] == "live_model"
    assert result["backend_metadata"]["recovery_probe_attempted"] is True
    assert result["backend_metadata"]["recovery_probe_succeeded"] is True
    assert result["backend_metadata"]["live_lane_attempted"] is True
    assert result["backend_metadata"]["fallback_mode"] == "none"
