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


@pytest.mark.parametrize("query", [
    "what still seems messy to you?",
    "where is this still fighting itself?",
    "what is starting to stabilize now?",
    "what seems like the biggest drag on coherence?",
    "does this still feel fragile to you or is it getting more solid?",
])
def test_reflective_followups_ground_without_naming_the_project(monkeypatch, query):
    """An earlier guard required the query to say "obeos" to receive grounding.

    That silenced every ordinary reflective follow-up -- the operator asks about
    the work without naming it -- which is the conversation the grounding exists
    to support. Scope is decided by subject matter now, not by the project name.
    """
    service = object.__new__(ConversationMemoryService)
    established = [
        {"role": "user", "text": "We have been working on response latency and Telegram cohesion."},
    ]
    assert service._introduces_outside_subject(query, session_messages=established) is False


def test_project_name_is_not_treated_as_a_new_entity():
    """OBEOS is capitalized mid-sentence but is never outside subject matter."""
    service = object.__new__(ConversationMemoryService)
    assert service._introduces_outside_subject(
        "talk to me honestly about where you think OBEOS is heading",
        session_messages=[],
    ) is False


def test_unseen_named_entities_put_a_turn_out_of_scope():
    service = object.__new__(ConversationMemoryService)
    assert service._introduces_outside_subject(
        "I am planning a picnic on Tuesday at Cedar Park. Remember that for this chat.",
        session_messages=[],
    ) is True


def test_a_topic_the_session_already_discussed_stays_in_scope():
    service = object.__new__(ConversationMemoryService)
    established = [{"role": "user", "text": "The sourdough starter needs feeding twice a day."}]
    assert service._introduces_outside_subject(
        "how long should sourdough rise?", session_messages=established
    ) is False
    # ...but the same question is out of scope in a session that never raised it.
    assert service._introduces_outside_subject(
        "how long should sourdough rise?", session_messages=[]
    ) is True


def test_the_turn_being_judged_does_not_establish_its_own_context():
    """Otherwise any new topic would qualify as established on the turn that raises it."""
    service = object.__new__(ConversationMemoryService)
    query = "What matters most when making bread?"
    assert service._introduces_outside_subject(
        query, session_messages=[{"role": "user", "text": query}]
    ) is True
