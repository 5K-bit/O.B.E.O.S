from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


SCHEMA_VERSION = "1.0"
READYLIKE_STATES = {"ready", "active", "manual_only"}
PROBLEM_STATES = {"degraded", "offline", "missing"}
WARMING_STATES = {"booting", "warming"}
OPTIONAL_WARMING_SUBSYSTEMS = frozenset({"tinyprogrammer"})
SUBSYSTEM_ORDER = [
    "blackfong_core",
    "daise",
    "session_memory",
    "pinned_memory",
    "black_box_archive",
    "operator_queue",
    "backend_model",
    "tinyprogrammer",
    "dream_layer",
    "sensors",
    "integrations",
]
SUBSYSTEM_LABELS = {
    "blackfong_core": "Blackfong Core",
    "daise": "D.A.I.S.E.",
    "session_memory": "Session Memory",
    "pinned_memory": "Pinned Memory",
    "black_box_archive": "Black Box Archive",
    "operator_queue": "Operator Queue",
    "backend_model": "Backend Model",
    "tinyprogrammer": "TinyProgrammer",
    "dream_layer": "Dream Layer",
    "sensors": "Sensors",
    "integrations": "Integrations",
}


@dataclass
class StartupSubsystem:
    key: str
    label: str
    state: str = "booting"
    summary: str = ""
    reason_code: Optional[str] = None
    human_reason: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    retry_count: int = 0
    max_retries: int = 0
    next_retry_at: Optional[float] = None
    launch_attempts: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state,
            "summary": self.summary,
            "reason_code": self.reason_code,
            "human_reason": self.human_reason,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "next_retry_at": self.next_retry_at,
            "launch_attempts": self.launch_attempts,
            "meta": dict(self.meta),
        }


