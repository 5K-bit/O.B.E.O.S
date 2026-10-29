"""Thread-owned desktop work; results cross the Qt boundary only through signals."""

from __future__ import annotations
from typing import Any, Optional
from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from ..cancellation import CancellationToken, cancellation_scope


class TinyProgrammerAutoStartWorker(QThread):
    """Background worker to run TinyProgrammer auto-start without blocking UI."""

    finished = Signal(dict)

    def __init__(self, tinyprogrammer: Any) -> None:
        super().__init__()
        self.tinyprogrammer = tinyprogrammer

    def run(self) -> None:
        try:
            result = self.tinyprogrammer.ensure_running(
                require_stream=True,
                adopt_external=True,
                open_browser=False,
                consent={"approved": True, "level": 1},
            )
            self.finished.emit(result)
        except Exception as exc:
            self.finished.emit({"success": False, "error": str(exc)})


class ChatWorker(QThread):
    """Run desktop-native D.A.I.S.E chat without freezing the workspace."""

    finished = Signal(dict)
    token_received = Signal(str)

    def __init__(
        self,
        *,
        operator_chat: Any,
        message: str,
        session_id: Optional[str],
        agent_override: Optional[str],
        analysis_mode: str,
        stream: bool = True,
    ) -> None:
        super().__init__()
        self.operator_chat = operator_chat
        self.message = message
        self.session_id = session_id
        self.agent_override = agent_override
        self.analysis_mode = analysis_mode
        self.stream = stream
        self._cancelled = False
        self._cancel_token = CancellationToken()

    def cancel(self) -> None:
        self._cancelled = True
        self._cancel_token.cancel()

    def run(self) -> None:
        if self._cancelled:
            self.finished.emit({"status": "cancelled", "error": "Chat cancelled"})
            return

        def on_token(token):
            self._cancel_token.raise_if_cancelled()
            self.token_received.emit(token)

        try:
            with cancellation_scope(self._cancel_token):
                result = self.operator_chat.send_message(
                    message=self.message,
                    session_id=self.session_id,
                    agent_override=self.agent_override,
                    analysis_mode=self.analysis_mode,
                    stream_callback=on_token if self.stream else None,
                )
            if self._cancelled:
                result = {"status": "cancelled", "error": "Chat cancelled"}
        except Exception as exc:
            result = {
                "status": "cancelled" if self._cancelled else "error",
                "error": str(exc),
            }
        self.finished.emit(result)


class WarmupStatusBridge(QObject):
    """Dispatch every warmup callback on the Qt thread that owns this bridge."""

    status = Signal(dict)

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback
        self.status.connect(self._deliver, Qt.QueuedConnection)

    @Slot(dict)
    def _deliver(self, value):
        self.callback(value)
