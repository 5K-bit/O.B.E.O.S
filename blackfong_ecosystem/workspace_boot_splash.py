"""Cinematic boot splash overlay for the PySide6 workspace."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    from PySide6.QtCore import (
        Property,
        QEasingCurve,
        QParallelAnimationGroup,
        QPropertyAnimation,
        QSequentialAnimationGroup,
        Qt,
        QTimer,
    )
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QGraphicsOpacityEffect,
        QLabel,
        QProgressBar,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover
    QWidget = object  # type: ignore[misc, assignment]
    Property = lambda *args, **kwargs: None  # type: ignore[misc, assignment]

from .debug_session_log import debug_session_log


TERMINAL_BOOT_STATES = frozenset({"ready", "active", "degraded", "offline", "manual_only"})

_BOOT_FILES = {
    "earth": ("boot", "01-earth.jpg"),
    "forming": ("boot", "02-ring-forming.jpg"),
    "ring": ("boot", "03-ring-arc.jpg"),
    "wordmark": ("boot", "04-wordmark.jpg"),
    "end": ("boot", "07-end-look.jpg"),
}


def branding_root() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "branding"


def boot_asset_path(name: str) -> Path:
    parts = _BOOT_FILES[name]
    return branding_root().joinpath(*parts)


def _brain_metrics_line(snapshot: Dict[str, Any]) -> str:
    cognition = snapshot.get("cognition") or snapshot.get("memory_chamber") or {}
    if not isinstance(cognition, dict):
        return ""
    graph = cognition.get("graph") or cognition.get("snapshot") or {}
    if isinstance(graph, dict):
        nodes = graph.get("visible_nodes") or graph.get("nodes") or []
        node_count = len(nodes) if isinstance(nodes, list) else int(graph.get("node_count") or 0)
    else:
        node_count = int(cognition.get("node_count") or 0)
    event_count = int(cognition.get("event_count") or cognition.get("capsule_count") or 0)
    if node_count <= 0 and event_count <= 0:
        return ""
    growth = 0.85 + min(0.65, (node_count + event_count) / 80.0)
    return f"Brain scale {growth:.2f}× · {node_count} nodes"


def _cover_pixmap(source: QPixmap, width: int, height: int) -> QPixmap:
    if source.isNull() or width <= 0 or height <= 0:
        return QPixmap()
    scaled = source.scaled(width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    if scaled.width() == width and scaled.height() == height:
        return scaled
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    return scaled.copy(x, y, width, height)


class FrameLayer(QWidget):
    """Full-bleed pixmap layer with opacity and optional left-to-right reveal."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._source = QPixmap()
        self._fitted = QPixmap()
        self._opacity = 0.0
        self._reveal = 1.0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_source(self, pixmap: QPixmap) -> None:
        self._source = pixmap
        self._fitted = QPixmap()
        self.update()

    def getOpacity(self) -> float:
        return self._opacity

    def setOpacity(self, value: float) -> None:
        self._opacity = max(0.0, min(1.0, float(value)))
        self.update()

    def getReveal(self) -> float:
        return self._reveal

    def setReveal(self, value: float) -> None:
        self._reveal = max(0.0, min(1.0, float(value)))
        self.update()

    opacity = Property(float, getOpacity, setOpacity)
    reveal = Property(float, getReveal, setReveal)

    def resizeEvent(self, event: Any) -> None:  # pragma: no cover - GUI
        self._fitted = QPixmap()
        super().resizeEvent(event)

    def paintEvent(self, event: Any) -> None:  # pragma: no cover - GUI
        if self._opacity <= 0.01 or self._source.isNull():
            return
        if self._fitted.isNull() or self._fitted.size() != self.size():
            self._fitted = _cover_pixmap(self._source, self.width(), self.height())
        if self._fitted.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setOpacity(self._opacity)
        width = max(1, int(self.width() * self._reveal))
        painter.setClipRect(0, 0, width, self.height())
        painter.drawPixmap(0, 0, self._fitted)
        painter.end()


