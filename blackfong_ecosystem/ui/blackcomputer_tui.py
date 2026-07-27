from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from typing import Any, Dict, Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Static, TabPane, TabbedContent

try:
    from textual.widgets import RichLog as LogWidget

    LOG_WIDGET_SELECTOR = "RichLog"
except ImportError:
    from textual.widgets import TextLog as LogWidget

    LOG_WIDGET_SELECTOR = "TextLog"

from blackfong_ecosystem.cognition import DEFAULT_COGNITION_FOCUS, CognitionTelemetryService
from blackfong_ecosystem.display_names import display_agent_name, format_chat_status_line
from blackfong_ecosystem.operator_chat import OperatorChatService
from blackfong_ecosystem.config import tui_settings
from blackfong_ecosystem.conversation_memory import conversation_memory_service
from blackfong_ecosystem.file_scope_service import FileScopeService
from blackfong_ecosystem.file_workflow import FileWorkflowService
from blackfong_ecosystem.notification_service import notification_service
from blackfong_ecosystem.obeos_linux import ASCII_BOOT_INTRO, BOOT_HOME_OPTIONS, OBEOSLinuxPlatformService
from blackfong_ecosystem.operator_actions import OperatorActionService
from blackfong_ecosystem.operator_summary import OperatorSummaryService
from blackfong_ecosystem.orchestrator_loader import OrchestratorUnavailable, load_unified_orchestrator
from blackfong_ecosystem.service_registry import get_ui_services
from blackfong_ecosystem.system_awareness import SystemAwarenessService
from blackfong_ecosystem.tinyprogrammer_companion import tinyprogrammer_companion
from blackfong_ecosystem.ui_commands import CommandContext, get_command_registry

logger = logging.getLogger(__name__)


_orchestrator_components = None


def _get_orchestrator_components():
    global _orchestrator_components
    if _orchestrator_components is None:
        loaded = load_unified_orchestrator(required=True, instance=False)
        _orchestrator_components = (loaded.input_modality, loaded.orchestrator_class, True)
    return _orchestrator_components


