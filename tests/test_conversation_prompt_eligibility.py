from types import SimpleNamespace

import pytest

from blackfong_ecosystem.conversation_memory import ConversationMemoryService


@pytest.mark.parametrize("payload", [
    {"error": "timeout"}, {"backend_error": "unavailable"},
    {"success": False},
    {"status": "cancelled"}, {"demonstrably_incorrect": True},
    {"backend_metadata": {"response_complete": False}},
    {"backend_metadata": {"fallback_mode": "reactive"}},
])
def test_failed_assistant_output_is_not_prompt_evidence(payload):
    assert not ConversationMemoryService._response_prompt_eligible("agent_response", payload)
    assert ConversationMemoryService._response_prompt_eligible("agent_query", payload)


def test_history_keeps_failed_turns_and_requests_latest_events():
    calls = []
    def events(session, **kwargs):
        calls.append(kwargs)
        return [SimpleNamespace(id=str(index), type="agent_response", source="daise",
            timestamp=index * 1000, metadata={}, payload={"text": str(index),
            "error": "timeout" if index == 3 else None}) for index in (3, 2)]
    service = object.__new__(ConversationMemoryService)
    service.event_store = SimpleNamespace(list_events_for_session=events)
    messages = service.get_session_messages("isolated", limit=2)
    assert calls[0]["ascending"] is False
    assert [message["text"] for message in messages] == ["2", "3"]
    assert messages[1]["prompt_eligible"] is False


def test_pipeline_omits_failed_assistant_but_preserves_user():
    from blackfong_ecosystem.daise.reasoning_pipeline import _normalize_conversation
    rows = _normalize_conversation({"session_messages": [
        {"role": "user", "text": "Are you there?"},
        {"role": "assistant", "text": "Invented status", "prompt_eligible": False},
        {"role": "assistant", "text": "Hello", "prompt_eligible": True},
    ]})
    assert [row["content"] for row in rows] == ["Are you there?", "Hello"]


def test_new_summary_does_not_relearn_failed_output():
    service = object.__new__(ConversationMemoryService)
    summary = service._summarize_messages([
        {"role": "user", "text": "Hello"},
        {"role": "assistant", "text": "Bad old answer", "prompt_eligible": False},
    ])
    assert "Bad old answer" not in summary
