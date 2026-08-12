from __future__ import annotations

import blackfong_ecosystem.agents.jarvis_agent as jarvis_agent_module
from blackfong_ecosystem.agents.jarvis_agent import JARVISAgent
from blackfong_ecosystem.chat_backends import BaseChatBackend, ChatBackendError, ChatBackendResult
from blackfong_ecosystem.config import ChatSettings
from blackfong_ecosystem.conversation_memory import ConversationMemoryService
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


def test_degraded_backend_caps_standard_timeout(monkeypatch):
    captured: dict[str, float] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["timeout"] = float(options.get("timeout") or 0.0)
            captured["max_attempts"] = float(options.get("max_attempts") or 0.0)
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
                    "last_backend_error": "Ollama request timed out after 45s",
                }
            },
        },
    )

    assert captured["timeout"] == 18.0
    assert captured["max_attempts"] == 1.0
    assert result["provider"] == "ollama"
    assert result["backend_metadata"]["backend_health"]["state"] == "degraded"
