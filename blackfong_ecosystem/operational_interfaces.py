"""Browser HUD, session explorer, and admin controls for O.B.E.O.S."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import webbrowser
import base64
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from flask import Flask, Response, jsonify, render_template_string, request, send_from_directory, stream_with_context

try:
    from flask_socketio import SocketIO, emit

    HAS_SOCKETIO = True
except ImportError:
    HAS_SOCKETIO = False

    class SocketIO:  # pragma: no cover - fallback when flask-socketio is absent
        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            pass

        def run(self, app, host: str, port: int, debug: bool = False):
            app.run(host=host, port=port, debug=debug)

    def emit(*args, **kwargs):
        return None

from blackfong_ecosystem.cognition import (
    DEFAULT_COGNITION_FOCUS,
    CognitionEventRouter,
    CognitionTelemetryService,
    THOUGHTGLASS_LAYERS,
    apply_gesture,
    normalize_gesture,
)
from blackfong_ecosystem.config import hud_settings, paths
from blackfong_ecosystem.conversation_memory import conversation_memory_service
from blackfong_ecosystem.decision_quality_control import decision_quality_controller
from blackfong_ecosystem.device_registry import (
    LEGION_SESSION_COOKIE,
    DeviceRegistryService,
    LegionAuthService,
)
from blackfong_ecosystem.file_scope_service import FileScopeService
from blackfong_ecosystem.file_workflow import FileWorkflowService
from blackfong_ecosystem.identity_semantics import identity_registry
from blackfong_ecosystem.memory_lifecycle_management import memory_lifecycle_manager
from blackfong_ecosystem.notification_service import NotificationService
from blackfong_ecosystem.observability import LogLevel, log_action
from blackfong_ecosystem.orchestrator_loader import OrchestratorUnavailable, load_unified_orchestrator
from blackfong_ecosystem.operator_actions import OperatorActionService
from blackfong_ecosystem.operator_summary import OperatorSummaryService
from blackfong_ecosystem.service_registry import get_ui_services
from blackfong_ecosystem.storage import EventStore
from blackfong_ecosystem.events import Event, validate_event_type
from blackfong_ecosystem.system_awareness import SystemAwarenessService
from blackfong_ecosystem.tinyprogrammer_companion import tinyprogrammer_companion
from blackfong_ecosystem.ui_commands import CommandContext, get_command_registry

logger = logging.getLogger(__name__)

InputModality = None  # type: ignore[assignment]
unified_orchestrator = None  # type: ignore[assignment]
HAS_ORCHESTRATOR = True
HUD_ASSET_ROOT = Path(__file__).resolve().parent / "ui" / "hud"
LEGION_ASSET_ROOT = Path(__file__).resolve().parent / "ui" / "legion"


def _load_unified_orchestrator() -> bool:
    """Lazily import the unified orchestrator so the HUD can boot quickly."""
    global InputModality, unified_orchestrator, HAS_ORCHESTRATOR
    if unified_orchestrator is not None and InputModality is not None:
        return True
    try:
        loaded = load_unified_orchestrator(required=True, instance=True)
        InputModality = loaded.input_modality  # type: ignore[assignment]
        unified_orchestrator = loaded.orchestrator  # type: ignore[assignment]
        HAS_ORCHESTRATOR = True
        return True
    except OrchestratorUnavailable as exc:
        InputModality = None  # type: ignore[assignment]
        unified_orchestrator = None  # type: ignore[assignment]
        HAS_ORCHESTRATOR = False
        logger.exception("D.A.I.S.E orchestrator unavailable for HUD")
        log_action(LogLevel.ERROR, "dashboard", "orchestrator_unavailable", error_message=str(exc))
        return False


COMMAND_CATALOG = [
    {"command": "/status", "description": "System health and voice status"},
    {"command": "/sessions", "description": "List persisted sessions"},
    {"command": "/memory", "description": "Recall pinned memory and summaries"},
    {"command": "/pin", "description": "Promote the latest session reply to durable memory"},
    {"command": "/trace", "description": "Show the current request operator trace"},
    {"command": "/whatchanged", "description": "Summarize state changes since the last operator checkpoint"},
    {"command": "/cognition", "description": "Show the current Thoughtglass cognition snapshot"},
    {"command": "/cognition memory", "description": "Focus Thoughtglass on the Memory layer"},
    {"command": "/cognition tasks", "description": "Focus Thoughtglass on the Tasks layer"},
    {"command": "/cognition sensors", "description": "Focus Thoughtglass on the Sensors layer"},
    {"command": "/cognition faults", "description": "Focus Thoughtglass on the Faults layer"},
    {"command": "/cognition forge", "description": "Focus Thoughtglass on the Forge layer"},
    {"command": "/awareness", "description": "Show canonical D.A.I.S.E awareness snapshot"},
    {"command": "/performance", "description": "Show latency, backend, memory, and queue pressure"},
    {"command": "/backend", "description": "Show local model backend health"},
    {"command": "/environment", "description": "Show local host and service readiness"},
    {"command": "/sensors", "description": "Show speak/hear/see capability without active capture"},
    {"command": "/sensors snapshot", "description": "Refresh safe sensor capability observations without live capture"},
    {"command": "/sensors live start", "description": "Request explicit live perception mode with consent"},
    {"command": "/sensors live stop", "description": "Stop live perception mode"},
    {"command": "/diagnose", "description": "Summarize bottlenecks, risks, and recommendations"},
    {"command": "/capabilities", "description": "Show the canonical capability registry"},
    {"command": "/limits", "description": "Show policy limits and safety gates"},
    {"command": "/identity <name>", "description": "Resolve a legacy or canonical identity name"},
    {"command": "/desktop status", "description": "Show the Kubuntu/Plasma desktop session contract and current target state"},
    {"command": "/desktop start", "description": "Request a transition to graphical.target on a Linux/systemd OBEOS install"},
    {"command": "/desktop stop", "description": "Request a transition back to multi-user.target and the TUI home"},
    {"command": "/desktop home", "description": "Return from Plasma to the OBEOS TUI home without surrendering session authority"},
    {"command": "/desktop workspace", "description": "Launch the managed Blackfong Workspace shell inside the current desktop session"},
    {"command": "/home", "description": "Return to the OBEOS TUI home and retain session authority"},
    {"command": "/docker status", "description": "Show the bounded Blackfong Docker service stack status"},
    {"command": "/docker up", "description": "Start the bounded Docker-backed Thoughtglass HUD/API worker stack"},
    {"command": "/docker down", "description": "Stop the bounded Docker-backed Thoughtglass HUD/API worker stack"},
    {"command": "/docker logs", "description": "Show Docker-backed Thoughtglass HUD/API worker stack logs"},
    {"command": "/obeos installer-status", "description": "Show whether the external installer is shipping truth or migration-only input"},
    {"command": "/obeos manifest", "description": "Show the Kubuntu-first OBEOS remaster manifest"},
    {"command": "/obeos shell-compat", "description": "Show the Ubuntu-compatible shell command contract"},
    {"command": "/obeos vm-checklist", "description": "Show the canonical Kubuntu VM-first install and validation checklist"},
    {"command": "/obeos vm-acceptance-snapshot", "description": "Capture the current Kubuntu VM acceptance snapshot or blocked prerequisites"},
    {"command": "/obeos activate-home", "description": "Activate TUI-home boot after the safe Plasma bootstrap checks pass"},
    {"command": "/summary", "description": "Show the shared operator summary across session, Tiny, notifications, and file scopes"},
    {"command": "/files policy", "description": "Show safe file workflow workspace policy"},
    {"command": "/files proposals", "description": "List governed file proposals and their current states"},
    {"command": "/files roots", "description": "List governed roots, approved read-map scopes, and candidate machine roots"},
    {"command": "/files approve-root <path>", "description": "Approve a machine path for D.A.I.S.E read-map access outside governed roots"},
    {"command": "/files map <path>", "description": "Browse an approved machine path as a bounded tree map"},
    {"command": "/files read <path>", "description": "Read text content from an approved machine path or governed root"},
    {"command": "/why", "description": "Explain the latest decision or a requested topic with provenance"},
    {"command": "/voice-start", "description": "Start backend voice loop"},
    {"command": "/voice-stop", "description": "Stop backend voice loop"},
    {"command": "/notifications", "description": "Show notification inbox"},
    {"command": "/notifications read-all", "description": "Mark all notifications as read"},
    {"command": "/tiny status", "description": "Show TinyProgrammer companion status"},
    {"command": "/tiny launch", "description": "Ensure TinyProgrammer is running with stream available"},
    {"command": "/tiny native", "description": "Ensure TinyProgrammer native companion window is available"},
    {"command": "/tiny open", "description": "Open TinyProgrammer browser dashboard"},
    {"command": "/tiny recent", "description": "Show recent TinyProgrammer creations"},
    {"command": "/tiny bbs", "description": "Show TinyProgrammer BBS activity or reason-coded availability"},
    {"command": "/tiny live", "description": "Open TinyProgrammer live stream"},
    {"command": "/tiny log", "description": "Show the latest managed TinyProgrammer log tail"},
    {"command": "/tiny brief <text>", "description": "Send a creative brief to TinyProgrammer"},
    {"command": "/tiny mood <value>", "description": "Set TinyProgrammer preferred mood"},
    {"command": "/tiny theme <value>", "description": "Set TinyProgrammer creative theme"},
    {"command": "/tiny model <value>", "description": "Set TinyProgrammer model override"},
    {"command": "/tiny restart", "description": "Restart TinyProgrammer loop"},
    {"command": "/tiny screensaver <on|off>", "description": "Toggle TinyProgrammer screensaver"},
    {"command": "/tiny nudge <text>", "description": "Apply a short creative brief to TinyProgrammer"},
    {"command": "/tiny rate <1-5> [feedback]", "description": "Rate the latest TinyProgrammer creation"},
]


@dataclass
class DashboardMetrics:
    total_requests: int = 0
    active_sessions: int = 0
    avg_response_time: float = 0.0
    error_rate: float = 0.0
    memory_usage_mb: float = 0.0
    decision_quality_score: float = 0.0
    agent_performance: Dict[str, float] = field(default_factory=dict)
    system_health: str = "healthy"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "active_sessions": self.active_sessions,
            "avg_response_time": self.avg_response_time,
            "error_rate": self.error_rate,
            "memory_usage_mb": self.memory_usage_mb,
            "decision_quality_score": self.decision_quality_score,
            "agent_performance": self.agent_performance,
            "system_health": self.system_health,
            "timestamp": time.time(),
        }


@dataclass
class SessionInfo:
    session_id: str
    user_id: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 3600)
    total_requests: int = 0
    total_errors: int = 0
    agents_used: Set[str] = field(default_factory=set)
    current_workflow: Optional[str] = None
    status: str = "active"
    preview: str = ""
    event_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "start_time": self.start_time,
            "last_activity": self.last_activity,
            "expires_at": self.expires_at,
            "duration": max(0.0, time.time() - self.start_time),
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "agents_used": sorted(self.agents_used),
            "current_workflow": self.current_workflow,
            "status": self.status,
            "preview": self.preview,
            "event_count": self.event_count,
        }


class WebDashboard:
    """Secondary Thoughtglass HUD cockpit backed by persisted events and shared memory."""

    def __init__(self, host: str = "localhost", port: int = 8080, *, memory_service=None, orchestrator_instance=None):
        self.host = host
        self.port = port
        self.services = get_ui_services(memory_service=memory_service, orchestrator_instance=orchestrator_instance)
        self.command_registry = get_command_registry()
        self.memory_service = self.services.memory_service
        self.orchestrator = orchestrator_instance
        self.tinyprogrammer = self.services.tinyprogrammer
        self.event_store = self.services.event_store or getattr(self.memory_service, "event_store", EventStore())
        self.device_registry = DeviceRegistryService(event_store=self.event_store)
        self.legion_auth = LegionAuthService(self.device_registry)
        self.notification_service = self.services.notification_service
        self.operator_actions = self.services.operator_actions
        self.tinyprogrammer.memory_service = self.memory_service
        self.tinyprogrammer.event_store = self.event_store
        self.tinyprogrammer.notification_service = self.notification_service
        shared_file_workflow = getattr(orchestrator_instance, "file_workflow", None) if orchestrator_instance is not None else None
        shared_file_scope = getattr(orchestrator_instance, "file_scope_service", None) if orchestrator_instance is not None else None
        self.file_workflow = shared_file_workflow or self.services.file_workflow
        self.file_scope_service = shared_file_scope or self.services.file_scope_service
        self.gps_service = self.services.gps_service
        self.sensor_mode = "off"
        self.awareness_service = self.services.awareness_service
        self.operator_summary = self.services.operator_summary
        self.integration_registry = self.services.integration_registry
        self.redamon_workflow = self.services.redamon_workflow
        self.operator_chat = self.services.operator_chat
        self.memory_chamber = self.services.memory_chamber
        self.cognition_focus: Dict[str, Any] = dict(DEFAULT_COGNITION_FOCUS)
        self.cognition_service = self.services.cognition_service
        self.app = Flask(__name__)
        self.socketio = SocketIO(self.app, cors_allowed_origins="*") if HAS_SOCKETIO else None
        self.cognition_router = CognitionEventRouter(
            socketio=self.socketio,
            telemetry_service=self.cognition_service,
        )
        self.metrics = DashboardMetrics()
        self.sessions: Dict[str, SessionInfo] = {}
        self.recent_events: List[Dict[str, Any]] = []
        self.alerts: List[Dict[str, Any]] = []
        self.request_states: Dict[str, Dict[str, Any]] = {}
        self.request_threads: Dict[str, threading.Thread] = {}
        self.live_stream_subscribers: List[Any] = []
        self.live_stream_lock = threading.Lock()
        self.connected_dashboard_clients = 0
        self.backend_bootstrap: Dict[str, Any] = {
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
        self.backend_bootstrap_thread: Optional[threading.Thread] = None
        self.knowledge_sync_lock = threading.Lock()
        self.knowledge_sync_thread: Optional[threading.Thread] = None
        self.knowledge_sync_status: Dict[str, Any] = {
            "status": "idle",
            "started_at": None,
            "completed_at": None,
            "error": None,
            "result": None,
        }
        self.update_active = False
        self.update_thread: Optional[threading.Thread] = None
        self._tiny_autostart_thread: Optional[threading.Thread] = None
        self._cognition_snapshot_cache: Optional[tuple[float, str, Dict[str, Any]]] = None
        self._cognition_snapshot_cache_ttl_s = 2.5
        self._metrics_cache_updated_at = 0.0
        self._metrics_cache_ttl_s = 2.0
        self._blackcomputer_device_sync_lock = threading.Lock()
        self._blackcomputer_device_sync_thread: Optional[threading.Thread] = None
        self._blackcomputer_device_sync_updated_at = 0.0
        self._blackcomputer_device_sync_ttl_s = 15.0
        self.session_explorer: Optional["SessionExplorer"] = None
        self.admin_controls: Optional["AdminControls"] = None
        self._seed_blackcomputer_device_fast()
        self._setup_routes()
        if HAS_SOCKETIO:
            self._setup_socket_events()

    def _ensure_orchestrator(self) -> bool:
        if self.orchestrator is not None:
            return True
        if not _load_unified_orchestrator():
            return False
        self.orchestrator = unified_orchestrator
        shared_file_workflow = getattr(self.orchestrator, "file_workflow", None)
        if shared_file_workflow is not None:
            self.file_workflow = shared_file_workflow
        return self.orchestrator is not None

    def _sync_services_from_dashboard_state(self) -> None:
        self.services.memory_service = self.memory_service
        self.services.event_store = self.event_store
        self.services.notification_service = self.notification_service
        self.services.operator_actions = self.operator_actions
        self.services.file_workflow = self.file_workflow
        self.services.file_scope_service = self.file_scope_service
        self.services.gps_service = self.gps_service
        self.services.awareness_service = self.awareness_service
        self.services.operator_summary = self.operator_summary
        self.services.cognition_service = self.cognition_service
        self.services.tinyprogrammer = self.tinyprogrammer
        self.services.integration_registry = self.integration_registry
        self.services.redamon_workflow = self.redamon_workflow
        self.services.operator_chat = self.operator_chat

    def _command_context(self, *, session_id: Optional[str], payload: Optional[Dict[str, Any]]) -> CommandContext:
        payload = payload or {}
        self._sync_services_from_dashboard_state()
        return CommandContext(
            services=self.services,
            source="hud",
            session_id=session_id,
            request_id=payload.get("request_id"),
            payload=payload,
            orchestrator=self.orchestrator,
            ensure_orchestrator=self._ensure_orchestrator,
            voice_status_provider=self._safe_voice_status,
            metrics_provider=self._metrics_snapshot,
            awareness_snapshot_provider=lambda emit=False: self._build_awareness_snapshot(emit=emit),
            cognition_snapshot_provider=lambda layer=None: self._build_cognition_snapshot(
                session_id=session_id or self.cognition_focus.get("selected_session"),
                request_id=payload.get("request_id") or self.cognition_focus.get("selected_request"),
                layer=layer or self.cognition_focus.get("layer", "live_brain"),
            ),
            set_cognition_layer=lambda layer: self._sync_cognition_focus(
                session_id=session_id,
                request_id=payload.get("request_id"),
                layer=layer,
                mode="thoughtglass",
            ),
            sensor_mode_setter=self._set_sensor_mode,
        )

    def _set_sensor_mode(self, mode: str) -> Dict[str, Any]:
        self.sensor_mode = str(mode or "off")
        return {"sensor_mode": self.sensor_mode}

    def _metrics_snapshot(self) -> Dict[str, Any]:
        self.update_metrics()
        return self.metrics.to_dict()

    def _start_backend_warmup(self) -> None:
        if self.backend_bootstrap["status"] in {"warming", "ready"}:
            return
        self.backend_bootstrap.update(
            {
                "status": "warming",
                "started_at": time.time(),
                "completed_at": None,
                "error": None,
            }
        )
        self.backend_bootstrap_thread = threading.Thread(target=self._warm_backend_services, daemon=True)
        self.backend_bootstrap_thread.start()

    def _warm_backend_services(self) -> None:
        try:
            from .backend_warmup import warm_model_chain

            warm_model_chain()
            ready = self._ensure_orchestrator()
            self.backend_bootstrap.update(
                {
                    "status": "ready" if ready else "error",
                    "completed_at": time.time(),
                    "error": None if ready else "Unified orchestrator is unavailable",
                }
            )
        except Exception as exc:  # pragma: no cover - defensive startup path
            logger.exception("HUD backend warmup failed")
            self.backend_bootstrap.update(
                {
                    "status": "error",
                    "completed_at": time.time(),
                    "error": str(exc),
                }
            )

    def _safe_voice_status(self) -> Dict[str, Any]:
        if self.orchestrator is None:
            return {"active": False, "message": "Voice loop not initialized"}
        try:
            return self.orchestrator.get_voice_loop_status()
        except Exception as exc:
            logger.exception("HUD voice status unavailable")
            return {"active": False, "message": f"Voice status unavailable: {exc}"}

    def _build_awareness_snapshot(self, *, emit: bool = False) -> Dict[str, Any]:
        return self.awareness_service.build_snapshot(
            request_states=self.request_states,
            request_threads=self.request_threads,
            backend_bootstrap=self.backend_bootstrap,
            voice_status=self._safe_voice_status(),
            sensor_mode=self.sensor_mode,
            emit=emit,
        )

    def _build_cognition_snapshot(
        self,
        *,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        layer: Optional[str] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        resolved_layer = layer or self.cognition_focus.get("layer", "live_brain")
        cache_key = f"{session_id or ''}:{request_id or ''}:{resolved_layer}"
        now = time.time()
        active_requests = any(
            str(state.get("status") or "") not in {"complete", "error", "failed"}
            for state in self.request_states.values()
        )
        if use_cache and not active_requests and self._cognition_snapshot_cache is not None:
            cached_at, cached_key, cached_snapshot = self._cognition_snapshot_cache
            if cached_key == cache_key and (now - cached_at) < self._cognition_snapshot_cache_ttl_s:
                return dict(cached_snapshot)
        snapshot = self._json_safe_value(
            self.cognition_service.build_snapshot(
                session_id=session_id,
                request_id=request_id,
                layer=resolved_layer,
                focus=self.cognition_focus,
                request_states=self.request_states,
                request_threads=self.request_threads,
                backend_bootstrap=self.backend_bootstrap,
                sensor_mode=self.sensor_mode,
                voice_status=self._safe_voice_status(),
            )
        )
        if use_cache and not active_requests:
            self._cognition_snapshot_cache = (now, cache_key, snapshot)
        return snapshot

    def _has_live_consumers(self) -> bool:
        return self.services.event_bus.has_subscribers() or self.connected_dashboard_clients > 0

    def _emit_cognition_delta(
        self,
        *,
        reason: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        layer: Optional[str] = None,
    ) -> None:
        if not self._has_live_consumers():
            return
        delta = self.cognition_service.build_delta(
            reason=reason,
            session_id=session_id,
            request_id=request_id,
            layer=layer or self.cognition_focus.get("layer", "live_brain"),
            focus=self.cognition_focus,
            request_states=self.request_states,
            request_threads=self.request_threads,
            backend_bootstrap=self.backend_bootstrap,
            sensor_mode=self.sensor_mode,
            voice_status=self._safe_voice_status(),
        )
        if HAS_SOCKETIO and self.socketio:
            self.cognition_router.emit_delta(delta)
        self._publish_live_event("cognition_delta", delta)

    def _emit_cognition_fault(
        self,
        *,
        code: str,
        message: str,
        severity: str = "warning",
        source: str = "thoughtglass_projection",
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._has_live_consumers():
            return
        fault = self.cognition_service.build_fault(
            code=code,
            message=message,
            severity=severity,
            source=source,
            session_id=session_id,
            request_id=request_id,
            meta=meta,
        )
        if HAS_SOCKETIO and self.socketio:
            self.cognition_router.emit_fault(fault)
        self._publish_live_event("cognition_fault", fault)

    def _sync_cognition_focus(
        self,
        *,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        selected_node: Optional[str] = None,
        layer: Optional[str] = None,
        mode: Optional[str] = None,
        opened_module: Optional[str] = None,
        emit_focus_event: bool = False,
    ) -> Dict[str, Any]:
        allowed_layers = {entry["id"] for entry in THOUGHTGLASS_LAYERS}
        focus = dict(self.cognition_focus)
        if session_id is not None:
            focus["selected_session"] = session_id
        if request_id is not None:
            focus["selected_request"] = request_id
        if selected_node is not None:
            focus["selected_node"] = selected_node
        if opened_module is not None:
            focus["opened_module"] = opened_module
        if layer:
            normalized_layer = str(layer).strip().lower().replace(" ", "_")
            focus["layer"] = normalized_layer if normalized_layer in allowed_layers else focus.get("layer", "live_brain")
        if mode:
            focus["mode"] = str(mode)
        focus["updated_at"] = time.time()
        self.cognition_focus = focus
        if emit_focus_event and self._has_live_consumers():
            if HAS_SOCKETIO and self.socketio:
                self.cognition_router.emit_focus(dict(self.cognition_focus))
            self._publish_live_event("cognition_focus", dict(self.cognition_focus))
        return dict(self.cognition_focus)

    def _load_hud_asset_text(self, filename: str) -> Optional[str]:
        asset_path = HUD_ASSET_ROOT / filename
        if not asset_path.exists():
            return None
        return asset_path.read_text(encoding="utf-8")

    def _load_legion_asset_text(self, filename: str) -> Optional[str]:
        asset_path = LEGION_ASSET_ROOT / filename
        if not asset_path.exists():
            return None
        return asset_path.read_text(encoding="utf-8")

    def _legion_session(self) -> Optional[Dict[str, Any]]:
        session_id = request.cookies.get(LEGION_SESSION_COOKIE) or request.headers.get("X-Legion-Session")
        return self.legion_auth.validate_session(session_id)

    def _legion_unauthorized(self):
        return jsonify({"status": "unauthorized", "reason_code": "legion_pairing_required"}), 401

    def _require_legion_session(self) -> tuple[Optional[Dict[str, Any]], Optional[Any]]:
        client = self._legion_session()
        if client is None:
            return None, self._legion_unauthorized()
        return client, None

    @staticmethod
    def _normalize_chat_transport(value: Optional[str]) -> str:
        normalized = str(value or "").strip().lower().replace("-", "_")
        aliases = {
            "browser": "hud",
            "dashboard": "hud",
            "mobile": "legion",
            "legion_pwa": "legion",
            "thoughtglass": "hud",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"hud", "desktop", "legion", "telegram", "frame", "operator_chat", "tui"}:
            return normalized
        return "hud"

    @staticmethod
    def _shared_surface_for_transport(transport: str) -> str:
        if transport in {"desktop", "legion", "tui"}:
            return transport
        return "hud"

    def _resolve_chat_session(self, payload: Dict[str, Any]) -> tuple[str, str, str]:
        from .operator_session import get_shared_session_id, link_surface

        transport = self._normalize_chat_transport(
            payload.get("transport") or payload.get("input_origin") or payload.get("surface")
        )
        surface = self._shared_surface_for_transport(transport)
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            link_surface(surface)
            return session_id, transport, surface
        return get_shared_session_id(surface=surface), transport, surface

    def _allocate_chat_request_id(self, requested_request_id: Optional[str]) -> str:
        requested = str(requested_request_id or "").strip()
        if not requested:
            return self.memory_service.create_correlation_id()[:8]

        if requested in self.request_states:
            return self.memory_service.create_correlation_id()[:8]

        with contextlib.suppress(Exception):
            if self.event_store.list_events_for_request(requested, limit=1):
                return self.memory_service.create_correlation_id()[:8]

        return requested

    def _authorize_device_report(self) -> bool:
        token = request.headers.get("X-Sentinel-Agent-Token") or request.headers.get("X-Blackfong-Agent-Token")
        auth_header = request.headers.get("Authorization", "")
        if not token and auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        payload = request.get_json(silent=True) or {}
        token = token or payload.get("token")
        return self.legion_auth.validate_agent_token(token) or self._legion_session() is not None

    def _require_legion_or_frame_agent(self, device_id: str) -> tuple[str, Optional[Any]]:
        _, error = self._require_legion_session()
        if error is None:
            return "legion", None
        if str(device_id or "").strip().lower() == "frame" and self._authorize_device_report():
            return "frame_bridge", None
        return "unknown", error

    def _persist_frame_artifact(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data_b64 = payload.pop("artifact_base64", None)
        if not data_b64:
            return payload
        try:
            raw = base64.b64decode(str(data_b64), validate=True)
        except Exception as exc:
            raise ValueError(f"Invalid frame artifact payload: {exc}") from exc
        requested_name = str(payload.pop("artifact_name", "") or "frame_artifact.bin")
        safe_name = "".join(ch if ch.isalnum() or ch in {".", "_", "-"} else "_" for ch in requested_name)[:80]
        if not safe_name:
            safe_name = "frame_artifact.bin"
        artifact_dir = paths.artifacts_root / "frame"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / f"{int(time.time() * 1000)}_{safe_name}"
        artifact_path.write_bytes(raw)
        payload["artifact"] = {
            "path": str(artifact_path),
            "media_type": payload.pop("media_type", "application/octet-stream"),
            "byte_count": len(raw),
        }
        payload["artifact_exists"] = True
        return payload

    def _ingest_frame_event(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        event_type = str(envelope.get("event_type") or envelope.get("type") or "").strip()
        validate_event_type(event_type)
        allowed = {
            "frame_tap_event",
            "frame_imu_observation",
            "frame_display_update",
            "audio_sensor_observation",
            "vision_sensor_observation",
            "sentinel_detection_event",
            "sentinel_raw_artifact_captured",
            "agent_query",
            "agent_response",
        }
        if event_type not in allowed:
            raise ValueError(f"Unsupported Frame event type: {event_type}")
        payload = self._persist_frame_artifact(dict(envelope.get("payload") or {}))
        payload.setdefault("schema_version", "1.0")
        payload.setdefault("device_id", "frame")
        payload.setdefault(
            "provenance",
            {"source": "frame", "origin_service": "frame_bridge", "schema_version": "1.0"},
        )
        tags = list(envelope.get("tags") or ["frame", event_type])
        event = Event.new(
            source="frame",
            type=event_type,
            payload=payload,
            confidence=float(envelope.get("confidence", 0.9)),
            parent_event=envelope.get("parent_event"),
            session_id=envelope.get("session_id"),
            correlation_id=envelope.get("correlation_id"),
            request_id=envelope.get("request_id"),
            tags=tags,
        )
        self.event_store.add_event(event)
        result = {"schema_version": "1.0", "status": "ok", "event": event.to_dict()}
        self._publish_live_event("frame_event", result)
        if HAS_SOCKETIO and self.socketio:
            self.socketio.emit("frame_event", result)
        return result

    def _blackcomputer_device_endpoint(self) -> str:
        host = self.host if self.host not in {"0.0.0.0", "::"} else "blackcomputer.local"
        return f"http://{host}:{self.port}"

    def _seed_blackcomputer_device_fast(self) -> Dict[str, Any]:
        try:
            self.update_metrics()
            metrics = self.metrics.to_dict()
        except Exception:
            metrics = {}
        report = {
            "device_id": "blackcomputer",
            "label": "Blackcomputer",
            "role": "obeos_hub",
            "endpoint": self._blackcomputer_device_endpoint(),
            "status": "online",
            "health": {
                "overall": metrics.get("system_health", "available"),
                "system_health": metrics.get("system_health", "available"),
                "memory_usage_mb": metrics.get("memory_usage_mb", 0.0),
                "avg_response_time": metrics.get("avg_response_time", 0.0),
                "error_rate": metrics.get("error_rate", 0.0),
                "backend": "pending_awareness_refresh",
            },
            "sensors": {},
            "services": {"hud": {"status": "available"}},
            "capabilities": ["obeos_hud", "daise", "awareness", "diagnostics", "legion_pwa"],
            "telemetry": {"metrics": metrics, "refresh_mode": "fast_seed"},
            "provenance": {"source": "blackfong", "origin_service": "web_dashboard_fast_seed"},
        }
        return self.device_registry.record_report("blackcomputer", report, publish_events=False)

    def _sync_blackcomputer_device(self) -> Dict[str, Any]:
        self.update_metrics()
        awareness = self._build_awareness_snapshot(emit=False)
        performance = self.awareness_service.performance_slice(awareness)
        environment = self.awareness_service.environment_slice(awareness)
        result = self.device_registry.update_blackcomputer(
            metrics=self.metrics.to_dict(),
            awareness=awareness,
            performance=performance,
            environment=environment,
            endpoint=self._blackcomputer_device_endpoint(),
        )
        self._blackcomputer_device_sync_updated_at = time.time()
        return result

    def _sync_blackcomputer_device_background(self) -> None:
        try:
            result = self._sync_blackcomputer_device()
            self._publish_live_event("device_update", result)
        except Exception as exc:
            logger.exception("Blackcomputer device background sync failed")
            log_action(LogLevel.ERROR, "legion", "blackcomputer_background_sync_failed", error_message=str(exc))
        finally:
            with self._blackcomputer_device_sync_lock:
                self._blackcomputer_device_sync_thread = None

    def _sync_blackcomputer_device_if_needed(self, *, force: bool = False, background: bool = True) -> Dict[str, Any]:
        current = self.device_registry.get_device("blackcomputer", include_history=True)
        if current.get("last_seen") is None or str(current.get("status") or "").lower() in {"", "unknown"}:
            current = self._seed_blackcomputer_device_fast()
        fresh = (
            not force
            and current.get("last_seen") is not None
            and time.time() - float(current.get("updated_at") or current.get("last_seen") or 0.0) < self._blackcomputer_device_sync_ttl_s
        )
        if fresh:
            return current
        if not background or force:
            return self._sync_blackcomputer_device()
        with self._blackcomputer_device_sync_lock:
            thread = self._blackcomputer_device_sync_thread
            if thread is None or not thread.is_alive():
                self._blackcomputer_device_sync_thread = threading.Thread(
                    target=self._sync_blackcomputer_device_background,
                    name="legion-blackcomputer-device-sync",
                    daemon=True,
                )
                self._blackcomputer_device_sync_thread.start()
        return current

    def _setup_routes(self) -> None:
        @self.app.route("/hud-static/<path:filename>")
        def hud_static(filename: str):
            return send_from_directory(HUD_ASSET_ROOT, filename)

        @self.app.route("/legion-static/<path:filename>")
        def legion_static(filename: str):
            return send_from_directory(LEGION_ASSET_ROOT, filename)

        @self.app.route("/legion")
        def legion_dashboard():
            return render_template_string(self._get_legion_html())

        @self.app.route("/legion/manifest.webmanifest")
        def legion_manifest():
            return send_from_directory(LEGION_ASSET_ROOT, "manifest.webmanifest", mimetype="application/manifest+json")

        @self.app.route("/legion/service-worker.js")
        def legion_service_worker():
            return send_from_directory(LEGION_ASSET_ROOT, "service-worker.js", mimetype="application/javascript")

        @self.app.route("/api/legion/pair", methods=["POST"])
        def legion_pair():
            payload = request.get_json(silent=True) or {}
            result = self.legion_auth.pair(
                str(payload.get("token") or ""),
                label=str(payload.get("label") or "Legion"),
                user_agent=str(request.headers.get("User-Agent") or ""),
            )
            if result.get("status") != "paired":
                return jsonify(result), 401
            response = jsonify(result)
            response.set_cookie(
                LEGION_SESSION_COOKIE,
                str(result["session_id"]),
                max_age=int(result.get("ttl_hours", 720)) * 3600,
                httponly=True,
                samesite="Strict",
            )
            return response

        @self.app.route("/api/legion/session")
        def legion_session():
            client, error = self._require_legion_session()
            if error is not None:
                return error
            from .operator_session import load_operator_session

            session_id, transport, surface = self._resolve_chat_session({"transport": "legion"})
            payload = load_operator_session()
            payload["session_id"] = session_id
            payload["transport"] = transport
            payload["surface"] = surface
            payload["legion_client_id"] = str((client or {}).get("client_id") or "")
            return jsonify(payload)

        @self.app.route("/api/devices")
        def devices():
            _, error = self._require_legion_session()
            if error is not None:
                return error
            try:
                self._sync_blackcomputer_device_if_needed(background=True)
            except Exception as exc:
                logger.exception("Blackcomputer device sync failed")
                log_action(LogLevel.ERROR, "legion", "blackcomputer_sync_failed", error_message=str(exc))
            return jsonify(self.device_registry.list_devices(include_history=False))

        @self.app.route("/api/devices/<device_id>")
        def device_detail(device_id: str):
            _, error = self._require_legion_session()
            if error is not None:
                return error
            if device_id == "blackcomputer":
                self._sync_blackcomputer_device_if_needed(background=True)
            return jsonify(self.device_registry.get_device(device_id, include_history=True))

        @self.app.route("/api/devices/<device_id>/report", methods=["POST"])
        def device_report(device_id: str):
            if not self._authorize_device_report():
                return jsonify({"status": "unauthorized", "reason_code": "device_report_token_required"}), 401
            payload = request.get_json(silent=True) or {}
            report = dict(payload.get("report") or payload)
            report.pop("token", None)
            result = self.device_registry.record_report(device_id, report)
            self._publish_live_event("device_update", result)
            return jsonify({"status": "ok", "device": result})

        @self.app.route("/api/devices/<device_id>/refresh", methods=["POST"])
        def device_refresh(device_id: str):
            _, error = self._require_legion_session()
            if error is not None:
                return error
            if device_id == "blackcomputer":
                self._sync_blackcomputer_device_if_needed(force=True, background=False)
            result = self.device_registry.refresh_device(device_id)
            self._publish_live_event("device_update", result.get("device", {}))
            return jsonify(result)

        @self.app.route("/api/devices/<device_id>/diagnose", methods=["POST"])
        def device_diagnose(device_id: str):
            _, error = self._require_legion_session()
            if error is not None:
                return error
            if device_id == "blackcomputer":
                self._sync_blackcomputer_device_if_needed(background=True)
            return jsonify(self.device_registry.diagnose_device(device_id))

        @self.app.route("/api/devices/<device_id>/sensors/start", methods=["POST"])
        def device_sensors_start(device_id: str):
            requested_by, error = self._require_legion_or_frame_agent(device_id)
            if error is not None:
                return error
            payload = request.get_json(silent=True) or {}
            result = self.device_registry.set_sensor_mode(
                device_id,
                active=True,
                mode=str(payload.get("mode") or "live_vision"),
                consent=payload.get("consent"),
                requested_by=requested_by,
            )
            status_code = 409 if result.get("status") == "requires_consent" else 200
            if status_code == 200:
                self._publish_live_event("device_update", result.get("device", {}))
            return jsonify(result), status_code

        @self.app.route("/api/devices/<device_id>/sensors/stop", methods=["POST"])
        def device_sensors_stop(device_id: str):
            requested_by, error = self._require_legion_or_frame_agent(device_id)
            if error is not None:
                return error
            payload = request.get_json(silent=True) or {}
            result = self.device_registry.set_sensor_mode(
                device_id,
                active=False,
                mode=str(payload.get("mode") or "off"),
                consent=payload.get("consent") or {"approved": True, "level": 1},
                requested_by=requested_by,
            )
            self._publish_live_event("device_update", result.get("device", {}))
            return jsonify(result)

        @self.app.route("/api/frame/events", methods=["POST"])
        def frame_events():
            if not self._authorize_device_report():
                return jsonify({"status": "unauthorized", "reason_code": "frame_agent_token_required"}), 401
            payload = request.get_json(silent=True) or {}
            try:
                return jsonify(self._ingest_frame_event(payload))
            except ValueError as exc:
                return jsonify({"status": "error", "error": str(exc)}), 400

        @self.app.route("/api/frame/chat", methods=["POST"])
        def frame_chat():
            if not self._authorize_device_report():
                return jsonify({"status": "unauthorized", "reason_code": "frame_agent_token_required"}), 401
            payload = request.get_json(silent=True) or {}
            message = str(payload.get("message") or "").strip()
            if not message:
                return jsonify({"status": "error", "error": "message is required"}), 400
            context = dict(payload.get("context") or {})
            context.setdefault("input_origin", "frame")
            context.setdefault("device_id", "frame")
            result = self.operator_chat.send_message(
                message=message,
                session_id=context.get("session_id"),
                request_id=context.get("request_id"),
                analysis_mode=str(payload.get("analysis_mode") or context.get("analysis_mode") or "standard"),
                agent_override=payload.get("agent_override") or context.get("agent_override"),
                include_memory=bool(payload.get("include_memory", False)),
                modality=str(payload.get("modality") or context.get("input_modality") or "text"),
                context=context,
            )
            self._publish_live_event("chat_complete", result)
            return jsonify(result), 200 if result.get("status") != "error" else 500

        @self.app.route("/")
        @self.app.route("/hud")
        @self.app.route("/sessions")
        @self.app.route("/admin")
        def dashboard():
            return render_template_string(self._get_dashboard_html())

        @self.app.route("/api/live/stream")
        def live_stream():
            subscriber = self.services.event_bus.subscribe(max_queue_size=64)
            with self.live_stream_lock:
                self.live_stream_subscribers.append(subscriber)

            @stream_with_context
            def event_stream():
                try:
                    yield self._format_sse("transport", {"status": "connected", "mode": "sse", "timestamp": time.time()})
                    yield self._format_sse("metrics_update", self.metrics.to_dict())
                    yield self._format_sse(
                        "cognition_snapshot",
                        self._build_cognition_snapshot(
                            session_id=self.cognition_focus.get("selected_session"),
                            request_id=self.cognition_focus.get("selected_request"),
                            layer=self.cognition_focus.get("layer", "live_brain"),
                        ),
                    )
                    while True:
                        try:
                            event = subscriber.queue.get(timeout=15.0)
                            yield self._format_sse(event.event_type, event.payload)
                        except queue.Empty:
                            yield self._format_sse("heartbeat", {"timestamp": time.time()})
                finally:
                    self.services.event_bus.unsubscribe(subscriber)
                    with self.live_stream_lock:
                        self.live_stream_subscribers = [item for item in self.live_stream_subscribers if item is not subscriber]

            return Response(
                event_stream(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        @self.app.route("/api/metrics")
        def get_metrics():
            self.update_metrics()
            return jsonify(self.metrics.to_dict())

        @self.app.route("/api/events")
        def get_events():
            limit = self._bounded_int_arg("limit", 50, minimum=1, maximum=500)
            cursor = self._bounded_int_arg("cursor", 0, minimum=0, maximum=100000)
            self.recent_events = self.memory_service.list_recent_events(limit=limit + cursor + 1)
            page = self.recent_events[cursor : cursor + limit]
            next_cursor = cursor + limit if len(self.recent_events) > cursor + limit else None
            return jsonify({"events": page, "total_events": len(self.recent_events), "limit": limit, "cursor": cursor, "next_cursor": next_cursor})

        @self.app.route("/api/alerts")
        def get_alerts():
            return jsonify({"alerts": self.alerts[-20:], "total_alerts": len(self.alerts)})

        @self.app.route("/api/notifications")
        def get_notifications():
            limit = int(request.args.get("limit", "50"))
            unread_only = request.args.get("unread", "false").lower() == "true"
            return jsonify(self.notification_service.list_notifications(limit=limit, unread_only=unread_only))

        @self.app.route("/api/operator/summary")
        def operator_summary():
            self.operator_summary.tinyprogrammer = self.tinyprogrammer
            self.operator_summary.memory_chamber = self.memory_chamber
            summary = self.operator_summary.build_summary(
                session_id=request.args.get("session_id") or None,
                request_id=request.args.get("request_id") or None,
                include_awareness=request.args.get("include_awareness", "true").lower() != "false",
            )
            return jsonify(self._json_safe_value(summary))

        @self.app.route("/api/memory-chamber/summary")
        def memory_chamber_summary():
            return jsonify(self._json_safe_value(self.memory_chamber.summary()))

        @self.app.route("/api/memory-chamber/graph")
        def memory_chamber_graph():
            selected_node = request.args.get("selected_node") or None
            search = request.args.get("search") or None
            mode = request.args.get("mode") or "memory_chamber"
            layer = request.args.get("layer") or "live_brain"
            limit = self._bounded_int_arg("limit", 36, minimum=6, maximum=160)
            max_nodes = self._bounded_int_arg("max_nodes", limit, minimum=6, maximum=200)
            max_edges = self._bounded_int_arg("max_edges", limit * 3, minimum=12, maximum=600)
            edge_filter = request.args.get("edge_filter") or "top"
            zoom_bucket = request.args.get("zoom_bucket") or None
            result = self.memory_chamber.graph(
                mode=str(mode),
                layer=str(layer),
                selected_node=selected_node,
                search=search,
                limit=limit,
                edge_filter=str(edge_filter),
                zoom_bucket=str(zoom_bucket) if zoom_bucket else None,
                max_nodes=max_nodes,
                max_edges=max_edges,
            )
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/memory-chamber/neighborhood")
        def memory_chamber_neighborhood():
            center = str(request.args.get("center") or request.args.get("node") or "").strip()
            depth = self._bounded_int_arg("depth", 1, minimum=1, maximum=4)
            limit = self._bounded_int_arg("limit", 18, minimum=4, maximum=120)
            return jsonify(self._json_safe_value(self.memory_chamber.neighborhood(center, depth=depth, limit=limit)))

        @self.app.route("/api/memory-chamber/clusters")
        def memory_chamber_clusters():
            limit = self._bounded_int_arg("limit", 8, minimum=1, maximum=64)
            return jsonify(self._json_safe_value({"clusters": self.memory_chamber.clusters(limit=limit)}))

        @self.app.route("/api/memory-chamber/orphans")
        def memory_chamber_orphans():
            limit = self._bounded_int_arg("limit", 20, minimum=1, maximum=100)
            return jsonify(self._json_safe_value({"orphans": self.memory_chamber.orphans(limit=limit)}))

        @self.app.route("/api/memory-chamber/missing-links")
        def memory_chamber_missing_links():
            limit = self._bounded_int_arg("limit", 12, minimum=1, maximum=100)
            return jsonify(self._json_safe_value({"missing_links": self.memory_chamber.missing_links(limit=limit)}))

        @self.app.route("/api/memory-chamber/deltas")
        def memory_chamber_deltas():
            limit = self._bounded_int_arg("limit", 8, minimum=1, maximum=64)
            return jsonify(self._json_safe_value({"recent_deltas": self.memory_chamber.recent_deltas(limit=limit)}))

        @self.app.route("/api/memory-chamber/reasoning-path")
        def memory_chamber_reasoning_path():
            limit = self._bounded_int_arg("limit", 10, minimum=2, maximum=40)
            result = self.memory_chamber.trace_reasoning_path(
                source=request.args.get("source") or None,
                target=request.args.get("target") or None,
                query=request.args.get("query") or None,
                limit=limit,
            )
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/memory-chamber/reindex", methods=["POST"])
        def memory_chamber_reindex():
            payload = request.get_json(silent=True) or {}
            execute_background = bool(payload.get("background", True))
            roots = [Path(item) for item in payload.get("roots", []) if str(item).strip()]
            result = self.memory_chamber.request_reindex(
                reason=str(payload.get("reason") or "api"),
                roots=roots or None,
                background=execute_background,
            )
            status_code = 202 if result.get("status") in {"accepted", "queued"} else 200 if result.get("status") == "ok" else 500
            return jsonify(self._json_safe_value(result)), status_code

        @self.app.route("/api/knowledge/status")
        def knowledge_status():
            result = self.memory_service.knowledge_status()
            result["sync"] = self._knowledge_sync_snapshot()
            if "ensure_ecosystem" in request.args or "max_files_per_root" in request.args:
                result["deprecated_query_params_ignored"] = ["ensure_ecosystem", "max_files_per_root"]
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/knowledge/sync", methods=["POST"])
        def knowledge_sync():
            payload = request.get_json(silent=True) or {}
            max_files_per_root = self._bounded_int_payload(payload, "max_files_per_root", 80, minimum=10, maximum=200)
            force = bool(payload.get("force", False))
            background = bool(payload.get("background", True))
            if not background:
                result = self._execute_knowledge_sync(max_files_per_root=max_files_per_root, force=force)
                status_code = 200 if result.get("status") == "ok" else 500
                return jsonify(self._json_safe_value(result)), status_code

            with self.knowledge_sync_lock:
                if self.knowledge_sync_status.get("status") == "running":
                    return jsonify(self._json_safe_value(self._knowledge_sync_snapshot())), 202
                self.knowledge_sync_status = {
                    "status": "running",
                    "started_at": time.time(),
                    "completed_at": None,
                    "error": None,
                    "result": None,
                    "max_files_per_root": max_files_per_root,
                    "force": force,
                }
                self.knowledge_sync_thread = threading.Thread(
                    target=self._background_knowledge_sync,
                    kwargs={"max_files_per_root": max_files_per_root, "force": force},
                    daemon=True,
                )
                self.knowledge_sync_thread.start()
            return jsonify(self._json_safe_value(self._knowledge_sync_snapshot())), 202

        @self.app.route("/api/knowledge/sync/status")
        def knowledge_sync_status():
            return jsonify(self._json_safe_value(self._knowledge_sync_snapshot()))

        @self.app.route("/api/knowledge/search", methods=["POST"])
        def knowledge_search():
            payload = request.get_json(silent=True) or {}
            query = str(payload.get("query") or payload.get("question") or "").strip()
            if not query:
                return jsonify({"error": "Query is required"}), 400
            result = self.memory_service.knowledge_search(
                query=query,
                retrieval_profile=payload.get("retrieval_profile") or payload.get("mode") or payload.get("analysis_mode"),
                skip_vector_search=bool(payload.get("skip_vector_search", False)),
                max_context_chars=payload.get("max_context_chars"),
            )
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/knowledge/context", methods=["POST"])
        def knowledge_context():
            payload = request.get_json(silent=True) or {}
            query = str(payload.get("query") or payload.get("question") or "").strip()
            if not query:
                return jsonify({"error": "Query is required"}), 400
            from .retrieval_profiles import retrieval_profile_settings

            session_id = str(payload.get("session_id") or self.memory_service.create_session_id())
            retrieval_profile = str(payload.get("retrieval_profile") or payload.get("mode") or payload.get("analysis_mode") or "standard")
            request_id = payload.get("request_id") or None
            bundle = self.memory_service.build_context(
                session_id=session_id,
                query=query,
                agent_name=str(payload.get("agent_name") or "daise"),
                analysis_mode=str(payload.get("analysis_mode") or retrieval_profile),
                skip_vector_search=bool(payload.get("skip_vector_search", False)),
                retrieval_profile=retrieval_profile,
                request_id=request_id,
                transport=str(payload.get("transport") or "").strip() or None,
            )
            settings = retrieval_profile_settings(retrieval_profile)
            cognition_graph = None
            if self.memory_chamber is not None and settings.get("include_graph"):
                cognition_graph = self.memory_chamber.reasoning_context(
                    query,
                    session_id=session_id,
                    request_id=str(payload.get("request_id") or ""),
                    limit=self._bounded_int_arg("limit", 6, minimum=2, maximum=16),
                    profile=retrieval_profile,
                )
            result = {
                "query": query,
                "session_id": session_id,
                "request_id": request_id,
                "retrieval_profile": bundle.get("retrieval_profile"),
                "retrieval_contract": bundle.get("retrieval_contract"),
                "context": bundle.get("prompt_context"),
                "knowledge_context": bundle.get("knowledge_context"),
                "knowledge_hits": bundle.get("knowledge_hits"),
                "global_memories": bundle.get("global_memories"),
                "grounded_memory_answer": bundle.get("grounded_memory_answer"),
                "operator_profile": bundle.get("operator_profile"),
                "citations": bundle.get("citations"),
                "prompt_metrics": bundle.get("prompt_metrics"),
                "timing_ms": bundle.get("timing_ms"),
                "cognition_graph": cognition_graph,
            }
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/knowledge/bench", methods=["POST"])
        def knowledge_bench():
            payload = request.get_json(silent=True) or {}
            query = str(payload.get("query") or payload.get("question") or "").strip()
            if not query:
                return jsonify({"error": "Query is required"}), 400
            from .retrieval_profiles import retrieval_profile_settings

            requested_profiles = payload.get("profiles")
            profiles = [str(item) for item in requested_profiles] if isinstance(requested_profiles, list) and requested_profiles else None
            result = self.memory_service.benchmark_retrieval(
                query=query,
                session_id=payload.get("session_id"),
                agent_name=str(payload.get("agent_name") or "daise"),
                profiles=profiles,
            )
            for profile_result in result.get("profiles", []):
                settings = retrieval_profile_settings(profile_result.get("retrieval_profile"))
                if self.memory_chamber is None or not settings.get("include_graph"):
                    continue
                graph_bundle = self.memory_chamber.reasoning_context(
                    query,
                    session_id=payload.get("session_id"),
                    request_id=payload.get("request_id"),
                    limit=int(settings.get("graph_limit") or 6),
                    profile=str(profile_result.get("retrieval_profile") or "deep"),
                )
                profile_result["graph"] = {
                    "matched_nodes": len(graph_bundle.get("matched_nodes") or []),
                    "citations": len(graph_bundle.get("citations") or []),
                    "timing_ms": graph_bundle.get("timing_ms") or {},
                }
                profile_result.setdefault("timing_ms", {})["graph_enrichment"] = (
                    (graph_bundle.get("timing_ms") or {}).get("graph_enrichment", 0.0)
                )
            self.memory_service.persist_workflow_event(
                source="daise",
                event_type="memory_retrieval_observation",
                payload={
                    "schema_version": "1.0",
                    "query": query,
                    "bench_profiles": result.get("profiles", []),
                },
                confidence=0.92,
                session_id=payload.get("session_id"),
                correlation_id=payload.get("correlation_id"),
                request_id=payload.get("request_id"),
                tags=["knowledge", "benchmark"],
            )
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/knowledge/ingest/text", methods=["POST"])
        def knowledge_ingest_text():
            payload = request.get_json(silent=True) or {}
            text = str(payload.get("text") or "").strip()
            if not text:
                return jsonify({"error": "Text is required"}), 400
            try:
                result = self.memory_service.ingest_knowledge_text(
                    text=text,
                    title=payload.get("title"),
                    collection=payload.get("collection"),
                    domain=payload.get("domain"),
                    tags=[str(item) for item in payload.get("tags", [])] if isinstance(payload.get("tags"), list) else None,
                    source_path=payload.get("source_path"),
                    session_id=payload.get("session_id"),
                    correlation_id=payload.get("correlation_id"),
                    request_id=payload.get("request_id"),
                )
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            if self.memory_chamber is not None:
                self.memory_chamber.request_reindex(reason="knowledge_api_ingest", background=True)
            return jsonify(self._json_safe_value(result)), 201

        @self.app.route("/api/gps/status")
        def gps_status():
            return jsonify(self._json_safe_value(self.gps_service.snapshot()))

        @self.app.route("/api/gps/provider", methods=["POST"])
        def gps_provider():
            payload = request.get_json(silent=True) or {}
            try:
                result = self.gps_service.set_provider_mode(
                    str(payload.get("mode") or "auto"),
                    session_id=payload.get("session_id") or request.args.get("session_id") or None,
                    request_id=payload.get("request_id") or request.args.get("request_id") or None,
                )
            except ValueError as exc:
                return jsonify({"status": "error", "error": str(exc)}), 400
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/gps/recording/start", methods=["POST"])
        def gps_recording_start():
            payload = request.get_json(silent=True) or {}
            result = self.gps_service.start_recording(
                session_id=payload.get("session_id") or request.args.get("session_id") or None,
                request_id=payload.get("request_id") or request.args.get("request_id") or None,
            )
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/gps/recording/stop", methods=["POST"])
        def gps_recording_stop():
            payload = request.get_json(silent=True) or {}
            result = self.gps_service.stop_recording(
                session_id=payload.get("session_id") or request.args.get("session_id") or None,
                request_id=payload.get("request_id") or request.args.get("request_id") or None,
            )
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/verify/<mode>")
        def verify_mode(mode: str):
            from blackfong_ecosystem.verification import VerificationService

            verification = VerificationService(
                workspace_root=self.services.platform_service.workspace_root,
                services=self.services,
            )
            normalized = str(mode or "").strip().lower()
            if normalized == "automated":
                result = verification.automated()
            elif normalized == "surfaces":
                result = verification.surfaces()
            elif normalized == "integrations":
                result = verification.integrations()
            elif normalized == "full":
                result = verification.full()
            else:
                return jsonify({"success": False, "error": f"Unknown verification mode: {mode}"}), 404
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/integrations")
        def integrations_index():
            return jsonify(self._json_safe_value(self.integration_registry.list_integrations()))

        @self.app.route("/api/integrations/<integration_id>")
        def integrations_detail(integration_id: str):
            try:
                result = self.integration_registry.describe(integration_id)
            except KeyError:
                return jsonify({"success": False, "error": f"Unknown integration: {integration_id}"}), 404
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/integrations/<integration_id>/launch", methods=["POST"])
        def integrations_launch(integration_id: str):
            payload = request.get_json(silent=True) or {}
            try:
                if str(integration_id).strip().lower() == "redamon_mode":
                    result = self.redamon_workflow.launch(
                        session_id=payload.get("session_id"),
                        request_id=payload.get("request_id"),
                        intent_id=payload.get("intent_id"),
                        consent=payload.get("consent"),
                        execute=bool(payload.get("execute", True)),
                    )
                else:
                    result = self.integration_registry.launch_contract(
                        integration_id,
                        execute=bool(payload.get("execute", True)),
                        mode=payload.get("mode"),
                    )
            except KeyError:
                return jsonify({"success": False, "error": f"Unknown integration: {integration_id}"}), 404
            status_code = 200 if result.get("success") else 409 if result.get("status") == "requires_consent" else 400
            return jsonify(self._json_safe_value(result)), status_code

        @self.app.route("/api/redamon/status")
        def redamon_status():
            result = self.redamon_workflow.status(session_id=request.args.get("session_id") or None)
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/redamon/enter", methods=["POST"])
        def redamon_enter():
            payload = request.get_json(silent=True) or {}
            result = self.redamon_workflow.enter_mode(
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                objective=str(payload.get("objective") or ""),
            )
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/redamon/propose", methods=["POST"])
        def redamon_propose():
            payload = request.get_json(silent=True) or {}
            result = self.redamon_workflow.propose_launch(
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                objective=str(payload.get("objective") or ""),
                target=str(payload.get("target") or ""),
            )
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/redamon/launch", methods=["POST"])
        def redamon_launch():
            payload = request.get_json(silent=True) or {}
            result = self.redamon_workflow.launch(
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                intent_id=payload.get("intent_id"),
                consent=payload.get("consent"),
                execute=bool(payload.get("execute", True)),
            )
            status_code = 200 if result.get("success") else 409 if result.get("status") == "requires_consent" else 400
            return jsonify(self._json_safe_value(result)), status_code

        @self.app.route("/api/notifications/<notification_id>/read", methods=["POST"])
        def read_notification(notification_id: str):
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="notification_read",
                handler=lambda: self.notification_service.mark_read(notification_id),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=notification_id,
                proposed_by="operator",
                human_reason="Notification read requested.",
            )
            return jsonify(result)

        @self.app.route("/api/notifications/read-all", methods=["POST"])
        def read_all_notifications():
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="notifications_read_all",
                handler=self.notification_service.mark_all_read,
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target="notification_inbox",
                proposed_by="operator",
                human_reason="Mark-all notifications requested.",
            )
            return jsonify(result)

        @self.app.route("/api/notifications/test", methods=["POST"])
        def test_notification():
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="notification_test",
                handler=lambda: {
                    "status": "created",
                    "notification": self.notification_service.create(
                        title=str(payload.get("title") or "Blackfong notification test"),
                        message=str(payload.get("message") or "HUD inbox and optional Windows toast delivery are connected."),
                        level=str(payload.get("level") or "info"),
                        category=str(payload.get("category") or "test"),
                        desktop=bool(payload.get("desktop", True)),
                        session_id=payload.get("session_id"),
                        request_id=payload.get("request_id"),
                    ),
                },
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target="notification_test",
                proposed_by="operator",
                human_reason="Test notification requested.",
            )
            return jsonify(result)

        @self.app.route("/api/sessions")
        def get_sessions():
            limit = self._bounded_int_arg("limit", 200, minimum=1, maximum=500)
            cursor = self._bounded_int_arg("cursor", 0, minimum=0, maximum=100000)
            sessions = self._get_sessions_from_store()
            page = sessions[cursor : cursor + limit]
            next_cursor = cursor + limit if len(sessions) > cursor + limit else None
            return jsonify({"sessions": page, "total_active": len(sessions), "limit": limit, "cursor": cursor, "next_cursor": next_cursor})

        @self.app.route("/api/session/<session_id>")
        def get_session_details(session_id: str):
            details = self.memory_service.get_session_details(session_id)
            if not details:
                return jsonify({"error": "Session not found"}), 404
            return jsonify(details)

        @self.app.route("/api/session/<session_id>/messages")
        def get_session_messages(session_id: str):
            messages = self.memory_service.get_session_messages(session_id, limit=200)
            if not messages and self._session_missing(session_id):
                return jsonify({"error": "Session not found"}), 404
            return jsonify({"session_id": session_id, "messages": messages, "count": len(messages)})

        @self.app.route("/api/request/<request_id>/trace")
        def get_request_trace(request_id: str):
            trace = self._get_request_snapshot(request_id)
            if trace is None:
                return jsonify({"error": "Trace not found"}), 404
            return jsonify(trace)

        @self.app.route("/api/session/<session_id>/replay", methods=["POST"])
        def replay_session(session_id: str):
            details = self.memory_service.get_session_details(session_id)
            if not details:
                return jsonify({"error": "Session not found"}), 404
            for event in details["messages"]:
                if HAS_SOCKETIO and self.socketio:
                    self.socketio.emit("replay_event", {"session_id": session_id, "event": event, "timestamp": time.time()})
            return jsonify({"status": "replay_started", "events_replayed": len(details["messages"])})

        @self.app.route("/api/session/<session_id>/pin", methods=["POST"])
        def pin_session_memory(session_id: str):
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="pin_session_memory",
                handler=lambda: self._pin_session_memory_result(session_id, payload),
                session_id=session_id,
                request_id=payload.get("request_id"),
                target=session_id,
                proposed_by="operator",
                human_reason="Pin durable memory requested.",
            )
            if result.get("status") == "failed" or result.get("error"):
                return jsonify(result), 400
            self.add_alert({"type": "memory", "message": f"Pinned memory for {session_id[:8]}", "level": "info"})
            return jsonify(result)

        @self.app.route("/api/memory")
        def list_memory():
            query = request.args.get("query", "")
            limit = int(request.args.get("limit", "12"))
            agent_name = request.args.get("agent")
            session_id = request.args.get("session_id")
            return jsonify(
                {
                    "memories": self.memory_service.list_global_memories(
                        query=query,
                        limit=limit,
                        agent_name=agent_name,
                        session_id=session_id,
                    ),
                    "stats": self.memory_service.get_memory_stats(),
                }
            )

        @self.app.route("/api/awareness")
        def get_awareness():
            emit_events = request.args.get("emit", "false").lower() == "true"
            return jsonify(self._build_awareness_snapshot(emit=emit_events))

        @self.app.route("/api/performance")
        def get_performance():
            snapshot = self._build_awareness_snapshot(emit=False)
            return jsonify(self.awareness_service.performance_slice(snapshot))

        @self.app.route("/api/environment")
        def get_environment():
            snapshot = self._build_awareness_snapshot(emit=False)
            return jsonify(self.awareness_service.environment_slice(snapshot))

        @self.app.route("/api/sensors")
        def get_sensors():
            snapshot = self._build_awareness_snapshot(emit=False)
            return jsonify(
                {
                    "schema_version": snapshot.get("schema_version"),
                    "timestamp": snapshot.get("timestamp"),
                    "sensors": snapshot.get("sensors", {}),
                    "health": snapshot.get("health", {}),
                    "provenance": snapshot.get("provenance", {}),
                }
            )

        @self.app.route("/api/sensors/snapshot", methods=["POST"])
        def sensor_snapshot():
            payload = request.get_json(silent=True) or {}
            self.sensor_mode = "snapshot"
            consent_decision = self.awareness_service.policy_engine.evaluate_consent(
                action="sensor_snapshot",
                consent=payload.get("consent") or {"approved": True, "level": 1},
                target="sensor_snapshot",
            )
            self._record_operator_consent("sensor_snapshot", "sensor_snapshot", consent_decision, payload.get("consent"))
            snapshot = self._build_awareness_snapshot(emit=True)
            return jsonify(
                {
                    "status": "ok",
                    "sensor_mode": self.sensor_mode,
                    "consent": consent_decision,
                    "sensors": snapshot.get("sensors", {}),
                }
            )

        @self.app.route("/api/sensors/live/start", methods=["POST"])
        def sensor_live_start():
            payload = request.get_json(silent=True) or {}
            consent_decision = self.awareness_service.policy_engine.evaluate_consent(
                action="start_live_sensor_mode",
                consent=payload.get("consent"),
                target=payload.get("target", "live_perception_mode"),
            )
            self._record_operator_consent("start_live_sensor_mode", "live_perception_mode", consent_decision, payload.get("consent"))
            if not consent_decision.get("approved"):
                return jsonify({"status": "requires_consent", "consent": consent_decision}), 409
            self.sensor_mode = str(payload.get("mode") or "live_vision").strip().lower()
            snapshot = self._build_awareness_snapshot(emit=True)
            return jsonify({"status": "ok", "sensor_mode": self.sensor_mode, "consent": consent_decision, "sensors": snapshot.get("sensors", {})})

        @self.app.route("/api/sensors/live/stop", methods=["POST"])
        def sensor_live_stop():
            self.sensor_mode = "off"
            snapshot = self._build_awareness_snapshot(emit=True)
            return jsonify({"status": "ok", "sensor_mode": self.sensor_mode, "sensors": snapshot.get("sensors", {})})

        @self.app.route("/api/identity/resolve")
        def resolve_identity():
            name = request.args.get("name", "")
            resolution = identity_registry.resolve(name)
            if request.args.get("emit", "false").lower() == "true":
                event = identity_registry.emit_resolution_event(resolution, memory_service=self.memory_service)
                return jsonify({"resolution": resolution.to_dict(), "event_id": event.id})
            return jsonify({"resolution": resolution.to_dict(), "doctrine": identity_registry.manifest()})

        @self.app.route("/api/files/policy")
        def file_policy():
            return jsonify({"policy": self.file_workflow.workspace_policy()})

        @self.app.route("/api/files/proposals")
        def file_proposals():
            return jsonify(self.file_workflow.list_proposals())

        @self.app.route("/api/files/roots", methods=["GET", "POST"])
        def file_roots():
            if request.method == "POST":
                payload = request.get_json(silent=True) or {}
                path = str(payload.get("path") or payload.get("root") or "")
                if not path:
                    return jsonify({"success": False, "error": "path is required"}), 400
                result = self._run_operator_action(
                    action="file_scope_approve_root",
                    handler=lambda: self.file_scope_service.approve_root(
                        path,
                        session_id=payload.get("session_id"),
                        correlation_id=payload.get("correlation_id"),
                        request_id=payload.get("request_id"),
                    ),
                    session_id=payload.get("session_id"),
                    request_id=payload.get("request_id"),
                    target=path,
                    proposed_by="operator",
                    human_reason="File scope approval requested.",
                )
                return jsonify(result), 200 if result.get("success") else 403
            result = self.file_scope_service.list_roots(
                session_id=request.args.get("session_id") or None,
                correlation_id=request.args.get("correlation_id") or None,
                request_id=request.args.get("request_id") or None,
                emit=request.args.get("emit", "false").lower() == "true",
            )
            return jsonify(self._json_safe_value(result))

        @self.app.route("/api/files/map", methods=["GET", "POST"])
        def file_map():
            payload = request.get_json(silent=True) or {} if request.method == "POST" else request.args
            path = str(payload.get("path") or "")
            if not path:
                return jsonify({"success": False, "error": "path is required"}), 400
            depth = int(payload.get("depth") or 2)
            limit = int(payload.get("limit") or 120)
            result = self._run_operator_action(
                action="file_scope_map",
                handler=lambda: self.file_scope_service.map_path(
                    path,
                    depth=depth,
                    limit=limit,
                    session_id=payload.get("session_id"),
                    correlation_id=payload.get("correlation_id"),
                    request_id=payload.get("request_id"),
                ),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=path,
                proposed_by="daise",
                human_reason="Machine file scope map requested.",
            )
            return jsonify(result), 200 if result.get("success") else 403

        @self.app.route("/api/files/scan", methods=["POST"])
        def file_scan():
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="file_scan",
                handler=lambda: self.file_workflow.scan(str(payload.get("path") or ".")),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=str(payload.get("path") or "."),
                proposed_by="operator",
                human_reason="Workspace scan requested.",
            )
            return jsonify(result), 200 if result.get("success") else 403

        @self.app.route("/api/files/read", methods=["GET", "POST"])
        def file_read():
            payload = request.get_json(silent=True) or {} if request.method == "POST" else request.args
            path = str(payload.get("path") or "")
            if not path:
                return jsonify({"success": False, "error": "path is required"}), 400
            result = self._run_operator_action(
                action="file_read",
                handler=lambda: self.file_scope_service.read_path(
                    path,
                    max_bytes=int(payload.get("max_bytes") or 262144),
                    session_id=payload.get("session_id"),
                    correlation_id=payload.get("correlation_id"),
                    request_id=payload.get("request_id"),
                ),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=path,
                proposed_by="operator",
                human_reason="File read-map content requested.",
            )
            return jsonify(result), 200 if result.get("success") else 403

        @self.app.route("/api/files/propose", methods=["POST"])
        def file_propose():
            payload = request.get_json(silent=True) or {}
            path = str(payload.get("path") or "")
            if not path:
                return jsonify({"success": False, "error": "path is required"}), 400
            if "proposed_content" not in payload:
                return jsonify({"success": False, "error": "proposed_content is required"}), 400
            result = self._run_operator_action(
                action="file_propose",
                handler=lambda: self.file_workflow.propose(
                    path=path,
                    proposed_content=str(payload.get("proposed_content") or ""),
                    summary=str(payload.get("summary") or ""),
                    ttl_seconds=payload.get("ttl_seconds"),
                ),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=path,
                priority="high",
                proposed_by="daise",
                human_reason="Governed file proposal requested.",
            )
            return jsonify(result), 200 if result.get("success") else 403

        @self.app.route("/api/files/<proposal_id>/approve", methods=["POST"])
        def file_approve(proposal_id: str):
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="file_approve",
                handler=lambda: self.file_workflow.approve(proposal_id, consent=payload.get("consent")),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=proposal_id,
                priority="high",
                proposed_by="operator",
                human_reason="File proposal approval requested.",
            )
            return jsonify(result), 200 if result.get("success") else 409

        @self.app.route("/api/files/<proposal_id>/pre-apply-validation", methods=["POST"])
        def file_pre_apply_validation(proposal_id: str):
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="file_pre_apply_validation",
                handler=lambda: self.file_workflow.pre_apply_validation(proposal_id),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=proposal_id,
                priority="high",
                proposed_by="daise",
                human_reason="Pre-apply validation requested.",
            )
            return jsonify(result), 200 if result.get("success") else 409

        @self.app.route("/api/files/<proposal_id>/apply", methods=["POST"])
        def file_apply(proposal_id: str):
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="file_apply",
                handler=lambda: self.file_workflow.apply(proposal_id),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=proposal_id,
                priority="high",
                proposed_by="daise",
                human_reason="Governed file apply requested.",
            )
            return jsonify(result), 200 if result.get("success") else 409

        @self.app.route("/api/files/<proposal_id>/rollback", methods=["POST"])
        def file_rollback(proposal_id: str):
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="file_rollback",
                handler=lambda: self.file_workflow.rollback(proposal_id, consent=payload.get("consent")),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=proposal_id,
                priority="high",
                proposed_by="operator",
                human_reason="File rollback requested.",
            )
            return jsonify(result), 200 if result.get("success") else 409

        @self.app.route("/api/diagnose")
        def get_diagnose():
            snapshot = self._build_awareness_snapshot(emit=True)
            if snapshot.get("health", {}).get("contradictions"):
                self._emit_cognition_fault(
                    code="consistency_conflict",
                    message="System awareness detected contradictory observations.",
                    severity="error",
                    source="awareness",
                    meta={"contradictions": snapshot.get("health", {}).get("contradictions", [])},
                )
            return jsonify(
                {
                    "schema_version": snapshot.get("schema_version"),
                    "timestamp": snapshot.get("timestamp"),
                    "diagnostic_summary": snapshot.get("diagnostic_summary", {}),
                    "health": snapshot.get("health", {}),
                    "inferred": snapshot.get("inferred", {}),
                    "provenance": snapshot.get("provenance", {}),
                }
            )

        @self.app.route("/api/capabilities")
        def get_capabilities():
            snapshot = self._build_awareness_snapshot(emit=False)
            return jsonify(snapshot.get("observed", {}).get("capabilities", {}))

        @self.app.route("/api/why")
        def get_why():
            topic = request.args.get("topic", "last decision")
            request_id = request.args.get("request_id")
            snapshot = self._build_awareness_snapshot(emit=False)
            return jsonify(self.awareness_service.explain_why(topic, request_id=request_id, snapshot=snapshot))

        @self.app.route("/api/cognition/snapshot")
        def cognition_snapshot():
            layer = request.args.get("layer") or self.cognition_focus.get("layer", "live_brain")
            session_id = request.args.get("session_id") or self.cognition_focus.get("selected_session")
            request_id = request.args.get("request_id") or self.cognition_focus.get("selected_request")
            snapshot = self._build_cognition_snapshot(
                session_id=str(session_id) if session_id else None,
                request_id=str(request_id) if request_id else None,
                layer=str(layer),
            )
            return jsonify(snapshot)

        @self.app.route("/api/cognition/gesture", methods=["POST"])
        def cognition_gesture():
            payload = request.get_json(silent=True) or {}
            try:
                gesture = normalize_gesture(payload)
            except ValueError as exc:
                return jsonify({"status": "error", "error": str(exc)}), 400
            self.cognition_focus = apply_gesture(self.cognition_focus, gesture)
            self._sync_cognition_focus(
                session_id=gesture.get("session_id"),
                request_id=gesture.get("request_id"),
                layer=self.cognition_focus.get("layer"),
                mode=gesture.get("mode") or self.cognition_focus.get("mode"),
                selected_node=self.cognition_focus.get("selected_node"),
                opened_module=self.cognition_focus.get("opened_module"),
            )
            snapshot = self._build_cognition_snapshot(
                session_id=gesture.get("session_id"),
                request_id=gesture.get("request_id"),
                layer=self.cognition_focus.get("layer", "live_brain"),
                use_cache=False,
            )
            self._cognition_snapshot_cache = None
            self.cognition_router.emit_focus(snapshot["focus"])
            self.cognition_router.emit_delta(
                {
                    "schema_version": snapshot["schema_version"],
                    "reason": f"gesture:{gesture['action']}",
                    "timestamp": snapshot["timestamp"],
                    "focus": snapshot["focus"],
                    "active_layer": snapshot["active_layer"],
                    "nodes": snapshot["nodes"],
                    "edges": snapshot["edges"],
                    "overlays": snapshot["overlays"],
                    "trace_ribbon": snapshot["trace_ribbon"],
                    "faults": snapshot["faults"],
                }
            )
            return jsonify({"status": "ok", "gesture": gesture, "focus": snapshot["focus"], "snapshot": snapshot})

        @self.app.route("/api/operator/actions")
        def get_operator_actions():
            statuses = request.args.get("status")
            session_id = request.args.get("session_id")
            parsed_statuses = [item.strip() for item in str(statuses or "").split(",") if item.strip()] or None
            limit = self._bounded_int_arg("limit", 50, minimum=1, maximum=500)
            cursor = self._bounded_int_arg("cursor", 0, minimum=0, maximum=100000)
            try:
                payload = self.operator_actions.list_actions(
                    limit=limit + cursor + 1,
                    session_id=session_id,
                    statuses=parsed_statuses,
                )
                actions = payload.get("actions", [])
                payload["actions"] = actions[cursor : cursor + limit]
                payload["limit"] = limit
                payload["cursor"] = cursor
                payload["next_cursor"] = cursor + limit if len(actions) > cursor + limit else None
            except Exception as exc:
                logger.exception("Operator actions unavailable")
                log_action(
                    LogLevel.WARNING,
                    "dashboard",
                    "operator_actions_unavailable",
                    error_message=str(exc),
                )
                payload = {
                    "actions": [],
                    "pending_actions": 0,
                    "status": "degraded",
                    "reason_code": "event_store_temporarily_unavailable",
                    "human_reason": "Operator action queue is temporarily unavailable.",
                }
            return jsonify(payload)

        @self.app.route("/api/operator/actions/<intent_id>")
        def get_operator_action(intent_id: str):
            action = self.operator_actions.get_action(intent_id)
            if action is None:
                return jsonify({"error": "Unknown operator intent"}), 404
            return jsonify(action)

        @self.app.route("/api/operator/actions/<intent_id>/cancel", methods=["POST"])
        def cancel_operator_action(intent_id: str):
            payload = request.get_json(silent=True) or {}
            result = self.operator_actions.cancel_intent(
                intent_id,
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
            )
            return jsonify(result), 200 if result.get("status") == "cancelled" else 409

        @self.app.route("/api/whatchanged")
        def get_whatchanged():
            session_id = request.args.get("session_id")
            since = request.args.get("since_timestamp")
            return jsonify(
                self.operator_actions.summarize_changes(
                    session_id=session_id,
                    since_timestamp=float(since) if since else None,
                )
            )

        @self.app.route("/api/chat", methods=["POST"])
        def chat():
            payload = request.get_json(silent=True) or {}
            message = str(payload.get("message", "")).strip()
            if not message:
                return jsonify({"error": "Message is required"}), 400
            session_id, transport, _surface = self._resolve_chat_session(payload)
            request_id = self._allocate_chat_request_id(payload.get("request_id"))
            agent_override = payload.get("agent") or payload.get("agent_override")
            from .model_routing import normalize_response_mode

            analysis_mode = normalize_response_mode(str(payload.get("analysis_mode") or "standard"))
            background = bool(payload.get("background", True))
            input_origin = str(payload.get("input_origin") or transport).strip().lower() or transport

            self._initialize_request_state(
                request_id=request_id,
                session_id=session_id,
                message=message,
                agent_override=agent_override,
                analysis_mode=analysis_mode,
                transport=transport,
            )

            if background:
                worker = threading.Thread(
                    target=self._run_chat_request_in_background,
                    kwargs={
                        "message": message,
                        "session_id": session_id,
                        "request_id": request_id,
                        "agent_override": agent_override,
                        "analysis_mode": analysis_mode,
                        "transport": transport,
                        "input_origin": input_origin,
                    },
                    daemon=True,
                )
                self.request_threads[request_id] = worker
                worker.start()
                return (
                    jsonify(
                        {
                            "status": "accepted",
                            "request_id": request_id,
                            "session_id": session_id,
                            "phase": "accepted",
                            "message": "Request accepted by D.A.I.S.E.",
                            "agent_override": agent_override,
                            "analysis_mode": analysis_mode,
                            "transport": transport,
                            "input_origin": input_origin,
                        }
                    ),
                    202,
                )

            if not self._ensure_orchestrator():
                return jsonify({"error": "Unified orchestrator is unavailable"}), 503
            response_payload = self._execute_chat_request(
                message=message,
                session_id=session_id,
                request_id=request_id,
                agent_override=agent_override,
                analysis_mode=analysis_mode,
                transport=transport,
                input_origin=input_origin,
            )
            status_code = 500 if response_payload.get("result", {}).get("error") else 200
            return jsonify(response_payload), status_code

        @self.app.route("/api/workspace/shortcuts")
        def workspace_shortcuts():
            from .workspace_shortcuts import shortcuts_json_for_hud

            return jsonify({"shortcuts": shortcuts_json_for_hud()})

        @self.app.route("/api/operator/session")
        def operator_session():
            from .operator_session import load_operator_session

            return jsonify(load_operator_session())

        @self.app.route("/api/commands")
        def get_commands():
            return jsonify({"commands": self.command_registry.catalog()})

        @self.app.route("/api/commands/execute", methods=["POST"])
        def execute_command():
            payload = request.get_json(silent=True) or {}
            command = str(payload.get("command", "")).strip()
            if not command:
                return jsonify({"error": "Command is required"}), 400
            return jsonify(self._execute_command(command, session_id=payload.get("session_id"), payload=payload))

        @self.app.route("/api/tinyprogrammer/status")
        def tiny_status():
            payload = self.tinyprogrammer.get_status(require_stream=self.tinyprogrammer.auto_stream)
            payload["graph_participant"] = {
                "agent_node_id": "agent:tinyprogrammer",
                "status": "tracked",
                "recent_artifacts": self.memory_chamber.recent_agent_artifacts("tinyprogrammer", limit=5),
            }
            return jsonify(self._json_safe_value(payload))

        @self.app.route("/api/tinyprogrammer/process")
        def tiny_process():
            return jsonify(self.tinyprogrammer.process_status(require_stream=self.tinyprogrammer.auto_stream))

        @self.app.route("/api/tinyprogrammer/log")
        def tiny_log():
            lines = int(request.args.get("lines") or 40)
            result = self._run_operator_action(
                action="tiny_log",
                handler=lambda: self.tinyprogrammer.get_log_tail(lines=lines),
                session_id=request.args.get("session_id") or None,
                request_id=request.args.get("request_id") or None,
                target=str(self.tinyprogrammer.log_path),
                proposed_by="operator",
                human_reason="TinyProgrammer diagnostic log requested.",
            )
            return jsonify(result), 200 if result.get("success") else 404

        @self.app.route("/api/tinyprogrammer/launch", methods=["POST"])
        def tiny_launch():
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="tiny_launch",
                handler=lambda: self.tinyprogrammer.ensure_running(
                    require_stream=bool(payload.get("enable_stream", True)),
                    adopt_external=bool(payload.get("adopt_external", True)),
                    open_browser=bool(payload.get("open_browser", True)),
                    consent=payload.get("consent") or {"approved": True, "level": 1},
                ),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=self._tinyprogrammer_target(),
                priority="normal",
                proposed_by="operator",
                human_reason="TinyProgrammer launch requested.",
            )
            return jsonify(result), 200 if result.get("success") else 409

        @self.app.route("/api/tinyprogrammer/open", methods=["POST"])
        def tiny_open():
            payload = request.get_json(silent=True) or {}
            target = str(payload.get("target") or "dashboard")
            result = self._run_operator_action(
                action=f"tiny_open:{target}",
                handler=lambda: self.tinyprogrammer.open_live(
                    consent=payload.get("consent"),
                    open_browser=bool(payload.get("open_browser", True)),
                )
                if target in {"stream", "live"}
                else self.tinyprogrammer.open(
                    target=target,
                    consent=payload.get("consent"),
                    open_browser=bool(payload.get("open_browser", True)),
                ),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=target,
                proposed_by="operator",
                human_reason=f"TinyProgrammer {target} open requested.",
            )
            return jsonify(result), 200 if result.get("success") else 409

        @self.app.route("/api/tinyprogrammer/stop", methods=["POST"])
        def tiny_stop():
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="tiny_stop",
                handler=self.tinyprogrammer.stop,
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=self._tinyprogrammer_target(),
                proposed_by="operator",
                human_reason="TinyProgrammer stop requested.",
            )
            return jsonify(result), 200 if result.get("success") else 409

        @self.app.route("/api/tinyprogrammer/recent")
        def tiny_recent():
            limit = int(request.args.get("limit", "10"))
            return jsonify(self.tinyprogrammer.get_recent(limit=limit))

        @self.app.route("/api/tinyprogrammer/bbs")
        def tiny_bbs():
            limit = int(request.args.get("limit", "12"))
            return jsonify(self.tinyprogrammer.get_bbs(limit=limit))

        @self.app.route("/api/tinyprogrammer/brief", methods=["POST"])
        def tiny_brief():
            payload = request.get_json(silent=True) or {}
            brief = str(payload.get("brief", "")).strip()
            if not brief:
                return jsonify({"error": "Brief is required"}), 400
            result = self._run_operator_action(
                action="tiny_brief",
                handler=lambda: self.tinyprogrammer.send_brief(brief, metadata={k: v for k, v in payload.items() if k != "brief"}),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target="brief",
                proposed_by="operator",
                human_reason="TinyProgrammer brief requested.",
            )
            return jsonify(result), 200 if result.get("success") else 409

        @self.app.route("/api/tinyprogrammer/control", methods=["POST"])
        def tiny_control():
            payload = request.get_json(silent=True) or {}
            action = str(payload.get("action", "")).strip()
            if not action:
                return jsonify({"error": "Action is required"}), 400
            result = self._run_operator_action(
                action=f"tiny_control:{action}",
                handler=lambda: self.tinyprogrammer.send_control(action, payload.get("value")),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target=action,
                proposed_by="operator",
                human_reason=f"TinyProgrammer control {action} requested.",
            )
            return jsonify(result), 200 if result.get("success") else 409

        @self.app.route("/api/tinyprogrammer/feedback", methods=["POST"])
        def tiny_feedback():
            payload = request.get_json(silent=True) or {}
            rating = int(payload.get("rating", 0))
            if rating <= 0:
                return jsonify({"error": "Rating is required"}), 400
            result = self._run_operator_action(
                action="tiny_feedback",
                handler=lambda: self.tinyprogrammer.rate_last_creation(rating, feedback=str(payload.get("feedback", ""))),
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target="feedback",
                proposed_by="operator",
                human_reason="TinyProgrammer feedback requested.",
            )
            return jsonify(result), 200 if result.get("success") else 409

        @self.app.route("/api/voice/status")
        def voice_status():
            if not self._ensure_orchestrator():
                return jsonify({"active": False, "message": "Voice loop unavailable"}), 503
            return jsonify(self.orchestrator.get_voice_loop_status())

        @self.app.route("/api/voice/start", methods=["POST"])
        def voice_start():
            if not self._ensure_orchestrator():
                self.notification_service.create(
                    title="Voice unavailable",
                    message="The voice loop could not start because the orchestrator is unavailable.",
                    level="warning",
                    category="voice",
                )
                return jsonify({"active": False, "message": "Voice loop unavailable"}), 503
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="voice_start",
                handler=lambda: {"status": "started" if self.orchestrator.start_voice_loop() else "unchanged", "voice": self.orchestrator.get_voice_loop_status()},
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target="voice_loop",
                proposed_by="operator",
                human_reason="Voice loop start requested.",
            )
            self.notification_service.create(
                title="Voice loop started" if result.get("status") == "started" else "Voice loop unchanged",
                message=f"Voice status: {result.get('status')}",
                level="info" if result.get("status") == "started" else "warning",
                category="voice",
            )
            return jsonify(result)

        @self.app.route("/api/voice/stop", methods=["POST"])
        def voice_stop():
            if not self._ensure_orchestrator():
                self.notification_service.create(
                    title="Voice unavailable",
                    message="The voice loop could not stop because the orchestrator is unavailable.",
                    level="warning",
                    category="voice",
                )
                return jsonify({"active": False, "message": "Voice loop unavailable"}), 503
            payload = request.get_json(silent=True) or {}
            result = self._run_operator_action(
                action="voice_stop",
                handler=lambda: {"status": "stopped" if self.orchestrator.stop_voice_loop() else "unchanged", "voice": self.orchestrator.get_voice_loop_status()},
                session_id=payload.get("session_id"),
                request_id=payload.get("request_id"),
                target="voice_loop",
                proposed_by="operator",
                human_reason="Voice loop stop requested.",
            )
            self.notification_service.create(
                title="Voice loop stopped" if result.get("status") == "stopped" else "Voice loop unchanged",
                message=f"Voice status: {result.get('status')}",
                level="info",
                category="voice",
            )
            return jsonify(result)

        @self.app.route("/api/help")
        def get_help():
            return jsonify({"commands": self.command_registry.catalog(), "surfaces": {"hud": "Secondary Thoughtglass HUD cockpit", "tui": "Terminal boot-home and fallback", "event_api": "Optional external producer API"}})

    def _setup_socket_events(self) -> None:
        if not HAS_SOCKETIO or not self.socketio:
            return

        @self.socketio.on("connect")
        def handle_connect():
            self.connected_dashboard_clients += 1
            emit("metrics_update", self.metrics.to_dict())
            snapshot = self._build_cognition_snapshot(
                session_id=self.cognition_focus.get("selected_session"),
                request_id=self.cognition_focus.get("selected_request"),
                layer=self.cognition_focus.get("layer", "live_brain"),
            )
            emit("cognition_snapshot", snapshot)
            emit("cognition_focus", snapshot["focus"])
            log_action(LogLevel.INFO, "dashboard", "client_connected")

        @self.socketio.on("disconnect")
        def handle_disconnect():
            self.connected_dashboard_clients = max(0, self.connected_dashboard_clients - 1)
            log_action(LogLevel.INFO, "dashboard", "client_disconnected")

    def _initialize_request_state(
        self,
        *,
        request_id: str,
        session_id: str,
        message: str,
        agent_override: Optional[str],
        analysis_mode: str,
        transport: Optional[str] = None,
    ) -> None:
        intent = self.operator_actions.create_intent(
            action="chat_request",
            session_id=session_id,
            request_id=request_id,
            target=str(agent_override or "auto"),
            priority="high" if analysis_mode == "deep" else "normal",
            proposed_by="operator",
            human_reason="Request accepted by D.A.I.S.E.",
            ttl_seconds=900.0,
        )
        self.request_states[request_id] = {
            "request_id": request_id,
            "session_id": session_id,
            "intent_id": intent.get("intent_id"),
            "query": message,
            "agent_override": agent_override,
            "analysis_mode": analysis_mode,
            "transport": str(transport or "hud"),
            "status": "pending",
            "phase": "accepted",
            "phase_message": "Request accepted by D.A.I.S.E.",
            "selected_agent": agent_override or "auto",
            "reports": [],
            "started_at": time.time(),
            "updated_at": time.time(),
        }
        self._sync_cognition_focus(
            session_id=session_id,
            request_id=request_id,
            selected_node="daise_interface",
        )
        self._handle_chat_phase(
            {
                "request_id": request_id,
                "session_id": session_id,
                "phase": "accepted",
                "agent": agent_override or "daise",
                "message": "Request accepted by D.A.I.S.E.",
            }
        )

    def _run_chat_request_in_background(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        agent_override: Optional[str],
        analysis_mode: str,
        transport: str,
        input_origin: str,
    ) -> None:
        try:
            if self.orchestrator is None:
                self._handle_chat_phase(
                    {
                        "request_id": request_id,
                        "session_id": session_id,
                        "phase": "accepted",
                        "agent": "daise",
                        "message": "Warming local orchestrator and specialist agents.",
                    }
                )
            response_payload = self._execute_chat_request(
                message=message,
                session_id=session_id,
                request_id=request_id,
                agent_override=agent_override,
                analysis_mode=analysis_mode,
                transport=transport,
                input_origin=input_origin,
            )
            if HAS_SOCKETIO and self.socketio:
                self.socketio.emit("chat_complete", response_payload)
            self._publish_live_event("chat_complete", response_payload)
        except Exception as exc:  # pragma: no cover - defensive background path
            logger.exception("Background chat request failed")
            state = self.request_states.setdefault(request_id, {"request_id": request_id, "session_id": session_id})
            state.update(
                {
                    "status": "error",
                    "phase": "error",
                    "phase_message": str(exc),
                    "backend_error": str(exc),
                    "updated_at": time.time(),
                    "completed_at": time.time(),
                }
            )
            intent_id = state.get("intent_id")
            if intent_id:
                self.operator_actions.transition(
                    str(intent_id),
                    status="failed",
                    session_id=session_id,
                    request_id=request_id,
                    reason_code="action_failed",
                    human_reason=str(exc),
                )
            self._handle_chat_phase(
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "phase": "error",
                    "agent": "daise",
                    "message": str(exc),
                }
                )
            self._emit_cognition_fault(
                code="request_background_error",
                message=str(exc),
                severity="error",
                source="daise",
                session_id=session_id,
                request_id=request_id,
            )
            if HAS_SOCKETIO and self.socketio:
                self.socketio.emit(
                    "chat_error",
                    {
                        "request_id": request_id,
                        "session_id": session_id,
                        "error": str(exc),
                    },
                )
            self._publish_live_event(
                "chat_error",
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "error": str(exc),
                },
            )
        finally:
            self.request_threads.pop(request_id, None)

    def _execute_chat_request(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        agent_override: Optional[str],
        analysis_mode: str,
        transport: str,
        input_origin: str,
    ) -> Dict[str, Any]:
        if not self._ensure_orchestrator():
            raise RuntimeError("Unified orchestrator is unavailable")
        input_modality = InputModality.TEXT if InputModality is not None else "text"

        progress_callback = lambda event: self._handle_chat_phase(event)
        report_callback = lambda event: self._handle_agent_report(event)
        selection_callback = lambda event: self._handle_agent_selection(event)
        stream_tokens: list[str] = []

        def stream_callback(token: str) -> None:
            stream_tokens.append(str(token))
            self._publish_live_event(
                "chat_token",
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "token": str(token),
                    "text": "".join(stream_tokens),
                },
            )

        result = self.orchestrator.process_input(
            message,
            input_modality,
            context={
                "session_id": session_id,
                "request_id": request_id,
                "agent_override": agent_override,
                "analysis_mode": analysis_mode,
                "transport": transport,
                "input_origin": input_origin,
                "progress_callback": progress_callback,
                "agent_report_callback": report_callback,
                "agent_selection_callback": selection_callback,
                "stream_callback": stream_callback,
            },
        )
        if result.get("fast_path"):
            self._publish_live_event(
                "chat_phase",
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "phase": "routing",
                    "agent": result.get("selected_agent") or "daise",
                    "message": "Fast path: single-agent quick response.",
                    "fast_path": True,
                },
            )

        if self.session_explorer is not None:
            self.session_explorer.track_session_event(
                session_id,
                {
                    "event_type": "agent_response",
                    "payload": result.get("event_payload", {}),
                    "agent": result.get("selected_agent") or result.get("agent"),
                    "response": result.get("response"),
                    "timestamp": result.get("timestamp", time.time()),
                },
            )

        self.update_metrics()

        response_payload = self._build_chat_response_payload(
            message=message,
            session_id=session_id,
            request_id=request_id,
            result=result,
            agent_override=agent_override,
            transport=transport,
        )

        state = self.request_states.setdefault(
            request_id,
            {
                "request_id": request_id,
                "session_id": session_id,
                "started_at": time.time(),
            },
        )
        state.update(
            {
                "status": "error" if result.get("error") else "complete",
                "phase": "error" if result.get("error") else "complete",
                "phase_message": result.get("error") or "Request complete.",
                "updated_at": time.time(),
                "completed_at": time.time(),
                "selected_agent": result.get("selected_agent") or result.get("agent"),
                "provider": result.get("provider"),
                "model": result.get("model"),
                "backend_error": result.get("backend_error"),
                "result": response_payload["result"],
                "response_text": result.get("response"),
            }
        )

        self._handle_chat_phase(
            {
                "request_id": request_id,
                "session_id": session_id,
                "phase": "error" if result.get("error") or result.get("backend_error") else "complete",
                "agent": result.get("selected_agent") or result.get("agent") or "daise",
                "message": result.get("error") or result.get("backend_error") or "Request complete.",
            }
        )

        intent_id = state.get("intent_id")
        if intent_id:
            self.operator_actions.transition(
                str(intent_id),
                status="failed" if result.get("error") else "completed",
                session_id=session_id,
                request_id=request_id,
                reason_code="action_failed" if result.get("error") else "completed",
                human_reason=str(result.get("error") or "Chat request completed."),
            )
        elapsed = float(state.get("completed_at", time.time()) or time.time()) - float(state.get("started_at", time.time()) or time.time())
        if result.get("error") or result.get("backend_error"):
            self.notification_service.create(
                title="D.A.I.S.E request failed",
                message=str(result.get("error") or result.get("backend_error")),
                level="error",
                category="chat",
                request_id=request_id,
                desktop=True,
            )
            self._emit_cognition_fault(
                code="request_error",
                message=str(result.get("error") or result.get("backend_error")),
                severity="error",
                source=str(result.get("selected_agent") or result.get("agent") or "daise"),
                session_id=session_id,
                request_id=request_id,
            )
        elif elapsed >= 30:
            self.notification_service.create(
                title="Long request completed",
                message=f"D.A.I.S.E completed request {request_id} in {elapsed:.0f}s.",
                level="info",
                category="chat",
                request_id=request_id,
            )
        self._sync_cognition_focus(
            session_id=session_id,
            request_id=request_id,
            selected_node=f"agent_{result.get('selected_agent')}" if result.get("selected_agent") else "daise_interface",
        )
        self._emit_cognition_delta(
            reason="request_error" if result.get("error") or result.get("backend_error") else "request_complete",
            session_id=session_id,
            request_id=request_id,
        )
        return response_payload

    def _build_chat_response_payload(
        self,
        *,
        message: str,
        session_id: str,
        request_id: str,
        result: Dict[str, Any],
        agent_override: Optional[str],
        transport: str,
    ) -> Dict[str, Any]:
        selected_agent = str(result.get("selected_agent") or result.get("agent") or agent_override or "daise")
        bundle = self.memory_service.build_context(
            session_id=session_id,
            query=message,
            agent_name=selected_agent,
            analysis_mode=str(result.get("analysis_mode") or "standard"),
            request_id=request_id,
            transport=transport,
        )
        try:
            self._publish_live_event(
                "memory_retrieval",
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "transport": transport,
                    "retrieval_profile": bundle.get("retrieval_profile"),
                    "knowledge_hits": bundle.get("knowledge_hits"),
                    "grounded_memory_answer": bundle.get("grounded_memory_answer"),
                    "citations": bundle.get("citations"),
                    "timing_ms": bundle.get("timing_ms"),
                },
            )
        except Exception:
            logger.exception("Failed to publish memory_retrieval live event")
        return {
            "status": "error" if result.get("error") else "complete",
            "session_id": session_id,
            "request_id": request_id,
            "transport": transport,
            "result": result,
            "messages": self.memory_service.get_session_messages(session_id, limit=40),
            "trace": self._get_request_snapshot(request_id) or self.memory_service.get_request_trace(request_id),
            "memory": bundle,
        }

    def _get_request_snapshot(self, request_id: str) -> Optional[Dict[str, Any]]:
        trace = self.memory_service.get_request_trace(request_id)
        state = self.request_states.get(request_id)
        if not trace["events"] and not state:
            return None

        snapshot = dict(trace)
        if state:
            phase_rank = {
                "accepted": 0,
                "policy": 1,
                "routing": 2,
                "memory": 3,
                "agent": 4,
                "evaluation": 5,
                "persisting": 6,
                "complete": 7,
                "error": 7,
            }
            trace_phase = str(snapshot.get("phase") or "accepted")
            state_phase = str(state.get("phase") or trace_phase)
            if phase_rank.get(state_phase, -1) >= phase_rank.get(trace_phase, -1):
                snapshot["phase"] = state_phase
                snapshot["status"] = state.get("status", snapshot.get("status"))
            snapshot.update(
                {
                    "phase_message": state.get("phase_message"),
                    "session_id": snapshot.get("session_id") or state.get("session_id"),
                    "started_at": state.get("started_at"),
                    "updated_at": state.get("updated_at"),
                    "completed_at": state.get("completed_at"),
                    "selected_agent": state.get("selected_agent"),
                    "intent_id": state.get("intent_id"),
                    "provider": state.get("provider"),
                    "model": state.get("model"),
                    "backend_error": state.get("backend_error"),
                    "reports": list(state.get("reports", [])),
                    "selection": state.get("selection"),
                    "result": state.get("result"),
                }
            )
        snapshot["queue_pressure"] = snapshot.get("queue_pressure") or self.awareness_service.observe_queue_pressure(
            request_states=self.request_states,
            request_threads=self.request_threads,
            backend_bootstrap=self.backend_bootstrap,
        )
        snapshot["diagnostic_hints"] = self.awareness_service.diagnostic_hints_from_trace(snapshot)
        return snapshot

    def _handle_chat_phase(self, event: Dict[str, Any]) -> None:
        request_id = str(event.get("request_id") or "")
        session_id = str(event.get("session_id") or "")
        if request_id:
            state = self.request_states.setdefault(request_id, {})
            previous_phase = str(state.get("phase") or "")
            phase = str(event.get("phase") or state.get("phase") or "accepted")
            status = "pending"
            if phase == "complete":
                status = "complete"
            elif phase == "error":
                status = "error"
            state.update(event)
            state["phase"] = phase
            state["status"] = status
            state["phase_message"] = event.get("message") or state.get("phase_message")
            state["updated_at"] = time.time()
            if phase == "accepted":
                state.setdefault("started_at", time.time())
            if phase in {"complete", "error"}:
                state["completed_at"] = time.time()
            if event.get("agent"):
                state["selected_agent"] = event.get("agent")
            intent_id = state.get("intent_id")
            if intent_id and phase not in {"accepted", "complete", "error"} and phase != previous_phase:
                self.operator_actions.transition(
                    str(intent_id),
                    status="executing",
                    session_id=state.get("session_id"),
                    request_id=request_id,
                    reason_code=f"phase_{phase}",
                    human_reason=str(event.get("message") or f"Request entered {phase} phase."),
                )
            phase_node = "daise_interface"
            if event.get("agent"):
                agent_name = str(event.get("agent") or "").strip().lower()
                phase_node = f"agent_{agent_name}" if agent_name not in {"daise", ""} else "daise_interface"
            self._sync_cognition_focus(
                session_id=state.get("session_id") or session_id or None,
                request_id=request_id,
                selected_node=phase_node,
            )
            self._emit_cognition_delta(
                reason=f"chat_phase:{state.get('phase', 'accepted')}",
                session_id=state.get("session_id") or session_id or None,
                request_id=request_id,
            )
            if phase == "error":
                self._emit_cognition_fault(
                    code="request_phase_error",
                    message=str(event.get("message") or "Request entered the error phase."),
                    severity="error",
                    source=str(event.get("agent") or "daise"),
                    session_id=state.get("session_id") or session_id or None,
                    request_id=request_id,
                )
        if HAS_SOCKETIO and self.socketio:
            self.socketio.emit("chat_phase", event)
        self._publish_live_event("chat_phase", event)

    def _handle_agent_report(self, event: Dict[str, Any]) -> None:
        request_id = str(event.get("request_id") or "")
        if request_id:
            state = self.request_states.setdefault(request_id, {})
            reports = list(state.get("reports", []))
            reports.append(event.get("report"))
            state["reports"] = reports
            state["updated_at"] = time.time()
            report = event.get("report") or {}
            agent_name = str(report.get("agent") or "daise")
            self._sync_cognition_focus(
                session_id=state.get("session_id"),
                request_id=request_id,
                selected_node=f"agent_{agent_name}" if agent_name != "daise" else "daise_interface",
            )
            self._emit_cognition_delta(
                reason="agent_report",
                session_id=state.get("session_id"),
                request_id=request_id,
            )
        if HAS_SOCKETIO and self.socketio:
            self.socketio.emit("agent_report", event)
        self._publish_live_event("agent_report", event)

    def _handle_agent_selection(self, event: Dict[str, Any]) -> None:
        request_id = str(event.get("request_id") or "")
        if request_id:
            state = self.request_states.setdefault(request_id, {})
            state["selection"] = event
            state["selected_agent"] = event.get("selected_agent")
            state["provider"] = event.get("provider")
            state["model"] = event.get("model")
            state["updated_at"] = time.time()
            selected_agent = str(event.get("selected_agent") or "daise")
            self._sync_cognition_focus(
                session_id=state.get("session_id"),
                request_id=request_id,
                selected_node=f"agent_{selected_agent}" if selected_agent != "daise" else "daise_interface",
                emit_focus_event=True,
            )
            self._emit_cognition_delta(
                reason="agent_selection",
                session_id=state.get("session_id"),
                request_id=request_id,
            )
        if HAS_SOCKETIO and self.socketio:
            self.socketio.emit("agent_selection", event)
        self._publish_live_event("agent_selection", event)

    def _session_missing(self, session_id: str) -> bool:
        return not any(session["session_id"] == session_id for session in self.memory_service.list_sessions(limit=500))

    def _get_sessions_from_store(self) -> List[Dict[str, Any]]:
        sessions = self.memory_service.list_sessions(limit=200)
        self.sessions = {}
        for session in sessions:
            info = SessionInfo(
                session_id=session["session_id"],
                start_time=session["start_time"],
                last_activity=session["last_activity"],
                total_requests=session["total_requests"],
                total_errors=session.get("total_errors", 0),
                agents_used=set(session.get("agents_used", [])),
                status=session.get("status", "active"),
                preview=session.get("preview", ""),
                event_count=session.get("event_count", 0),
            )
            self.sessions[info.session_id] = info
        return [session.to_dict() for session in self.sessions.values()]

    def _sync_recent_events(self) -> None:
        self.recent_events = self.memory_service.list_recent_events(limit=60)

    def update_metrics(self, *, force: bool = False) -> None:
        if not force and self._metrics_cache_updated_at:
            age_s = time.time() - self._metrics_cache_updated_at
            if age_s < self._metrics_cache_ttl_s:
                return
        try:
            self._get_sessions_from_store()
            self._sync_recent_events()
            memory_stats = memory_lifecycle_manager.get_memory_stats()
            quality_metrics = decision_quality_controller.get_quality_metrics()
            responses = self.event_store.list_events_by_type("agent_response", limit=200)
            timings = [float(event.payload.get("execution_time_ms", 0.0)) for event in responses if float(event.payload.get("execution_time_ms", 0.0)) > 0]
            errors = [event for event in responses if event.payload.get("success") is False]
            agent_scores: Dict[str, List[float]] = {}
            for event in responses:
                agent_name = str(event.payload.get("agent") or event.source)
                agent_scores.setdefault(agent_name, []).append(float(event.confidence))
            self.metrics.total_requests = self.event_store.count_events(type="agent_query")
            self.metrics.active_sessions = len(self.sessions)
            self.metrics.avg_response_time = round(sum(timings) / len(timings) / 1000, 3) if timings else 0.0
            self.metrics.error_rate = round(len(errors) / len(responses), 3) if responses else 0.0
            self.metrics.memory_usage_mb = float(memory_stats.get("total_size_mb", 0.0))
            self.metrics.decision_quality_score = float(quality_metrics.get("success_rate", quality_metrics.get("quality_score", 0.0)))
            self.metrics.agent_performance = {agent: round(sum(scores) / len(scores), 3) for agent, scores in agent_scores.items() if scores}
            health_score = int(self.metrics.error_rate <= 0.1) + int(self.metrics.avg_response_time <= 5.0) + int(self.metrics.decision_quality_score >= 0.75)
            self.metrics.system_health = "healthy" if health_score >= 2 else "warning" if health_score == 1 else "error"
            metrics_payload = self.metrics.to_dict()
            self._metrics_cache_updated_at = time.time()
            if HAS_SOCKETIO and self.socketio:
                self.socketio.emit("metrics_update", metrics_payload)
            self._publish_live_event("metrics_update", metrics_payload)
        except Exception as exc:
            logger.exception("HUD metrics update failed")
            log_action(LogLevel.ERROR, "dashboard", "metrics_update_error", error_message=str(exc))

    def add_event(self, event: Dict[str, Any]) -> None:
        event = dict(event)
        event["timestamp"] = event.get("timestamp", time.time())
        self.recent_events.append(event)
        if len(self.recent_events) > 1000:
            self.recent_events = self.recent_events[-1000:]

    def add_alert(self, alert: Dict[str, Any]) -> None:
        alert = dict(alert)
        alert["timestamp"] = alert.get("timestamp", time.time())
        self.alerts.append(alert)
        if len(self.alerts) > 100:
            self.alerts = self.alerts[-100:]
        self.notification_service.create(
            title=str(alert.get("title") or alert.get("type") or "Alert"),
            message=str(alert.get("message") or ""),
            level=str(alert.get("level") or "info"),
            category=str(alert.get("type") or "alert"),
        )

    def start_dashboard(self, *, open_browser: Optional[bool] = None) -> None:
        self.update_active = True
        self.update_thread = threading.Thread(target=self._metrics_update_loop, daemon=True)
        self.update_thread.start()
        self._autostart_tiny_if_enabled()
        self._start_backend_warmup()
        should_open = hud_settings.open_browser if open_browser is None else open_browser
        if should_open:
            threading.Timer(0.7, lambda: webbrowser.open(f"http://{self.host}:{self.port}/")).start()
        if HAS_SOCKETIO and self.socketio:
            self.socketio.run(self.app, host=self.host, port=self.port, debug=False, allow_unsafe_werkzeug=True)
        else:
            self.app.run(host=self.host, port=self.port, debug=False)

    def stop_dashboard(self, *, interactive: bool = False, prompt=None) -> None:
        self.update_active = False
        if self.update_thread:
            self.update_thread.join(timeout=5)
        self.tinyprogrammer.handle_managed_shutdown(interactive=interactive, prompt=prompt)
        log_action(LogLevel.INFO, "dashboard", "server_stopped")

    def _autostart_tiny_if_enabled(self, *, sync: bool = False) -> Optional[Dict[str, Any]]:
        if not getattr(self.tinyprogrammer, "autostart_enabled", False):
            return None
        runner = lambda: self.tinyprogrammer.ensure_running(
            require_stream=bool(getattr(self.tinyprogrammer, "auto_stream", True)),
            adopt_external=bool(getattr(self.tinyprogrammer, "adopt_external", True)),
            open_browser=False,
            consent={"approved": True, "level": 1},
        )
        if sync:
            return runner()
        if self._tiny_autostart_thread is not None and self._tiny_autostart_thread.is_alive():
            return None
        self._tiny_autostart_thread = threading.Thread(target=runner, daemon=True)
        self._tiny_autostart_thread.start()
        return None

    def _metrics_update_loop(self) -> None:
        while self.update_active:
            self.update_metrics(force=True)
            time.sleep(5)

    def _record_operator_consent(
        self,
        action: str,
        target: str,
        consent_decision: Dict[str, Any],
        consent: Optional[Dict[str, Any]],
    ) -> None:
        try:
            self.memory_service.persist_workflow_event(
                source="blackfong",
                event_type="operator_consent",
                payload={
                    "schema_version": "1.0",
                    "level": consent_decision.get("provided_level"),
                    "required_level": consent_decision.get("required_level"),
                    "action": action,
                    "target": target,
                    "approved": consent_decision.get("approved"),
                    "expires_at": (consent or {}).get("expires_at") or time.time() + 300,
                    "policy_decision": consent_decision,
                    "provenance": {
                        "source": "hud_operator_action",
                        "origin_service": "web_dashboard",
                        "derived_from": [],
                        "collection_method": "api_consent_evaluation",
                        "observed_at": time.time(),
                        "schema_version": "1.0",
                    },
                },
                confidence=0.95 if consent_decision.get("approved") else 0.7,
                tags=["consent", action],
            )
        except Exception:
            logger.exception("Unable to persist operator consent event")
            return

    def _augment_operator_result(self, result: Dict[str, Any], action_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged = dict(result)
        if action_state:
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
        return merged

    def _json_safe_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self._json_safe_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe_value(item) for item in value]
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _bounded_int_arg(self, name: str, default: int, *, minimum: int = 0, maximum: int = 500) -> int:
        try:
            value = int(request.args.get(name, str(default)))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _bounded_int_payload(self, payload: Dict[str, Any], name: str, default: int, *, minimum: int = 0, maximum: int = 500) -> int:
        try:
            value = int(payload.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _knowledge_sync_snapshot(self) -> Dict[str, Any]:
        with self.knowledge_sync_lock:
            return dict(self.knowledge_sync_status)

    def _execute_knowledge_sync(self, *, max_files_per_root: int = 80, force: bool = False) -> Dict[str, Any]:
        started_at = time.time()
        with self.knowledge_sync_lock:
            self.knowledge_sync_status = {
                "status": "running",
                "started_at": started_at,
                "completed_at": None,
                "error": None,
                "result": None,
                "max_files_per_root": max_files_per_root,
                "force": force,
            }
        try:
            result = self.memory_service.hydrate_knowledge_runtime(
                max_files_per_root=max_files_per_root,
                force=force,
            )
            status = "ok" if result.get("status") == "ok" else "failed"
            error = result.get("error")
        except Exception as exc:
            logger.exception("Knowledge ecosystem sync failed")
            result = {"status": "failed", "error": str(exc)}
            status = "failed"
            error = str(exc)
        completed_at = time.time()
        final = {
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_ms": int((completed_at - started_at) * 1000),
            "error": error,
            "result": result,
            "max_files_per_root": max_files_per_root,
            "force": force,
        }
        with self.knowledge_sync_lock:
            self.knowledge_sync_status = final
        return final

    def _background_knowledge_sync(self, *, max_files_per_root: int, force: bool) -> None:
        self._execute_knowledge_sync(max_files_per_root=max_files_per_root, force=force)

    def _format_sse(self, event_type: str, payload: Dict[str, Any]) -> str:
        return f"event: {event_type}\ndata: {json.dumps(self._json_safe_value(payload), default=str)}\n\n"

    def _publish_live_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            self.services.event_bus.publish(event_type, payload)
        except Exception:
            logger.exception("Unable to publish HUD live event: %s", event_type)

    def _pin_session_memory_result(self, session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            event = self.memory_service.pin_session_memory(
                session_id,
                text=payload.get("text"),
                title=payload.get("title"),
                event_id=payload.get("event_id"),
                tags=payload.get("tags") or [],
            )
        except ValueError as exc:
            return {"success": False, "status": "failed", "error": str(exc)}
        return {"success": True, "status": "pinned", "event_id": event.id}

    def _tinyprogrammer_target(self) -> str:
        project_path = getattr(self.tinyprogrammer, "project_path", None)
        base_url = getattr(self.tinyprogrammer, "base_url", None)
        if project_path:
            return str(project_path)
        if base_url:
            return str(base_url)
        return "tinyprogrammer"

    def _run_operator_action(
        self,
        *,
        action: str,
        handler,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        target: Optional[str] = None,
        priority: str = "normal",
        proposed_by: str = "operator",
        human_reason: str = "",
        ttl_seconds: float = 300.0,
        record_session_result: bool = True,
    ) -> Dict[str, Any]:
        intent = self.operator_actions.create_intent(
            action=action,
            session_id=session_id,
            request_id=request_id,
            target=target,
            priority=priority,
            proposed_by=proposed_by,
            human_reason=human_reason or f"{action} queued.",
            ttl_seconds=ttl_seconds,
        )
        intent_id = str(intent.get("intent_id"))
        self.operator_actions.transition(
            intent_id,
            status="executing",
            session_id=session_id,
            request_id=request_id,
            reason_code="in_progress",
            human_reason=human_reason or f"{action} is executing.",
        )
        try:
            result = handler()
        except Exception as exc:
            logger.exception("HUD operator action failed: %s", action)
            result = {"success": False, "status": "failed", "error": str(exc)}
        result = self._json_safe_value(result)
        if record_session_result:
            action_state = self.operator_actions.record_result(
                intent_id,
                result=result,
                session_id=session_id,
                request_id=request_id,
            )
        else:
            action_state = self.operator_actions.transition(
                intent_id,
                status="failed" if result.get("error") or result.get("success") is False else "completed",
                session_id=session_id,
                request_id=request_id,
                reason_code="action_failed" if result.get("error") or result.get("success") is False else "completed",
                human_reason=str(result.get("error") or result.get("message") or f"{action} completed."),
            )
        augmented = self._augment_operator_result(result, action_state)
        effective_session_id = session_id or augmented.get("session_id")
        effective_request_id = request_id or augmented.get("request_id")
        self._emit_cognition_delta(
            reason=f"operator_action:{action}",
            session_id=effective_session_id,
            request_id=effective_request_id,
        )
        status = str(augmented.get("action_status") or augmented.get("status") or "")
        if status in {"failed", "blocked", "expired"} or augmented.get("error"):
            self._emit_cognition_fault(
                code="operator_action_failed" if status != "expired" else "operator_action_expired",
                message=str(augmented.get("error") or augmented.get("human_reason") or f"{action} did not complete cleanly."),
                severity="error" if status == "failed" or augmented.get("error") else "warning",
                source="operator_action",
                session_id=effective_session_id,
                request_id=effective_request_id,
                meta={"action": action, "status": status},
            )
        self._publish_live_event(
            "operator_action",
            {
                "action": action,
                "status": status,
                "session_id": effective_session_id,
                "request_id": effective_request_id,
                "result": augmented,
            },
        )
        return augmented

    def _execute_command(self, command: str, *, session_id: Optional[str], payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        request_id = str((payload or {}).get("request_id") or "")
        return self._run_operator_action(
            action=f"command:{command}",
            handler=lambda: self._execute_command_impl(command, session_id=session_id, payload=payload),
            session_id=session_id,
            request_id=request_id or None,
            target=command,
            priority="normal",
            proposed_by="operator",
            human_reason=f"Operator command {command} queued.",
        )

    def _execute_command_impl(self, command: str, *, session_id: Optional[str], payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return self.command_registry.execute(command, self._command_context(session_id=session_id, payload=payload))

    def _get_dashboard_html(self) -> str:
        asset_html = self._load_hud_asset_text("dashboard.html")
        if asset_html:
            enabled = os.getenv("BLACKFONG_HUD_DEBUG_TELEMETRY", "").strip() == "1"
            flag = "true" if enabled else "false"
            endpoint = '"http://127.0.0.1:7779/ingest/1c6b9c23-4689-4a1f-b6cf-eb58789d9b6e"' if enabled else "null"
            telemetry_config = (
                f"<script>window.BLACKFONG_HUD_DEBUG_TELEMETRY = {flag};"
                f"window.BLACKFONG_HUD_DEBUG_TELEMETRY_URL = {endpoint};</script>"
            )
            return asset_html.replace("</head>", f"  {telemetry_config}\n</head>", 1)
        return self._get_dashboard_emergency_html()

    def _get_legion_html(self) -> str:
        asset_html = self._load_legion_asset_text("index.html")
        if asset_html:
            return asset_html
        return """
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Legion O.B.E.O.S</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#05070a;color:#f4f6f8;font-family:Segoe UI,Aptos,sans-serif}
.shell{max-width:720px;padding:28px;border:1px solid rgba(255,255,255,.08);border-radius:8px;background:#10151b}
h1{margin:0 0 10px;font-size:1.25rem}
p{line-height:1.5;color:#cfd6df}
code{display:block;margin-top:12px;padding:12px;border-radius:8px;background:#070a0d;color:#f6d487;overflow:auto}
</style></head><body>
<div class="shell">
  <h1>Legion cockpit assets unavailable</h1>
  <p>Restore the files under <code>blackfong_ecosystem/ui/legion/</code> and refresh this page.</p>
</div>
</body></html>
"""

    def _get_dashboard_emergency_html(self) -> str:
        return """
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Thoughtglass HUD Emergency Shell</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#05070a;color:#f4f6f8;font-family:Segoe UI,Aptos,sans-serif}
.shell{max-width:720px;padding:28px;border:1px solid rgba(255,255,255,.08);border-radius:18px;background:rgba(12,16,22,.92);box-shadow:0 20px 60px rgba(0,0,0,.35)}
h1{margin:0 0 10px;font-size:1.25rem}
p{line-height:1.5;color:#cfd6df}
code{display:block;margin-top:12px;padding:12px;border-radius:12px;background:#0b1017;color:#f6d487;overflow:auto}
</style></head><body>
<div class="shell">
  <h1>Thoughtglass HUD emergency shell</h1>
  <p>The asset-based browser cockpit bundle could not be loaded, so O.B.E.O.S. started a minimal fallback page instead of serving a second full dashboard implementation.</p>
  <p>Restore the asset files under <code>blackfong_ecosystem/ui/hud/</code> and refresh the browser to recover the full Thoughtglass HUD cockpit.</p>
</div>
</body></html>
"""


class SessionExplorer:
    """Compatibility wrapper plus replay buffer for tests and legacy callers."""

    def __init__(self, dashboard: WebDashboard):
        self.dashboard = dashboard
        self.dashboard.session_explorer = self
        self.session_workflows: Dict[str, List[Dict[str, Any]]] = {}
        self.event_replay_buffer: Dict[str, List[Dict[str, Any]]] = {}

    def track_session_event(self, session_id: str, event: Dict[str, Any]) -> None:
        if session_id not in self.dashboard.sessions:
            self.dashboard.sessions[session_id] = SessionInfo(session_id=session_id)
        session = self.dashboard.sessions[session_id]
        session.last_activity = time.time()
        session.total_requests += 1
        session.event_count += 1
        session.preview = str(event.get("response") or event.get("payload") or session.preview)[:120]
        if event.get("agent"):
            session.agents_used.add(str(event["agent"]))
        self.session_workflows.setdefault(session_id, []).append(dict(event))
        self.event_replay_buffer.setdefault(session_id, []).append(dict(event))
        if len(self.session_workflows[session_id]) > 1000:
            self.session_workflows[session_id] = self.session_workflows[session_id][-1000:]
        if len(self.event_replay_buffer[session_id]) > 1000:
            self.event_replay_buffer[session_id] = self.event_replay_buffer[session_id][-1000:]

    def cleanup_old_sessions(self, max_age_hours: int = 24) -> int:
        cutoff = time.time() - (max_age_hours * 60 * 60)
        expired = [sid for sid, session in self.dashboard.sessions.items() if session.last_activity < cutoff and session.status != "active"]
        for sid in expired:
            del self.dashboard.sessions[sid]
            self.session_workflows.pop(sid, None)
            self.event_replay_buffer.pop(sid, None)
        return len(expired)


class AdminControls:
    """Administrative endpoints and system configuration."""

    def __init__(self, dashboard: WebDashboard):
        self.dashboard = dashboard
        self.dashboard.admin_controls = self
        self.admin_sessions: Set[str] = set()
        self.system_config = {"maintenance_mode": False, "debug_logging": False, "max_sessions": 1000, "memory_cleanup_interval": 3600}
        self._setup_admin_routes()

    def _setup_admin_routes(self) -> None:
        @self.dashboard.app.route("/api/admin/config", methods=["GET", "POST"])
        def admin_config():
            if request.method == "POST":
                self.system_config.update(request.get_json(silent=True) or {})
                log_action(LogLevel.INFO, "admin", "config_updated", new_config=self.system_config)
            return jsonify(self.system_config)

        @self.dashboard.app.route("/api/admin/maintenance/start", methods=["POST"])
        def start_maintenance():
            self.system_config["maintenance_mode"] = True
            self.dashboard.add_alert({"type": "maintenance", "message": "System entered maintenance mode", "level": "warning"})
            log_action(LogLevel.WARNING, "admin", "maintenance_started")
            return jsonify({"status": "maintenance_started"})

        @self.dashboard.app.route("/api/admin/maintenance/stop", methods=["POST"])
        def stop_maintenance():
            self.system_config["maintenance_mode"] = False
            log_action(LogLevel.INFO, "admin", "maintenance_stopped")
            return jsonify({"status": "maintenance_stopped"})

        @self.dashboard.app.route("/api/admin/cleanup", methods=["POST"])
        def trigger_cleanup():
            expired_memory = memory_lifecycle_manager.cleanup_expired_items()
            expired_sessions = self.dashboard.session_explorer.cleanup_old_sessions() if self.dashboard.session_explorer is not None else 0
            return jsonify({"expired_memory_items": expired_memory, "expired_sessions": expired_sessions, "status": "cleanup_completed"})

        @self.dashboard.app.route("/api/admin/reset", methods=["POST"])
        def reset_system():
            decision_quality_controller.decision_history.clear()
            memory_lifecycle_manager.memory_store.clear()
            memory_lifecycle_manager.tier_storage.clear()
            memory_lifecycle_manager.stats = {"total_items": 0, "items_by_tier": {}, "items_by_type": {}, "summarized_traces": 0, "expired_items": 0, "tier_transitions": 0}
            log_action(LogLevel.CRITICAL, "admin", "system_reset_performed")
            return jsonify({"status": "system_reset_completed"})

    def is_maintenance_mode(self) -> bool:
        return bool(self.system_config.get("maintenance_mode", False))

    def get_system_status(self) -> Dict[str, Any]:
        self.dashboard.update_metrics()
        return {
            "maintenance_mode": self.is_maintenance_mode(),
            "config": self.system_config,
            "active_sessions": len(self.dashboard._get_sessions_from_store()),
            "total_memory_items": memory_lifecycle_manager.stats["total_items"],
            "decision_quality": decision_quality_controller.get_quality_metrics(),
            "timestamp": time.time(),
        }


web_dashboard = WebDashboard(host=hud_settings.host, port=hud_settings.port)
session_explorer = SessionExplorer(web_dashboard)
admin_controls = AdminControls(web_dashboard)
