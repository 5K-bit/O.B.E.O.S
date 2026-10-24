from types import SimpleNamespace

import pytest

from blackfong_ecosystem.daise.core import DAISE


@pytest.mark.parametrize("mode,error", [("reactive", None), ("preemptive", None), ("none", "timeout")])
def test_failed_live_turn_cannot_be_rewritten_as_success(mode, error):
    report = SimpleNamespace(provider="ollama", model="candidate", response="Unavailable",
                             backend_metadata={"fallback_mode": mode}, backend_error=error)
    result = DAISE._grounded_conversation_override_for_generic_report(
        object.__new__(DAISE), user_input="Remember my fictional picnic", top_report=report,
        selected_agent="jarvis", synthesized=False, memory_bundles={})
    assert result is None
