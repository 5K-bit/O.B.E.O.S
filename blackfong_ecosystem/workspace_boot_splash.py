"""Boot splash overlay for the PySide6 workspace."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QFrame,
        QHBoxLayout,
        QLabel,
        QProgressBar,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover
    QWidget = object  # type: ignore[misc, assignment]

from .obeos_linux import ASCII_BOOT_INTRO


from .debug_session_log import debug_session_log


TERMINAL_BOOT_STATES = frozenset({"ready", "active", "degraded", "offline", "manual_only"})

_SPLASH_STYLE = """
WorkspaceBootSplash {
    background: qradialgradient(
        cx: 0.5, cy: 0.42, radius: 0.95,
        fx: 0.5, fy: 0.42,
        stop: 0 rgba(14, 22, 32, 0.98),
        stop: 0.55 rgba(8, 12, 18, 0.97),
        stop: 1 rgba(4, 6, 10, 0.99)
    );
    color: #ece7dd;
}
QFrame#bootLoadingCard {
    background: rgba(8, 14, 20, 0.88);
    border: 1px solid rgba(143, 119, 67, 0.35);
    border-radius: 16px;
}
QLabel#bootAscii {
    background: transparent;
    color: #f0d49a;
    font-family: Consolas, "Courier New", monospace;
    padding: 8px 0;
}
QLabel#bootPercent {
    color: #f0d49a;
    font-weight: 700;
    min-width: 48px;
}
QProgressBar {
    border: 1px solid #273647;
    border-radius: 8px;
    background: #0d141b;
    text-align: center;
    color: transparent;
    min-height: 14px;
}
QProgressBar::chunk {
    border-radius: 7px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #5eead4,
        stop: 1 #f0d49a
    );
}
"""


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


class WorkspaceBootSplash(QWidget):
    """Semi-modal boot overlay until StartupCoordinator reports ready or timeout."""

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

        self.setObjectName("WorkspaceBootSplash")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_SPLASH_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(48, 48, 48, 48)
        root.addStretch(1)

        center = QWidget(self)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(18)
        center_layout.setAlignment(Qt.AlignHCenter)

        self.intro = QLabel(ASCII_BOOT_INTRO.strip("\n"), center)
        self.intro.setObjectName("bootAscii")
        self.intro.setAlignment(Qt.AlignCenter)
        ascii_font = QFont("Consolas")
        ascii_font.setStyleHint(QFont.Monospace)
        ascii_font.setPointSize(14)
        self.intro.setFont(ascii_font)

        self.status_label = QLabel("Booting OBEOS workspace…", center)
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setProperty("variant", "status")

        loading_card = QFrame(center)
        loading_card.setObjectName("bootLoadingCard")
        card_layout = QVBoxLayout(loading_card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        self.loading_caption = QLabel("Initializing subsystems…", loading_card)
        self.loading_caption.setAlignment(Qt.AlignCenter)

        progress_row = QWidget(loading_card)
        progress_layout = QHBoxLayout(progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(12)

        self.progress = QProgressBar(progress_row)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)

        self.percent_label = QLabel("0%", progress_row)
        self.percent_label.setObjectName("bootPercent")
        self.percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        progress_layout.addWidget(self.progress, 1)
        progress_layout.addWidget(self.percent_label)

        self.brain_label = QLabel("", loading_card)
        self.brain_label.setAlignment(Qt.AlignCenter)
        self.brain_label.setStyleSheet("color: #97a8c2; font-size: 12px;")

        card_layout.addWidget(self.loading_caption)
        card_layout.addWidget(progress_row)
        card_layout.addWidget(self.brain_label)

        self.subsystems_label = QLabel("", center)
        self.subsystems_label.setWordWrap(True)
        self.subsystems_label.setAlignment(Qt.AlignCenter)
        self.subsystems_label.setStyleSheet("color: #97a8c2; font-size: 11px; font-family: Consolas, monospace;")

        self.continue_button = QPushButton("Continue in degraded mode", center)
        self.continue_button.hide()
        self.continue_button.clicked.connect(self._dismiss)

        center_layout.addWidget(self.intro)
        center_layout.addWidget(self.status_label)
        center_layout.addWidget(loading_card)
        center_layout.addWidget(self.subsystems_label)
        center_layout.addWidget(self.continue_button, 0, Qt.AlignHCenter)

        root.addWidget(center, 0, Qt.AlignHCenter)
        root.addStretch(2)

        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

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
        percent = int((ready_count / max(len(keys), 1)) * 100) if keys else 5
        display_percent = max(5, percent)
        self.progress.setValue(display_percent)
        self.percent_label.setText(f"{display_percent}%")

        brain_line = _brain_metrics_line(snapshot)
        self.brain_label.setText(brain_line)
        self.brain_label.setVisible(bool(brain_line))

        lines = []
        optional_pending = []
        for key in keys[:11]:
            state = str((subsystems.get(key) or {}).get("state") or "warming")
            suffix = " (optional)" if key == "tinyprogrammer" and state in {"warming", "booting"} else ""
            line = f"{key}: {state}{suffix}"
            if key == "tinyprogrammer" and state in {"warming", "booting"}:
                optional_pending.append(line)
            else:
                lines.append(line)
        if optional_pending:
            lines.extend(optional_pending)
        self.subsystems_label.setText("\n".join(lines))

        warmup_line = ""
        if self._warmup_status_provider is not None:
            warmup = self._warmup_status_provider() or {}
            warmup_state = str(warmup.get("status") or "idle")
            warmup_model = str(warmup.get("model") or "")
            if warmup_state == "warming":
                warmup_line = f" | Model: warming {warmup_model}".strip()
            elif warmup_state == "ready":
                warmup_line = f" | Model: ready {warmup_model}".strip()
            elif warmup_state == "error":
                warmup_line = f" | Model: {warmup.get('error', 'warmup failed')}"
        self.status_label.setText(f"Overall: {overall.upper()}{warmup_line}")

        elapsed = time.time() - self._started
        if overall in {"ready", "active"} or ready_degraded:
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
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "_boot_splash"):
            parent._boot_splash = None  # type: ignore[attr-defined]
        self.hide()
        self.deleteLater()

    def resizeEvent(self, event: Any) -> None:  # pragma: no cover - GUI
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())
        width = max(self.width(), 640)
        point_size = min(18, max(11, width // 72))
        font = self.intro.font()
        font.setPointSize(point_size)
        self.intro.setFont(font)
        super().resizeEvent(event)
