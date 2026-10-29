"""Provider-independent, conservative history budgeting (not exact tokenization)."""


def estimated_tokens(text: str) -> int:
    return (len(str(text).encode("utf-8")) + 2) // 3 + 8


def recent_history(messages: list[dict], budget: int) -> list[dict]:
    """Keep a chronological suffix without truncating messages or skipping corrections.

    Stop at the first oversized message rather than selecting older facts around
    a newer correction that no longer fits. Never retain an orphan assistant turn.
    """
    selected = []
    remaining = max(0, budget)
    for message in reversed(messages):
        cost = estimated_tokens(message.get("text", ""))
        if cost > remaining:
            break
        selected.append(message)
        remaining -= cost
    selected.reverse()
    while selected and selected[0].get("role") == "assistant":
        selected.pop(0)
    return selected
