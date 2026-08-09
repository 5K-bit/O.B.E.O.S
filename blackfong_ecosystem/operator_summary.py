from __future__ import annotations

from typing import Any, Dict, Optional

from .conversation_memory import ConversationMemoryService, conversation_memory_service
from .external_integrations import ExternalIntegrationRegistry
from .file_scope_service import FileScopeService
from .notification_service import NotificationService, notification_service
from .operator_actions import OperatorActionService
from .redamon_mode import RedAmonWorkflowService
from .system_awareness import SystemAwarenessService
from .tinyprogrammer_companion import TinyProgrammerCompanion, tinyprogrammer_companion
from .startup_coordinator import StartupCoordinator


class OperatorSummaryService:
    """Shared operator summary for HUD, TUI, and managed workspace shells."""

    def __init__(
        self,
        *,
        memory_service: Optional[ConversationMemoryService] = None,
        notification_service_instance: Optional[NotificationService] = None,
        operator_actions: Optional[OperatorActionService] = None,
        tinyprogrammer: Optional[TinyProgrammerCompanion] = None,
        file_scope_service: Optional[FileScopeService] = None,
        awareness_service: Optional[SystemAwarenessService] = None,
        startup_coordinator: Optional[StartupCoordinator] = None,
        integration_registry: Optional[ExternalIntegrationRegistry] = None,
        redamon_workflow: Optional[RedAmonWorkflowService] = None,
        memory_chamber: Any = None,
    ) -> None:
        self.memory_service = memory_service or conversation_memory_service
        self.notification_service = notification_service_instance or notification_service
        self.operator_actions = operator_actions or OperatorActionService(memory_service=self.memory_service)
        self.tinyprogrammer = tinyprogrammer or tinyprogrammer_companion
        self.file_scope_service = file_scope_service or FileScopeService(memory_service=self.memory_service)
        self.awareness_service = awareness_service
        self.startup_coordinator = startup_coordinator
        self.integration_registry = integration_registry or ExternalIntegrationRegistry()
        self.redamon_workflow = redamon_workflow or RedAmonWorkflowService(
            memory_service=self.memory_service,
            operator_actions=self.operator_actions,
            integration_registry=self.integration_registry,
        )
        self.memory_chamber = memory_chamber

    def build_summary(
        self,
        *,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        include_awareness: bool = True,
    ) -> Dict[str, Any]:
        sessions = self.memory_service.list_sessions(limit=8)
        current_session = next((item for item in sessions if item.get("session_id") == session_id), sessions[0] if sessions else None)
        notifications = self.notification_service.list_notifications(limit=8)
        actions = self.operator_actions.list_actions(limit=8, session_id=session_id)
        tiny_status = self.tinyprogrammer.get_status(require_stream=getattr(self.tinyprogrammer, "auto_stream", True))
        scopes = self.file_scope_service.list_roots()
        integration_listing = self.integration_registry.list_integrations()
        awareness_snapshot: Dict[str, Any] = {}
        if include_awareness and self.awareness_service is not None:
            awareness_snapshot = self.awareness_service.build_snapshot(emit=False)
        observed = awareness_snapshot.get("observed", {}) if awareness_snapshot else {}
        backend = observed.get("backend", {})
        sensors = observed.get("sensors", {}) if awareness_snapshot else {}
        gps = sensors.get("gps", {}) if isinstance(sensors.get("gps"), dict) else {}
        telegram = (observed.get("services", {}) or {}).get("telegram", {}) if awareness_snapshot else {}
        health = awareness_snapshot.get("health", {}) if awareness_snapshot else {}
        if self.startup_coordinator is not None:
            self.startup_coordinator.refresh_from_runtime(
                awareness_snapshot=awareness_snapshot,
                tiny_status=tiny_status,
                integration_listing=integration_listing,
            )
        startup = (
            awareness_snapshot.get("startup")
            or (self.startup_coordinator.snapshot() if self.startup_coordinator is not None else {})
        )
        return {
            "schema_version": "1.0",
            "session_id": session_id,
            "request_id": request_id,
            "doctrine": {
                "session_authority": "OBEOS owns the session.",
                "workspace_rule": "Desktop is a launched workspace.",
                "assistant": "D.A.I.S.E is the visible assistant.",
                "governance": "Blackfong adjudicates truth.",
            },
            "session": {
                "current": current_session,
                "recent_count": len(sessions),
                "recent": sessions[:5],
            },
            "notifications": {
                "unread_count": notifications.get("unread_count", 0),
                "items": notifications.get("notifications", [])[:5],
            },
            "actions": {
                "pending_actions": actions.get("pending_actions", 0),
                "priority": dict(actions.get("action_priority", {})),
                "items": actions.get("actions", [])[:5],
            },
            "tinyprogrammer": {
                "online": tiny_status.get("online"),
                "state": tiny_status.get("state") or tiny_status.get("mode"),
                "process": tiny_status.get("process", {}),
                "stream_reason": tiny_status.get("stream_reason"),
                "bbs_reason": tiny_status.get("bbs_reason"),
                "log_path": tiny_status.get("log_path"),
                "retry_count": tiny_status.get("retry_count"),
                "max_retries": tiny_status.get("max_retries"),
                "last_error_code": tiny_status.get("last_error_code"),
                "last_error_summary": tiny_status.get("last_error_summary"),
                "next_retry_at": tiny_status.get("next_retry_at"),
                "launch_attempts": tiny_status.get("launch_attempts"),
                "management_state": tiny_status.get("management_state"),
                "native_window_state": tiny_status.get("native_window_state"),
            },
            "startup": startup,
            "files": {
                "governed_roots": scopes.get("governed_roots", []),
                "approved_roots": scopes.get("approved_roots", []),
                "counts": scopes.get("counts", {}),
                "policy": scopes.get("policy", {}),
            },
            "integrations": {
                "counts": integration_listing.get("counts", {}),
                "items": integration_listing.get("integrations", [])[:4],
            },
            "gps": gps,
            "telegram": telegram,
            "security_modes": {
                "redamon": self.redamon_workflow.status(session_id=session_id),
            },
            "cognition_graph": self.memory_chamber.summary() if self.memory_chamber is not None else {},
            "system": {
                "health": health,
                "backend": backend,
                "sensors": sensors,
                "environment": awareness_snapshot.get("environment", {}),
            },
        }
