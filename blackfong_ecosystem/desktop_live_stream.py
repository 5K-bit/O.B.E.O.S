"""Optional HUD SSE bridge for the PySide6 workspace."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from .debug_session_log import debug_session_log

try:
    from PySide6.QtCore import QThread, Signal
except ImportError:  # pragma: no cover
    QThread = object  # type: ignore[misc, assignment]
    Signal = object  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)


class DesktopLiveStreamWorker(QThread):
    """Subscribe to the Thoughtglass HUD live SSE feed when available."""

    event_received = Signal(str, dict)
    connection_changed = Signal(bool)

    def __init__(self, *, url: str = "http://127.0.0.1:8080/api/live/stream", parent: Optional[Any] = None) -> None:
        super().__init__(parent)
        self._url = url
        self._stop_requested = False
        debug_session_log(
            hypothesis_id="A",
            location="desktop_live_stream.py:__init__",
            message="DesktopLiveStreamWorker initialized",
            data={"url": url, "parent_is_none": parent is None},
        )

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        debug_session_log(
            hypothesis_id="A",
            location="desktop_live_stream.py:run",
            message="DesktopLiveStreamWorker thread started",
            data={"url": self._url},
        )
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            debug_session_log(
                hypothesis_id="A",
                location="desktop_live_stream.py:run",
                message="httpx import failed",
                data={"error": str(exc)},
            )
            self.connection_changed.emit(False)
            return

        self._stop_requested = False
        try:
            from .http_clients import client_for

            with client_for(self._url, timeout=httpx.Timeout(connect=3.0, read=None, write=10.0, pool=5.0)) as client:
                with client.stream("GET", self._url, headers={"Accept": "text/event-stream"}) as response:
                    debug_session_log(
                        hypothesis_id="A",
                        location="desktop_live_stream.py:run",
                        message="SSE connect response",
                        data={"status_code": response.status_code},
                    )
                    if response.status_code >= 400:
                        self.connection_changed.emit(False)
                        return
                    self.connection_changed.emit(True)
                    event_type: Optional[str] = None
                    data_lines: list[str] = []
                    for raw_line in response.iter_lines():
                        if self._stop_requested:
                            break
                        line = str(raw_line or "")
                        if line.startswith("event:"):
                            event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].strip())
                        elif not line.strip():
                            if event_type:
                                payload: Dict[str, Any] = {}
                                if data_lines:
                                    try:
                                        payload = json.loads("\n".join(data_lines))
                                    except json.JSONDecodeError:
                                        payload = {"raw": "\n".join(data_lines)}
                                self.event_received.emit(event_type, payload)
                            event_type = None
                            data_lines = []
        except Exception as exc:
            logger.debug("Desktop live stream unavailable: %s", exc)
            debug_session_log(
                hypothesis_id="A",
                location="desktop_live_stream.py:run",
                message="SSE stream exception",
                data={"error": str(exc), "error_type": type(exc).__name__},
            )
            self.connection_changed.emit(False)
        else:
            self.connection_changed.emit(False)
