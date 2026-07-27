from __future__ import annotations

from unittest.mock import patch

import pytest

from blackfong_ecosystem.chat_backends import ChatBackendError, is_chain_retry_error
from blackfong_ecosystem.display_names import display_agent_name, format_chat_status_line
from blackfong_ecosystem.ollama_models import filter_model_chain


def test_display_agent_name_normalizes_jarvis_to_daise() -> None:
    assert display_agent_name("jarvis") == "D.A.I.S.E"
    assert display_agent_name("daise") == "D.A.I.S.E"


def test_format_chat_status_line_includes_mode_and_confidence() -> None:
    line = format_chat_status_line(
        result={"selected_agent": "jarvis", "confidence": 0.82, "execution_time_ms": 1500},
        analysis_mode="fast",
        request_id="abc123",
    )
    assert "D.A.I.S.E" in line
    assert "fast" in line
    assert "0.82" in line
    assert "abc123" in line


def test_filter_model_chain_keeps_installed_only() -> None:
    with patch("blackfong_ecosystem.ollama_models.fetch_installed_models", return_value={"llama3.2:3b", "deepseek-r1:8b"}):
        filtered = filter_model_chain(["gemma2:2b", "llama3.2:3b", "phi3:mini"])
    assert filtered == ["llama3.2:3b"]


@pytest.mark.parametrize(
    "error_type,expected",
    [
        ("http_404", True),
        ("model_not_found", True),
        ("empty_stream", True),
        ("timeout", False),
    ],
)
def test_is_chain_retry_error(error_type: str, expected: bool) -> None:
    exc = ChatBackendError("failed", metadata={"error_type": error_type})
    assert is_chain_retry_error(exc) is expected