_SPLASH_STYLE = """
WorkspaceBootSplash {
    background: #02040a;
    color: #f4f7fb;
}
QLabel#bootCorner {
    color: rgba(236, 242, 250, 0.72);
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 5px;
    line-height: 1.7;
    background: transparent;
}
QLabel#bootWordmark {
    color: #f5f8fc;
    font-size: 42px;
    font-weight: 300;
    letter-spacing: 18px;
    background: transparent;
}
QLabel#bootTagline {
    color: rgba(232, 238, 246, 0.78);
    font-size: 13px;
    font-weight: 400;
    letter-spacing: 8px;
    background: transparent;
}
QLabel#bootStarting {
    color: rgba(236, 242, 250, 0.86);
    font-size: 14px;
    background: transparent;
}
QLabel#bootStatus {
    color: rgba(170, 186, 210, 0.75);
    font-size: 11px;
    background: transparent;
}
QProgressBar#bootProgress {
    border: none;
    border-radius: 4px;
    background: rgba(255, 255, 255, 0.12);
    min-height: 6px;
    max-height: 6px;
    max-width: 280px;
}
QProgressBar#bootProgress::chunk {
    border-radius: 4px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #3aa7ff,
        stop: 0.55 #6a6cff,
        stop: 1 #c06bff
    );
}
QPushButton#bootContinue {
    background: transparent;
    color: rgba(236, 242, 250, 0.88);
    border: 1px solid rgba(236, 242, 250, 0.28);
    border-radius: 16px;
    padding: 8px 18px;
    letter-spacing: 1px;
}
QPushButton#bootContinue:hover {
    border-color: rgba(122, 176, 255, 0.7);
    color: #ffffff;
}
"""


