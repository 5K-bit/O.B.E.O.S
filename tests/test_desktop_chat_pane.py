from __future__ import annotations

import pytest


def _build_chat_pane(*, session_mode: str = "resume_shared", messages: list[dict] | None = None):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from desktop_shell import ChatPane

    QApplication.instance() or QApplication([])

    class _FakeOperatorChat:
        def send_message(self, **kwargs):
            return kwargs

    class _FakeMemoryService:
        def get_session_messages(self, session_id: str, limit: int = 80):
            return list(messages or [])

    return ChatPane(
        operator_chat=_FakeOperatorChat(),
        memory_service=_FakeMemoryService(),
        session_boot_mode_getter=lambda: session_mode,
    )


def test_chat_pane_streaming_state_and_full_reply_render():
    pane = _build_chat_pane()
    long_reply = "X" * 1400

    pane._append_stream_token(long_reply[:700])
    assert pane.status_label.text().startswith("STREAMING")

    pane._append_stream_token(long_reply[700:])
    pane._handle_result(
        {
            "status": "complete",
            "result": {"response": long_reply},
            "messages": [],
            "selection": None,
            "analysis_mode": "fast",
            "request_id": "req-1",
        }
    )

    transcript_text = pane.transcript.toPlainText()
    assert long_reply in transcript_text
    assert pane.status_label.text().startswith("COMPLETE")
    from PySide6.QtWidgets import QTextEdit

    assert pane.transcript.lineWrapMode() == QTextEdit.WidgetWidth
    assert pane.analysis_select.currentData() == "standard"


def test_chat_pane_history_renders_full_assistant_message():
    pane = _build_chat_pane()
    long_reply = "memory-context-" * 120

    html = pane._messages_to_html(
        [
            {"role": "assistant", "agent": "daise", "text": long_reply},
        ]
    )

    assert long_reply in html


def test_stream_has_separate_speaker_and_final_response_wins():
    pane = _build_chat_pane()
    pane.transcript.setHtml(pane._message_card_html(role="user", text="Hello"))
    pane._append_stream_token("Partial")
    assert "Hello\nD.A.I.S.E" in pane.transcript.toPlainText()
    pane._handle_result({"status": "complete", "result": {"response": "Final answer"}})
    assert "Final answer" in pane.transcript.toPlainText()
    assert "Partial" not in pane.transcript.toPlainText()


def test_blank_reply_is_not_success_and_late_tokens_are_ignored():
    pane = _build_chat_pane()
    pane._handle_result({"status": "complete", "result": {"response": " "}})
    assert pane.status_label.text().startswith("FAILED")
    before = pane.transcript.toPlainText()
    pane._append_stream_token("Late token")
    assert pane.transcript.toPlainText() == before


def test_chat_pane_message_body_preserves_line_breaks():
    pane = _build_chat_pane()
    html = pane._message_card_html(role="assistant", text="line one\nline two", agent="daise")

    assert "line one<br/>line two" in html


def test_chat_pane_resume_shared_session_shows_history_hint():
    pane = _build_chat_pane(
        session_mode="resume_shared",
        messages=[{"role": "assistant", "text": "prior context"}],
    )

    assert "(shared)" in pane.session_label.text()
    assert "Shared session resumed." in pane.transcript.toPlainText()
    assert pane.refresh_button.isEnabled() is True


def test_chat_pane_fresh_local_session_stays_local():
    pane = _build_chat_pane(session_mode="fresh_local")

    assert "(local)" in pane.session_label.text()
    assert "Fresh local session ready." in pane.transcript.toPlainText()
    assert pane.new_session_button.text() == "Fresh Local Session"


def test_busy_chat_rejects_shortcut_send_and_history_changes():
    from types import SimpleNamespace
    pane = _build_chat_pane()
    pane._worker = SimpleNamespace(isRunning=lambda: True)
    pane.transcript.setPlainText("You: original\nD.A.I.S.E: streaming")
    before = pane.transcript.toPlainText()
    session = pane.session_id
    pane.input.setPlainText("Second send")
    pane.new_session()
    pane.refresh_history()
    pane.send_message()
    assert pane.session_id == session
    assert pane.transcript.toPlainText() == before
    assert pane.input.toPlainText() == "Second send"
    pane._worker = None
