"""Command Center home tab: live subsystem status, recent alerts, and launchers.

Read-only view over the workspace snapshot. It never infers health: a missing or old reading is
shown as ``unknown`` or ``stale``, never as ok.
"""
from __future__ import annotations

import time
from html import escape
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .workspace_theme import TOKENS

STALE_AFTER_SECONDS = 120.0
CARD_COLUMNS = 3

_STATE_GROUPS = {
    "ok": {"ready", "active"},
    "warn": {"booting", "warming", "manual_only", "degraded"},
    "bad": {"offline", "missing", "fault", "error"},
}


def classify_state(state: Any, updated_at: Optional[float], *, now: Optional[float] = None) -> Tuple[str, str]:
    """Return (label, token_key) for a subsystem reading. Stale or absent readings are not ok."""
    now = time.time() if now is None else now
    text = str(state or "").strip().lower()
    if not text:
        return "unknown", "state_unknown"
    stale = updated_at is not None and (now - float(updated_at)) > STALE_AFTER_SECONDS
    for group, members in _STATE_GROUPS.items():
        if text in members:
            if stale:
                return f"stale ({text})", "state_unknown"
            return text, f"state_{group}"
    return text, "state_unknown"


def age_text(updated_at: Optional[float], *, now: Optional[float] = None) -> str:
    if updated_at is None:
        return "no timestamp"
    seconds = max(0, int((time.time() if now is None else now) - float(updated_at)))
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


class StatusCard(QFrame):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        self.name = QLabel(self)
        self.state = QLabel(self)
        self.detail = QLabel(self)
        self.detail.setWordWrap(True)
        self.name.setProperty("variant", "kicker")
        for widget in (self.name, self.state, self.detail):
            widget.setStyleSheet("background: transparent;")
            layout.addWidget(widget)

    def set_reading(self, *, label: str, state: Any, summary: str, updated_at: Optional[float]) -> None:
        text, token = classify_state(state, updated_at)
        color = TOKENS[token]
        self.setStyleSheet(
            f"QFrame#statusCard {{ background: {TOKENS['surface_raised']}; "
            f"border: 1px solid {TOKENS['border']}; border-left: 3px solid {color}; border-radius: 6px; }}"
        )
        self.name.setText(label)
        self.state.setText(f"<span style='color:{color}; font-weight:700'>{escape(text.replace('_', ' ').upper())}</span>")
        self.detail.setText(
            f"<span style='color:{TOKENS['text_muted']}'>{escape(summary or '—')} · {age_text(updated_at)}</span>"
        )


class CommandCenterPane(QWidget):
    """Home dashboard. Feed it with ``apply_snapshot`` from the workspace refresh payload."""

    def __init__(self, *, launchers: Sequence[Tuple[str, Callable[[], None]]] = (), parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        head = QLabel("Command Center", self)
        head.setProperty("variant", "kicker")
        self.headline = QLabel("<h2 style='margin:0'>Waiting for live service data…</h2>", self)
        self.subline = QLabel("No snapshot received yet. Nothing is shown as healthy until it is measured.", self)
        self.subline.setWordWrap(True)
        root.addWidget(head)
        root.addWidget(self.headline)
        root.addWidget(self.subline)

        launcher_row = QHBoxLayout()
        launcher_row.setSpacing(8)
        for label, callback in launchers:
            button = QPushButton(label, self)
            button.setProperty("toolbarRole", "primary")
            button.clicked.connect(lambda _checked=False, cb=callback: cb())
            launcher_row.addWidget(button)
        launcher_row.addStretch(1)
        root.addLayout(launcher_row)

        self._cards: Dict[str, StatusCard] = {}
        self._grid_host = QWidget(self)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(10)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)
        body_layout.addWidget(self._grid_host)
        alerts_title = QLabel("Recent alerts", body)
        alerts_title.setProperty("variant", "kicker")
        self.alerts = QLabel("No alert data yet.", body)
        self.alerts.setWordWrap(True)
        self.alerts.setTextFormat(Qt.RichText)
        body_layout.addWidget(alerts_title)
        body_layout.addWidget(self.alerts)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def apply_snapshot(self, payload: Dict[str, Any]) -> None:
        startup = ((payload.get("summary") or {}).get("startup")) or {}
        subsystems: Dict[str, Any] = startup.get("subsystems") or {}
        order: List[str] = list(startup.get("subsystem_order") or subsystems.keys())
        overall = str(startup.get("overall_state") or "").lower()
        label, token = classify_state(overall, startup.get("updated_at"))
        color = TOKENS[token]
        degraded = bool(startup.get("ready_but_degraded"))
        suffix = " · degraded" if degraded else ""
        self.headline.setText(
            f"<h2 style='margin:0'>System <span style='color:{color}'>{escape(label.upper())}</span>{suffix}</h2>"
        )
        counts = startup.get("counts") or {}
        shown = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in counts.items() if n)
        refreshed = f" · refreshed in {payload['refresh_ms']} ms" if payload.get("refresh_ms") is not None else ""
        self.subline.setText(f"{shown or 'No subsystem counts reported.'}{refreshed}")

        for index, key in enumerate(order):
            entry = subsystems.get(key) or {}
            card = self._cards.get(key)
            if card is None:
                card = StatusCard(self._grid_host)
                self._cards[key] = card
                self._grid.addWidget(card, index // CARD_COLUMNS, index % CARD_COLUMNS)
            card.set_reading(
                label=str(entry.get("label") or key.replace("_", " ").title()),
                state=entry.get("state"),
                summary=str(entry.get("human_reason") or entry.get("summary") or ""),
                updated_at=entry.get("updated_at"),
            )
        self._apply_alerts((payload.get("notifications") or {}).get("notifications") or [])

    def _apply_alerts(self, items: Sequence[Dict[str, Any]]) -> None:
        if not items:
            self.alerts.setText(f"<span style='color:{TOKENS['text_muted']}'>No recent alerts.</span>")
            return
        rows = []
        for item in items[:8]:
            title = escape(str(item.get("title") or item.get("message") or item.get("type") or "Notification"))
            severity = str(item.get("severity") or item.get("level") or "info").lower()
            color = TOKENS["state_bad"] if severity in {"critical", "error"} else (
                TOKENS["state_warn"] if severity in {"warning", "warn"} else TOKENS["text_muted"]
            )
            unread = "" if item.get("read") else f" <span style='color:{TOKENS['accent']}'>●</span>"
            rows.append(
                f"<div style='margin-bottom:6px'><span style='color:{color}'>{escape(severity.upper())}</span> "
                f"{title}{unread} <span style='color:{TOKENS['text_muted']}'>· {age_text(item.get('timestamp'))}</span></div>"
            )
        self.alerts.setText("".join(rows))