class WorkspaceBootSplash(QWidget):
    """Full-window cinematic boot overlay until startup reports ready or timeout."""

    def __init__(
        self,
        parent: QWidget,
        *,
        snapshot_provider: Callable[[], Dict[str, Any]],
        warmup_status_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        max_seconds: float = 30.0,
    ) -> None:
        super().__init__(parent)
        self._snapshot_provider = snapshot_provider
        self._warmup_status_provider = warmup_status_provider
        self._max_seconds = max_seconds
        self._started = time.time()
        self._dismissed = False
        self._intro_complete = False
        self._ready_to_leave = False
        self._fade_effect: Optional[QGraphicsOpacityEffect] = None
        self._timeline: Optional[QSequentialAnimationGroup] = None

        self.setObjectName("WorkspaceBootSplash")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_SPLASH_STYLE)

        self.earth = FrameLayer(self)
        self.forming = FrameLayer(self)
        self.ring = FrameLayer(self)
        self.wordmark_scene = FrameLayer(self)
        self.end_look = FrameLayer(self)

        self._load_frames()
        self.earth.setOpacity(1.0)
        self.forming.setOpacity(0.0)
        self.ring.setOpacity(1.0)
        self.ring.setReveal(0.0)
        self.wordmark_scene.setOpacity(0.0)
        self.end_look.setOpacity(0.0)

        self.corner_tl = QLabel("SIMPLE\nOPEN\nHUMAN\nTOMORROW", self)
        self.corner_tl.setObjectName("bootCorner")
        self.corner_tr = QLabel("PEOPLE\nIDEAS\nPOSSIBILITY", self)
        self.corner_tr.setObjectName("bootCorner")
        self.corner_tr.setAlignment(Qt.AlignRight)
        self.corner_bl = QLabel("OBEOS\nEST. 2024", self)
        self.corner_bl.setObjectName("bootCorner")
        self.corner_br = QLabel("BUILT\nFOR A MORE\nOPEN WORLD", self)
        self.corner_br.setObjectName("bootCorner")
        self.corner_br.setAlignment(Qt.AlignRight)

        self.chrome = QWidget(self)
        chrome_layout = QVBoxLayout(self.chrome)
        chrome_layout.setContentsMargins(0, 0, 0, 0)
        chrome_layout.setSpacing(10)
        chrome_layout.setAlignment(Qt.AlignHCenter)

        self.wordmark = QLabel("OBEOS", self.chrome)
        self.wordmark.setObjectName("bootWordmark")
        self.wordmark.setAlignment(Qt.AlignCenter)
        word_font = QFont("Segoe UI")
        word_font.setWeight(QFont.Light)
        word_font.setLetterSpacing(QFont.AbsoluteSpacing, 14)
        word_font.setPointSize(36)
        self.wordmark.setFont(word_font)
        if not self.wordmark_scene._source.isNull():
            self.wordmark.hide()

        self.tagline = QLabel("A BRIGHTER WAY FORWARD", self.chrome)
        self.tagline.setObjectName("bootTagline")
        self.tagline.setAlignment(Qt.AlignCenter)

        self.starting = QLabel("Starting OBEOS...", self.chrome)
        self.starting.setObjectName("bootStarting")
        self.starting.setAlignment(Qt.AlignCenter)

        self.progress = QProgressBar(self.chrome)
        self.progress.setObjectName("bootProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(4)
        self.progress.setTextVisible(False)

        self.status_label = QLabel("Booting workspace", self.chrome)
        self.status_label.setObjectName("bootStatus")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)

        self.continue_button = QPushButton("Continue in degraded mode", self.chrome)
        self.continue_button.setObjectName("bootContinue")
        self.continue_button.hide()
        self.continue_button.clicked.connect(self._dismiss)

        chrome_layout.addStretch(7)
        chrome_layout.addWidget(self.wordmark)
        chrome_layout.addWidget(self.tagline)
        chrome_layout.addSpacing(18)
        chrome_layout.addWidget(self.starting)
        chrome_layout.addWidget(self.progress, 0, Qt.AlignHCenter)
        chrome_layout.addWidget(self.status_label)
        chrome_layout.addWidget(self.continue_button, 0, Qt.AlignHCenter)
        chrome_layout.addStretch(3)

        self._chrome_effect = QGraphicsOpacityEffect(self.chrome)
        self._chrome_effect.setOpacity(0.0)
        self.chrome.setGraphicsEffect(self._chrome_effect)
        for corner in (self.corner_tl, self.corner_tr, self.corner_bl, self.corner_br):
            effect = QGraphicsOpacityEffect(corner)
            effect.setOpacity(0.0)
            corner.setGraphicsEffect(effect)
            corner._opacity_effect = effect  # type: ignore[attr-defined]

        self._timer = QTimer(self)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        QTimer.singleShot(80, self._play_intro)
        for corner in (self.corner_tl, self.corner_tr, self.corner_bl, self.corner_br):
            corner.raise_()

    def _load_frames(self) -> None:
        for layer, key in (
            (self.earth, "earth"),
            (self.forming, "forming"),
            (self.ring, "ring"),
            (self.wordmark_scene, "wordmark"),
            (self.end_look, "end"),
        ):
            path = boot_asset_path(key)
            pixmap = QPixmap(str(path)) if path.is_file() else QPixmap()
            layer.set_source(pixmap)

    def _animate_prop(self, target: Any, name: bytes, start: float, end: float, duration: int, curve: QEasingCurve.Type) -> QPropertyAnimation:
        anim = QPropertyAnimation(target, name, self)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setDuration(duration)
        anim.setEasingCurve(curve)
        return anim

    def _play_intro(self) -> None:
        if self._dismissed:
            return
        timeline = QSequentialAnimationGroup(self)

        form = self._animate_prop(self.forming, b"opacity", 0.0, 1.0, 1100, QEasingCurve.InOutCubic)
        timeline.addPause(280)
        timeline.addAnimation(form)

        sweep = QParallelAnimationGroup(self)
        sweep.addAnimation(self._animate_prop(self.ring, b"reveal", 0.0, 1.0, 1400, QEasingCurve.InOutCubic))
        sweep.addAnimation(self._animate_prop(self.forming, b"opacity", 1.0, 0.0, 900, QEasingCurve.OutCubic))
        timeline.addAnimation(sweep)

        mark = self._animate_prop(self.wordmark_scene, b"opacity", 0.0, 1.0, 1100, QEasingCurve.InOutQuad)
        timeline.addAnimation(mark)

        chrome = QParallelAnimationGroup(self)
        chrome.addAnimation(self._animate_prop(self._chrome_effect, b"opacity", 0.0, 1.0, 900, QEasingCurve.OutCubic))
        for corner in (self.corner_tl, self.corner_tr, self.corner_bl, self.corner_br):
            chrome.addAnimation(
                self._animate_prop(corner._opacity_effect, b"opacity", 0.0, 1.0, 1000, QEasingCurve.OutCubic)  # type: ignore[attr-defined]
            )
        timeline.addAnimation(chrome)
        timeline.finished.connect(self._on_intro_finished)
        self._timeline = timeline
        timeline.start()

    def _on_intro_finished(self) -> None:
        self._intro_complete = True
        if self._ready_to_leave:
            self._dismiss()

    def _tick(self) -> None:
        snapshot = self._snapshot_provider()
        startup = snapshot if snapshot.get("subsystems") else snapshot.get("startup", snapshot)
        subsystems = startup.get("subsystems", {})
        overall = str(startup.get("overall_state") or "booting")
        ready_degraded = bool(startup.get("ready_but_degraded"))

        keys = list(subsystems.keys())
        ready_count = sum(
            1
            for key in keys
            if str((subsystems.get(key) or {}).get("state") or "") in TERMINAL_BOOT_STATES
        )
        percent = int((ready_count / max(len(keys), 1)) * 100) if keys else 6
        display_percent = max(6, percent)
        self.progress.setValue(display_percent)

        warmup_line = ""
        if self._warmup_status_provider is not None:
            warmup = self._warmup_status_provider() or {}
            warmup_state = str(warmup.get("status") or "idle")
            warmup_model = str(warmup.get("model") or "")
            if warmup_state == "warming":
                warmup_line = f"Model warming {warmup_model}".strip()
            elif warmup_state == "ready":
                warmup_line = f"Model ready {warmup_model}".strip()
            elif warmup_state == "error":
                warmup_line = str(warmup.get("error") or "warmup failed")
        brain_line = _brain_metrics_line(snapshot)
        status_bits = [f"Overall {overall.replace('_', ' ')}"]
        if warmup_line:
            status_bits.append(warmup_line)
        if brain_line:
            status_bits.append(brain_line)
        self.status_label.setText("  ·  ".join(status_bits))

        elapsed = time.time() - self._started
        if overall in {"ready", "active"} or ready_degraded:
            self._ready_to_leave = True
            if self._intro_complete:
                self._dismiss()
            return
        if elapsed >= self._max_seconds:
            self.continue_button.show()
            self.status_label.setText("Boot is taking longer than expected. Continue anyway or wait.")

    def _dismiss(self) -> None:
        if self._dismissed:
            return
        self._dismissed = True
        self._timer.stop()
        if self._timeline is not None:
            self._timeline.stop()
        snapshot = self._snapshot_provider()
        startup = snapshot if snapshot.get("subsystems") else snapshot.get("startup", snapshot)
        debug_session_log(
            hypothesis_id="J",
            location="workspace_boot_splash.py:_dismiss",
            message="Boot splash dismissed",
            data={
                "overall_state": startup.get("overall_state"),
                "ready_but_degraded": startup.get("ready_but_degraded"),
                "elapsed_s": round(time.time() - self._started, 2),
            },
        )
        self._fade_effect = QGraphicsOpacityEffect(self)
        self._fade_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._fade_effect)
        fade = QPropertyAnimation(self._fade_effect, b"opacity", self)
        fade.setDuration(700)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.InOutQuad)
        fade.finished.connect(self._finish_dismiss)
        self._fade_anim = fade
        fade.start()

    def _finish_dismiss(self) -> None:
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "_boot_splash"):
            parent._boot_splash = None  # type: ignore[attr-defined]
        self.hide()
        self.deleteLater()

    def resizeEvent(self, event: Any) -> None:  # pragma: no cover - GUI
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        rect = self.rect()
        for layer in (self.earth, self.forming, self.ring, self.wordmark_scene, self.end_look, self.chrome):
            layer.setGeometry(rect)
        margin = max(36, rect.width() // 28)
        self.corner_tl.adjustSize()
        self.corner_tr.adjustSize()
        self.corner_bl.adjustSize()
        self.corner_br.adjustSize()
        self.corner_tl.move(margin, margin)
        self.corner_tr.move(rect.width() - margin - self.corner_tr.width(), margin)
        self.corner_bl.move(margin, rect.height() - margin - self.corner_bl.height())
        self.corner_br.move(
            rect.width() - margin - self.corner_br.width(),
            rect.height() - margin - self.corner_br.height(),
        )
        for corner in (self.corner_tl, self.corner_tr, self.corner_bl, self.corner_br):
            corner.raise_()
        width = max(self.width(), 640)
        point_size = min(48, max(28, width // 28))
        font = self.wordmark.font()
        font.setPointSize(point_size)
        self.wordmark.setFont(font)
        super().resizeEvent(event)
