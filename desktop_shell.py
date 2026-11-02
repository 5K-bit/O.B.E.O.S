"""Managed PySide6 workspace shell for O.B.E.O.S / Blackfong."""

from __future__ import annotations

import logging
import os
import sys
import sqlite3
import time
import webbrowser

# Blackfong Workspace is the default surface of a bare `desktop_shell.py`
# launch. Earth used to be the default instead (triggered whenever
# "--classic" was absent), which meant a plain OBEOS launch cold-started
# both PySide6/model-warmup processes at once - too much for a machine that
# can't comfortably run both together. Earth is now opt-in only: pass
# --earth (or use `blackfong desktop earth` / the in-workspace "Start Earth"
# button) when you actually want it, separately, whenever you want it.
# --classic is still accepted and is now simply a no-op, kept so Earth's own
# "Classic Workspace" button (earth/desktop.py's workspace() method, which
# launches `desktop_shell.py --classic`) keeps working unchanged.
if __name__ == "__main__" and "--earth" in sys.argv:
    from blackfong_ecosystem.earth.desktop import main as earth_main
    raise SystemExit(earth_main())

from blackfong_ecosystem.cancellation import CancellationToken, RequestCancelled, cancellation_scope
from typing import Any, Callable, Dict, Optional

try:
    from PySide6.QtCore import QPoint, QSettings, QThread, QTimer, Qt, Signal
    from PySide6.QtGui import QAction, QKeySequence, QShortcut, QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QDockWidget,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QPushButton,
        QSplitter,
        QTabWidget,
        QTextEdit,
        QToolBar,
        QToolButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - GUI not available in some test environments
    raise RuntimeError(
        "PySide6 is required to run the Blackfong Workspace shell. Install it via 'pip install pyside6'."
    ) from exc

from blackfong_ecosystem.debug_session_log import debug_session_log, log_ui_action, timed_ui_action
from blackfong_ecosystem.docker_stack import DockerStackService
from blackfong_ecosystem.file_scope_service import FileScopeService
from blackfong_ecosystem.file_workflow import FileWorkflowService
from blackfong_ecosystem.json_utils import dump_pretty_json
from blackfong_ecosystem.native_cognition_graph import NativeCognitionGraph
from blackfong_ecosystem.obeos_linux import OBEOSLinuxPlatformService
from blackfong_ecosystem.operator_actions import OperatorActionService
from blackfong_ecosystem.operator_summary import OperatorSummaryService
from blackfong_ecosystem.service_registry import get_ui_services
from blackfong_ecosystem.system_awareness import SystemAwarenessService
from blackfong_ecosystem.backend_warmup import BackendWarmupWorker
from blackfong_ecosystem.llamacpp_process import LlamaCppServerManager
from blackfong_ecosystem.cognition_renderer import NativeGraphCognitionRenderer
from blackfong_ecosystem.config import chat_settings, hud_settings
from blackfong_ecosystem.display_names import display_agent_name, format_chat_status_line
from blackfong_ecosystem.desktop_live_stream import DesktopLiveStreamWorker
from blackfong_ecosystem.operator_session import (
    begin_operator_session,
    get_shared_session_id,
    link_surface,
    normalize_session_boot_mode,
    session_boot_mode_from_env,
)
from blackfong_ecosystem.thoughtglass_scene_3d import Thoughtglass3DHost
from blackfong_ecosystem.tiny_stream_view import TinyStreamView
from blackfong_ecosystem.ui_commands import CommandContext, CommandRegistry, get_command_registry
from blackfong_ecosystem.workspace_boot_splash import WorkspaceBootSplash
from blackfong_ecosystem.workspace_theme import build_stylesheet
from blackfong_ecosystem.workspace_command_center import CommandCenterPane
from blackfong_ecosystem.workspace_shortcuts import (
    install_desktop_shortcuts,
    load_shortcut_overrides,
    shortcuts_reference_html,
)
from blackfong_ecosystem.workspace_window_manager import ManagedDockWidget, WorkspaceWindowManager

logger = logging.getLogger(__name__)


def _pretty(payload: Any) -> str:
    try:
        return dump_pretty_json(payload)
    except (TypeError, ValueError):
        return str(payload)


def _html(text: Any) -> str:
    value = str(text or "")
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def apply_workspace_theme(app: QApplication) -> None:
    app.setStyleSheet(build_stylesheet())


from blackfong_ecosystem.ui.desktop_workers import ChatWorker, TinyProgrammerAutoStartWorker, WarmupStatusBridge

def build_workspace_snapshot(
    services: Any,
    *,
    session_id: Optional[str] = None,
    layer: str = "live_brain",
    require_tiny_stream: bool = False,
    lightweight: bool = False,
) -> Dict[str, Any]:
    if not lightweight:
        services.memory_chamber.ensure_live_watch(start_index=True)
    tiny_status = services.tinyprogrammer.get_status(require_stream=require_tiny_stream)
    tiny_recent = services.tinyprogrammer.get_recent(limit=6)
    integrations = services.integration_registry.list_integrations()
    awareness = services.awareness_service.build_snapshot(emit=False)
    services.startup_coordinator.refresh_from_runtime(
        awareness_snapshot=awareness,
        tiny_status=tiny_status,
        integration_listing=integrations,
    )
    summary = services.operator_summary.build_summary(
        session_id=session_id,
        include_awareness=False,
    )
    summary["startup"] = services.startup_coordinator.snapshot()
    summary["system"] = {
        "health": awareness.get("health", {}),
        "backend": awareness.get("observed", {}).get("backend", {}),
        "environment": awareness.get("observed", {}).get("environment", {}),
    }
    cognition = services.cognition_service.build_snapshot(
        layer=layer,
        focus={"layer": layer, "mode": "thoughtglass"},
        request_states={},
        request_threads={},
        backend_bootstrap={"status": summary["startup"].get("overall_state", "warming")},
        sensor_mode="off",
        voice_status={"active": False, "message": "Desktop shell does not own the voice loop."},
    )
    memory_chamber = {"summary": {"status": "deferred"}, "home": {}, "chamber": {}}
    if not lightweight:
        memory_summary = services.memory_chamber.summary()
        memory_chamber = {
            "summary": memory_summary,
            "home": services.memory_chamber.graph(
                mode="live_brain",
                layer=layer,
                selected_node=(cognition.get("focus") or {}).get("selected_node"),
                limit=32,
                summary_override=memory_summary,
            ),
            "chamber": services.memory_chamber.graph(
                mode="cluster_map",
                layer="memory",
                selected_node=(cognition.get("focus") or {}).get("selected_node"),
                limit=56,
                summary_override=memory_summary,
            ),
        }
    notifications = services.notification_service.list_notifications(limit=50)
    trace_events = services.memory_service.event_store.list_events(limit=40)
    redamon = services.redamon_workflow.status(session_id=session_id)
    return {
        "summary": summary,
        "awareness": awareness,
        "cognition": cognition,
        "tiny": {
            "status": tiny_status,
            "recent": tiny_recent,
        },
        "notifications": notifications,
        "trace": {"recent_events": [event.to_dict() for event in trace_events]},
        "integrations": integrations,
        "redamon": redamon,
        "memory_chamber": memory_chamber,
    }


