from blackfong_ecosystem.agents.jarvis_agent import JARVISAgent


def test_history_precedes_changing_evidence_and_current_turn_is_unique():
    agent = object.__new__(JARVISAgent)
    context = {"memory_context": {"prompt_mode": "standard",
        "prompt_context": "Source A: verified evidence",
        "session_messages": [
            {"role": "user", "text": "I missed a bus."},
            {"role": "assistant", "text": "What happened next?"},
            {"role": "user", "text": "It began raining."},
        ]}}
    messages, metrics = agent._build_chat_messages("It began raining.", context)
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "I missed a bus."}
    assert messages[2] == {"role": "assistant", "content": "What happened next?"}
    assert "Source A" in messages[3]["content"]
    assert messages[-1] == {"role": "user", "content": "It began raining."}
    assert sum(m["content"] == "It began raining." for m in messages) == 1
    assert "Direct Artificial Intelligence System Environment" in messages[0]["content"]
    assert metrics["system_prompt_chars"] < 1000
