from blackfong_ecosystem.history_budget import estimated_tokens, recent_history


def test_short_history_is_not_limited_to_four_messages():
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "text": str(i)} for i in range(12)]
    assert recent_history(messages, 500) == messages


def test_new_correction_wins_over_old_history():
    messages = [
        {"role": "user", "text": "I took the bus."},
        {"role": "assistant", "text": "Understood."},
        {"role": "user", "text": "Correction: I took the train."},
    ]
    assert recent_history(messages, estimated_tokens(messages[-1]["text"])) == messages[-1:]


def test_never_skips_oversized_correction_to_restore_stale_facts():
    messages = [{"role": "user", "text": "bus"}, {"role": "user", "text": "correction " * 100}]
    assert recent_history(messages, 30) == []


def test_drops_orphan_assistant_and_obeys_empty_budget():
    messages = [{"role": "assistant", "text": "yes"}, {"role": "user", "text": "why?"}]
    assert recent_history(messages, 100) == messages[1:]
    assert recent_history(messages, 0) == []