class StartupCoordinator:
    """Shared startup/readiness truth for HUD, workspace, and future surfaces."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._boot_session_id = ""
        self._started_at = 0.0
        self._updated_at = 0.0
        self._surface = "workspace"
        self._subsystems: Dict[str, StartupSubsystem] = {}
        self.begin_boot(surface="workspace")

    def begin_boot(self, *, surface: str = "workspace") -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            self._boot_session_id = uuid.uuid4().hex[:12]
            self._started_at = now
            self._updated_at = now
            self._surface = surface
            self._subsystems = {
                "blackfong_core": StartupSubsystem(
                    key="blackfong_core",
                    label=SUBSYSTEM_LABELS["blackfong_core"],
                    state="booting",
                    summary="Restoring operator runtime and workspace state.",
                ),
                "daise": StartupSubsystem(
                    key="daise",
                    label=SUBSYSTEM_LABELS["daise"],
                    state="warming",
                    summary="Bringing D.A.I.S.E. online for local operator chat.",
                ),
                "session_memory": StartupSubsystem(
                    key="session_memory",
                    label=SUBSYSTEM_LABELS["session_memory"],
                    state="warming",
                    summary="Loading recent session context and traces.",
                ),
                "pinned_memory": StartupSubsystem(
                    key="pinned_memory",
                    label=SUBSYSTEM_LABELS["pinned_memory"],
                    state="warming",
                    summary="Loading durable memory and promoted operator notes.",
                ),
                "black_box_archive": StartupSubsystem(
                    key="black_box_archive",
                    label=SUBSYSTEM_LABELS["black_box_archive"],
                    state="warming",
                    summary="Loading archive capsule visibility.",
                ),
                "operator_queue": StartupSubsystem(
                    key="operator_queue",
                    label=SUBSYSTEM_LABELS["operator_queue"],
                    state="warming",
                    summary="Scanning queued actions and unresolved requests.",
                ),
                "backend_model": StartupSubsystem(
                    key="backend_model",
                    label=SUBSYSTEM_LABELS["backend_model"],
                    state="booting",
                    summary="Checking local model backend reachability.",
                ),
                "tinyprogrammer": StartupSubsystem(
                    key="tinyprogrammer",
                    label=SUBSYSTEM_LABELS["tinyprogrammer"],
                    state="booting",
                    summary="Preparing TinyProgrammer companion startup.",
                    max_retries=3,
                ),
                "dream_layer": StartupSubsystem(
                    key="dream_layer",
                    label=SUBSYSTEM_LABELS["dream_layer"],
                    state="warming",
                    summary="Loading cognition and creative context overlays.",
                ),
                "sensors": StartupSubsystem(
                    key="sensors",
                    label=SUBSYSTEM_LABELS["sensors"],
                    state="manual_only",
                    summary="Sensors are visible, but live capture stays operator-enabled only.",
                    reason_code="manual_only",
                    human_reason="Microphone and camera remain manual-only until explicitly enabled.",
                ),
                "integrations": StartupSubsystem(
                    key="integrations",
                    label=SUBSYSTEM_LABELS["integrations"],
                    state="warming",
                    summary="Discovering optional tacklebox integrations.",
                ),
            }
            return self.snapshot()

    def set_subsystem(
        self,
        key: str,
        *,
        state: str,
        summary: str,
        reason_code: Optional[str] = None,
        human_reason: Optional[str] = None,
        retry_count: Optional[int] = None,
        max_retries: Optional[int] = None,
        next_retry_at: Optional[float] = None,
        launch_attempts: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            subsystem = self._subsystems.get(key)
            if subsystem is None:
                subsystem = StartupSubsystem(key=key, label=SUBSYSTEM_LABELS.get(key, key.replace("_", " ").title()))
                self._subsystems[key] = subsystem
            subsystem.state = state
            subsystem.summary = summary
            subsystem.reason_code = reason_code
            subsystem.human_reason = human_reason
            subsystem.updated_at = now
            if retry_count is not None:
                subsystem.retry_count = retry_count
            if max_retries is not None:
                subsystem.max_retries = max_retries
            if next_retry_at is not None or next_retry_at is None:
                subsystem.next_retry_at = next_retry_at
            if launch_attempts is not None:
                subsystem.launch_attempts = launch_attempts
            if meta is not None:
                subsystem.meta = dict(meta)
            self._updated_at = now
            return subsystem.to_dict()

    def refresh_from_runtime(
        self,
        *,
        awareness_snapshot: Optional[Dict[str, Any]] = None,
        tiny_status: Optional[Dict[str, Any]] = None,
        integration_listing: Optional[Dict[str, Any]] = None,
        current_request_active: bool = False,
    ) -> Dict[str, Any]:
        awareness_snapshot = awareness_snapshot or {}
        observed = awareness_snapshot.get("observed", {})
        health = awareness_snapshot.get("health", {})
        memory = observed.get("memory", {})
        backend = observed.get("backend", {})
        queue = observed.get("queue", {})
        sensors = awareness_snapshot.get("sensors", {})

        backend_state = self._backend_state(backend)
        backend_reason = backend.get("error")
        self.set_subsystem(
            "blackfong_core",
            state="active" if current_request_active or queue.get("pending_requests", 0) else "ready",
            summary="Workspace runtime is interactive and session authority is held by Blackfong.",
            meta={"queue_depth": int(queue.get("pending_requests", 0))},
        )
        self.set_subsystem(
            "backend_model",
            state=backend_state,
            summary=self._backend_summary(backend),
            reason_code=self._backend_reason_code(backend),
            human_reason=backend_reason or self._backend_summary(backend),
            meta={"provider": backend.get("provider"), "model": backend.get("configured_model")},
        )
        self.set_subsystem(
            "daise",
            state="active" if current_request_active else ("degraded" if backend_state in PROBLEM_STATES else "ready"),
            summary=(
                "D.A.I.S.E. is routing or answering an active request."
                if current_request_active
                else "D.A.I.S.E. is available for operator chat."
            ),
            reason_code="backend_degraded" if backend_state in PROBLEM_STATES else None,
            human_reason=(
                "The preferred local model is degraded, so D.A.I.S.E. is available with reduced confidence."
                if backend_state in PROBLEM_STATES
                else None
            ),
        )
        memory_available = memory.get("status", "available") == "available"
        self.set_subsystem(
            "session_memory",
            state="ready" if memory_available else "degraded",
            summary="Recent sessions and request traces are available." if memory_available else "Session memory is degraded.",
            reason_code=None if memory_available else "memory_degraded",
        )
        self.set_subsystem(
            "pinned_memory",
            state="ready" if memory_available else "degraded",
            summary=(
                f"{int(memory.get('pinned_memories', 0))} pinned memories available."
                if memory_available
                else "Pinned memory is degraded."
            ),
            reason_code=None if memory_available else "memory_degraded",
            meta={"count": int(memory.get("pinned_memories", 0))},
        )
        self.set_subsystem(
            "black_box_archive",
            state="ready" if memory_available else "degraded",
            summary="Archive and memory capsule visibility is available." if memory_available else "Archive visibility is degraded.",
            reason_code=None if memory_available else "archive_degraded",
        )
        pending_actions = int(queue.get("pending_requests", 0))
        self.set_subsystem(
            "operator_queue",
            state="active" if pending_actions else "ready",
            summary=f"{pending_actions} request(s) pending." if pending_actions else "Operator queue is clear.",
            meta={"pending_requests": pending_actions},
        )
        tiny_state = self._tiny_startup_state(tiny_status or {})
        self.set_subsystem(
            "tinyprogrammer",
            state=tiny_state["state"],
            summary=tiny_state["summary"],
            reason_code=tiny_state.get("reason_code"),
            human_reason=tiny_state.get("human_reason"),
            retry_count=int((tiny_status or {}).get("retry_count") or 0),
            max_retries=int((tiny_status or {}).get("max_retries") or 3),
            next_retry_at=(tiny_status or {}).get("next_retry_at"),
            launch_attempts=int((tiny_status or {}).get("launch_attempts") or 0),
            meta={
                "management_state": (tiny_status or {}).get("management_state"),
                "stream_state": (tiny_status or {}).get("stream_load_state"),
            },
        )
        self.set_subsystem(
            "dream_layer",
            state="ready",
            summary="Cognition overlays and dream-layer state are loaded into Thoughtglass.",
        )
        sensor_reason = "Sensors are visible and policy-gated until live mode is explicitly enabled."
        if self._sensor_active(sensors):
            self.set_subsystem(
                "sensors",
                state="active",
                summary="One or more sensors are active with explicit operator intent.",
                reason_code="sensor_mode_live",
                human_reason=sensor_reason,
            )
        else:
            self.set_subsystem(
                "sensors",
                state="manual_only",
                summary="Sensors are available but not actively capturing.",
                reason_code="manual_only",
                human_reason=sensor_reason,
            )
        integration_listing = integration_listing or {}
        available_integrations = int(((integration_listing.get("counts") or {}).get("available")) or 0)
        self.set_subsystem(
            "integrations",
            state="ready" if available_integrations else "missing",
            summary=(
                f"{available_integrations} optional integration(s) available."
                if available_integrations
                else "No optional tacklebox integrations are currently available."
            ),
            reason_code=None if available_integrations else "integrations_missing",
        )
        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            subsystems = {key: subsystem.to_dict() for key, subsystem in self._subsystems.items()}
            states = [entry["state"] for entry in subsystems.values()]
            overall_state = self._overall_state(subsystems)
            degraded = any(state in PROBLEM_STATES for state in states)
            return {
                "schema_version": SCHEMA_VERSION,
                "boot_session_id": self._boot_session_id,
                "surface": self._surface,
                "canonical_shell": "pyside6_workspace",
                "home_surface": "thoughtglass",
                "started_at": self._started_at,
                "updated_at": self._updated_at,
                "overall_state": overall_state,
                "ready_but_degraded": overall_state == "ready" and degraded,
                "counts": {
                    "booting": sum(1 for state in states if state == "booting"),
                    "warming": sum(1 for state in states if state == "warming"),
                    "ready": sum(1 for state in states if state == "ready"),
                    "active": sum(1 for state in states if state == "active"),
                    "degraded": sum(1 for state in states if state == "degraded"),
                    "offline": sum(1 for state in states if state == "offline"),
                    "manual_only": sum(1 for state in states if state == "manual_only"),
                    "missing": sum(1 for state in states if state == "missing"),
                },
                "subsystem_order": list(SUBSYSTEM_ORDER),
                "subsystems": subsystems,
            }

    @staticmethod
    def _overall_state(subsystems: Dict[str, Any]) -> str:
        blocking_states = [
            str((subsystems.get(key) or {}).get("state") or "booting")
            for key in SUBSYSTEM_ORDER
            if key in subsystems and key not in OPTIONAL_WARMING_SUBSYSTEMS
        ]
        if any(state in WARMING_STATES for state in blocking_states):
            return "warming"
        states = [str((subsystems.get(key) or {}).get("state") or "booting") for key in SUBSYSTEM_ORDER if key in subsystems]
        if any(state == "active" for state in states):
            return "ready"
        if any(state in PROBLEM_STATES for state in states):
            return "ready"
        return "ready"

    @staticmethod
    def _backend_state(backend: Dict[str, Any]) -> str:
        status = str(backend.get("status") or "unknown").lower()
        if status == "available":
            return "ready"
        if status in {"degraded", "unavailable"}:
            return "degraded"
        if status in {"unknown", "bootstrap", "warming"}:
            return "warming"
        return "warming"

    @staticmethod
    def _backend_reason_code(backend: Dict[str, Any]) -> Optional[str]:
        status = str(backend.get("status") or "unknown").lower()
        if status == "degraded":
            return "backend_degraded"
        if status == "unavailable":
            return "backend_unavailable"
        return None

    @staticmethod
    def _backend_summary(backend: Dict[str, Any]) -> str:
        provider = backend.get("provider") or "backend"
        model = backend.get("configured_model") or "unknown model"
        status = backend.get("status") or "unknown"
        return f"{provider} / {model} is {status}."

    @staticmethod
    def _sensor_active(sensors: Dict[str, Any]) -> bool:
        for sensor in ("microphone", "camera"):
            if (sensors.get(sensor) or {}).get("mode_enabled"):
                return True
        return False

    @staticmethod
    def _tiny_startup_state(tiny_status: Dict[str, Any]) -> Dict[str, Any]:
        if not tiny_status:
            return {
                "state": "booting",
                "summary": "Preparing TinyProgrammer startup.",
            }
        if tiny_status.get("online"):
            return {
                "state": "active",
                "summary": "TinyProgrammer is online and visible as a managed companion.",
                "reason_code": None,
                "human_reason": tiny_status.get("stream_human_reason"),
            }
        management_state = str(tiny_status.get("management_state") or "").lower()
        retry_count = int(tiny_status.get("retry_count") or 0)
        max_retries = int(tiny_status.get("max_retries") or 3)
        reason_code = tiny_status.get("last_error_code") or tiny_status.get("stream_reason")
        human_reason = tiny_status.get("last_error_summary") or tiny_status.get("stream_human_reason")
        if management_state == "launching":
            return {
                "state": "warming",
                "summary": "TinyProgrammer is launching in the background.",
                "reason_code": "launching",
                "human_reason": human_reason,
            }
        if retry_count >= max_retries and reason_code:
            return {
                "state": "degraded",
                "summary": "TinyProgrammer exhausted its bounded startup retries and is waiting for manual retry.",
                "reason_code": reason_code,
                "human_reason": human_reason,
            }
        if retry_count > 0:
            return {
                "state": "warming",
                "summary": f"TinyProgrammer is retrying startup ({retry_count}/{max_retries}).",
                "reason_code": reason_code,
                "human_reason": human_reason,
            }
        if management_state == "error":
            return {
                "state": "degraded",
                "summary": "TinyProgrammer failed to launch cleanly.",
                "reason_code": reason_code or "managed_launch_failed",
                "human_reason": human_reason,
            }
        return {
            "state": "booting",
            "summary": "TinyProgrammer has not completed startup yet.",
            "reason_code": reason_code,
            "human_reason": human_reason,
        }
