import pytest

from blackfong_ecosystem.daise.core import DAISE


@pytest.mark.parametrize("text", [
    "test the connection", "debug the HUD", "what progress have we made?",
    "let us change the topic", "what is a certification?",
    "my exam was exhausting", "the logs show a failed test",
])
def test_conversation_does_not_request_training(text):
    assert not DAISE._is_training_intent(text)


@pytest.mark.parametrize("text", [
    "quiz me on networking", "help me study python", "start a quiz",
    "resume my training", "make a study plan", "test me on security",
])
def test_explicit_training_intent(text):
    assert DAISE._is_training_intent(text)


@pytest.mark.parametrize("text", ["test the connection", "debug the HUD", "what progress have we made?", "change the topic"])
def test_active_pipeline_does_not_dispatch_lilo_for_keywords(text):
    from blackfong_ecosystem.daise.reasoning_pipeline import _select_agent
    assert _select_agent(text, agents={"jarvis": object(), "lilo": object()},
        routing_keywords={"lilo": ["test", "debug", "progress", "topic"]},
        agent_override=None, modality="text") == "jarvis"


def test_active_pipeline_keeps_explicit_lilo_override():
    from blackfong_ecosystem.daise.reasoning_pipeline import _select_agent
    assert _select_agent("hello", agents={"jarvis": object(), "lilo": object()},
        routing_keywords={}, agent_override="lilo", modality="text") == "lilo"
