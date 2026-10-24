"""Embedded MJPEG stream viewer for TinyProgrammer inside the workspace."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from .http_clients import client_for

try:
    from PySide6.QtCore import QByteArray, Qt, QThread, Signal
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
except ImportError:  # pragma: no cover
    QWidget = object  # type: ignore[misc, assignment]
    QThread = object  # type: ignore[misc, assignment]
    Signal = object  # type: ignore[misc, assignment]

logger = logging.getLogger(__name__)


class TinyStreamWorker(QThread):
    """Read MJPEG frames off the UI thread."""

    frame_ready = Signal(bytes)
    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, stream_url: str) -> None:
        super().__init__()
        self._stream_url = stream_url
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        if not self._stream_url:
            self.status_changed.emit("offline")
            return
        self.status_changed.emit("connecting")
        buffer = bytearray()
        try:
            timeout = httpx.Timeout(connect=3.0, read=30.0, write=5.0, pool=3.0)
            with client_for(self._stream_url, timeout=timeout) as client:
                with client.stream("GET", self._stream_url) as response:
                    response.raise_for_status()
                    self.status_changed.emit("connected")
                    for chunk in response.iter_bytes(chunk_size=4096):
                        if not self._running:
                            break
                        buffer.extend(chunk)
                        while True:
                            frame = TinyStreamView.extract_jpeg(buffer)
                            if frame is None:
                                break
                            self.frame_ready.emit(frame)
        except Exception as exc:
            logger.debug("Tiny stream worker failed: %s", exc)
            self.error_occurred.emit(str(exc))
            self.status_changed.emit("error")


class TinyStreamView(QWidget):
    """Displays TinyProgrammer /stream frames from a background worker."""

    status_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._stream_url = ""
        self._worker: Optional[TinyStreamWorker] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.display = QLabel("TinyProgrammer stream offline", self)
        self.display.setAlignment(Qt.AlignCenter)
        self.display.setMinimumHeight(240)
        self.display.setStyleSheet(
            "background: #0a0f14; border: 1px solid #273647; border-radius: 10px; color: #9db2c6;"
        )
        layout.addWidget(self.display)

    def set_stream_url(self, url: str) -> None:
        normalized = str(url or "").strip()
        if normalized == self._stream_url:
            return
        self.stop()
        self._stream_url = normalized
        if not normalized:
            self.display.setText("No stream URL")
            self.status_changed.emit("offline")
            return
        self._worker = TinyStreamWorker(normalized)
        self._worker.frame_ready.connect(self._show_frame)
        self._worker.status_changed.connect(self._on_worker_status)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.start()

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            if self._worker.isRunning():
                self._worker.wait(1500)
            self._worker.deleteLater()
            self._worker = None

    def _on_worker_status(self, status: str) -> None:
        self.status_changed.emit(status)
        if status == "connecting":
            self.display.setText("Connecting to TinyProgrammer stream…")

    def _on_worker_error(self, message: str) -> None:
        self.display.setText(f"Stream unavailable: {message}")

    def _show_frame(self, frame: bytes) -> None:
        image = QImage.fromData(QByteArray(frame))
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(self.display.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.display.setPixmap(scaled)

    @staticmethod
    def extract_jpeg(buffer: bytearray) -> Optional[bytes]:
        start = buffer.find(b"\xff\xd8")
        if start < 0:
            if len(buffer) > 65536:
                del buffer[:-4096]
            return None
        end = buffer.find(b"\xff\xd9", start + 2)
        if end < 0:
            if len(buffer) > 262144:
                del buffer[: start + 1]
            return None
        frame = bytes(buffer[start : end + 2])
        del buffer[: end + 2]
        return frame
