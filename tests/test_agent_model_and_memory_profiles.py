from __future__ import annotations

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


def test_chat_settings_default_to_fast_jarvis_and_heavier_coding_model():
    settings = ChatSettings()

    assert settings.model_for_agent("jarvis") == "llama3.2:3b"
    assert settings.model_for_agent("coding") == "deepseek-r1:8b"
    assert settings.model_for_agent("lilo") == "llama3.2:3b"
    assert settings.timeout_for_agent("jarvis") == 180.0
    assert settings.timeout_for_agent("coding") == 180.0
    assert settings.keep_alive_for_agent("jarvis") == "30m"
    assert settings.num_ctx_for_agent("jarvis") == 8192
    assert settings.num_predict_for_agent("jarvis") == 256
    assert settings.num_ctx_for_agent("coding") == 16384
    assert settings.num_predict_for_agent("coding") == 768


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
