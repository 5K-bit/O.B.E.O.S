from __future__ import annotations

from blackfong_ecosystem.model_routing import (
    backend_options_for_context,
    detect_intent,
    model_chain_for_agent,
    normalize_response_mode,
    resolve_response_mode,
    should_use_fast_path,
)


def test_normalize_response_mode_aliases():
    assert normalize_response_mode("quick") == "fast"
    assert normalize_response_mode("fast") == "fast"
    assert normalize_response_mode("standard") == "standard"
    assert normalize_response_mode("deep") == "deep"


def test_casual_chat_uses_fast_path():
    context = {"analysis_mode": "fast"}
    assert should_use_fast_path("hello there", context) is True


def test_coding_intent_disables_fast_path():
    context = {"analysis_mode": "fast"}
    assert should_use_fast_path("review this python function for bugs", context) is False


def test_deep_intent_upgrades_mode():
    assert resolve_response_mode("give me a thorough architecture review", "fast") == "deep"


def test_model_chains():
    fast = model_chain_for_agent("jarvis", "fast")
    deep = model_chain_for_agent("jarvis", "deep")
    coding = model_chain_for_agent("coding", "standard")
    assert fast
    assert deep
    assert coding
    assert "deepseek" in deep[0].lower() or "r1" in deep[0].lower()


def test_fast_backend_caps_tokens():
    options = backend_options_for_context({"analysis_mode": "fast"}, "jarvis")
    assert options["mode"] == "fast"
    assert options["num_predict"] >= 384
