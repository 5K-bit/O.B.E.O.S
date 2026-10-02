import pytest

from blackfong_ecosystem.conversation_memory import ConversationMemoryService


@pytest.mark.parametrize("query", [
    "I am planning a fictional picnic on Tuesday at Cedar Park. Remember that for this chat.",
    "What is the purpose of a rain garden?",
    "What matters most when making bread?",
])
def test_general_topics_do_not_receive_obeos_project_narratives(monkeypatch, query):
    service = object.__new__(ConversationMemoryService)
    monkeypatch.setattr(service, "_detect_conversational_recall_frame", lambda q: "mission")
    assert service.grounded_conversational_answer(
        session_id="evaluation", query=query,
        session_messages=[{"role": "user", "text": query}],
    ) is None