class WorkspaceRefreshWorker(QThread):
    """Gather the heavyweight workspace snapshot off the UI thread."""

    finished = Signal(dict)

    def __init__(
        self,
        *,
        services: Any,
        session_id: Optional[str],
        layer: str,
        reason: str = "manual",
        require_tiny_stream: bool = False,
        lightweight: bool = False,
    ) -> None:
        super().__init__()
        self.services = services
        self.session_id = session_id
        self.layer = layer
        self.reason = reason
        self.require_tiny_stream = require_tiny_stream
        self.lightweight = lightweight

    def run(self) -> None:
        started = time.perf_counter()
        try:
            payload = build_workspace_snapshot(
                self.services,
                session_id=self.session_id,
                layer=self.layer,
                require_tiny_stream=self.require_tiny_stream,
                lightweight=self.lightweight,
            )
            payload["refresh_reason"] = self.reason
            payload["refresh_ms"] = round((time.perf_counter() - started) * 1000, 2)
        except Exception as exc:
            payload = {
                "status": "error",
                "error": str(exc),
                "refresh_reason": self.reason,
                "refresh_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        self.finished.emit(payload)


class WorkspacePane(QWidget):
    """Text-first pane with controls and a readable output surface."""

    def __init__(self, *, title: str, subtitle: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        title_block = QWidget(self)
        title_layout = QVBoxLayout(title_block)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(4)
        self.kicker = QLabel("Operator Surface", title_block)
        self.kicker.setProperty("variant", "kicker")
        self.title_label = QLabel(f"<h2 style='margin:0'>{_html(title)}</h2>", title_block)
        self.subtitle_label = QLabel(subtitle, title_block)
        self.subtitle_label.setWordWrap(True)
        title_layout.addWidget(self.kicker)
        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.subtitle_label)
        self.controls_widget = QWidget(self)
        self.controls = QHBoxLayout(self.controls_widget)
        self.controls.setContentsMargins(0, 0, 0, 0)
        self.controls.setSpacing(8)
        self.output = QTextEdit(self)
        self.output.setReadOnly(True)
        layout.addWidget(title_block)
        layout.addWidget(self.controls_widget)
        layout.addWidget(self.output, 1)
        self.setLayout(layout)

    def add_button(self, label: str, handler: Callable[[], None]) -> QPushButton:
        button = QPushButton(label, self)

        def guarded_handler() -> None:
            started = time.perf_counter()
            pane_name = type(self).__name__
            try:
                handler()
                log_ui_action(
                    surface="desktop",
                    action=f"{pane_name}.{label}",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            except Exception as exc:
                logger.exception("Desktop shell action failed: %s", label)
                log_ui_action(
                    surface="desktop",
                    action=f"{pane_name}.{label}",
                    status="error",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error=str(exc),
                )
                self.write({"status": "error", "error": str(exc), "action": label})

        button.clicked.connect(guarded_handler)
        self.controls.addWidget(button)
        return button

    def write(self, payload: Any) -> None:
        self.output.setPlainText(_pretty(payload) if isinstance(payload, (dict, list, tuple)) else str(payload))

    def write_html(self, html: str) -> None:
        self.output.setHtml(html)


class ChatPane(QWidget):
    """Shared local chat pane that uses the same OBEOS memory/orchestrator as the HUD."""

    chat_started = Signal()
    chat_finished = Signal(dict)

    def __init__(
        self,
        *,
        operator_chat: Any,
        memory_service: Any,
        command_registry: Optional[CommandRegistry] = None,
        command_context: Optional[Callable[[], CommandContext]] = None,
        session_boot_mode_getter: Optional[Callable[[], str]] = None,
        session_boot_mode_setter: Optional[Callable[[str], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.operator_chat = operator_chat
        self.memory_service = memory_service
        self.command_registry = command_registry
        self.command_context = command_context
        self.session_boot_mode_getter = session_boot_mode_getter
        self.session_boot_mode_setter = session_boot_mode_setter
        self.session_id: Optional[str] = None
        self._worker: Optional[ChatWorker] = None
        self._streaming_buffer = ""
        self._stream_render_timer = QTimer(self)
        self._stream_render_timer.setSingleShot(True)
        self._stream_render_timer.setInterval(50)
        self._stream_render_timer.timeout.connect(self._render_stream_buffer)
        self._stream_inserted = False
        self._stream_start = 0
        self._last_analysis_mode = "standard"
        self._chat_state = "ready"
        self._session_scope = "shared"
        self._session_mode = "resume_shared"
        self._last_interaction_at = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QWidget(self)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        kicker = QLabel("Conversation", header)
        kicker.setProperty("variant", "kicker")
        title = QLabel("<h2 style='margin:0'>D.A.I.S.E Chat</h2>", header)
        subtitle = QLabel(
            "Local workspace chat shares memory, trace, and agent routing with the HUD. "
            "Use /status, /trace, and other slash commands here.",
            header,
        )
        subtitle.setWordWrap(True)
        self.session_label = QLabel("Session: not started", header)
        self.session_label.setProperty("variant", "status")
        header_layout.addWidget(kicker)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        header_layout.addWidget(self.session_label)

        controls_row = QWidget(self)
        controls = QHBoxLayout(controls_row)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        self.agent_select = QComboBox(self)
        self.agent_select.addItem("Auto route", "")
        self.agent_select.addItem("D.A.I.S.E", "daise")
        self.agent_select.addItem("Coding", "coding")
        self.agent_select.addItem("Lilo", "lilo")
        self.agent_select.addItem("Stitch", "stitch")
        self.agent_select.addItem("Voice", "voice")
        self.analysis_select = QComboBox(self)
        self.analysis_select.addItem("Fast", "fast")
        self.analysis_select.addItem("Standard", "standard")
        self.analysis_select.addItem("Deep", "deep")
        self.analysis_select.setCurrentIndex(1)
        self.session_mode_select = QComboBox(self)
        self.session_mode_select.addItem("Resume Shared", "resume_shared")
        self.session_mode_select.addItem("New Shared", "new_shared")
        self.session_mode_select.addItem("Fresh Local", "fresh_local")
        self.new_session_button = QPushButton(self)
        self.new_session_button.clicked.connect(self.new_session)
        self.refresh_button = QPushButton("Load History", self)
        self.refresh_button.clicked.connect(self.refresh_history)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_message)
        controls.addWidget(self.agent_select)
        controls.addWidget(self.analysis_select)
        controls.addWidget(self.session_mode_select)
        controls.addWidget(self.new_session_button)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.cancel_button)

        self.transcript = QTextEdit(self)
        self.transcript.setReadOnly(True)
        self.transcript.setLineWrapMode(QTextEdit.WidgetWidth)
        self.transcript.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.transcript.document().setDocumentMargin(8)

        composer = QWidget(self)
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(0, 0, 0, 0)
        composer_layout.setSpacing(8)
        self.input = QTextEdit(self)
        self.input.setPlaceholderText(
            "Talk to D.A.I.S.E or enter slash commands (/status, /trace, /memory)."
        )
        self.input.setMaximumHeight(150)
        self.input.setLineWrapMode(QTextEdit.WidgetWidth)
        self.input.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        composer_actions = QWidget(self)
        composer_actions_layout = QHBoxLayout(composer_actions)
        composer_actions_layout.setContentsMargins(0, 0, 0, 0)
        composer_actions_layout.setSpacing(8)
        self.status_label = QLabel("Idle", composer_actions)
        self.status_label.setProperty("variant", "status")
        self.send_button = QPushButton("Send", composer_actions)
        self.send_button.clicked.connect(self.send_message)
        composer_actions_layout.addWidget(self.status_label)
        composer_actions_layout.addStretch(1)
        composer_actions_layout.addWidget(self.send_button)
        composer_layout.addWidget(self.input)
        composer_layout.addWidget(composer_actions)

        send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        send_shortcut.activated.connect(self.send_message)

        layout.addWidget(header)
        layout.addWidget(controls_row)
        self.companion_label = QLabel(self)
        self.companion_label.setTextFormat(Qt.PlainText)
        self.companion_label.setWordWrap(True)
        self.companion_label.hide()
        layout.addWidget(self.companion_label)
        layout.addWidget(self.transcript, 1)
        layout.addWidget(composer)
        self.setLayout(layout)
        self._apply_boot_mode_selection(self._configured_session_boot_mode())
        self.session_mode_select.currentIndexChanged.connect(self._handle_session_mode_changed)
        self.new_session()

    def new_session(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        mode = self._selected_session_boot_mode()
        if self.session_boot_mode_setter is not None:
            self.session_boot_mode_setter(mode)
        session = begin_operator_session(surface="desktop", mode=mode)
        self.session_id = str(session.get("session_id") or "")
        self._session_scope = str(session.get("scope") or "shared")
        self._session_mode = str(session.get("mode") or mode)
        self._update_session_label()
        history_count = self._session_history_count(limit=80)
        if self._session_mode == "resume_shared" and history_count > 0:
            self.transcript.setHtml(self._resume_session_html(history_count))
            self._set_chat_state("ready", f"{history_count} recent messages available")
        else:
            self.transcript.setHtml(self._session_intro_html(history_count=history_count))
            self._set_chat_state("ready")
        self.refresh_button.setEnabled(history_count > 0)

    def refresh_history(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if not self.session_id:
            self.new_session()
            return
        messages = self.memory_service.get_session_messages(self.session_id, limit=80)
        self.transcript.setHtml(self._messages_to_html(messages))
        self._scroll_transcript_to_end()
        self.refresh_button.setEnabled(bool(messages))
        self._set_chat_state("complete", "history refreshed")

    def cancel_message(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._set_chat_state("cancelled", "cancelling request")

    def send_message(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        text = self.input.toPlainText().strip()
        if not text:
            self._set_chat_state("failed", "add a message first")
            return
        if text.startswith("/") and self.command_registry is not None and self.command_context is not None:
            self._run_slash_command(text)
            return
        self._last_interaction_at = time.monotonic()
        self.companion_label.hide()
        self._last_analysis_mode = str(self.analysis_select.currentData() or "fast")
        self.send_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._set_chat_state("working", "D.A.I.S.E is working")
        self.chat_started.emit()
        self._streaming_buffer = ""
        self._stream_inserted = False
        self._stream_start = 0
        self.transcript.append(
            "<div style='margin-bottom:10px;padding:12px;border:1px solid #223344;border-radius:12px;"
            f"background:#0f1b24;white-space:pre-wrap'><strong>You</strong><br/>{_html(text)}</div>"
        )
        self._scroll_transcript_to_end()
        self._worker = ChatWorker(
            operator_chat=self.operator_chat,
            message=text,
            session_id=self.session_id,
            agent_override=self.agent_select.currentData(),
            analysis_mode=self._last_analysis_mode,
        )
        self._worker.token_received.connect(self._append_stream_token)
        self._worker.finished.connect(self._handle_result)
        self._worker.start()

    def _run_slash_command(self, command: str) -> None:
        self.input.clear()
        self.transcript.append(
            "<div style='margin-bottom:10px;padding:12px;border:1px solid #223344;border-radius:12px;"
            f"background:#0f1b24;white-space:pre-wrap'><strong>You</strong><br/>{_html(command)}</div>"
        )
        try:
            result = self.command_registry.execute(command, self.command_context())
            self.transcript.append(
                "<div style='margin-bottom:10px;padding:12px;border:1px solid #27505e;border-radius:12px;"
                f"background:#0d1720;white-space:pre-wrap'><strong>System</strong><br/>{_html(_pretty(result))}</div>"
            )
            self._set_chat_state("complete", f"command completed: {command.split()[0]}")
        except Exception as exc:
            self.transcript.append(f"<div><strong>System</strong><br/>{_html(str(exc))}</div>")
            self._set_chat_state("failed", f"command failed: {exc}")
        self._scroll_transcript_to_end()

    def _append_stream_token(self, token: str) -> None:
        if self._chat_state in {"cancelled", "failed", "complete"}:
            return
        self._streaming_buffer += str(token)
        self._set_chat_state("streaming", "receiving reply")
        if not self._stream_inserted:
            self._render_stream_buffer()
        elif not self._stream_render_timer.isActive():
            self._stream_render_timer.start()

    def _render_stream_buffer(self) -> None:
        if self._chat_state in {"cancelled", "failed", "complete"}:
            return
        scrollbar = self.transcript.verticalScrollBar()
        previous_scroll = scrollbar.value()
        follow = scrollbar.maximum() - previous_scroll <= 40
        cursor = self.transcript.textCursor()
        if not self._stream_inserted:
            cursor.movePosition(QTextCursor.End)
            cursor.insertBlock()
            self._stream_start = cursor.position()
            self._stream_inserted = True
        cursor.setPosition(self._stream_start)
        cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertHtml(self._message_card_html(role="assistant", text=self._streaming_buffer, agent="daise"))
        cursor.movePosition(QTextCursor.End)
        if follow:
            self.transcript.setTextCursor(cursor)
            self._scroll_transcript_to_end()
        else:
            scrollbar.setValue(previous_scroll)
        self._set_chat_state("streaming", "receiving reply")

    def _handle_result(self, payload: Dict[str, Any]) -> None:
        self._stream_render_timer.stop()
        self.send_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.chat_finished.emit(payload)
        if payload.get("session_id"):
            self.session_id = payload.get("session_id")
            self._update_session_label()
        if payload.get("status") == "cancelled":
            self._set_chat_state("cancelled")
            self._stream_inserted = False
            return
        if payload.get("status") == "error":
            failed_result = payload.get("result") or {}
            reason = str(payload.get("error") or failed_result.get("error")
                         or failed_result.get("backend_error") or "Chat failed")
            partial = bool(self._streaming_buffer.strip())
            if partial:
                self._render_stream_buffer()
            self._stream_inserted = False
            self._streaming_buffer = ""
            self._set_chat_state("failed", reason)
            detail = ("Incomplete reply. " if partial else "") + reason
            self.transcript.append(
                f"<div style='color:#9fe9f5'><strong>System</strong><br/>{_html(detail)}</div>"
            )
            self._scroll_transcript_to_end()
            return
        self.input.clear()
        result = payload.get("result") or {}
        if result.get("error") or result.get("success") is False:
            self._set_chat_state("failed", str(result.get("error") or "Generation failed. Please retry."))
            self._stream_inserted = False
            self._streaming_buffer = ""
            return
        streamed = self._streaming_buffer.strip()
        db_response = str(result.get("response") or "").strip()
        final_text = db_response or streamed
        if not final_text:
            self._stream_inserted = False
            self._streaming_buffer = ""
            self._set_chat_state("failed", "No reply was returned. Please retry.")
            return
        if final_text and self._stream_inserted:
            self._set_chat_state("finalizing", "finalizing reply")
            cursor = self.transcript.textCursor()
            cursor.setPosition(self._stream_start)
            cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            cursor.insertHtml(self._message_card_html(role="assistant", text=final_text, agent="daise"))
        elif not self._stream_inserted:
            messages = list(payload.get("messages") or [])
            if final_text and (not messages or messages[-1].get("role") != "assistant"):
                messages.append({"role": "assistant", "text": final_text})
            self.transcript.setHtml(self._messages_to_html(messages))
        self._stream_inserted = False
        self._streaming_buffer = ""
        self._scroll_transcript_to_end()
        self.refresh_button.setEnabled(True)
        self._set_chat_state(
            "complete",
            format_chat_status_line(
                result=result,
                selection=payload.get("selection"),
                analysis_mode=str(payload.get("analysis_mode") or self._last_analysis_mode),
                request_id=str(payload.get("request_id") or ""),
            ),
        )

    def _format_message_body(self, text: str) -> str:
        escaped = _html(str(text or ""))
        return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")

    def _configured_session_boot_mode(self) -> str:
        configured = self.session_boot_mode_getter() if self.session_boot_mode_getter is not None else None
        if configured is None:
            configured = session_boot_mode_from_env()
        return normalize_session_boot_mode(configured)

    def _selected_session_boot_mode(self) -> str:
        return normalize_session_boot_mode(self.session_mode_select.currentData())

    def _apply_boot_mode_selection(self, mode: str) -> None:
        normalized = normalize_session_boot_mode(mode)
        for index in range(self.session_mode_select.count()):
            if self.session_mode_select.itemData(index) == normalized:
                self.session_mode_select.setCurrentIndex(index)
                break
        self._update_session_mode_button()

    def _handle_session_mode_changed(self, _index: int = -1) -> None:
        mode = self._selected_session_boot_mode()
        if self.session_boot_mode_setter is not None:
            self.session_boot_mode_setter(mode)
        self._update_session_mode_button()

    def _update_session_mode_button(self) -> None:
        mode = self._selected_session_boot_mode()
        labels = {
            "resume_shared": "Resume Session",
            "new_shared": "New Shared Session",
            "fresh_local": "Fresh Local Session",
        }
        self.new_session_button.setText(labels.get(mode, "Apply Session"))

    def _session_history_count(self, *, limit: int = 80) -> int:
        if not self.session_id:
            return 0
        try:
            return len(self.memory_service.get_session_messages(self.session_id, limit=limit))
        except Exception:
            return 0

    def _update_session_label(self) -> None:
        if not self.session_id:
            self.session_label.setText("Session: not started")
            return
        scope = "shared" if self._session_scope == "shared" else "local"
        self.session_label.setText(f"Session: {self.session_id} ({scope})")

    def _session_intro_html(self, *, history_count: int = 0) -> str:
        if self._session_scope == "local":
            return (
                "<div style='padding:12px;border:1px solid #223344;border-radius:12px;background:#101a23'>"
                "<strong>Fresh local session ready.</strong><br/>"
                "This thread stays local to the workspace until you switch back to a shared operator session."
                "</div>"
            )
        if self._session_mode == "new_shared":
            return (
                "<div style='padding:12px;border:1px solid #223344;border-radius:12px;background:#101a23'>"
                "<strong>New shared operator session ready.</strong><br/>"
                "Desktop and HUD can now converge on this fresh shared task state."
                "</div>"
            )
        detail = (
            f"{history_count} recent messages are available. Use Load History if you want to reopen the transcript.<br/>"
            if history_count > 0
            else ""
        )
        return (
            "<div style='padding:12px;border:1px solid #223344;border-radius:12px;background:#101a23'>"
            "<strong>Shared operator session ready.</strong><br/>"
            f"{detail}"
            "This desktop chat shares memory and orchestration with the HUD cockpit."
            "</div>"
        )

    def _resume_session_html(self, history_count: int) -> str:
        return (
            "<div style='padding:12px;border:1px solid #27505e;border-radius:12px;background:#0d1720'>"
            "<strong>Shared session resumed.</strong><br/>"
            f"{history_count} recent messages are available for this task. "
            "Use Load History to reopen the transcript, or switch modes to start clean."
            "</div>"
        )

    def _set_chat_state(self, state: str, detail: Optional[str] = None) -> None:
        normalized = str(state or "ready").strip().lower() or "ready"
        self._chat_state = normalized
        label = normalized.upper()
        suffix = f" | {detail}" if detail else ""
        self.status_label.setText(f"{label}{suffix}")
        self.status_label.setProperty("chat_state", normalized)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _message_card_html(self, *, role: str, text: str, agent: Optional[str] = None) -> str:
        # Flat background-tint differences between roles read as near-identical at
        # normal screen brightness (they're all close to the base dark surface), so
        # distinguish roles with a visible left accent bar and asymmetric margins
        # (assistant hugs the left edge, user sits indented from the right) instead
        # of relying on subtle fill alone - that's the difference between a real
        # chat UI and a flat log dump.
        normalized_role = str(role or "system").strip().lower()
        tone = {
            "assistant": "#132530",
            "user": "#111a22",
            "system": "#0d1720",
        }.get(normalized_role, "#0d1720")
        accent_bar = {
            "assistant": "#1fb5cc",
            "user": "#4a6075",
            "system": "#27505e",
        }.get(normalized_role, "#27505e")
        margin = {
            "assistant": "margin:0 48px 12px 0",
            "user": "margin:0 0 12px 48px",
            "system": "margin:0 24px 12px 24px",
        }.get(normalized_role, "margin:0 24px 12px 24px")
        if normalized_role == "assistant":
            label = display_agent_name(agent or "daise")
        elif normalized_role == "user":
            label = "You"
        else:
            label = "System"
        body = self._format_message_body(text)
        return (
            f"<div style='{margin};padding:10px 14px;border-left:3px solid {accent_bar};"
            f"border-radius:4px;background:{tone};white-space:pre-wrap'>"
            f"<strong>{_html(label)}</strong><br/>{body}</div>"
        )

    def _scroll_transcript_to_end(self) -> None:
        cursor = self.transcript.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()

    def _messages_to_html(self, messages: list[dict[str, Any]]) -> str:
        cards: list[str] = []
        for item in messages:
            role = str(item.get("role") or "system")
            cards.append(
                self._message_card_html(
                    role=role,
                    text=str(item.get("text") or item.get("content") or ""),
                    agent=str(item.get("agent") or "daise"),
                )
            )
        return "".join(cards) or "<div>No messages yet.</div>"


class FilesPane(WorkspacePane):
    def __init__(
        self,
        *,
        file_scope_service: FileScopeService,
        file_workflow: FileWorkflowService,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            title="Files + Scope",
            subtitle="Machine-wide read-map first. Governed writes stay inside project roots.",
            parent=parent,
        )
        self.kicker.setText("Files")
        self.file_scope_service = file_scope_service
        self.file_workflow = file_workflow
        self.path_input = QLineEdit(self)
        self.path_input.setPlaceholderText("Enter a machine path to approve, map, read, or scan.")
        self.controls.addWidget(self.path_input)
        self.add_button("Roots", self.show_roots)
        self.add_button("Approve Root", self.approve_root)
        self.add_button("Map", self.map_path)
        self.add_button("Read", self.read_path)
        self.add_button("Scan", self.scan_path)
        self.add_button("Policy", self.show_policy)

    def _path(self) -> str:
        return self.path_input.text().strip()

    def show_roots(self) -> None:
        self.write(self.file_scope_service.list_roots())

    def approve_root(self) -> None:
        path = self._path()
        self.write({"status": "error", "error": "Enter a path first."} if not path else self.file_scope_service.approve_root(path))

    def map_path(self) -> None:
        path = self._path()
        self.write({"status": "error", "error": "Enter a path first."} if not path else self.file_scope_service.map_path(path))

    def read_path(self) -> None:
        path = self._path()
        self.write({"status": "error", "error": "Enter a path first."} if not path else self.file_scope_service.read_path(path))

    def scan_path(self) -> None:
        path = self._path() or "."
        self.write(self.file_workflow.scan(path))

    def show_policy(self) -> None:
        self.write({"policy": self.file_workflow.workspace_policy(), "proposals": self.file_workflow.list_proposals()})


class TinyPane(WorkspacePane):
    def __init__(self, *, tinyprogrammer: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            title="TinyProgrammer Cockpit",
            subtitle="Embedded live stream inside the workspace. The retro Mac display is watch-only, not clickable.",
            parent=parent,
        )
        self.kicker.setText("Companion")
        self.tinyprogrammer = tinyprogrammer
        self._auto_start_worker: Optional[TinyProgrammerAutoStartWorker] = None
        self.diagnostics_label = QLabel("", self)
        self.diagnostics_label.setWordWrap(True)
        self.diagnostics_label.setProperty("variant", "status")
        self.stream_view = TinyStreamView(self)
        layout = self.layout()
        if layout is not None:
            layout.insertWidget(2, self.stream_view)
            layout.insertWidget(3, self.diagnostics_label)
        self.add_button("Refresh", self.refresh)
        self.add_button("Launch", self.launch)
        self.add_button("Open Web", self.open_dashboard)
        self.add_button("Copy Diagnostics", self.copy_diagnostics)
        self.add_button("Log", self.show_log)

    def refresh(self) -> None:
        status = self.tinyprogrammer.get_status(require_stream=True)
        self.apply_status({"status": status, "recent": self.tinyprogrammer.get_recent(limit=6)})

    def apply_status(self, payload: Dict[str, Any]) -> None:
        status = dict(payload.get("status") or {})
        stream_url = ""
        if status.get("stream_available") or status.get("stream_enabled"):
            stream_url = str(status.get("stream_url") or f"{self.tinyprogrammer.base_url.rstrip('/')}/stream")
        self.stream_view.set_stream_url(stream_url)
        path = str(getattr(self.tinyprogrammer, "project_path", ""))
        reason = status.get("stream_human_reason") or status.get("last_error_summary") or status.get("management_state")
        mood = status.get("mood") or "unknown"
        self.diagnostics_label.setText(
            f"Path: {path} | Online: {status.get('online')} | Mood: {mood} | Stream: {status.get('stream_reason')} | {reason or ''}"
        )
        self.write(payload)

    def copy_diagnostics(self) -> None:
        status = self.tinyprogrammer.get_status(require_stream=True)
        log_tail = self.tinyprogrammer.get_log_tail(lines=40)
        self.write({"status": status, "log_tail": log_tail})

    def _trigger_background_autostart(self) -> None:
        if self._auto_start_worker is not None and self._auto_start_worker.isRunning():
            return
        self._auto_start_worker = TinyProgrammerAutoStartWorker(self.tinyprogrammer)
        self._auto_start_worker.finished.connect(self._on_autostart_complete)
        self._auto_start_worker.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._trigger_background_autostart()

    def _on_autostart_complete(self, result: Dict[str, Any]) -> None:
        self.refresh()
        if not result.get("success"):
            try:
                self.tinyprogrammer.notification_service.create(
                    title="TinyProgrammer auto-start failed",
                    message=result.get("error", "Unknown error during auto-start"),
                    level="warning",
                    category="tinyprogrammer",
                    desktop=False,
                )
            except Exception:
                pass

    def launch(self) -> None:
        self.diagnostics_label.setText("Checking TinyProgrammer and waiting for its first stream frame…")
        self._trigger_background_autostart()

    def open_dashboard(self) -> None:
        self.write(self.tinyprogrammer.open(open_browser=True))

    def show_log(self) -> None:
        self.write(self.tinyprogrammer.get_log_tail(lines=60))


class OverviewPane(WorkspacePane):
    def __init__(
        self,
        *,
        operator_summary: OperatorSummaryService,
        tinyprogrammer: Any,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            title="Blackfong Workspace",
            subtitle="OBEOS owns the session. Plasma is the managed workspace. This shell is your local cockpit.",
            parent=parent,
        )
        self.kicker.setText("Workspace")
        self.operator_summary = operator_summary
        self.tinyprogrammer = tinyprogrammer
        self.add_button("Refresh", self.refresh)
        self.add_button("Open HUD", lambda: webbrowser.open("http://127.0.0.1:8080/"))
        self.add_button("Tiny Dashboard", lambda: self.tinyprogrammer.open(open_browser=True))
        self.add_button("Tiny Live", lambda: self.tinyprogrammer.open_live(open_browser=True, consent={"approved": True, "level": 1}))
        self.add_button("Setup Check", self.run_setup_check)

    def run_setup_check(self) -> None:
        path = getattr(self.tinyprogrammer, "project_path", None)
        checks = {
            "tiny_project_path": str(path),
            "tiny_main_exists": bool(path and (path / "main.py").exists()),
            "shared_session": get_shared_session_id(surface="desktop"),
        }
        try:
            import httpx

            response = httpx.get(f"{self.tinyprogrammer.base_url}/api/status", timeout=3.0)
            checks["tiny_http_status"] = response.status_code
            checks["tiny_status"] = response.json() if response.status_code == 200 else {}
        except Exception as exc:
            checks["tiny_http_error"] = str(exc)
        self.write(checks)

    def refresh(self) -> None:
        summary = self.operator_summary.build_summary(include_awareness=True)
        integrations = summary.get("integrations", {})
        redamon = summary.get("security_modes", {}).get("redamon", {})
        summary["workspace_focus"] = {
            "integrations_available": integrations.get("counts", {}).get("available", 0),
            "redamon_ready": redamon.get("integration", {}).get("available"),
        }
        self.apply_summary(summary)

    def apply_summary(self, summary: Dict[str, Any]) -> None:
        integrations = summary.get("integrations", {})
        redamon = summary.get("security_modes", {}).get("redamon", {})
        summary = dict(summary)
        summary["workspace_focus"] = {
            "integrations_available": integrations.get("counts", {}).get("available", 0),
            "redamon_ready": redamon.get("integration", {}).get("available"),
        }
        self.write(summary)


class SystemPane(WorkspacePane):
    def __init__(
        self,
        *,
        awareness_service: SystemAwarenessService,
        platform_service: OBEOSLinuxPlatformService,
        docker_stack: DockerStackService,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(
            title="System Status",
            subtitle="Shared operator state, environment awareness, and session authority contract.",
            parent=parent,
        )
        self.kicker.setText("System")
        self.awareness_service = awareness_service
        self.platform_service = platform_service
        self.docker_stack = docker_stack
        self.add_button("Refresh", self.refresh)
        self.add_button("Desktop Status", self.desktop_status)
        self.add_button("VM Snapshot", self.vm_snapshot)
        self.add_button("Workspace Contract", self.workspace_contract)
        self.add_button("Home Contract", self.home_contract)
        self.add_button("Docker Status", self.docker_status)
        self.add_button("Docker Up", self.docker_up)
        self.add_button("Docker Down", self.docker_down)
        self.add_button("Docker Logs", self.docker_logs)

    def refresh(self) -> None:
        self.apply_runtime({"awareness": self.awareness_service.build_snapshot(emit=False)})

    def apply_runtime(self, payload: Dict[str, Any]) -> None:
        self.write(payload)

    def desktop_status(self) -> None:
        self.write(self.platform_service.desktop_status())

    def vm_snapshot(self) -> None:
        self.write(self.platform_service.vm_acceptance_snapshot())

    def workspace_contract(self) -> None:
        self.write(self.platform_service.launch_workspace(execute=False))

    def home_contract(self) -> None:
        self.write(self.platform_service.home(execute=False))

    def docker_status(self) -> None:
        self.write(self.docker_stack.status())

    def docker_up(self) -> None:
        self.write(self.docker_stack.up(execute=False, build=False))

    def docker_down(self) -> None:
        self.write(self.docker_stack.down(execute=False))

    def docker_logs(self) -> None:
        self.write(self.docker_stack.logs(execute=False, tail=120))


class GraphWorkbenchPane(QWidget):
    def __init__(
        self,
        *,
        title: str,
        subtitle: str,
        kicker: str,
        default_mode: str,
        available_modes: tuple[tuple[str, str], ...],
        mode_descriptions: Dict[str, str],
        mode_edge_filters: Dict[str, str],
        cognition_service: Any,
        awareness_service: SystemAwarenessService,
        memory_chamber: Any,
        default_layer: str = "live_brain",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.cognition_service = cognition_service
        self.awareness_service = awareness_service
        self.memory_chamber = memory_chamber
        self.default_mode = default_mode
        self.available_modes = available_modes
        self.mode_descriptions = dict(mode_descriptions)
        self.mode_edge_filters = dict(mode_edge_filters)
        self.default_layer = default_layer
        self._drill_target: Optional[str] = None
        self._search_target: Optional[str] = None
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setSingleShot(True)
        self._auto_refresh_timer.setInterval(180)
        self._auto_refresh_timer.timeout.connect(lambda: self.refresh())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title_block = QWidget(self)
        title_layout = QVBoxLayout(title_block)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(4)
        self.kicker = QLabel(kicker, title_block)
        self.kicker.setProperty("variant", "kicker")
        self.title_label = QLabel(f"<h2 style='margin:0'>{_html(title)}</h2>", title_block)
        self.subtitle_label = QLabel(subtitle, title_block)
        self.subtitle_label.setWordWrap(True)
        title_layout.addWidget(self.kicker)
        title_layout.addWidget(self.title_label)
        title_layout.addWidget(self.subtitle_label)

        controls_row = QWidget(self)
        controls = QHBoxLayout(controls_row)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        self.layer_select = QComboBox(self)
        for layer in ("live_brain", "memory", "tasks", "sensors", "faults", "forge"):
            self.layer_select.addItem(layer.replace("_", " ").title(), layer)
        default_layer_index = max(0, self.layer_select.findData(default_layer))
        self.layer_select.setCurrentIndex(default_layer_index)
        self.mode_select = QComboBox(self)
        for mode_id, label in available_modes:
            self.mode_select.addItem(label, mode_id)
        default_index = max(0, self.mode_select.findData(default_mode))
        self.mode_select.setCurrentIndex(default_index)
        self.edge_filter_select = QComboBox(self)
        for value, label in (
            ("top", "Top Edges"),
            ("show_more", "Show More"),
            ("show_all", "Show All"),
            ("concept", "Concept"),
            ("agent", "Agent"),
            ("document", "Document"),
            ("fault", "Fault"),
            ("event", "Event"),
            ("sequence", "Sequence"),
        ):
            self.edge_filter_select.addItem(label, value)
        controls.addWidget(self.layer_select)
        controls.addWidget(self.mode_select)
        controls.addWidget(self.edge_filter_select)

        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.refresh)
        self.awareness_button = QPushButton("Awareness", self)
        self.awareness_button.clicked.connect(self.show_awareness)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.awareness_button)

        self.graph = NativeCognitionGraph(self)
        self.graph.drillRequested.connect(self._handle_drill_request)
        self.graph.searchRequested.connect(self._handle_search_request)
        self._graph_renderer = NativeGraphCognitionRenderer(
            self.graph,
            mode_label_provider=lambda: self.mode_select.currentText(),
        )
        self.mode_hint = QLabel(self)
        self.mode_hint.setProperty("variant", "status")
        self.mode_hint.setWordWrap(True)
        layout.addWidget(title_block)
        layout.addWidget(controls_row)
        layout.addWidget(self.mode_hint)
        layout.addWidget(self.graph, 1)

        self.layer_select.currentIndexChanged.connect(self._queue_refresh)
        self.mode_select.currentIndexChanged.connect(self._handle_mode_changed)
        self.edge_filter_select.currentIndexChanged.connect(self._queue_refresh)
        self._apply_mode_defaults(initial=True)
        self._update_mode_hint()

    def _queue_refresh(self) -> None:
        self._auto_refresh_timer.start()

    def _handle_mode_changed(self) -> None:
        self._apply_mode_defaults(initial=False)
        self._update_mode_hint()
        self._queue_refresh()

    def _apply_mode_defaults(self, *, initial: bool) -> None:
        mode = str(self.mode_select.currentData() or self.default_mode)
        default_filter = str(self.mode_edge_filters.get(mode) or "top")
        index = self.edge_filter_select.findData(default_filter)
        if index >= 0:
            previous_block = self.edge_filter_select.blockSignals(True)
            self.edge_filter_select.setCurrentIndex(index)
            self.edge_filter_select.blockSignals(previous_block)
        if initial:
            layer_index = self.layer_select.findData(self.default_layer)
            if layer_index >= 0:
                previous_block = self.layer_select.blockSignals(True)
                self.layer_select.setCurrentIndex(layer_index)
                self.layer_select.blockSignals(previous_block)

    def _update_mode_hint(self) -> None:
        mode = str(self.mode_select.currentData() or self.default_mode)
        layer = str(self.layer_select.currentData() or self.default_layer)
        edge_filter = str(self.edge_filter_select.currentData() or "top").replace("_", " ")
        search_placeholder = {
            "live_brain": "Search anchors, sessions, or active concepts...",
            "cluster_map": "Search clusters, archives, or concept constellations...",
            "neighborhood": "Search for a node to open its local neighborhood...",
            "documents": "Search documents, paths, or memory artifacts...",
            "agents": "Search agents, participants, or operator entities...",
            "faults": "Search degraded nodes, warnings, or fault paths...",
            "full_debug_graph": "Search any raw graph label, file, hash, or UUID...",
        }.get(mode, "Search concepts, files, agents, or sessions...")
        self.graph.search_input.setPlaceholderText(search_placeholder)
        summary = self.mode_descriptions.get(mode) or "Curated cognition view."
        self.mode_hint.setText(
            f"{summary} Layer: {layer.replace('_', ' ').title()} | Edge focus: {edge_filter.title()}."
        )

    def refresh(self) -> None:
        self.memory_chamber.ensure_live_watch(start_index=True)
        layer = str(self.layer_select.currentData() or "live_brain")
        mode = str(self.mode_select.currentData() or self.default_mode)
        selected_node = self._drill_target or self.graph.selected_node_id
        search = self._search_target
        self._drill_target = None
        self._search_target = None
        snapshot = self.cognition_service.build_snapshot(
            layer=layer,
            focus={"layer": layer, "mode": "thoughtglass"},
            request_states={},
            request_threads={},
            backend_bootstrap={"status": "warming", "started_at": None, "completed_at": None, "error": None},
            sensor_mode="off",
            voice_status={"active": False, "message": "Desktop shell does not own the voice loop."},
        )
        graph_payload = self.memory_chamber.graph(
            mode=mode,
            layer=layer,
            selected_node=selected_node or (snapshot.get("focus") or {}).get("selected_node"),
            search=search,
            edge_filter=str(self.edge_filter_select.currentData() or "top"),
            zoom_bucket=self.graph.zoom_bucket,
            limit=56 if mode == "cluster_map" else 36,
        )
        self.apply_runtime(cognition_snapshot=snapshot, graph_payload=graph_payload)

    def show_awareness(self) -> None:
        snapshot = self.awareness_service.build_snapshot(emit=False)
        self.graph.show_developer_payload(snapshot, title="System Awareness")

    def _handle_drill_request(self, payload: Dict[str, Any]) -> None:
        target_mode = str(payload.get("mode") or self.default_mode)
        target_layer = str(payload.get("layer") or self.layer_select.currentData() or self.default_layer)
        self._drill_target = str(payload.get("selected_node") or "") or None
        mode_index = self.mode_select.findData(target_mode)
        if mode_index >= 0:
            self.mode_select.setCurrentIndex(mode_index)
        layer_index = self.layer_select.findData(target_layer)
        if layer_index >= 0:
            self.layer_select.setCurrentIndex(layer_index)
        self.refresh()

    def _handle_search_request(self, search: str) -> None:
        self._search_target = search
        neighborhood_index = self.mode_select.findData("neighborhood")
        if neighborhood_index >= 0:
            self.mode_select.setCurrentIndex(neighborhood_index)
        self.refresh()

    def apply_runtime(self, *, cognition_snapshot: Dict[str, Any], graph_payload: Dict[str, Any]) -> None:
        self._graph_renderer.apply_snapshot(
            snapshot=cognition_snapshot,
            graph_payload=graph_payload,
        )
        graph = dict((graph_payload or {}).get("graph") or graph_payload or {})
        visible_nodes = len(graph.get("visible_nodes") or graph.get("nodes") or [])
        visible_edges = len(graph.get("visible_edges") or graph.get("edges") or [])
        self.mode_hint.setText(
            f"{self.mode_descriptions.get(str(self.mode_select.currentData() or self.default_mode), 'Curated cognition view.')} "
            f"Layer: {str(self.layer_select.currentData() or self.default_layer).replace('_', ' ').title()} | "
            f"Visible nodes: {visible_nodes} | Visible edges: {visible_edges} | "
            f"Edge focus: {str(self.edge_filter_select.currentData() or 'top').replace('_', ' ').title()}."
        )


class ThoughtglassPane(GraphWorkbenchPane):
    def __init__(self, *, cognition_service: Any, awareness_service: SystemAwarenessService, memory_chamber: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            title="Thoughtglass",
            subtitle="Live operational surface for the unified OBEOS cognition graph: startup truth, activity, faults, and graph focus.",
            kicker="Cognition",
            default_mode="live_brain",
            available_modes=(
                ("live_brain", "Live Brain"),
                ("neighborhood", "Neighborhood"),
                ("agents", "Agents"),
                ("faults", "Faults"),
            ),
            mode_descriptions={
                "live_brain": "Thoughtglass Home keeps the living operator anchors and active cognition surface in view.",
                "neighborhood": "Neighborhood drills into the currently selected node and its strongest nearby relationships.",
                "agents": "Agents emphasizes Blackfong, D.A.I.S.E., Tiny, Mobray, and the active operator cast.",
                "faults": "Faults isolates degraded, warning, and unstable regions so problems are easier to read.",
            },
            mode_edge_filters={
                "live_brain": "top",
                "neighborhood": "show_more",
                "agents": "agent",
                "faults": "fault",
            },
            cognition_service=cognition_service,
            awareness_service=awareness_service,
            memory_chamber=memory_chamber,
            default_layer="live_brain",
            parent=parent,
        )
        layout = self.layout()
        if layout is not None:
            layout.removeWidget(self.graph)
            self.brain_host = Thoughtglass3DHost(self.graph, self)
            self.brain_host.drill_requested.connect(self._handle_drill_request)
            layout.addWidget(self.brain_host, 1)

    def apply_runtime(self, *, cognition_snapshot: Dict[str, Any], graph_payload: Dict[str, Any]) -> None:
        super().apply_runtime(cognition_snapshot=cognition_snapshot, graph_payload=graph_payload)
        if hasattr(self, "brain_host"):
            self.brain_host.apply_graph(snapshot=cognition_snapshot, graph_payload=graph_payload)


class MemoryChamberPane(GraphWorkbenchPane):
    def __init__(self, *, cognition_service: Any, awareness_service: SystemAwarenessService, memory_chamber: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            title="Memory Chamber",
            subtitle="Analysis workbench over the same authoritative cognition graph: clusters, files, agents, weak links, and reasoning paths.",
            kicker="Analysis",
            default_mode="cluster_map",
            available_modes=(
                ("cluster_map", "Cluster Map"),
                ("neighborhood", "Neighborhood"),
                ("documents", "Documents"),
                ("agents", "Agents"),
                ("faults", "Faults"),
                ("full_debug_graph", "Full Debug Graph"),
            ),
            mode_descriptions={
                "cluster_map": "Cluster Map treats Memory Chamber like an archive atlas instead of a live cockpit.",
                "neighborhood": "Neighborhood follows one concept, file, session, or agent through nearby graph structure.",
                "documents": "Documents lifts file and session artifacts so archive trails are easier to inspect.",
                "agents": "Agents shows the participants, producers, and interpreters writing into the same cognition graph.",
                "faults": "Faults reveals degraded paths, unresolved alerts, and unstable graph neighborhoods.",
                "full_debug_graph": "Full Debug Graph exposes the densest developer-facing slice without changing the backend graph.",
            },
            mode_edge_filters={
                "cluster_map": "concept",
                "neighborhood": "show_more",
                "documents": "document",
                "agents": "agent",
                "faults": "fault",
                "full_debug_graph": "show_all",
            },
            cognition_service=cognition_service,
            awareness_service=awareness_service,
            memory_chamber=memory_chamber,
            default_layer="memory",
            parent=parent,
        )


class NotificationPane(WorkspacePane):
    def __init__(self, *, notification_service: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            title="Notifications",
            subtitle="Recent alerts, status updates, and operator notices.",
            parent=parent,
        )
        self.kicker.setText("Inbox")
        self.notification_service = notification_service
        self.add_button("Refresh", self.refresh)
        self.add_button("Mark All Read", self.mark_all_read)
        self.write({"status": "warming", "notifications": []})

    def refresh(self) -> None:
        self.apply_notifications(self.notification_service.list_notifications(limit=50))

    def apply_notifications(self, payload: Dict[str, Any]) -> None:
        self.write(payload)

    def mark_all_read(self) -> None:
        self.write(self.notification_service.mark_all_read())


class TracePane(WorkspacePane):
    def __init__(self, *, memory_service: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            title="Trace",
            subtitle="Recent reasoning, startup states, and operator event traces.",
            parent=parent,
        )
        self.kicker.setText("Trace")
        self.memory_service = memory_service
        self.add_button("Refresh", self.refresh)
        self.add_button("Operator Log", self.show_operator_log)
        self.add_button("Reasoning Trace", self.show_reasoning)
        self.add_button("Verify Bundle", self.verify_bundle)
        self.write({"status": "warming", "recent_events": []})

    def refresh(self) -> None:
        events = self.memory_service.event_store.list_events(limit=40)
        self.apply_trace({"recent_events": [event.to_dict() for event in events]})

    def show_operator_log(self) -> None:
        events = self.memory_service.event_store.list_events(limit=80)
        self.apply_trace({"recent_events": [event.to_dict() for event in events], "operator_log_mode": True})

    def show_reasoning(self) -> None:
        events = self.memory_service.event_store.list_events_by_type("reasoning_trace", limit=40)
        self.write({"reasoning_trace": [event.to_dict() for event in events]})

    def verify_bundle(self) -> None:
        from blackfong_ecosystem.verification import VerificationService

        self.write(VerificationService().bundle())

    def apply_trace(self, payload: Dict[str, Any]) -> None:
        self.write(self._build_merged_payload(payload))

    def show_request_trace(self, request_id: str) -> None:
        if not request_id:
            return
        trace = self.memory_service.get_request_trace(request_id)
        self.apply_trace({"request_trace": trace, "request_id": request_id})

    @staticmethod
    def _build_merged_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        from blackfong_ecosystem.config import paths

        merged = dict(payload or {})
        log_path = paths.data_root / "logs" / "tinyprogrammer.log"
        if log_path.exists():
            try:
                merged["tinyprogrammer_log_tail"] = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-24:]
            except OSError:
                merged["tinyprogrammer_log_tail"] = []
        else:
            merged["tinyprogrammer_log_tail"] = []
        startup = merged.get("startup") or {}
        if startup.get("subsystems"):
            merged["startup_subsystems"] = startup.get("subsystems")
        return merged


class CommandPane(WorkspacePane):
    def __init__(self, *, command_registry: CommandRegistry, command_context: Callable[[], CommandContext], parent: Optional[QWidget] = None) -> None:
        super().__init__(
            title="Command Palette",
            subtitle="Search and run the same commands used by the HUD and future TUI.",
            parent=parent,
        )
        self.kicker.setText("Commands")
        self.command_registry = command_registry
        self.command_context = command_context
        self.command_input = QLineEdit(self)
        self.command_input.setPlaceholderText("Search or run commands like /status, /cognition, /redamon status.")
        self.controls.addWidget(self.command_input)
        self.add_button("Search", self.search)
        self.add_button("Run", self.run)
        self.add_button("Catalog", self.catalog)
        self.add_button("Keyboard", self.show_keyboard)
        self.add_button("Verify Bundle", self.verify_bundle)
        self.command_input.returnPressed.connect(self.run)

    def verify_bundle(self) -> None:
        from blackfong_ecosystem.verification import VerificationService

        self.write(VerificationService().bundle())

    def show_keyboard(self) -> None:
        self.write_html(shortcuts_reference_html())

    def _text(self) -> str:
        return self.command_input.text().strip()

    def search(self) -> None:
        query = self._text().lower()
        items = [
            item
            for item in self.command_registry.catalog()
            if not query
            or query in item.get("command", "").lower()
            or query in item.get("description", "").lower()
            or query in item.get("category", "").lower()
        ]
        self.write({"status": "ok", "matches": items[:40], "total_matches": len(items)})

    def run(self) -> None:
        command = self._text()
        if not command:
            self.write({"status": "error", "error": "Enter a command first."})
            return
        self.write(self.command_registry.execute(command, self.command_context()))

    def catalog(self) -> None:
        self.write({"status": "ok", "commands": self.command_registry.catalog()})


class IntegrationPane(WorkspacePane):
    def __init__(self, *, integration_registry: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            title="Integrations",
            subtitle="Optional tacklebox projects stay visibly external and keep distinct authority boundaries.",
            parent=parent,
        )
        self.kicker.setText("Tacklebox")
        self.integration_registry = integration_registry
        self.integration_select = QComboBox(self)
        for integration_id in self.integration_registry.discoverable_ids():
            self.integration_select.addItem(integration_id, integration_id)
        self.mode_select = QComboBox(self)
        self.mode_select.addItem("Default", "")
        self.mode_select.addItem("CLI", "cli")
        self.mode_select.addItem("TUI", "tui")
        self.mode_select.addItem("GUI", "gui")
        self.controls.addWidget(self.integration_select)
        self.controls.addWidget(self.mode_select)
        self.add_button("Refresh", self.refresh)
        self.add_button("Describe", self.describe)
        self.add_button("Dry Launch", self.dry_launch)
        self.refresh()

    def _current_id(self) -> str:
        return str(self.integration_select.currentData() or "codingagent_external")

    def refresh(self) -> None:
        self.apply_listing(self.integration_registry.list_integrations())

    def describe(self) -> None:
        self.write(self.integration_registry.describe(self._current_id()))

    def dry_launch(self) -> None:
        self.write(self.integration_registry.launch_contract(self._current_id(), execute=False, mode=self.mode_select.currentData()))

    def apply_listing(self, payload: Dict[str, Any]) -> None:
        self.write(payload)


class RedAmonPane(WorkspacePane):
    def __init__(self, *, redamon_workflow: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            title="RedAmon Mode",
            subtitle="Separate security workflow mode with approval, review, and result-ingestion boundaries.",
            parent=parent,
        )
        self.kicker.setText("Security")
        self.redamon_workflow = redamon_workflow
        self.objective_input = QLineEdit(self)
        self.objective_input.setPlaceholderText("Objective, for example: review recon plan for host or subnet.")
        self.target_input = QLineEdit(self)
        self.target_input.setPlaceholderText("Target or label for the proposed workflow.")
        self.controls.addWidget(self.objective_input)
        self.controls.addWidget(self.target_input)
        self.add_button("Status", self.refresh)
        self.add_button("Enter Mode", self.enter_mode)
        self.add_button("Propose Launch", self.propose_launch)
        self.add_button("Dry Launch", self.dry_launch)
        self.write({"status": "warming", "message": "RedAmon mode is loading its workflow status."})

    def refresh(self) -> None:
        self.apply_status(self.redamon_workflow.status())

    def enter_mode(self) -> None:
        self.write(self.redamon_workflow.enter_mode(objective=self.objective_input.text().strip()))

    def propose_launch(self) -> None:
        self.write(
            self.redamon_workflow.propose_launch(
                objective=self.objective_input.text().strip(),
                target=self.target_input.text().strip(),
            )
        )

    def dry_launch(self) -> None:
        self.write(
            self.redamon_workflow.launch(
                consent={"approved": True, "level": 2},
                execute=False,
            )
        )

    def apply_status(self, payload: Dict[str, Any]) -> None:
        self.write(payload)


class MainWindow(QMainWindow):
    """Main window for the managed Blackfong Workspace dashboard."""


    def __init__(self, *, start_timer: bool = True, initial_refresh: bool = True, start_background: bool = True) -> None:
        debug_session_log(hypothesis_id="B", location="desktop_shell.py:MainWindow.__init__", message="MainWindow init start")
        super().__init__()
        self.setWindowTitle("Blackfong Workspace")
        self.resize(1680, 1040)
        self._closing = False
        self._refresh_worker: Optional[WorkspaceRefreshWorker] = None
        self._refresh_pending = False
        self._chat_in_progress = False
        self._tiny_retry_worker: Optional[TinyProgrammerAutoStartWorker] = None
        self._tiny_retry_index = 0
        self._tiny_retry_timer = QTimer(self)
        self._tiny_retry_timer.setSingleShot(True)
        self._tiny_retry_timer.timeout.connect(self._start_tiny_retry)
        self.settings = QSettings("Blackfong", "Workspace")

        self.services = get_ui_services()
        debug_session_log(hypothesis_id="B", location="desktop_shell.py:MainWindow.__init__", message="UI services loaded")
        self.command_registry = get_command_registry()
        self.services.startup_coordinator.begin_boot(surface="workspace")

        self.chat_pane = ChatPane(
            operator_chat=self.services.operator_chat,
            memory_service=self.services.memory_service,
            command_registry=self.command_registry,
            command_context=self._command_context,
            session_boot_mode_getter=lambda: str(
                self.settings.value("workspace/chat_session_boot_mode", "resume_shared")
            ),
            session_boot_mode_setter=lambda mode: self.settings.setValue(
                "workspace/chat_session_boot_mode", mode
            ),
        )
        self.chat_pane.chat_started.connect(self._on_chat_started)
        self.chat_pane.chat_finished.connect(self._on_chat_finished)
        self.overview_pane = OverviewPane(
            operator_summary=self.services.operator_summary,
            tinyprogrammer=self.services.tinyprogrammer,
        )
        self.brain_pane = ThoughtglassPane(
            cognition_service=self.services.cognition_service,
            awareness_service=self.services.awareness_service,
            memory_chamber=self.services.memory_chamber,
        )
        self.memory_chamber_pane = MemoryChamberPane(
            cognition_service=self.services.cognition_service,
            awareness_service=self.services.awareness_service,
            memory_chamber=self.services.memory_chamber,
        )
        self.tiny_pane = TinyPane(tinyprogrammer=self.services.tinyprogrammer)
        self.files_pane = FilesPane(
            file_scope_service=self.services.file_scope_service,
            file_workflow=self.services.file_workflow,
        )
        self.system_pane = SystemPane(
            awareness_service=self.services.awareness_service,
            platform_service=self.services.platform_service,
            docker_stack=self.services.docker_stack,
        )
        self.command_pane = CommandPane(
            command_registry=self.command_registry,
            command_context=self._command_context,
        )
        self.integration_pane = IntegrationPane(integration_registry=self.services.integration_registry)
        self.redamon_pane = RedAmonPane(redamon_workflow=self.services.redamon_workflow)
        self.notification_pane = NotificationPane(notification_service=self.services.notification_service)
        self.trace_pane = TracePane(memory_service=self.services.memory_service)
        self.workbench_tabs = QTabWidget(self)
        self.workbench_tabs.setDocumentMode(True)
        self.command_center_pane = CommandCenterPane(
            launchers=[
                ("Open HUD", lambda: webbrowser.open("http://127.0.0.1:8080/")),
                ("Terminal", self._open_windows_terminal),
            ]
        )
        self.workbench_tabs.addTab(self.command_center_pane, "Command Center")
        self.workbench_tabs.addTab(self.brain_pane, "Thoughtglass Home")
        self.workbench_tabs.addTab(self.memory_chamber_pane, "Memory Chamber")
        self.workbench_tabs.addTab(self.chat_pane, "Chat")
        self.setCentralWidget(self.workbench_tabs)

        self._build_toolbar()
        self._build_docks()
        self.window_manager = WorkspaceWindowManager(self, self.settings)
        self._register_window_panels()
        self._restore_layout()
        self.window_manager.restore_persisted()
        debug_session_log(hypothesis_id="D", location="desktop_shell.py:MainWindow.__init__", message="Layout restored")
        self._install_shortcuts()
        self._warmup_bridge = WarmupStatusBridge(self._on_warmup_status, self)
        self._warmup_worker = BackendWarmupWorker(on_status=self._warmup_bridge.status.emit)
        self._llamacpp_worker = LlamaCppServerManager()
        if start_background:
            self._warmup_worker.start()
            # BackendWarmupWorker starts only the selected shared model lane.
        self._live_connected = False
        self._live_refresh_pending = False
        self._live_stream = DesktopLiveStreamWorker(parent=self)
        self._live_stream.event_received.connect(self._handle_live_event)
        self._live_stream.connection_changed.connect(self._handle_live_connection)
        if start_background:
            self._live_stream.start()
        debug_session_log(hypothesis_id="A", location="desktop_shell.py:MainWindow.__init__", message="Live stream worker started")
        self._warmup_timer = QTimer(self)
        self._warmup_timer.setInterval(2000)
        self._warmup_timer.timeout.connect(self._update_warmup_chip)
        link_surface("desktop")
        # OBEOS_START_WORKSPACE is set only when Earth spawns this window from its
        # Classic Workspace action. Earth has already shown a boot surface by then, so a
        # second splash is redundant and competes with Earth's renderer for the GPU.
        self._boot_splash = None
        if not os.getenv("OBEOS_START_WORKSPACE"):
            self._boot_splash = WorkspaceBootSplash(
                self,
                snapshot_provider=lambda: self.services.startup_coordinator.snapshot(),
                warmup_status_provider=lambda: self._warmup_worker.result,
            )
            self._boot_splash.destroyed.connect(self._on_boot_splash_destroyed)
            self._sync_boot_splash_geometry()
            self._boot_splash.show()
            self._boot_splash.raise_()

        self._timer = QTimer(self)
        self._timer.setInterval(15000)
        self._timer.timeout.connect(lambda: self.request_refresh(reason="timer"))
        if start_timer:
            self._timer.start()
        # While the splash animates, keep the GUI thread off the full refresh and the
        # warmup chip. Both resume in _flush_deferred_startup once it has gone.
        self._pending_startup_refresh = bool(initial_refresh)
        if self._boot_splash is None:
            self._flush_deferred_startup()
        else:
            QTimer.singleShot(12000, self._flush_deferred_startup)
        debug_session_log(hypothesis_id="B", location="desktop_shell.py:MainWindow.__init__", message="MainWindow init complete", data={"start_timer": start_timer, "initial_refresh": initial_refresh})

    def _logged_handler(self, action: str, handler: Callable[[], None]) -> Callable[[], None]:
        def wrapped() -> None:
            started = time.perf_counter()
            try:
                handler()
                log_ui_action(
                    surface="desktop.toolbar",
                    action=action,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            except Exception as exc:
                log_ui_action(
                    surface="desktop.toolbar",
                    action=action,
                    status="error",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error=str(exc),
                )
                raise

        return wrapped

    def _toolbar_section_label(self, label: str) -> QLabel:
        section = QLabel(label.upper(), self)
        section.setProperty("toolbarRole", "section")
        return section

    def _toolbar_action_button(self, label: str, handler: Callable[[], None], *, role: str = "primary") -> QPushButton:
        button = QPushButton(label, self)
        button.setProperty("toolbarRole", role)
        button.clicked.connect(handler)
        return button

    def _toolbar_menu_button(self, label: str, menu: QMenu) -> QToolButton:
        button = QToolButton(self)
        button.setText(label)
        button.setPopupMode(QToolButton.InstantPopup)
        button.setMenu(menu)
        button.setProperty("toolbarRole", "menu")
        return button

    def _build_toolbar_status_cluster(self) -> QWidget:
        cluster = QWidget(self)
        cluster.setProperty("toolbarRole", "statusCluster")
        cluster_layout = QVBoxLayout(cluster)
        cluster_layout.setContentsMargins(10, 8, 10, 8)
        cluster_layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        self.overall_chip = QLabel("BOOTING", cluster)
        self.overall_chip.setProperty("toolbarRole", "statusHero")
        top_row.addWidget(self.overall_chip, 2)
        top_row.addWidget(self._build_surface_dots(cluster), 1)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(6)
        self.backend_chip = QLabel("backend: warming", cluster)
        self.daise_chip = QLabel("daise: warming", cluster)
        self.tiny_chip = QLabel("tiny: booting", cluster)
        self.model_chip = QLabel(f"model: {chat_settings.model_for_agent('daise')}", cluster)
        self.transport_chip = QLabel("transport: polling", cluster)
        for chip in (self.backend_chip, self.daise_chip, self.tiny_chip, self.model_chip, self.transport_chip):
            chip.setProperty("toolbarRole", "statusMinor")
            bottom_row.addWidget(chip)

        cluster_layout.addLayout(top_row)
        cluster_layout.addLayout(bottom_row)
        return cluster

    def _build_surface_dots(self, parent: QWidget) -> QWidget:
        """Live online/offline dots per surface, replacing the old static
        "Desktop + HUD linked" text (which only ever recorded that a surface had
        *ever* connected, persisted forever - not whether it's up right now).
        New surfaces (Sentinel, etc.) just need an entry added to this tuple plus
        a case in _refresh_surface_dots."""
        container = QWidget(parent)
        container.setProperty("toolbarRole", "surfaceDots")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._surface_dot_marks: Dict[str, QLabel] = {}
        self._surface_dot_labels: Dict[str, QLabel] = {}
        for key, label in (("desktop", "Desktop"), ("hud", "HUD"), ("tiny", "Tiny"), ("earth", "Earth")):
            pair = QWidget(container)
            pair_layout = QHBoxLayout(pair)
            pair_layout.setContentsMargins(0, 0, 0, 0)
            pair_layout.setSpacing(4)
            mark = QLabel("●", pair)
            mark.setProperty("toolbarRole", "surfaceDotMark")
            mark.setProperty("surfaceState", "offline")
            text = QLabel(label, pair)
            text.setProperty("toolbarRole", "surfaceDotLabel")
            text.setProperty("surfaceState", "offline")
            pair_layout.addWidget(mark)
            pair_layout.addWidget(text)
            layout.addWidget(pair)
            self._surface_dot_marks[key] = mark
            self._surface_dot_labels[key] = text
        layout.addStretch(1)
        return container

    def _set_surface_state(self, key: str, online: bool) -> None:
        state = "online" if online else "offline"
        for registry in (getattr(self, "_surface_dot_marks", {}), getattr(self, "_surface_dot_labels", {})):
            widget = registry.get(key)
            if widget is None:
                continue
            widget.setProperty("surfaceState", state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    @staticmethod
    def _tcp_probe(host: str, port: int, timeout: float = 0.15) -> bool:
        """Cheap liveness check for a local service: can we open a TCP connection
        at all. Deliberately short timeout since this runs on the UI thread on
        every refresh tick - a hung/firewalled port should not visibly stall the
        workspace."""
        import socket

        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _earth_is_running() -> bool:
        """Best-effort: Earth's own RuntimeLease (blackfong_ecosystem/earth/lease.py)
        holds a non-blocking OS lock on <data-dir>/runtime.lock for as long as it's
        running. Try the same lock ourselves, non-blocking: acquiring it means
        nobody else holds it (Earth is not running), so release it immediately;
        failing to acquire it means Earth has it. Only covers Earth's default data
        dir (matches earth/desktop.py's default) - a custom --data-dir won't be
        detected, which is an acceptable gap for a status dot. Never raises.
        """
        try:
            from pathlib import Path

            default_dir = Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "OBEOS" / "Earth"
            lock_path = default_dir / "runtime.lock"
            if not lock_path.exists():
                return False
            with open(lock_path, "a+b") as handle:
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    return False
                except OSError:
                    return True
        except Exception:
            return False

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Workspace", self)
        toolbar.setObjectName("workspaceToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)

        toolbar.addWidget(self._toolbar_section_label("Primary"))
        primary_actions = [
            ("Open HUD", self._logged_handler("Open HUD", lambda: webbrowser.open("http://127.0.0.1:8080/"))),
            ("Refresh", self._logged_handler("Refresh", lambda: self.request_refresh(reason="operator"))),
            ("Brain Home", self._logged_handler("Brain Home", lambda: self.apply_layout_preset("brain_home"))),
            ("Memory Chamber", self._logged_handler("Memory Chamber", lambda: self.apply_layout_preset("memory_chamber"))),
            ("Chat Focus", self._logged_handler("Chat Focus", lambda: self.apply_layout_preset("chat_focus"))),
        ]
        for label, handler in primary_actions:
            toolbar.addWidget(self._toolbar_action_button(label, handler, role="primary"))

        toolbar.addSeparator()
        toolbar.addWidget(self._toolbar_section_label("Layouts"))
        layout_menu = QMenu(self)
        for label, preset in (
            ("Tiny Focus", "tiny_focus"),
            ("Security Review", "security_review"),
        ):
            action = layout_menu.addAction(label)
            action.triggered.connect(self._logged_handler(f"Layout:{label}", lambda checked=False, name=preset: self.apply_layout_preset(name)))
        layout_menu.addSeparator()
        for group_name in ("debug", "tiny_focus", "security"):
            action = layout_menu.addAction(f"Group: {group_name.replace('_', ' ').title()}")
            action.triggered.connect(self._logged_handler(f"Group:{group_name}", lambda checked=False, name=group_name: self.window_manager.apply_tab_group(name)))
        collapse_tiny = layout_menu.addAction("Collapse Tiny Dock")
        collapse_tiny.triggered.connect(self._logged_handler("Collapse Tiny Dock", lambda: self.window_manager.toggle_collapsed("dock_tiny")))
        collapse_active = layout_menu.addAction("Collapse Active Dock")
        collapse_active.setToolTip("Collapse the focused dock to a slim rail")
        collapse_active.triggered.connect(self._logged_handler("Collapse Active Dock", self._collapse_active_dock))
        layout_button = self._toolbar_menu_button("Layouts", layout_menu)
        toolbar.addWidget(layout_button)

        toolbar.addSeparator()
        toolbar.addWidget(self._toolbar_section_label("Pop Outs"))
        float_menu = QMenu(self)
        for label, panel_id in (
            ("Thoughtglass", "workbench_thoughtglass"),
            ("Memory Chamber", "workbench_memory"),
            ("D.A.I.S.E Chat", "workbench_chat"),
            ("TinyProgrammer", "dock_tiny"),
            ("Files", "dock_files"),
            ("RedAmon", "dock_redamon"),
            ("Trace", "dock_trace"),
            ("Notifications", "dock_notifications"),
        ):
            action = float_menu.addAction(label)
            action.triggered.connect(self._logged_handler(f"Float:{label}", lambda checked=False, pid=panel_id: self.window_manager.float_panel(pid)))
        # "TinyProgrammer" above only floats the dock into view - it says
        # nothing about the companion process itself, so with autostart off
        # by default (deliberately: no extra weight at OBEOS launch until the
        # operator actually wants it) the floated panel just shows "offline".
        # This action does the other half: it actually starts the companion,
        # on demand, the same way the automatic retry path would.
        start_tiny_action = float_menu.addAction("Start TinyProgrammer")
        start_tiny_action.triggered.connect(self._logged_handler("Start TinyProgrammer", self._manual_start_tiny))
        # Earth is its own process, started on request only - see the
        # --earth flip at the top of this file. This starts it the same way
        # Earth's own "Classic Workspace" button starts the workspace: a
        # plain subprocess, independent of this one, free to move to another
        # monitor or to simply not be started at all on a weaker machine.
        start_earth_action = float_menu.addAction("Start OBEOS Earth")
        start_earth_action.triggered.connect(self._logged_handler("Start OBEOS Earth", self._manual_start_earth))
        dock_back_action = float_menu.addAction("Dock Back Active")
        dock_back_action.triggered.connect(self._logged_handler("Dock Back Active", self._dock_back_active_panel))
        float_button = self._toolbar_menu_button("Pop Outs", float_menu)
        toolbar.addWidget(float_button)

        toolbar.addSeparator()
        toolbar.addWidget(self._toolbar_section_label("Developer"))
        developer_menu = QMenu(self)
        for label, handler in (
            ("Show System Dock", lambda: self._raise_dock(self.system_dock)),
            ("Show Trace Dock", lambda: self._raise_dock(self.trace_dock)),
            ("Show Notifications Dock", lambda: self._raise_dock(self.notifications_dock)),
            ("Reset Layout", self._reset_layout),
        ):
            action = developer_menu.addAction(label)
            action.triggered.connect(self._logged_handler(label, handler))
        developer_button = self._toolbar_menu_button("Developer", developer_menu)
        toolbar.addWidget(developer_button)

        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        toolbar.addWidget(self._build_toolbar_status_cluster())

    def _build_docks(self) -> None:
        self.left_nav_tabs = QTabWidget(self)
        self.left_nav_tabs.addTab(self.overview_pane, "Overview")
        self.left_nav_tabs.addTab(self.command_pane, "Commands")
        self.left_dock = ManagedDockWidget("Navigation", "left_navigation_dock", self)
        self.left_dock.setWidget(self.left_nav_tabs)
        self.left_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.left_dock)

        self.tiny_dock = self._make_dock("TinyProgrammer", "tiny_dock", self.tiny_pane, Qt.RightDockWidgetArea)
        self.files_dock = self._make_dock("Files", "files_dock", self.files_pane, Qt.RightDockWidgetArea)
        self.redamon_dock = self._make_dock("RedAmon", "redamon_dock", self.redamon_pane, Qt.RightDockWidgetArea)
        self.integrations_dock = self._make_dock("Integrations", "integrations_dock", self.integration_pane, Qt.RightDockWidgetArea)
        self.system_dock = self._make_dock("System", "system_dock", self.system_pane, Qt.RightDockWidgetArea)
        self.trace_dock = self._make_dock("Trace", "trace_dock", self.trace_pane, Qt.BottomDockWidgetArea)
        self.notifications_dock = self._make_dock("Notifications", "notifications_dock", self.notification_pane, Qt.BottomDockWidgetArea)

        self.tabifyDockWidget(self.tiny_dock, self.files_dock)
        self.tabifyDockWidget(self.files_dock, self.redamon_dock)
        self.tabifyDockWidget(self.redamon_dock, self.integrations_dock)
        self.tabifyDockWidget(self.integrations_dock, self.system_dock)
        self.tabifyDockWidget(self.trace_dock, self.notifications_dock)
        self.trace_dock.raise_()

    def _make_dock(self, title: str, object_name: str, widget: QWidget, area: Qt.DockWidgetArea) -> ManagedDockWidget:
        dock = ManagedDockWidget(title, object_name, self)
        dock.setWidget(widget)
        dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        self.addDockWidget(area, dock)
        return dock

    def _register_window_panels(self) -> None:
        self.window_manager.register_workbench_tab(
            "workbench_thoughtglass",
            tab_host=self.workbench_tabs,
            widget=self.brain_pane,
            title="Thoughtglass Home",
        )
        self.window_manager.register_workbench_tab(
            "workbench_memory",
            tab_host=self.workbench_tabs,
            widget=self.memory_chamber_pane,
            title="Memory Chamber",
        )
        self.window_manager.register_workbench_tab(
            "workbench_chat",
            tab_host=self.workbench_tabs,
            widget=self.chat_pane,
            title="Chat",
        )
        self.window_manager.register_dock("dock_left", self.left_dock)
        self.window_manager.register_dock("dock_tiny", self.tiny_dock)
        self.window_manager.register_dock("dock_files", self.files_dock)
        self.window_manager.register_dock("dock_redamon", self.redamon_dock)
        self.window_manager.register_dock("dock_integrations", self.integrations_dock)
        self.window_manager.register_dock("dock_system", self.system_dock)
        self.window_manager.register_dock("dock_trace", self.trace_dock)
        self.window_manager.register_dock("dock_notifications", self.notifications_dock)

    def _install_shortcuts(self) -> None:
        layer_map = {
            "layer_live_brain": "live_brain",
            "layer_memory": "memory",
            "layer_tasks": "tasks",
            "layer_sensors": "sensors",
            "layer_faults": "faults",
            "layer_forge": "forge",
        }
        handlers = {
            "refresh": lambda: self.request_refresh(reason="shortcut"),
            "command_palette": lambda: (self.left_dock.show(), self.left_dock.raise_(), self.left_nav_tabs.setCurrentWidget(self.command_pane)),
            "send_chat": self.chat_pane.send_message,
            "close_panel": self._dock_back_active_panel,
            "next_tab": lambda: self._cycle_workbench_tab(1),
            "prev_tab": lambda: self._cycle_workbench_tab(-1),
            "layout_brain": lambda: self.apply_layout_preset("brain_home"),
            "layout_memory": lambda: self.apply_layout_preset("memory_chamber"),
            "layout_chat": lambda: self.apply_layout_preset("chat_focus"),
            "layout_tiny": lambda: self.apply_layout_preset("tiny_focus"),
            "layout_security": lambda: self.apply_layout_preset("security_review"),
            "open_hud": lambda: webbrowser.open("http://127.0.0.1:8080/"),
        }
        for action_id, layer in layer_map.items():
            handlers[action_id] = lambda layer=layer: self._set_thoughtglass_layer(layer)
        install_desktop_shortcuts(self, handlers, overrides=load_shortcut_overrides(self.settings))

    def _cycle_workbench_tab(self, direction: int) -> None:
        count = self.workbench_tabs.count()
        if count <= 0:
            return
        next_index = (self.workbench_tabs.currentIndex() + direction) % count
        self.workbench_tabs.setCurrentIndex(next_index)

    def _set_thoughtglass_layer(self, layer: str) -> None:
        self.workbench_tabs.setCurrentWidget(self.brain_pane)
        index = self.brain_pane.layer_select.findData(layer)
        if index >= 0:
            self.brain_pane.layer_select.setCurrentIndex(index)
        self.brain_pane.refresh()

    def _dock_back_active_panel(self) -> None:
        active = self.workbench_tabs.currentWidget()
        for panel_id, slot in self.window_manager._slots.items():
            if slot.widget is active and slot.floating:
                self.window_manager.dock_back(panel_id)
                return
        for panel_id in list(self.window_manager._float_windows.keys()):
            self.window_manager.dock_back(panel_id)
            return
        focused = self.focusWidget()
        while focused is not None:
            for panel_id, slot in self.window_manager._slots.items():
                if slot.dock is not None and slot.dock.isAncestorOf(focused):
                    if slot.floating:
                        self.window_manager.dock_back(panel_id)
                    else:
                        slot.dock.hide()
                    return
            focused = focused.parentWidget() if hasattr(focused, "parentWidget") else None

    def _collapse_active_dock(self) -> None:
        focused = self.focusWidget()
        while focused is not None:
            for panel_id, slot in self.window_manager._slots.items():
                if slot.dock is not None and slot.dock.isAncestorOf(focused):
                    self.window_manager.toggle_collapsed(panel_id)
                    return
            focused = focused.parentWidget() if hasattr(focused, "parentWidget") else None
        self.window_manager.toggle_collapsed("dock_tiny") if hasattr(focused, "parentWidget") else None

    def _raise_dock(self, dock: QDockWidget) -> None:
        dock.show()
        dock.raise_()

    def _reset_layout(self) -> None:
        self.removeDockWidget(self.left_dock)
        self.removeDockWidget(self.tiny_dock)
        self.removeDockWidget(self.files_dock)
        self.removeDockWidget(self.redamon_dock)
        self.removeDockWidget(self.integrations_dock)
        self.removeDockWidget(self.system_dock)
        self.removeDockWidget(self.trace_dock)
        self.removeDockWidget(self.notifications_dock)
        self._build_docks()
        self.apply_layout_preset("brain_home")
        self.settings.remove("workspace/state")
        self.settings.remove("workspace/geometry")

    def _chat_session_id(self) -> Optional[str]:
        pane = getattr(self, "chat_pane", None)
        if pane is None:
            return None
        try:
            from shiboken6 import isValid

            if not isValid(pane):
                return None
            return pane.session_id
        except RuntimeError:
            return None

    def _focus_workbench_panel(self, panel_id: str) -> None:
        slot = self.window_manager._slots.get(panel_id)
        if slot is None or slot.widget is None:
            return
        if slot.floating and panel_id in self.window_manager._float_windows:
            window = self.window_manager._float_windows[panel_id]
            window.show()
            window.raise_()
            window.activateWindow()
            return
        if slot.tab_host is None:
            return
        index = slot.tab_host.indexOf(slot.widget)
        if index >= 0:
            slot.tab_host.setCurrentIndex(index)
            return
        if panel_id in self.window_manager._float_windows:
            self.window_manager.dock_back(panel_id)
            index = slot.tab_host.indexOf(slot.widget)
            if index >= 0:
                slot.tab_host.setCurrentIndex(index)

    def apply_layout_preset(self, name: str) -> None:
        with timed_ui_action(surface="desktop", action=f"layout.{name}"):
            if name == "brain_home":
                self._focus_workbench_panel("workbench_thoughtglass")
                self.tiny_dock.raise_()
                self.left_dock.show()
                self.trace_dock.raise_()
            elif name == "memory_chamber":
                self._focus_workbench_panel("workbench_memory")
                self.files_dock.raise_()
                self.left_dock.show()
            elif name == "chat_focus":
                self._focus_workbench_panel("workbench_chat")
                self.left_dock.show()
                self.notifications_dock.raise_()
            elif name == "tiny_focus":
                self.workbench_tabs.setCurrentWidget(self.brain_pane)
                self.tiny_dock.raise_()
                self.tiny_dock.show()
                if "dock_tiny" not in self.window_manager._float_windows:
                    self.window_manager.float_panel("dock_tiny")
            elif name == "security_review":
                self.workbench_tabs.setCurrentWidget(self.brain_pane)
                self.redamon_dock.raise_()
                self.trace_dock.raise_()
            self.settings.setValue("workspace/preset", name)

    def _command_context(self) -> CommandContext:
        return CommandContext(
            services=self.services,
            source="desktop",
            voice_status_provider=lambda: {"active": False, "message": "Desktop shell does not own the voice loop."},
            awareness_snapshot_provider=lambda emit=False: self.services.awareness_service.build_snapshot(emit=emit),
            cognition_snapshot_provider=lambda layer=None: self.services.cognition_service.build_snapshot(
                layer=layer or "live_brain",
                focus={"layer": layer or "live_brain", "mode": "thoughtglass"},
                request_states={},
                request_threads={},
                backend_bootstrap={"status": self.services.startup_coordinator.snapshot().get("overall_state", "warming"), "started_at": None, "completed_at": None, "error": None},
                sensor_mode="off",
                voice_status={"active": False, "message": "Desktop shell does not own the voice loop."},
            ),
        )

    _HIGH_PRIORITY_REFRESH = frozenset({"operator", "startup", "shortcut", "manual"})

    def _on_chat_started(self) -> None:
        self._chat_in_progress = True

    def _on_chat_finished(self, payload: Dict[str, Any]) -> None:
        self._chat_in_progress = False
        request_id = str(payload.get("request_id") or "")
        if request_id:
            self.trace_pane.show_request_trace(request_id)
        QTimer.singleShot(500, lambda: self.request_refresh(reason="chat_complete"))

    def _on_warmup_status(self, outcome: Dict[str, Any]) -> None:
        if str(outcome.get("status")) == "ready":
            try:
                model = str(outcome.get("model") or "")
                self.services.startup_coordinator.set_subsystem(
                    "backend_model",
                    state="ready",
                    summary=f"Model ready ({model})" if model else "Model ready",
                    meta={"model": model} if model else None,
                )
            except Exception:
                pass
        self._update_warmup_chip()

    def request_refresh(self, *, reason: str = "manual") -> None:
        if self._chat_in_progress and reason in {"timer", "live"}:
            return
        if self._refresh_worker is not None and self._refresh_worker.isRunning():
            if reason not in self._HIGH_PRIORITY_REFRESH:
                debug_session_log(
                    hypothesis_id="G",
                    location="desktop_shell.py:request_refresh",
                    message="Refresh dropped (worker busy)",
                    data={"reason": reason},
                )
                return
            self._refresh_pending = True
            debug_session_log(
                hypothesis_id="G",
                location="desktop_shell.py:request_refresh",
                message="Refresh queued (worker busy)",
                data={"reason": reason},
            )
            return
        current_graph_pane = self.workbench_tabs.currentWidget()
        current_layer = str(getattr(current_graph_pane, "layer_select", self.brain_pane.layer_select).currentData() or "live_brain")
        debug_session_log(
            hypothesis_id="G",
            location="desktop_shell.py:request_refresh",
            message="Refresh started",
            data={"reason": reason, "layer": current_layer},
        )
        self._refresh_worker = WorkspaceRefreshWorker(
            services=self.services,
            session_id=self._chat_session_id(),
            layer=current_layer,
            reason=reason,
            require_tiny_stream=reason in self._HIGH_PRIORITY_REFRESH,
            lightweight=self._chat_in_progress and reason not in self._HIGH_PRIORITY_REFRESH,
        )
        self._refresh_worker.finished.connect(self._apply_workspace_snapshot)
        self._refresh_worker.start()

    def _open_windows_terminal(self) -> None:
        """Open an external terminal at the project root; prefers Windows Terminal, falls back to PowerShell."""
        import shutil
        import subprocess

        root = os.path.dirname(os.path.abspath(__file__))
        try:
            if shutil.which("wt.exe"):
                subprocess.Popen(["wt.exe", "-d", root])
            else:
                subprocess.Popen(["powershell.exe", "-NoExit", "-Command", f"Set-Location -LiteralPath '{root}'"],
                                 creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        except OSError as exc:
            logging.getLogger(__name__).warning("Could not open terminal: %s", exc)

    def _apply_workspace_snapshot(self, payload: Dict[str, Any]) -> None:
        debug_session_log(
            hypothesis_id="C",
            location="desktop_shell.py:_apply_workspace_snapshot",
            message="Refresh snapshot applied",
            data={
                "status": payload.get("status"),
                "reason": payload.get("refresh_reason", "unknown"),
                "refresh_ms": payload.get("refresh_ms"),
            },
        )
        if self._refresh_worker is not None:
            self._refresh_worker.deleteLater()
            self._refresh_worker = None
        if payload.get("status") == "error":
            error = payload.get("error") or "Workspace refresh failed."
            self.overview_pane.write({"status": "error", "error": error})
            self.brain_pane.write({"status": "error", "error": error})
        else:
            self.overview_pane.apply_summary(payload.get("summary", {}))
            self.command_center_pane.apply_snapshot(payload)
            memory_payload = payload.get("memory_chamber", {})
            self.brain_pane.apply_runtime(
                cognition_snapshot=payload.get("cognition", {}),
                graph_payload=memory_payload.get("home", {}),
            )
            self.memory_chamber_pane.apply_runtime(
                cognition_snapshot=payload.get("cognition", {}),
                graph_payload=memory_payload.get("chamber", {}),
            )
            self.tiny_pane.apply_status(payload.get("tiny", {}))
            self.system_pane.apply_runtime(
                {
                    "summary": payload.get("summary", {}),
                    "awareness": payload.get("awareness", {}),
                }
            )
            self.notification_pane.apply_notifications(payload.get("notifications", {}))
            self.trace_pane.apply_trace(
                {
                    **(payload.get("trace") or {}),
                    "startup": (payload.get("summary") or {}).get("startup", {}),
                }
            )
            self.integration_pane.apply_listing(payload.get("integrations", {}))
            self.redamon_pane.apply_status(payload.get("redamon", {}))
            self._offer_companion_suggestion(payload)
            self._update_status_chips(payload)
            self._schedule_tiny_retry_if_needed(payload.get("tiny", {}).get("status", {}))
        if self._refresh_pending:
            self._refresh_pending = False
            self.request_refresh(reason="queued")

    def _offer_companion_suggestion(self, payload: Dict[str, Any]) -> None:
        chat = self.chat_pane
        busy = bool(chat.input.toPlainText().strip()) or bool(chat._worker and chat._worker.isRunning())
        busy = busy or time.monotonic() - chat._last_interaction_at < 60
        if busy or not chat.isVisible() or getattr(self, "_closing", False):
            return
        try:
            if not hasattr(self, "_companion_suggestions"):
                from blackfong_ecosystem.companion_suggestions import CompanionSuggestions
                from blackfong_ecosystem.config import paths
                self._companion_suggestions = CompanionSuggestions(paths.data_root / "companion-suggestions.sqlite3")
            suggestion = self._companion_suggestions.offer((payload.get("trace") or {}).get("recent_events", []))
            if suggestion:
                chat.companion_label.setText("D.A.I.S.E. suggestion: " + suggestion["text"])
                chat.companion_label.setToolTip("Evidence event: " + suggestion["evidence_event_id"])
                chat.companion_label.show()
        except (OSError, sqlite3.Error):
            pass  # Optional suggestions must not prevent the workspace from updating.

    def _update_status_chips(self, payload: Dict[str, Any]) -> None:
        startup = (payload.get("summary", {}) or {}).get("startup", {})
        subsystems = startup.get("subsystems", {})
        overall = "READY BUT DEGRADED" if startup.get("ready_but_degraded") else str(startup.get("overall_state") or "warming").upper()
        self.overall_chip.setText(overall)
        self.backend_chip.setText(f"backend: {self._subsystem_state(subsystems, 'backend_model')}")
        self.daise_chip.setText(f"daise: {self._subsystem_state(subsystems, 'daise')}")
        tiny_status = (payload.get("tiny") or {}).get("status", {})
        tiny_summary = self._tiny_chip_summary(subsystems, tiny_status)
        self.tiny_chip.setText(tiny_summary)
        self._update_warmup_chip()
        self._set_surface_state("desktop", True)
        self._set_surface_state("hud", self._tcp_probe(hud_settings.host, hud_settings.port))
        self._set_surface_state("tiny", bool(tiny_status.get("online")))
        self._set_surface_state("earth", self._earth_is_running())

    def _update_warmup_chip(self) -> None:
        warmup = getattr(self, "_warmup_worker", None)
        if warmup is None:
            return
        outcome = warmup.result
        status = str(outcome.get("status") or "idle")
        model = str(outcome.get("model") or chat_settings.model_for_agent("daise"))
        if status == "ready":
            self.model_chip.setText(f"model: ready ({model})")
        elif status == "warming":
            self.model_chip.setText(f"model: warming ({model})")
        elif status == "error":
            self.model_chip.setText("model: cold")
        else:
            self.model_chip.setText(f"model: {model}")

    def _handle_live_connection(self, connected: bool) -> None:
        self._live_connected = connected
        self.transport_chip.setText("transport: live" if connected else "transport: polling")
        if hasattr(self, "_timer"):
            self._timer.setInterval(60000 if connected else 15000)

    def _handle_live_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if event_type in {
            "cognition_snapshot",
            "cognition_delta",
            "cognition_focus",
            "cognition_fault",
            "metrics_update",
        }:
            if self._refresh_worker is not None and self._refresh_worker.isRunning():
                return
            if not self._live_refresh_pending:
                self._live_refresh_pending = True
                QTimer.singleShot(3000, self._live_refresh_tick)
        if event_type == "chat_phase" and payload.get("fast_path"):
            try:
                from shiboken6 import isValid

                if isValid(self.chat_pane):
                    self.chat_pane.status_label.setText("Fast path: single-agent fast route")
            except RuntimeError:
                pass

    def _live_refresh_tick(self) -> None:
        self._live_refresh_pending = False
        self.request_refresh(reason="live")

    @staticmethod
    def _tiny_chip_summary(subsystems: Dict[str, Any], tiny_status: Dict[str, Any]) -> str:
        state = str((subsystems.get("tinyprogrammer") or {}).get("state") or "warming")
        if tiny_status.get("online"):
            return "tiny: online"
        reason = (
            tiny_status.get("last_error_summary")
            or tiny_status.get("stream_human_reason")
            or tiny_status.get("last_error_code")
            or tiny_status.get("stream_reason")
        )
        if reason:
            return f"tiny: {state} ({reason})"
        return f"tiny: {state}"

    @staticmethod
    def _subsystem_state(subsystems: Dict[str, Any], key: str) -> str:
        return str((subsystems.get(key) or {}).get("state") or "warming")

    def _schedule_tiny_retry_if_needed(self, tiny_status: Dict[str, Any]) -> None:
        if not self.services.tinyprogrammer.autostart_enabled:
            return
        if tiny_status.get("online") or tiny_status.get("management_state") == "launching":
            self._tiny_retry_index = 0
            return
        max_retries = int(tiny_status.get("max_retries") or len(self.services.tinyprogrammer.retry_delays_s))
        current_retries = int(tiny_status.get("retry_count") or 0)
        if current_retries >= max_retries:
            return
        if self._tiny_retry_timer.isActive() or (self._tiny_retry_worker is not None and self._tiny_retry_worker.isRunning()):
            return
        delay_seconds = float(self.services.tinyprogrammer.retry_delays_s[current_retries])
        self.services.tinyprogrammer.record_startup_retry(
            retry_count=current_retries + 1,
            delay_seconds=delay_seconds,
        )
        self.services.startup_coordinator.set_subsystem(
            "tinyprogrammer",
            state="warming",
            summary=f"TinyProgrammer retry {current_retries + 1}/{max_retries} queued.",
            reason_code=tiny_status.get("last_error_code"),
            human_reason=tiny_status.get("last_error_summary"),
            retry_count=current_retries + 1,
            max_retries=max_retries,
            next_retry_at=time.time() + delay_seconds,
            launch_attempts=int(tiny_status.get("launch_attempts") or 0),
        )
        self._tiny_retry_timer.start(int(delay_seconds * 1000))

    def _start_tiny_retry(self) -> None:
        if self._tiny_retry_worker is not None and self._tiny_retry_worker.isRunning():
            return
        self._tiny_retry_worker = TinyProgrammerAutoStartWorker(self.services.tinyprogrammer)
        self._tiny_retry_worker.finished.connect(self._on_tiny_retry_finished)
        self._tiny_retry_worker.start()

    def _manual_start_tiny(self) -> None:
        """Operator-triggered start, independent of autostart_enabled.

        autostart_enabled stays False by default on purpose - it keeps
        TinyProgrammer from adding weight to every OBEOS launch - so
        _schedule_tiny_retry_if_needed's automatic path never fires on its
        own. This is the on-demand equivalent: float the dock so the
        operator can see status, then run the same background worker the
        automatic retry uses, so a manual start still can't block the UI
        thread. _start_tiny_retry already no-ops if a start is in flight.
        """
        self.window_manager.float_panel("dock_tiny")
        self._start_tiny_retry()

    def _manual_start_earth(self) -> None:
        """Launch OBEOS Earth as its own process, on demand.

        Earth no longer starts automatically with the workspace (see the
        --earth flip at the top of this file) - it is heavy enough, between
        its own PySide6/Chromium renderer and local model warmup, that
        running it alongside this workspace by default was overloading
        weaker machines. This mirrors how Earth itself starts the workspace
        from its own "Classic Workspace" button: a plain, independent
        subprocess, not something this window manages or owns afterward.
        """
        import subprocess
        from pathlib import Path

        script_path = Path(__file__).resolve()
        command = [sys.executable, str(script_path), "--earth"]
        try:
            subprocess.Popen(command, cwd=str(script_path.parent))
            self.statusBar().showMessage("Starting OBEOS Earth…", 5000)
        except OSError as exc:
            self.statusBar().showMessage(f"Could not start OBEOS Earth: {exc}", 8000)

    def _on_tiny_retry_finished(self, result: Dict[str, Any]) -> None:
        if self._tiny_retry_worker is not None:
            self._tiny_retry_worker.deleteLater()
            self._tiny_retry_worker = None
        if result.get("success"):
            self.services.tinyprogrammer.record_startup_success()
            self.services.startup_coordinator.set_subsystem(
                "tinyprogrammer",
                state="active",
                summary="TinyProgrammer is online and visible as a managed companion.",
            )
        else:
            reason_code = (
                (result.get("tinyprogrammer") or {}).get("last_error_code")
                or result.get("reason_code")
                or "managed_launch_failed"
            )
            summary = (
                (result.get("tinyprogrammer") or {}).get("last_error_summary")
                or result.get("error")
                or result.get("human_reason")
                or "TinyProgrammer launch failed."
            )
            self.services.tinyprogrammer.record_startup_failure(
                reason_code=reason_code,
                summary=summary,
            )
        self.request_refresh(reason="tiny_retry")

    def _restore_layout(self) -> None:
        geometry = self.settings.value("workspace/geometry")
        state = self.settings.value("workspace/state")
        current_tab = self.settings.value("workspace/current_tab")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)
        if current_tab:
            for index in range(self.workbench_tabs.count()):
                if self.workbench_tabs.tabText(index) == current_tab:
                    self.workbench_tabs.setCurrentIndex(index)
                    break
        preset = self.settings.value("workspace/preset")
        if preset:
            self.apply_layout_preset(str(preset))
        self._ensure_window_fits_screen()

    def _on_boot_splash_destroyed(self, *_args: Any) -> None:
        self._boot_splash = None
        self._flush_deferred_startup()

    def _flush_deferred_startup(self) -> None:
        """Start the warmup chip and the first refresh once the splash is out of the way.

        Safe to call more than once: the splash's destroyed signal and the safety-net
        timer both land here, and whichever arrives first does the work.
        """
        if self._closing:
            return
        if not self._warmup_timer.isActive():
            self._warmup_timer.start()
        if getattr(self, "_pending_startup_refresh", False):
            self._pending_startup_refresh = False
            self.request_refresh(reason="startup")

    def _boot_splash_is_active(self) -> bool:
        splash = getattr(self, "_boot_splash", None)
        if splash is None:
            return False
        try:
            from shiboken6 import isValid

            if not isValid(splash):
                self._boot_splash = None
                return False
        except ImportError:
            pass
        try:
            return bool(splash.isVisible())
        except RuntimeError:
            self._boot_splash = None
            return False

    def _sync_boot_splash_geometry(self) -> None:
        if not self._boot_splash_is_active():
            return
        splash = self._boot_splash
        if splash is None:
            return
        try:
            splash.setGeometry(self.rect())
        except RuntimeError:
            self._boot_splash = None

    def _ensure_window_fits_screen(self) -> None:
        """Avoid restoring a Windows-saved geometry that is off-screen in the VM."""
        screens = QApplication.screens()
        if not screens:
            return
        frame = self.frameGeometry()
        target_screen = None
        for screen in screens:
            if screen.geometry().intersects(frame):
                target_screen = screen
                break
        if target_screen is None:
            target_screen = QApplication.primaryScreen() or screens[0]
        available = target_screen.availableGeometry()
        width = min(max(frame.width(), 960), available.width() - 32)
        height = min(max(frame.height(), 640), available.height() - 32)
        self.resize(width, height)
        x = available.x() + max(16, (available.width() - width) // 2)
        y = available.y() + max(16, (available.height() - height) // 2)
        self.move(QPoint(x, y))

    def resizeEvent(self, event: Any) -> None:  # pragma: no cover - GUI
        self._sync_boot_splash_geometry()
        super().resizeEvent(event)

    def closeEvent(self, event: Any) -> None:  # pragma: no cover - GUI lifecycle
        self._closing = True
        for timer in self.findChildren(QTimer):
            timer.stop()
        if hasattr(self, "_warmup_worker"):
            self._warmup_worker.stop()
        if hasattr(self, "_llamacpp_worker"):
            self._llamacpp_worker.stop()
        chat = getattr(getattr(self, "chat_pane", None), "_worker", None)
        if chat and chat.isRunning():
            chat.cancel()
        workers = [chat, self._refresh_worker, self._tiny_retry_worker,
                   getattr(getattr(self, "tiny_pane", None), "_auto_start_worker", None)]
        if any(worker and worker.isRunning() for worker in workers):
            event.ignore()
            self.setEnabled(False)
            QTimer.singleShot(100, self.close)
            return
        if hasattr(self, "_live_stream"):

            self._live_stream.stop()
            self._live_stream.wait(1500)
        if hasattr(self, "services") and hasattr(self.services.memory_chamber, "shutdown"):
            self.services.memory_chamber.shutdown()
        if hasattr(self, "window_manager"):
            self.window_manager.close_all_floats()
        if hasattr(self, "tiny_pane") and hasattr(self.tiny_pane, "stream_view"):
            self.tiny_pane.stream_view.stop()
        self.settings.setValue("workspace/geometry", self.saveGeometry())
        self.settings.setValue("workspace/state", self.saveState())
        self.settings.setValue("workspace/current_tab", self.workbench_tabs.tabText(self.workbench_tabs.currentIndex()))
        if hasattr(self, "window_manager"):
            self.window_manager._persist()
        super().closeEvent(event)


def workspace_probe() -> Dict[str, Any]:
    try:
        app = QApplication.instance() or QApplication(["blackfong-workspace-probe", "-platform", "offscreen"])
        apply_workspace_theme(app)
        window = MainWindow(start_timer=False, initial_refresh=False, start_background=False)
        return {
            "success": True,
            "window_title": window.windowTitle(),
            "center_tabs": [window.workbench_tabs.tabText(index) for index in range(window.workbench_tabs.count())],
            "left_tabs": [window.left_nav_tabs.tabText(index) for index in range(window.left_nav_tabs.count())],
            "chat_available": True,
            "thoughtglass_home_default": window.workbench_tabs.tabText(1) == "Thoughtglass Home",
            "command_center_default": window.workbench_tabs.tabText(0) == "Command Center",
            "thoughtglass_default_mode": str(window.brain_pane.mode_select.currentData() or ""),
            "memory_chamber_available": any(window.workbench_tabs.tabText(index) == "Memory Chamber" for index in range(window.workbench_tabs.count())),
            "memory_chamber_default_mode": str(window.memory_chamber_pane.mode_select.currentData() or ""),
            "native_graph_available": hasattr(window.brain_pane, "graph") and hasattr(window.memory_chamber_pane, "graph"),
            "layout_persistence": True,
            "window_manager": True,
            "float_panel_ids": [
                "workbench_thoughtglass",
                "workbench_memory",
                "workbench_chat",
                "dock_tiny",
                "dock_files",
                "dock_trace",
            ],
            "workspace_shortcuts": True,
            "boot_splash": True,
            "thoughtglass_3d": True,
            "tiny_stream_embed": True,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def main() -> int:
    debug_session_log(hypothesis_id="E", location="desktop_shell.py:main", message="desktop_shell main entered")
    try:
        app = QApplication.instance() or QApplication(sys.argv)
        apply_workspace_theme(app)
        window = MainWindow()
        requested_workspace = os.getenv("OBEOS_START_WORKSPACE", "")
        for index in range(window.workbench_tabs.count()):
            if requested_workspace.lower() in window.workbench_tabs.tabText(index).lower() and requested_workspace:
                window.workbench_tabs.setCurrentIndex(index)
                break
        debug_session_log(hypothesis_id="B", location="desktop_shell.py:main", message="MainWindow constructed")
        if os.getenv("BLACKFONG_ENABLE_TRAY", "").lower() in {"1", "true", "yes"}:
            from PySide6.QtWidgets import QMenu, QSystemTrayIcon

            tray = QSystemTrayIcon(window)
            tray_menu = QMenu()
            tray_menu.addAction("Open Workspace", window.show)
            tray_menu.addAction("Open HUD", lambda: webbrowser.open("http://127.0.0.1:8080/"))
            tray.setContextMenu(tray_menu)
            tray.setToolTip("O.B.E.O.S. Blackfong Workspace")
            tray.show()
        window.show()
        debug_session_log(hypothesis_id="E", location="desktop_shell.py:main", message="MainWindow shown, entering event loop")
        return app.exec()
    except Exception as exc:
        debug_session_log(
            hypothesis_id="B",
            location="desktop_shell.py:main",
            message="desktop_shell main failed",
            data={"error": str(exc), "error_type": type(exc).__name__},
        )
        raise


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