class OBEOSBootHomeScreen(Screen[str]):
    """Branded boot-home surface shown before the full operator console."""

    CSS = """
    OBEOSBootHomeScreen {
        align: center middle;
        background: black;
        color: white;
    }
    #boot_home_shell {
        width: 96;
        padding: 1 2;
        align: center middle;
    }
    #boot_intro {
        content-align: center middle;
        width: 1fr;
        color: rgb(215, 240, 255);
        margin-bottom: 1;
    }
    #boot_enter {
        width: 24;
        min-width: 24;
        border: solid white;
        background: black;
        color: white;
        margin: 1 0 2 0;
    }
    .boot-row {
        align: center middle;
    }
    .boot-option {
        min-width: 16;
        margin: 0 1 1 0;
        border: solid rgb(80, 80, 80);
        background: rgb(8, 8, 8);
        color: rgb(225, 225, 225);
    }
    #boot_hint {
        width: 1fr;
        content-align: center middle;
        color: rgb(140, 140, 140);
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("enter", "accept", "Enter"),
        ("escape", "accept", "Enter"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="boot_home_shell"):
            yield Static(ASCII_BOOT_INTRO.strip("\n"), id="boot_intro")
            yield Button("ENTER", id="boot_enter", variant="primary")
            with Horizontal(classes="boot-row"):
                yield Button("Desktop", id="boot_desktop", classes="boot-option")
                yield Button("D.A.I.S.E.", id="boot_daise", classes="boot-option")
                yield Button("Files", id="boot_files", classes="boot-option")
            with Horizontal(classes="boot-row"):
                yield Button("System", id="boot_system", classes="boot-option")
                yield Button("Sensors", id="boot_sensors", classes="boot-option")
                yield Button("Power", id="boot_power", classes="boot-option")
            yield Static("Press Enter to continue or choose a launch surface.", id="boot_hint")
        yield Footer()

    def action_accept(self) -> None:
        self.dismiss("boot_enter")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "boot_enter")


class BlackcomputerTUIApp(App):
    """Tabbed terminal operator console for Blackfong/O.B.E.O.S."""

    BOOT_HOME_OPTIONS = BOOT_HOME_OPTIONS
    ASCII_BOOT_INTRO = ASCII_BOOT_INTRO

    CSS = """
    Screen { background: $surface; }
    TabPane { padding: 1; }
    .panel { border: solid $accent; padding: 1; height: 1fr; }
    Input { margin-top: 1; }
    Button { margin: 0 1 1 0; }
    #status_panel { border: solid $accent; padding: 1; margin-bottom: 1; }
    """ + f"\n    {LOG_WIDGET_SELECTOR} {{ height: 1fr; border: solid $primary; padding: 1; }}\n"

    BINDINGS = [
        ("v", "toggle_voice", "Toggle voice"),
        ("m", "cycle_analysis_mode", "Response mode"),
        ("ctrl+r", "refresh", "Refresh"),
        ("ctrl+c", "quit", "Quit"),
    ]

    system_state = reactive("ONLINE")
    voice_state = reactive("IDLE")
    active_agent = reactive("D.A.I.S.E")
    analysis_mode = reactive("fast")
    last_error = reactive("")
    heartbeat_state = reactive("·")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._services = get_ui_services()
        self._command_registry = get_command_registry()
        self._orchestrator = None
        self._orchestrator_loaded = False
        self._awareness_service: Optional[SystemAwarenessService] = None
        self._cognition_service: Optional[CognitionTelemetryService] = None
        self._operator_summary_service: Optional[OperatorSummaryService] = None
        self._operator_actions = self._services.operator_actions
        self._file_workflow = self._services.file_workflow
        self._file_scope_service = self._services.file_scope_service
        self._obeos_platform = self._services.platform_service
        self.current_request_id = ""
        self.current_session_id = ""
        self._sensor_mode = "off"
        self._cognition_focus = dict(DEFAULT_COGNITION_FOCUS)
        self._initial_refresh_pending = False
        self._pending_actions_count = 0
        self._latest_tiny_status: Dict[str, Any] = {}
        self._active_work_label = "idle"
        self._tiny_autostart_requested = False
        self._chat_service: Optional[OperatorChatService] = None

    @property
    def orchestrator(self):
        if not self._orchestrator_loaded:
            _, UnifiedOrchestrator, _ = _get_orchestrator_components()
            self._orchestrator = UnifiedOrchestrator()
            self._orchestrator_loaded = True
        return self._orchestrator

    def _get_chat_service(self) -> OperatorChatService:
        if self._chat_service is None:
            orchestrator = self._orchestrator if self._orchestrator_loaded else None
            memory_service = getattr(orchestrator, "memory_service", self._services.memory_service) if orchestrator else self._services.memory_service
            self._chat_service = OperatorChatService(memory_service=memory_service, orchestrator=orchestrator)
        elif self._orchestrator_loaded and self._chat_service.orchestrator is None:
            self._chat_service.orchestrator = self._orchestrator
        return self._chat_service

    def _ensure_orchestrator_available(self) -> bool:
        try:
            _ = self.orchestrator
            return True
        except OrchestratorUnavailable as exc:
            self.last_error = str(exc)
            logger.exception("D.A.I.S.E orchestrator unavailable in TUI")
            return False

    @property
    def file_workflow(self) -> FileWorkflowService:
        orchestrator = self._orchestrator if self._orchestrator_loaded else None
        shared = getattr(orchestrator, "file_workflow", None) if orchestrator is not None else None
        return shared or self._file_workflow

    @property
    def file_scope_service(self) -> FileScopeService:
        orchestrator = self._orchestrator if self._orchestrator_loaded else None
        shared = getattr(orchestrator, "file_scope_service", None) if orchestrator is not None else None
        return shared or self._file_scope_service

    def _command_context(self) -> CommandContext:
        self._sync_services_from_tui_state()
        orchestrator = self._orchestrator if self._orchestrator_loaded else None
        memory_service = getattr(orchestrator, "memory_service", self._services.memory_service) if orchestrator is not None else self._services.memory_service
        if self._services.tinyprogrammer is not tinyprogrammer_companion:
            self._services.tinyprogrammer = tinyprogrammer_companion
        return CommandContext(
            services=self._services,
            source="tui",
            session_id=self.current_session_id or None,
            request_id=self.current_request_id or None,
            payload={"request_id": self.current_request_id or None},
            orchestrator=orchestrator,
            ensure_orchestrator=self._ensure_orchestrator_available,
            voice_status_provider=self._safe_voice_status,
            awareness_snapshot_provider=lambda emit=False: self._get_awareness_service().build_snapshot(
                voice_status=self._safe_voice_status(),
                emit=emit,
            ),
            cognition_snapshot_provider=lambda layer=None: self._build_cognition_snapshot(layer=layer),
            set_cognition_layer=self._set_cognition_layer,
            sensor_mode_setter=self._set_sensor_mode,
        )

    def _set_cognition_layer(self, layer: str) -> Dict[str, Any]:
        self._cognition_focus["layer"] = layer
        self._cognition_focus["mode"] = "thoughtglass"
        return dict(self._cognition_focus)

    def _set_sensor_mode(self, mode: str) -> Dict[str, Any]:
        self._sensor_mode = mode
        return {"sensor_mode": self._sensor_mode}

    def _sync_services_from_tui_state(self) -> None:
        orchestrator = self._orchestrator if self._orchestrator_loaded else None
        memory_service = getattr(orchestrator, "memory_service", self._services.memory_service) if orchestrator is not None else self._services.memory_service
        self._services.memory_service = memory_service
        self._services.file_workflow = self.file_workflow
        self._services.file_scope_service = self.file_scope_service
        self._services.operator_actions = self._operator_actions
        self._services.platform_service = self._obeos_platform
        self._services.tinyprogrammer = tinyprogrammer_companion
        self._services.awareness_service.memory_service = memory_service
        self._services.awareness_service.tinyprogrammer = tinyprogrammer_companion
        self._services.operator_summary.memory_service = memory_service
        self._services.operator_summary.operator_actions = self._operator_actions
        self._services.operator_summary.tinyprogrammer = tinyprogrammer_companion
        self._services.operator_summary.file_scope_service = self.file_scope_service
        self._services.operator_summary.awareness_service = self._services.awareness_service
        self._services.cognition_service.memory_service = memory_service
        self._services.cognition_service.awareness_service = self._services.awareness_service
        self._services.cognition_service.operator_actions = self._operator_actions
        self._services.cognition_service.tinyprogrammer = tinyprogrammer_companion

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("", id="status_panel")
        with TabbedContent(initial="chat"):
            with TabPane("Chat", id="chat"):
                yield LogWidget(id="chat_log", wrap=True)
                yield Input(placeholder="Chat or slash command. Try /notifications, /tiny recent, /diagnose.", id="chat_input")
            with TabPane("Trace", id="trace"):
                yield LogWidget(id="trace_log", wrap=True)
            with TabPane("Actions", id="actions"):
                with Horizontal():
                    yield Button("Refresh Queue", id="actions_refresh")
                    yield Button("What Changed", id="actions_changed")
                yield LogWidget(id="actions_log", wrap=True)
            with TabPane("Cognition", id="cognition"):
                with Vertical():
                    with Horizontal():
                        yield Button("Live Brain", id="cognition_live_brain")
                        yield Button("Memory", id="cognition_memory")
                        yield Button("Tasks", id="cognition_tasks")
                    with Horizontal():
                        yield Button("Sensors", id="cognition_sensors")
                        yield Button("Faults", id="cognition_faults")
                        yield Button("Forge", id="cognition_forge")
                    yield LogWidget(id="cognition_log", wrap=True)
            with TabPane("Notifications", id="notifications"):
                with Horizontal():
                    yield Button("Refresh Inbox", id="notifications_refresh")
                    yield Button("Read All", id="notifications_read_all")
                    yield Button("Test Toast", id="notifications_test")
                yield LogWidget(id="notifications_log", wrap=True)
            with TabPane("TinyProgrammer", id="tiny"):
                with Horizontal():
                    yield Button("Status", id="tiny_status")
                    yield Button("Launch", id="tiny_launch")
                    yield Button("Native", id="tiny_native")
                    yield Button("Open", id="tiny_open")
                    yield Button("Live", id="tiny_live")
                    yield Button("Restart", id="tiny_restart")
                with Horizontal():
                    yield Button("Screensaver On", id="tiny_screen_on")
                    yield Button("Screensaver Off", id="tiny_screen_off")
                    yield Button("Recent", id="tiny_recent")
                    yield Button("BBS", id="tiny_bbs")
                    yield Button("Log", id="tiny_log_tail")
                    yield Button("Rate 5", id="tiny_rate")
                yield Input(placeholder="TinyProgrammer creative brief or nudge", id="tiny_brief_input")
                yield Static("", id="tiny_status_panel", classes="panel")
                yield LogWidget(id="tiny_log", wrap=True)
            with TabPane("Awareness", id="awareness"):
                with Horizontal():
                    yield Button("Awareness", id="awareness_refresh")
                    yield Button("Diagnose", id="awareness_diagnose")
                    yield Button("Sensors", id="awareness_sensors")
                    yield Button("Why", id="awareness_why")
                yield LogWidget(id="awareness_log", wrap=True)
            with TabPane("Files", id="files"):
                with Horizontal():
                    yield Button("Policy", id="files_policy")
                    yield Button("Proposals", id="files_proposals")
                    yield Button("Roots", id="files_roots")
                yield Input(placeholder="Machine path for /files approve-root, /files map, /files read, or /files scan", id="files_path_input")
                with Horizontal():
                    yield Button("Approve Root", id="files_approve_root")
                    yield Button("Map Path", id="files_map")
                    yield Button("Scan Path", id="files_scan")
                    yield Button("Read Path", id="files_read")
                yield LogWidget(id="files_log", wrap=True)
            with TabPane("Commands", id="commands"):
                yield LogWidget(id="commands_log", wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self._update_status()
        self._write("chat_log", "Welcome to Blackfong O.B.E.O.S. HUD parity mode is active in the TUI.")
        self._write(
            "commands_log",
            "Commands: /status, /sessions, /memory, /trace, /notifications, /notifications read-all, "
            "/cognition, /cognition memory, /cognition tasks, /cognition sensors, /cognition faults, /cognition forge, "
            "/desktop status, /desktop start, /desktop stop, /desktop workspace, /docker status, /docker up, /docker down, /docker logs, "
            "/obeos manifest, /obeos shell-compat, /obeos vm-checklist, /obeos vm-acceptance-snapshot, /obeos activate-home, "
            "/summary, /whatchanged, /files policy, /files proposals, /files roots, /files approve-root <path>, /files map <path>, "
            "/tiny status, /tiny launch, /tiny native, /tiny open, /tiny live, /tiny recent, /tiny bbs, /tiny log, /tiny brief <text>, "
            "/tiny restart, /tiny screensaver <on|off>, /tiny feedback <1-5> [text], "
            "/awareness, /performance, /backend, /environment, /sensors, /diagnose, /capabilities, /limits, /why.",
        )
        self._write(
            "commands_log",
            "Chat also supports governed file actions, for example: Create file `path` with content: ... | Read `path` | Approve proposal <id> | Apply proposal <id>.",
        )
        if self._boot_home_enabled():
            self._initial_refresh_pending = True
            self.push_screen(OBEOSBootHomeScreen(), self._handle_boot_home_choice)
        else:
            self.call_later(self._refresh_all)
        self.set_interval(max(0.25, float(tui_settings.heartbeat_interval)), self._tick_heartbeat)
        self.set_interval(max(1.0, float(tui_settings.status_refresh_interval)), self._schedule_periodic_refresh)
        self.run_worker(self._ensure_tiny_autostart(), exclusive=False)

    def _boot_home_enabled(self) -> bool:
        return os.getenv("BLACKFONG_TUI_BOOT_HOME_ENABLED", "true").lower() != "false"

    def _handle_boot_home_choice(self, choice: Optional[str]) -> None:
        self.run_worker(self._apply_boot_home_choice(choice or "boot_enter"), exclusive=False)

    def _tick_heartbeat(self) -> None:
        self.heartbeat_state = "•" if self.heartbeat_state == "·" else "·"
        self._update_status()

    def _schedule_periodic_refresh(self) -> None:
        self.run_worker(self._periodic_refresh(), exclusive=False)

    async def _periodic_refresh(self) -> None:
        await self._refresh_notifications()
        await self._refresh_actions()
        await self._refresh_tiny()
        await self._refresh_cognition()

    async def _ensure_tiny_autostart(self) -> None:
        if self._tiny_autostart_requested or not getattr(tinyprogrammer_companion, "autostart_enabled", False):
            return
        self._tiny_autostart_requested = True
        self._queue_feedback("tiny_autostart", "TinyProgrammer autostart queued.", log_id="tiny_log")
        result = await asyncio.to_thread(
            self._run_operator_action,
            "tiny_autostart",
            lambda: tinyprogrammer_companion.ensure_running(
                require_stream=bool(getattr(tinyprogrammer_companion, "auto_stream", True)),
                adopt_external=bool(getattr(tinyprogrammer_companion, "adopt_external", True)),
                open_browser=False,
                consent={"approved": True, "level": 1},
            ),
            "tinyprogrammer",
            "TinyProgrammer autostart requested at TUI startup.",
        )
        self._write_json("tiny_log", result)
        await self._refresh_tiny()
        await self._refresh_actions()
        await self._refresh_notifications()

    async def _apply_boot_home_choice(self, choice: str) -> None:
        normalized = (choice or "boot_enter").strip().lower()
        try:
            if normalized == "boot_desktop":
                result = await asyncio.to_thread(
                    self._run_operator_action,
                    "command:/desktop start",
                    lambda: self._execute_slash_command("/desktop start"),
                    "/desktop start",
                    "Desktop launch requested from the boot home.",
                )
                self._activate_tab("commands")
                self._write_json("commands_log", result)
                await self._refresh_actions()
                return
            if normalized == "boot_daise":
                self._activate_tab("chat")
                self._write("chat_log", "D.A.I.S.E. is ready. Continue in the operator console.")
                return
            if normalized == "boot_files":
                self._activate_tab("files")
                self._write_json("files_log", self._file_command("/files roots"))
                return
            if normalized == "boot_system":
                self._activate_tab("commands")
                self._write_json("commands_log", self._execute_slash_command("/obeos manifest"))
                return
            if normalized == "boot_sensors":
                self._activate_tab("awareness")
                self._write_json("awareness_log", self._execute_slash_command("/sensors"))
                await self._refresh_cognition()
                return
            if normalized == "boot_power":
                self._activate_tab("commands")
                self._write(
                    "commands_log",
                    "Power stays Ubuntu-native in v1. Use systemctl poweroff or systemctl reboot from the shell.",
                )
                return
            self._activate_tab("chat")
        finally:
            if self._initial_refresh_pending:
                self._initial_refresh_pending = False
                self.call_later(self._refresh_all)

    def _activate_tab(self, tab_id: str) -> None:
        try:
            self.query_one(TabbedContent).active = tab_id
        except Exception:
            logger.exception("Unable to activate TUI tab %s", tab_id)
            return

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat_input":
            text = event.value.strip()
            event.input.value = ""
            if text:
                await self._submit_chat_or_command(text)
        elif event.input.id == "tiny_brief_input":
            text = event.value.strip()
            event.input.value = ""
            if text:
                self._queue_feedback("tiny_brief", "Queued TinyProgrammer brief.", log_id="tiny_log")
                result = await asyncio.to_thread(
                    self._run_operator_action,
                    "tiny_brief",
                    lambda: tinyprogrammer_companion.send_brief(text),
                    "brief",
                    "TinyProgrammer brief requested from the TUI.",
                )
                self._write_json("tiny_log", result)
                await self._refresh_notifications()
                await self._refresh_actions()
                await self._refresh_tiny()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        event.button.disabled = True
        try:
            if button_id.startswith("notifications"):
                await self._handle_notification_button(button_id)
            elif button_id.startswith("tiny"):
                await self._handle_tiny_button(button_id)
            elif button_id.startswith("cognition"):
                await self._handle_cognition_button(button_id)
            elif button_id.startswith("awareness"):
                await self._handle_awareness_button(button_id)
            elif button_id.startswith("actions"):
                await self._handle_actions_button(button_id)
            elif button_id.startswith("files"):
                await self._handle_files_button(button_id)
        finally:
            event.button.disabled = False

    async def _submit_chat_or_command(self, text: str) -> None:
        self._write("chat_log", f"> {text}")
        if text.startswith("/"):
            self._queue_feedback(f"command:{text}", f"Queued {text}", log_id="actions_log")
            result = await asyncio.to_thread(
                self._run_operator_action,
                f"command:{text}",
                lambda: self._execute_slash_command(text),
                text,
                f"TUI command {text} queued.",
            )
            self._write_json("chat_log", result)
            if result.get("trace"):
                self._write_json("trace_log", result["trace"])
            await self._refresh_notifications()
            await self._refresh_actions()
            await self._refresh_cognition()
            return

        self._queue_feedback("chat_request", "Queued D.A.I.S.E request.", log_id="trace_log")
        self._write("trace_log", f"Working: D.A.I.S.E accepted the request ({self.analysis_mode} mode).")
        if not self._ensure_orchestrator_available():
            message = self.last_error or "D.A.I.S.E backend unavailable."
            self._write("chat_log", f"[D.A.I.S.E unavailable] {message}")
            self._write_json("trace_log", {"status": "error", "error": message})
            return
        request_id = f"tui-{uuid.uuid4().hex[:10]}"
        if not self.current_session_id:
            self.current_session_id = self._get_chat_service().memory_service.create_session_id()
        intent = await asyncio.to_thread(
            self._operator_actions.create_intent,
            action="chat_request",
            session_id=self.current_session_id,
            request_id=request_id,
            target="chat",
            proposed_by="operator",
            human_reason="Request accepted by D.A.I.S.E.",
            ttl_seconds=900.0,
        )
        intent_id = str(intent.get("intent_id"))
        await asyncio.to_thread(
            self._operator_actions.transition,
            intent_id,
            status="executing",
            session_id=self.current_session_id,
            request_id=request_id,
            reason_code="in_progress",
            human_reason="D.A.I.S.E is processing the TUI request.",
        )
        stream_lines: list[str] = []

        def stream_callback(token: str) -> None:
            stream_lines.append(str(token))
            partial = "".join(stream_lines)
            if len(partial) % 48 < max(1, len(token)):
                preview = partial[-120:] if len(partial) > 120 else partial
                self.call_from_thread(self._write, "trace_log", f"Streaming… {preview}")

        chat_payload = await asyncio.to_thread(
            self._get_chat_service().send_message,
            message=text,
            session_id=self.current_session_id,
            request_id=request_id,
            analysis_mode=self.analysis_mode,
            stream_callback=stream_callback,
        )
        if chat_payload.get("status") == "error" and not chat_payload.get("result"):
            error = chat_payload.get("error") or "Chat request failed."
            self._write("chat_log", f"[D.A.I.S.E unavailable] {error}")
            self._write_json("trace_log", {"status": "error", "error": error})
            return
        result = dict(chat_payload.get("result") or {})
        self.current_request_id = str(chat_payload.get("request_id") or result.get("request_id") or self.current_request_id)
        self.current_session_id = str(chat_payload.get("session_id") or result.get("session_id") or self.current_session_id)
        agent_key = str(result.get("output_agent") or result.get("selected_agent") or result.get("agent") or "daise")
        self.active_agent = display_agent_name(agent_key)
        await asyncio.to_thread(
            self._operator_actions.record_result,
            intent_id,
            result={
                "status": "failed" if result.get("error") or chat_payload.get("status") == "error" else "completed",
                "success": not bool(result.get("error")) and chat_payload.get("status") != "error",
                "message": result.get("error") or chat_payload.get("error") or "TUI chat request completed.",
                "agent": result.get("selected_agent") or result.get("agent"),
            },
            session_id=self.current_session_id or None,
            request_id=self.current_request_id or request_id,
        )
        final_response = result.get("response", "No response")
        if stream_lines:
            final_response = "".join(stream_lines) or final_response
        self._write("chat_log", f"[{self.active_agent}] {final_response}")
        if result.get("file_workflow"):
            self._write_json("files_log", result["file_workflow"])
        trace = chat_payload.get("trace") or {}
        status_line = format_chat_status_line(
            result=result,
            selection=chat_payload.get("selection"),
            analysis_mode=str(chat_payload.get("analysis_mode") or self.analysis_mode),
            request_id=self.current_request_id,
        )
        self._write("trace_log", status_line)
        if trace:
            self._write_json("trace_log", trace)
        self._active_work_label = "idle"
        self._update_status()
        await self._refresh_notifications()
        await self._refresh_actions()

    def _execute_slash_command(self, command: str) -> Dict[str, Any]:
        return self._command_registry.execute(command, self._command_context())

    def _desktop_command(self, command: str) -> Dict[str, Any]:
        return self._command_registry.execute(command, self._command_context())

    def _docker_command(self, command: str) -> Dict[str, Any]:
        return self._command_registry.execute(command, self._command_context())

    def _obeos_command(self, command: str) -> Dict[str, Any]:
        return self._command_registry.execute(command, self._command_context())

    def _tiny_command(self, command: str) -> Dict[str, Any]:
        return self._command_registry.execute(command, self._command_context())

    def _file_command(self, command: str) -> Dict[str, Any]:
        return self._command_registry.execute(command, self._command_context())

    async def _handle_notification_button(self, button_id: str) -> None:
        self._queue_feedback(button_id, f"Queued {button_id}.", log_id="actions_log")
        if button_id == "notifications_read_all":
            result = await asyncio.to_thread(
                self._run_operator_action,
                "notifications_read_all",
                self._services.notification_service.mark_all_read,
                "notifications",
                "Mark-all notifications requested.",
            )
            self._write_json("notifications_log", result)
        elif button_id == "notifications_test":
            result = await asyncio.to_thread(
                self._run_operator_action,
                "notifications_test",
                lambda: self._services.notification_service.create(
                    title="Blackfong TUI test",
                    message="Notifications are connected to the TUI.",
                    level="info",
                    category="test",
                    desktop=True,
                ),
                "notifications",
                "Notification test requested.",
            )
            self._write_json("notifications_log", result)
        await self._refresh_notifications()
        await self._refresh_actions()
        await self._refresh_cognition()

    async def _handle_tiny_button(self, button_id: str) -> None:
        mapping = {
            "tiny_status": lambda: tinyprogrammer_companion.get_status(require_stream=True),
            "tiny_launch": lambda: tinyprogrammer_companion.ensure_running(
                require_stream=True,
                adopt_external=True,
                open_browser=False,
                consent={"approved": True, "level": 1},
            ),
            "tiny_native": lambda: tinyprogrammer_companion.ensure_running(
                require_stream=True,
                adopt_external=True,
                open_browser=False,
                consent={"approved": True, "level": 1},
            ),
            "tiny_open": lambda: tinyprogrammer_companion.open(open_browser=True),
            "tiny_live": lambda: tinyprogrammer_companion.open_live(consent={"approved": True, "level": 1}, open_browser=True),
            "tiny_restart": lambda: tinyprogrammer_companion.send_control("restart"),
            "tiny_screen_on": lambda: tinyprogrammer_companion.send_control("screensaver", "on"),
            "tiny_screen_off": lambda: tinyprogrammer_companion.send_control("screensaver", "off"),
            "tiny_recent": lambda: tinyprogrammer_companion.get_recent(limit=12),
            "tiny_bbs": lambda: tinyprogrammer_companion.get_bbs(limit=12),
            "tiny_log_tail": lambda: tinyprogrammer_companion.get_log_tail(lines=40),
            "tiny_rate": lambda: tinyprogrammer_companion.rate_last_creation(5, feedback="Rated from TUI"),
        }
        self._queue_feedback(button_id, f"Queued {button_id}.", log_id="tiny_log")
        result = await asyncio.to_thread(
            self._run_operator_action,
            button_id,
            mapping[button_id],
            button_id,
            f"TinyProgrammer action {button_id} requested.",
        )
        self._write_json("tiny_log", result)
        await self._refresh_notifications()
        await self._refresh_actions()
        await self._refresh_cognition()
        await self._refresh_tiny()

    async def _handle_awareness_button(self, button_id: str) -> None:
        command = {
            "awareness_refresh": "/awareness",
            "awareness_diagnose": "/diagnose",
            "awareness_sensors": "/sensors",
            "awareness_why": "/why what is online?",
        }[button_id]
        self._queue_feedback(f"command:{command}", f"Queued {command}", log_id="actions_log")
        result = await asyncio.to_thread(
            self._run_operator_action,
            f"command:{command}",
            lambda: self._execute_slash_command(command),
            command,
            f"TUI command {command} queued.",
        )
        self._write_json("awareness_log", result)
        if button_id == "awareness_sensors":
            self._sensor_mode = "snapshot"
        await self._refresh_cognition()

    async def _handle_actions_button(self, button_id: str) -> None:
        if button_id == "actions_changed":
            self._queue_feedback("command:/whatchanged", "Queued /whatchanged", log_id="actions_log")
            result = await asyncio.to_thread(
                self._run_operator_action,
                "command:/whatchanged",
                lambda: self._execute_slash_command("/whatchanged"),
                "/whatchanged",
                "What changed requested from the TUI.",
            )
            self._write_json("actions_log", result)
            await self._refresh_cognition()
            return
        await self._refresh_actions()

    async def _handle_cognition_button(self, button_id: str) -> None:
        command = {
            "cognition_live_brain": "/cognition",
            "cognition_memory": "/cognition memory",
            "cognition_tasks": "/cognition tasks",
            "cognition_sensors": "/cognition sensors",
            "cognition_faults": "/cognition faults",
            "cognition_forge": "/cognition forge",
        }[button_id]
        self._queue_feedback(f"command:{command}", f"Queued {command}", log_id="actions_log")
        result = await asyncio.to_thread(
            self._run_operator_action,
            f"command:{command}",
            lambda: self._execute_slash_command(command),
            command,
            f"TUI command {command} queued.",
        )
        if result.get("cognition"):
            self._write_cognition_snapshot(result["cognition"])
        else:
            self._write_json("cognition_log", result)
        await self._refresh_actions()

    async def _handle_files_button(self, button_id: str) -> None:
        path = self.query_one("#files_path_input", Input).value.strip()
        if button_id == "files_policy":
            command = "/files policy"
        elif button_id == "files_proposals":
            command = "/files proposals"
        elif button_id == "files_roots":
            command = "/files roots"
        elif button_id == "files_approve_root":
            command = f"/files approve-root {path}"
        elif button_id == "files_map":
            command = f"/files map {path}"
        elif button_id == "files_scan":
            command = f"/files scan {path or '.'}"
        else:
            command = f"/files read {path}"
        self._queue_feedback(button_id, f"Queued {button_id}.", log_id="files_log")
        wrapped = await asyncio.to_thread(
            self._run_operator_action,
            button_id,
            lambda: self._file_command(command),
            path or button_id,
            f"File action {button_id} requested.",
        )
        self._write_json("files_log", wrapped)
        await self._refresh_actions()
        await self._refresh_cognition()

    def _get_awareness_service(self) -> SystemAwarenessService:
        if self._awareness_service is None:
            self._awareness_service = self._services.awareness_service
        return self._awareness_service

    def _get_operator_summary_service(self) -> OperatorSummaryService:
        if self._operator_summary_service is None:
            self._operator_summary_service = self._services.operator_summary
        return self._operator_summary_service

    def _get_cognition_service(self) -> CognitionTelemetryService:
        if self._cognition_service is None:
            self._cognition_service = self._services.cognition_service
        return self._cognition_service

    def _backend_bootstrap_state(self) -> Dict[str, Any]:
        return {
            "status": "ready" if self._orchestrator_loaded else "idle",
            "started_at": None,
            "completed_at": None,
            "error": None,
        }

    def _build_cognition_snapshot(self, *, layer: Optional[str] = None) -> Dict[str, Any]:
        if self.current_session_id:
            self._cognition_focus["selected_session"] = self.current_session_id
        if self.current_request_id:
            self._cognition_focus["selected_request"] = self.current_request_id
        if layer:
            self._cognition_focus["layer"] = layer
        return self._get_cognition_service().build_snapshot(
            session_id=self.current_session_id or None,
            request_id=self.current_request_id or None,
            layer=str(self._cognition_focus.get("layer") or "live_brain"),
            focus=self._cognition_focus,
            request_states={},
            request_threads={},
            backend_bootstrap=self._backend_bootstrap_state(),
            sensor_mode=self._sensor_mode,
            voice_status=self._safe_voice_status(),
        )

    def _safe_voice_status(self) -> Dict[str, Any]:
        if not self._orchestrator_loaded:
            return {"active": False, "message": "Voice loop not initialized"}
        try:
            return self.orchestrator.get_voice_loop_status()
        except Exception as exc:
            logger.exception("Voice status unavailable in TUI")
            return {"active": False, "message": f"Voice status unavailable: {exc}"}

    async def _refresh_notifications(self) -> None:
        inbox = await asyncio.to_thread(self._services.notification_service.list_notifications, limit=30)
        log = self.query_one("#notifications_log", LogWidget)
        log.clear()
        log.write(f"Unread: {inbox.get('unread_count', 0)}")
        for item in inbox.get("notifications", []):
            marker = "" if item.get("read") else "* "
            log.write(f"{marker}{item.get('level', 'info').upper()} {item.get('title')}: {item.get('message')}")

    async def _refresh_actions(self) -> None:
        actions = await asyncio.to_thread(self._operator_actions.list_actions, limit=20, session_id=self.current_session_id or None)
        self._pending_actions_count = int(actions.get("pending_actions", 0))
        log = self.query_one("#actions_log", LogWidget)
        log.clear()
        log.write(f"Pending: {self._pending_actions_count}")
        for item in actions.get("actions", []):
            log.write(
                f"{item.get('status', 'unknown').upper()} {item.get('action')} | "
                f"owner={item.get('owned_by')} | by={item.get('proposed_by')} | "
                f"{item.get('text') or item.get('human_reason', '')}"
            )
        self._update_status()

    async def _refresh_tiny(self) -> None:
        status_payload = await asyncio.to_thread(tinyprogrammer_companion.get_status, probe_live=True, require_stream=True)
        self._latest_tiny_status = status_payload
        log_tail = await asyncio.to_thread(tinyprogrammer_companion.get_log_tail, lines=12)
        try:
            panel = self.query_one("#tiny_status_panel", Static)
        except NoMatches:
            return
        process = status_payload.get("process", {})
        stream_state = "live" if status_payload.get("stream_available") else status_payload.get("stream_reason", "unavailable")
        panel.update(
            "TINYPROGRAMMER // STATUS\n"
            f"Ownership {process.get('ownership', 'offline')} | Mode {process.get('management_state', status_payload.get('mode', 'offline'))} | Stream {stream_state}\n"
            f"Program {status_payload.get('current_program_type') or 'idle'} | Mood {status_payload.get('mood') or 'steady'} | BBS {status_payload.get('bbs_reason') or 'unknown'}\n"
            f"Native {status_payload.get('native_window_state') or 'unknown'} | Autostart {status_payload.get('autostart_enabled')} | PID {process.get('pid') or 'none'}\n"
            f"Log {log_tail.get('log_path')} | Lines {log_tail.get('line_count', 0)} | Shutdown {status_payload.get('shutdown_policy') or 'ask'}"
        )
        try:
            log = self.query_one("#tiny_log", LogWidget)
            log.clear()
            log.write(f"Native window: {status_payload.get('native_window_human_reason') or 'unknown'}")
            log.write(f"Live view: {status_payload.get('stream_human_reason') or 'unavailable'}")
            if log_tail.get("success") and log_tail.get("content"):
                log.write("")
                log.write(log_tail["content"])
            elif log_tail.get("human_reason"):
                log.write("")
                log.write(log_tail["human_reason"])
        except NoMatches:
            pass
        self._update_status()

    async def _refresh_cognition(self) -> None:
        snapshot = await asyncio.to_thread(self._build_cognition_snapshot)
        self._write_cognition_snapshot(snapshot)

    def _refresh_all(self) -> None:
        self._update_status()
        self.run_worker(self._refresh_notifications(), exclusive=False)
        self.run_worker(self._refresh_actions(), exclusive=False)
        self.run_worker(self._refresh_cognition(), exclusive=False)
        self.run_worker(self._refresh_tiny(), exclusive=False)

    def _update_status(self) -> None:
        voice = self._safe_voice_status()
        self.voice_state = "LISTENING" if voice.get("active") else "IDLE"
        try:
            status = self.query_one("#status_panel", Static)
        except NoMatches:
            return
        cognition_layer = str(self._cognition_focus.get("layer") or "live_brain").replace("_", " ").upper()
        tiny_process = self._latest_tiny_status.get("process", {})
        tiny_state = tiny_process.get("management_state") or self._latest_tiny_status.get("mode") or "offline"
        tiny_owner = tiny_process.get("ownership") or "offline"
        status.update(
            f"O.B.E.O.S. // Blackcomputer // D.A.I.S.E. {self.heartbeat_state}\n"
            f"System {self.system_state} | Voice {self.voice_state} | Active {self.active_agent} | Mode {self.analysis_mode.upper()} | Queue {self._pending_actions_count}\n"
            f"Tiny {tiny_owner}/{tiny_state} | Work {self._active_work_label}\n"
            f"Request {self.current_request_id or 'none'} | Session {self.current_session_id or 'none'} | Cognition {cognition_layer}"
        )

    def action_cycle_analysis_mode(self) -> None:
        modes = ("fast", "standard", "deep")
        try:
            index = modes.index(str(self.analysis_mode).lower())
        except ValueError:
            index = 0
        self.analysis_mode = modes[(index + 1) % len(modes)]
        self._write("trace_log", f"Response mode set to {self.analysis_mode.upper()}.")
        self._update_status()

    async def action_toggle_voice(self) -> None:
        if self.voice_state == "LISTENING":
            result = await asyncio.to_thread(self.orchestrator.stop_voice_loop)
            self.voice_state = "IDLE" if result else "LISTENING"
            self._write("trace_log", "Voice loop stopped." if result else "Voice loop did not stop.")
        else:
            result = await asyncio.to_thread(self.orchestrator.start_voice_loop)
            self.voice_state = "LISTENING" if result else "FAILED"
            self._write("trace_log", "Voice loop started." if result else "Voice loop failed to start.")
        self._update_status()

    async def action_refresh(self) -> None:
        self._refresh_all()

    def _write(self, log_id: str, text: str) -> None:
        try:
            self.query_one(f"#{log_id}", LogWidget).write(text)
        except NoMatches:
            return

    def _queue_feedback(self, action: str, message: str, *, log_id: str = "actions_log") -> None:
        self._active_work_label = action
        self._write(log_id, f"QUEUED {message}")
        self._update_status()

    def _write_json(self, log_id: str, payload: Any) -> None:
        self._write(log_id, json.dumps(payload, indent=2, default=str))

    def _write_cognition_snapshot(self, snapshot: Dict[str, Any]) -> None:
        log = self.query_one("#cognition_log", LogWidget)
        log.clear()
        log.write(str(snapshot.get("ascii_summary") or "No cognition snapshot available."))
        summaries = snapshot.get("summaries") or {}
        overlays = snapshot.get("overlays") or {}
        focus = snapshot.get("focus") or {}
        source_snapshots = snapshot.get("source_snapshots") or {}
        actions = source_snapshots.get("actions") or {}
        tiny = source_snapshots.get("tinyprogrammer") or {}
        log.write("")
        log.write(f"Layer: {snapshot.get('active_layer')}")
        log.write(f"Focus: {summaries.get('focus_label', 'None')}")
        log.write(f"Selected node: {focus.get('selected_node') or 'none'}")
        log.write(
            "Backend: "
            f"{(overlays.get('backend') or {}).get('provider') or 'unknown'} / "
            f"{(overlays.get('backend') or {}).get('model') or 'unknown'}"
        )
        log.write(
            "Queue/Actions: "
            f"{(overlays.get('queue') or {}).get('pending_requests', 0)} requests | "
            f"{actions.get('pending_actions', 0)} operator actions"
        )
        log.write(
            "TinyProgrammer: "
            f"{tiny.get('state') or tiny.get('mode') or 'offline'} / "
            f"{tiny.get('mood') or 'steady'} / "
            f"{tiny.get('current_program_type') or 'idle'}"
        )
        if summaries.get("latest_event"):
            log.write(f"Latest event: {summaries.get('latest_event')}")
        for fault in (snapshot.get("faults") or [])[:5]:
            log.write(f"FAULT [{fault.get('severity', 'warning').upper()}] {fault.get('message')}")

    def _json_safe_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self._json_safe_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe_value(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _run_operator_action(self, action: str, handler, target: str, human_reason: str) -> Dict[str, Any]:
        intent = self._operator_actions.create_intent(
            action=action,
            session_id=self.current_session_id or None,
            request_id=self.current_request_id or None,
            target=target,
            proposed_by="operator",
            human_reason=human_reason,
        )
        intent_id = str(intent.get("intent_id"))
        self._operator_actions.transition(
            intent_id,
            status="executing",
            session_id=self.current_session_id or None,
            request_id=self.current_request_id or None,
            reason_code="in_progress",
            human_reason=human_reason,
        )
        try:
            result = handler()
        except Exception as exc:
            logger.exception("TUI operator action failed: %s", action)
            result = {"success": False, "error": str(exc), "status": "failed"}
        result = self._json_safe_value(result)
        action_state = self._operator_actions.record_result(
            intent_id,
            result=result,
            session_id=self.current_session_id or None,
            request_id=self.current_request_id or None,
        )
        merged = dict(result)
        merged.update(
            {
                "intent_id": action_state.get("intent_id"),
                "owned_by": action_state.get("owned_by"),
                "governed_by": action_state.get("governed_by"),
                "proposed_by": action_state.get("proposed_by"),
                "reason_code": action_state.get("reason_code"),
                "human_reason": action_state.get("human_reason"),
                "expires_at": action_state.get("expires_at"),
                "action_status": action_state.get("status"),
            }
        )
        self._active_work_label = str(action_state.get("status") or "idle")
        return merged


def run_tui() -> None:
    print("Starting Blackfong TUI...")
    app = BlackcomputerTUIApp()
    app.run()
    tinyprogrammer_companion.handle_managed_shutdown(
        interactive=bool(sys.stdin and sys.stdin.isatty()),
        prompt=lambda: input("Stop managed TinyProgrammer before Blackfong exits? [y/N]: ").strip().lower() in {"y", "yes"},
    )
