"""Deterministic runtime facts are evidence for the model, never the reply."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from blackfong_ecosystem.agents.jarvis_agent import JARVISAgent
from blackfong_ecosystem.chat_backends import BaseChatBackend, ChatBackendError, ChatBackendResult
from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.daise import DAISE
from blackfong_ecosystem.operator_chat import OperatorChatService
from blackfong_ecosystem.storage import EventStore

FACTS_HEADER = "Verified local runtime facts for this turn"


class RecordingBackend(BaseChatBackend):
    """Stands in for llama.cpp: records each prompt and writes a varied reply."""

    provider_name = "llamacpp"
    model = "stub-model"

    def __init__(self) -> None:
        self.prompts: List[List[Dict[str, str]]] = []

    def generate(self, messages, **options: Any) -> ChatBackendResult:
        self.prompts.append([dict(message) for message in messages])
        return ChatBackendResult(
            text=f"Hey there, good to see you (reply {len(self.prompts)}).",
            provider=self.provider_name,
            model=self.model,
            metadata={"response_path": "live_model"},
        )


class DownBackend(BaseChatBackend):
    provider_name = "llamacpp"
    model = "stub-model"

    def generate(self, messages, **options: Any) -> ChatBackendResult:
        raise ChatBackendError(
            "The configured D.A.I.S.E. endpoint is not ready",
            metadata={"provider": "llamacpp", "error_type": "endpoint_not_ready"},
        )


class DaiseOrchestrator:
    def __init__(self, daise: DAISE, agent: JARVISAgent) -> None:
        self.daise = daise
        self.agent = agent

    def process_input(self, text, modality, context=None):
        return self.daise.process_request(
            user_input=text,
            modality="text",
            context=dict(context or {}),
            agents={"jarvis": self.agent},
        )


def _memory(tmp_path: Path) -> ConversationMemoryService:
    return ConversationMemoryService(event_store=EventStore(tmp_path / "events.db"), vector_store=None)


def _service(tmp_path: Path, backend: BaseChatBackend):
    memory = _memory(tmp_path)
    daise = DAISE.create_default(event_store=memory.event_store, vector_store=None, memory_service=memory)
    agent = JARVISAgent(chat_backend=backend)
    return memory, OperatorChatService(memory_service=memory, orchestrator=DaiseOrchestrator(daise, agent))


def _system_text(prompt: List[Dict[str, str]]) -> str:
    return "\n".join(message["content"] for message in prompt if message["role"] == "system")


def test_hi_is_answered_by_the_model_not_a_template(tmp_path: Path) -> None:
    backend = RecordingBackend()
    _, service = _service(tmp_path, backend)

    first = service.send_message(message="hi", session_id="s1")
    second = service.send_message(message="hi", session_id="s1")

    assert len(backend.prompts) == 2
    for reply in (first, second):
        assert reply["status"] == "complete"
        assert reply["result"]["provider"] == "llamacpp"
        assert "Local evidence summary" not in reply["result"]["response"]
    assert first["result"]["response"] == "Hey there, good to see you (reply 1)."
    assert first["result"]["response"] != second["result"]["response"]
    assert first["result"]["runtime_truth"]["kind"] == "greeting"


def test_runtime_facts_follow_history_and_precede_the_user_turn(tmp_path: Path) -> None:
    backend = RecordingBackend()
    _, service = _service(tmp_path, backend)

    service.send_message(message="Let's talk about the HUD layout today.", session_id="s2")
    service.send_message(message="what time is it", session_id="s2")

    prompt = backend.prompts[-1]
    facts_index = next(i for i, message in enumerate(prompt) if FACTS_HEADER in message["content"])
    history_index = max(
        i for i, message in enumerate(prompt) if message["content"] == "Let's talk about the HUD layout today."
    )
    assert history_index < facts_index < len(prompt) - 1
    assert prompt[-1] == {"role": "user", "content": "what time is it"}
    assert "time:" in prompt[facts_index]["content"]


def test_deterministic_evidence_can_be_switched_off(tmp_path: Path) -> None:
    backend = RecordingBackend()
    _, service = _service(tmp_path, backend)

    service.send_message(message="hi", session_id="s3", context={"allow_deterministic_evidence": False})

    assert FACTS_HEADER not in _system_text(backend.prompts[-1])


def test_each_turn_persists_exactly_one_query_and_one_response(tmp_path: Path) -> None:
    backend = RecordingBackend()
    memory, service = _service(tmp_path, backend)

    reply = service.send_message(message="hi", session_id="s4", request_id="turn-1")

    events = memory.event_store.list_events_for_request("turn-1", limit=200, ascending=True)
    types = [event.type for event in events]
    assert types.count("agent_query") == 1
    assert types.count("agent_response") == 1
    assert "environment_snapshot" in types
    assert reply["result"]["provider"] == "llamacpp"


def test_quick_and_standard_modes_carry_real_transcripts() -> None:
    agent = object.__new__(JARVISAgent)
    session_messages = [
        {"role": "user" if index % 2 == 0 else "assistant", "text": f"message {index}"}
        for index in range(12)
    ]

    def history_count(prompt_mode: str) -> int:
        messages, _ = agent._build_chat_messages(
            "why that one",
            {"memory_context": {"session_messages": session_messages, "prompt_mode": prompt_mode}},
        )
        return sum(1 for message in messages if message["content"].startswith("message "))

    assert history_count("quick") > 0
    assert history_count("standard") > 4


def test_backend_down_is_reported_as_not_running(tmp_path: Path) -> None:
    _, service = _service(tmp_path, DownBackend())

    reply = service.send_message(message="hi", session_id="s5")

    assert reply["status"] == "error"
    assert reply["error_type"] == "endpoint_not_ready"
