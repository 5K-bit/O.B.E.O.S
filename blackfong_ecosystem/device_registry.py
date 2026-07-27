from __future__ import annotations

import hmac
import json
import platform
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .config import legion_settings, paths
from .events import Event
from .storage import EventStore


SCHEMA_VERSION = "1.0"
LEGION_SESSION_COOKIE = "blackfong_legion_session"


def _now() -> float:
    return time.time()


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(item) for item in value]
        return str(value)


def _bounded_history(items: list[Dict[str, Any]], limit: int) -> list[Dict[str, Any]]:
    if limit <= 0:
        return []
    return items[-limit:]


def _default_state() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "devices": {},
        "paired_clients": {},
        "created_at": _now(),
        "updated_at": _now(),
    }


def _default_devices() -> Dict[str, Dict[str, Any]]:
    now = _now()
    return {
        "blackcomputer": {
            "id": "blackcomputer",
            "label": "Blackcomputer",
            "role": "obeos_hub",
            "endpoint": "local://blackcomputer",
            "status": "unknown",
            "health": {},
            "sensors": {},
            "services": {},
            "capabilities": ["obeos_hud", "daise", "awareness", "diagnostics"],
            "last_seen": None,
            "last_report": {},
            "history": [],
            "provenance": {"source": "blackfong", "origin_service": "device_registry"},
            "created_at": now,
            "updated_at": now,
        },
        "sentinel": {
            "id": "sentinel",
            "label": "Sentinel",
            "role": "raspberry_pi_sentinel",
            "endpoint": "unconfigured://sentinel",
            "status": "offline",
            "health": {},
            "sensors": {
                "camera": {"available": False, "active": False},
                "microphone": {"available": False, "active": False},
                "presence": {"available": False, "active": False},
            },
            "services": {},
            "capabilities": ["system_health", "sensor_readiness", "sentinel_events"],
            "last_seen": None,
            "last_report": {},
            "history": [],
            "provenance": {"source": "sentinel", "origin_service": "device_registry"},
            "created_at": now,
            "updated_at": now,
        },
        "frame": {
            "id": "frame",
            "label": "Brilliant Frame",
            "role": "wearable_frame",
            "endpoint": "ble://brilliant-frame",
            "status": "offline",
            "health": {},
            "sensors": {
                "display": {"available": True, "active": True},
                "tap": {"available": True, "active": True},
                "imu": {"available": True, "active": True},
                "camera": {"available": True, "active": False},
                "microphone": {"available": True, "active": False},
                "bluetooth": {"available": True, "active": False},
            },
            "services": {},
            "capabilities": [
                "frame_display",
                "frame_taps",
                "frame_imu",
                "tap_to_talk",
                "look_and_ask",
                "thoughtglass_gestures",
            ],
            "last_seen": None,
            "last_report": {},
            "history": [],
            "provenance": {"source": "frame", "origin_service": "device_registry"},
            "created_at": now,
            "updated_at": now,
        },
    }


class DeviceRegistryService:
    """JSON-backed registry for local O.B.E.O.S device cockpit state."""

    def __init__(
        self,
        *,
        state_path: Optional[Path] = None,
        event_store: Optional[EventStore] = None,
        history_limit: Optional[int] = None,
        stale_after_seconds: Optional[float] = None,
    ) -> None:
        self.state_path = Path(state_path or (paths.data_root / "device_registry.json"))
        self.event_store = event_store or EventStore()
        self.history_limit = int(history_limit if history_limit is not None else legion_settings.device_history_limit)
        self.stale_after_seconds = float(
            stale_after_seconds if stale_after_seconds is not None else legion_settings.device_stale_after_seconds
        )
        self._lock = threading.RLock()

    def ensure_defaults(self) -> Dict[str, Any]:
        with self._lock:
            state = self._load_unlocked()
            defaults = _default_devices()
            for device_id, device in defaults.items():
                state["devices"].setdefault(device_id, device)
            state["updated_at"] = _now()
            self._save_unlocked(state)
            return state

    def list_devices(self, *, include_history: bool = False) -> Dict[str, Any]:
        with self._lock:
            state = self.ensure_defaults()
            devices = [
                self._public_device(device, include_history=include_history)
                for device in state.get("devices", {}).values()
            ]
            devices.sort(key=lambda item: (item.get("role") != "obeos_hub", item.get("label", "")))
            return {
                "schema_version": SCHEMA_VERSION,
                "devices": devices,
                "paired_client_count": len(self.active_paired_clients()),
                "updated_at": state.get("updated_at"),
            }

    def get_device(self, device_id: str, *, include_history: bool = True) -> Dict[str, Any]:
        normalized = self._normalize_device_id(device_id)
        with self._lock:
            state = self.ensure_defaults()
            device = state.get("devices", {}).get(normalized)
            if not device:
                return {
                    "schema_version": SCHEMA_VERSION,
                    "id": normalized,
                    "status": "unknown",
                    "error": "device_not_registered",
                }
            return self._public_device(device, include_history=include_history)

    def update_blackcomputer(
        self,
        *,
        metrics: Dict[str, Any],
        awareness: Dict[str, Any],
        performance: Dict[str, Any],
        environment: Dict[str, Any],
        endpoint: str,
    ) -> Dict[str, Any]:
        report = {
            "device_id": "blackcomputer",
            "label": "Blackcomputer",
            "role": "obeos_hub",
            "endpoint": endpoint,
            "status": "online",
            "health": {
                "overall": (awareness.get("health") or {}).get("overall", metrics.get("system_health", "unknown")),
                "system_health": metrics.get("system_health", "unknown"),
                "memory_usage_mb": metrics.get("memory_usage_mb", 0.0),
                "avg_response_time": metrics.get("avg_response_time", 0.0),
                "error_rate": metrics.get("error_rate", 0.0),
                "backend": ((environment.get("backend") or {}).get("status") or (performance.get("backend") or {}).get("status")),
            },
            "sensors": awareness.get("sensors") or {},
            "services": (environment.get("services") or {}),
            "capabilities": ["obeos_hud", "daise", "awareness", "diagnostics", "legion_pwa"],
            "telemetry": {
                "metrics": metrics,
                "diagnostic_summary": awareness.get("diagnostic_summary") or performance.get("diagnostic_summary") or {},
                "queue": performance.get("queue") or {},
                "environment": environment.get("environment") or {},
            },
            "provenance": {"source": "blackfong", "origin_service": "web_dashboard"},
        }
        return self.record_report("blackcomputer", report, publish_events=False)

    def record_report(
        self,
        device_id: str,
        report: Dict[str, Any],
        *,
        publish_events: bool = True,
    ) -> Dict[str, Any]:
        normalized = self._normalize_device_id(device_id)
        now = _now()
        report = dict(report or {})
        with self._lock:
            state = self.ensure_defaults()
            device = dict(state.get("devices", {}).get(normalized) or {})
            device.setdefault("id", normalized)
            device["id"] = normalized
            device["label"] = str(report.get("label") or device.get("label") or normalized.title())
            device["role"] = str(report.get("role") or device.get("role") or "device")
            device["endpoint"] = str(report.get("endpoint") or device.get("endpoint") or "")
            device["status"] = str(report.get("status") or "online")
            device["health"] = _json_safe(report.get("health") or {})
            device["sensors"] = _json_safe(report.get("sensors") or device.get("sensors") or {})
            device["services"] = _json_safe(report.get("services") or device.get("services") or {})
            device["capabilities"] = list(report.get("capabilities") or device.get("capabilities") or [])
            device["telemetry"] = _json_safe(report.get("telemetry") or {})
            device["last_seen"] = float(report.get("last_seen") or now)
            device["last_report"] = _json_safe(report)
            device["provenance"] = _json_safe(
                report.get("provenance")
                or device.get("provenance")
                or {"source": "system", "origin_service": "device_registry"}
            )
            device["updated_at"] = now
            device.setdefault("created_at", now)
            history_entry = {
                "timestamp": now,
                "status": device["status"],
                "health": device["health"],
                "sensors": device["sensors"],
                "source": device["provenance"].get("source", "device"),
            }
            device["history"] = _bounded_history(list(device.get("history") or []) + [history_entry], self.history_limit)
            state["devices"][normalized] = device
            state["updated_at"] = now
            self._save_unlocked(state)

        if publish_events:
            self._publish_report_events(normalized, device)
        return self._public_device(device, include_history=True)

    def refresh_device(self, device_id: str) -> Dict[str, Any]:
        return {"status": "ok", "device": self.get_device(device_id, include_history=True), "refreshed_at": _now()}

    def diagnose_device(self, device_id: str) -> Dict[str, Any]:
        device = self.get_device(device_id, include_history=True)
        health = device.get("health") or {}
        sensors = device.get("sensors") or {}
        recommendations: list[str] = []
        status = device.get("status", "unknown")
        if status in {"offline", "stale"}:
            recommendations.append("Start the device agent or confirm the LAN endpoint is reachable.")
        if str(health.get("overall", "")).lower() in {"degraded", "warning", "error"}:
            recommendations.append("Inspect the latest health snapshot and recent events.")
        inactive_sensors = [
            name for name, sensor in sensors.items()
            if isinstance(sensor, dict) and sensor.get("available") and not sensor.get("active")
        ]
        if inactive_sensors:
            recommendations.append(f"Available sensors are idle: {', '.join(sorted(inactive_sensors))}.")
        if not recommendations:
            recommendations.append("No immediate action required.")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "device_id": device.get("id", device_id),
            "device_status": status,
            "recommendations": recommendations,
            "history_count": len(device.get("history") or []),
            "diagnosed_at": _now(),
        }

    def set_sensor_mode(
        self,
        device_id: str,
        *,
        active: bool,
        mode: str,
        consent: Optional[Dict[str, Any]] = None,
        requested_by: str = "legion",
    ) -> Dict[str, Any]:
        consent = dict(consent or {})
        if not consent.get("approved"):
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "requires_consent",
                "device_id": self._normalize_device_id(device_id),
                "required_consent": {"approved": True, "level": 1},
            }
        normalized = self._normalize_device_id(device_id)
        now = _now()
        with self._lock:
            state = self.ensure_defaults()
            device = dict(state["devices"].get(normalized) or _default_devices().get(normalized) or {"id": normalized})
            sensors = dict(device.get("sensors") or {})
            target_sensor = str(mode or "live_vision").strip().lower() or "live_vision"
            if target_sensor in {"live_vision", "camera", "photo", "vision_snapshot", "camera_snapshot", "look_and_ask"}:
                sensor_key = "camera"
            elif target_sensor in {"audio", "microphone", "live_audio", "tap_to_talk", "audio_stream"}:
                sensor_key = "microphone"
            elif target_sensor in {"imu", "motion", "gesture", "gestures"}:
                sensor_key = "imu"
            elif target_sensor in {"tap", "taps"}:
                sensor_key = "tap"
            else:
                sensor_key = "presence"
            sensor_state = dict(sensors.get(sensor_key) or {})
            sensor_state.setdefault("available", True)
            sensor_state["active"] = bool(active)
            sensor_state["mode"] = target_sensor if active else "off"
            sensor_state["updated_at"] = now
            sensors[sensor_key] = sensor_state
            device["sensors"] = sensors
            device["sensor_mode"] = target_sensor if active else "off"
            device["updated_at"] = now
            history_entry = {
                "timestamp": now,
                "status": device.get("status", "unknown"),
                "sensor_mode": device["sensor_mode"],
                "sensors": sensors,
                "source": requested_by,
            }
            device["history"] = _bounded_history(list(device.get("history") or []) + [history_entry], self.history_limit)
            state["devices"][normalized] = device
            state["updated_at"] = now
            self._save_unlocked(state)

        self._publish_sensor_events(
            device_id=normalized,
            active=active,
            mode=target_sensor,
            consent=consent,
            requested_by=requested_by,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "device_id": normalized,
            "sensor_mode": target_sensor if active else "off",
            "device": self.get_device(normalized, include_history=True),
        }

    def register_pairing(
        self,
        *,
        session_id: str,
        client_id: str,
        label: str,
        user_agent: str,
        expires_at: float,
    ) -> Dict[str, Any]:
        now = _now()
        client = {
            "session_id": session_id,
            "client_id": client_id,
            "label": label,
            "user_agent": user_agent,
            "paired_at": now,
            "last_seen": now,
            "expires_at": expires_at,
            "role": "legion_client",
        }
        with self._lock:
            state = self.ensure_defaults()
            state.setdefault("paired_clients", {})[session_id] = client
            state["updated_at"] = now
            self._save_unlocked(state)
        self._publish_operator_event(
            "operator_action_result",
            {
                "schema_version": SCHEMA_VERSION,
                "action": "legion_pair",
                "status": "completed",
                "client_id": client_id,
                "label": label,
                "paired_at": now,
                "expires_at": expires_at,
            },
            tags=["legion", "pairing"],
        )
        return client

    def touch_pairing(self, session_id: str) -> Optional[Dict[str, Any]]:
        now = _now()
        with self._lock:
            state = self.ensure_defaults()
            client = state.get("paired_clients", {}).get(session_id)
            if not client:
                return None
            if float(client.get("expires_at") or 0.0) < now:
                state.get("paired_clients", {}).pop(session_id, None)
                self._save_unlocked(state)
                return None
            client["last_seen"] = now
            state["paired_clients"][session_id] = client
            state["updated_at"] = now
            self._save_unlocked(state)
            return dict(client)

    def active_paired_clients(self) -> list[Dict[str, Any]]:
        now = _now()
        with self._lock:
            state = self.ensure_defaults()
            clients = []
            changed = False
            for session_id, client in list(state.get("paired_clients", {}).items()):
                if float(client.get("expires_at") or 0.0) < now:
                    state["paired_clients"].pop(session_id, None)
                    changed = True
                    continue
                clients.append(dict(client))
            if changed:
                state["updated_at"] = now
                self._save_unlocked(state)
            clients.sort(key=lambda item: float(item.get("last_seen") or 0.0), reverse=True)
            return clients

    def _public_device(self, device: Dict[str, Any], *, include_history: bool) -> Dict[str, Any]:
        public = dict(device)
        last_seen = public.get("last_seen")
        if public.get("id") == "blackcomputer" and last_seen:
            public["status"] = "online"
        elif last_seen is None:
            public["status"] = public.get("status") or "offline"
        elif _now() - float(last_seen) > self.stale_after_seconds:
            public["status"] = "stale"
        public["freshness_seconds"] = None if last_seen is None else round(max(0.0, _now() - float(last_seen)), 1)
        if not include_history:
            public.pop("history", None)
        return _json_safe(public)

    def _publish_report_events(self, device_id: str, device: Dict[str, Any]) -> None:
        if self.event_store is None:
            return
        self.event_store.init_db()
        source = "sentinel" if device_id == "sentinel" else "frame" if device_id == "frame" else "system"
        payload = {
            "schema_version": SCHEMA_VERSION,
            "device_id": device_id,
            "label": device.get("label"),
            "role": device.get("role"),
            "status": device.get("status"),
            "health": device.get("health") or {},
            "telemetry": device.get("telemetry") or {},
            "provenance": device.get("provenance") or {},
        }
        self.event_store.add_event(
            Event.new(
                source=source,  # type: ignore[arg-type]
                type="environment_snapshot",
                payload=payload,
                confidence=0.9,
                tags=["device", device_id, "heartbeat"],
            )
        )
        if device.get("sensors"):
            self.event_store.add_event(
                Event.new(
                    source=source,  # type: ignore[arg-type]
                    type="sensor_capability_snapshot",
                    payload={
                        "schema_version": SCHEMA_VERSION,
                        "device_id": device_id,
                        "sensors": device.get("sensors") or {},
                        "provenance": device.get("provenance") or {},
                    },
                    confidence=0.86,
                    tags=["device", device_id, "sensors"],
                )
            )

    def _publish_sensor_events(
        self,
        *,
        device_id: str,
        active: bool,
        mode: str,
        consent: Dict[str, Any],
        requested_by: str,
    ) -> None:
        self._publish_operator_event(
            "operator_consent",
            {
                "schema_version": SCHEMA_VERSION,
                "action": "device_sensor_start" if active else "device_sensor_stop",
                "target": device_id,
                "mode": mode,
                "consent": consent,
                "requested_by": requested_by,
                "approved": True,
            },
            tags=["legion", "device", device_id, "sensors"],
        )
        self._publish_operator_event(
            "operator_action_result",
            {
                "schema_version": SCHEMA_VERSION,
                "action": "device_sensor_start" if active else "device_sensor_stop",
                "target": device_id,
                "mode": mode,
                "status": "completed",
                "requested_by": requested_by,
            },
            tags=["legion", "device", device_id, "sensors"],
        )
        if device_id == "sentinel":
            self.event_store.init_db()
            self.event_store.add_event(
                Event.new(
                    source="sentinel",
                    type="sentinel_detection_event",
                    payload={
                        "schema_version": SCHEMA_VERSION,
                        "modality": "control",
                        "detector": "legion_dashboard",
                        "detection": {"label": "sensor_mode_started" if active else "sensor_mode_stopped", "mode": mode},
                        "capture_profile": "metadata_only",
                        "observed_at": _now(),
                        "provenance": {
                            "source": "sentinel",
                            "origin_service": "legion_device_registry",
                            "collection_method": "operator_control",
                            "schema_version": SCHEMA_VERSION,
                        },
                    },
                    confidence=0.82,
                    tags=["sentinel", "control", "sensors"],
                )
            )

    def _publish_operator_event(self, event_type: str, payload: Dict[str, Any], *, tags: list[str]) -> None:
        if self.event_store is None:
            return
        self.event_store.init_db()
        self.event_store.add_event(
            Event.new(
                source="blackfong",
                type=event_type,
                payload=payload,
                confidence=0.9,
                tags=tags,
            )
        )

    def _load_unlocked(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            state = _default_state()
            self._save_unlocked(state)
            return state
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            data = _default_state()
        if not isinstance(data, dict):
            data = _default_state()
        data.setdefault("schema_version", SCHEMA_VERSION)
        data.setdefault("devices", {})
        data.setdefault("paired_clients", {})
        return data

    def _save_unlocked(self, state: Dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(_json_safe(state), indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _normalize_device_id(device_id: str) -> str:
        normalized = str(device_id or "").strip().lower().replace(" ", "_")
        return normalized or "unknown"


class LegionAuthService:
    """Pairing-token auth for the local Legion PWA."""

    def __init__(
        self,
        registry: DeviceRegistryService,
        *,
        token_path: Optional[Path] = None,
        session_ttl_hours: Optional[int] = None,
    ) -> None:
        self.registry = registry
        self.token_path = Path(token_path or (paths.data_root / "legion_pairing_token.txt"))
        self.session_ttl_hours = int(session_ttl_hours if session_ttl_hours is not None else legion_settings.session_ttl_hours)

    def current_pairing_token(self, *, create: bool = True, rotate: bool = False) -> str:
        if legion_settings.pairing_token and not rotate:
            return legion_settings.pairing_token
        if self.token_path.exists() and not rotate:
            token = self.token_path.read_text(encoding="utf-8").strip()
            if token:
                return token
        if not create:
            return ""
        token = secrets.token_urlsafe(18)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(token + "\n", encoding="utf-8")
        return token

    def pair(self, token: str, *, label: str = "Legion", user_agent: str = "") -> Dict[str, Any]:
        expected = self.current_pairing_token(create=True)
        if not expected or not hmac.compare_digest(str(token or "").strip(), expected):
            return {"status": "rejected", "reason_code": "invalid_pairing_token"}
        session_id = secrets.token_urlsafe(32)
        client_id = secrets.token_hex(8)
        expires_at = _now() + max(1, self.session_ttl_hours) * 3600
        client = self.registry.register_pairing(
            session_id=session_id,
            client_id=client_id,
            label=label or "Legion",
            user_agent=user_agent,
            expires_at=expires_at,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "paired",
            "session_id": session_id,
            "client": client,
            "expires_at": expires_at,
            "ttl_hours": self.session_ttl_hours,
        }

    def validate_session(self, session_id: str | None) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        return self.registry.touch_pairing(str(session_id))

    def expected_agent_token(self) -> str:
        return legion_settings.sentinel_agent_token or self.current_pairing_token(create=True)

    def validate_agent_token(self, token: str | None) -> bool:
        expected = self.expected_agent_token()
        return bool(expected) and hmac.compare_digest(str(token or "").strip(), expected)


def local_device_report(*, device_id: str = "sentinel", label: str = "Sentinel") -> Dict[str, Any]:
    """Collect a lightweight local host report for the Sentinel agent."""

    try:
        import psutil
    except ImportError:  # pragma: no cover - Pi setup installs psutil
        psutil = None  # type: ignore[assignment]
    now = _now()
    health: Dict[str, Any] = {"overall": "unknown"}
    telemetry: Dict[str, Any] = {"platform": platform.platform(), "hostname": socket.gethostname()}
    sensors: Dict[str, Any] = {
        "camera": {"available": False, "active": False},
        "microphone": {"available": False, "active": False},
        "presence": {"available": True, "active": False},
    }
    if psutil is not None:
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        temp_c = None
        try:
            temps = psutil.sensors_temperatures(fahrenheit=False)
            for entries in temps.values():
                for entry in entries:
                    if entry.current is not None:
                        temp_c = float(entry.current)
                        break
                if temp_c is not None:
                    break
        except Exception:
            temp_c = None
        health.update(
            {
                "overall": "healthy",
                "cpu_percent": float(psutil.cpu_percent(interval=0.1)),
                "ram_percent": float(vm.percent),
                "ram_used_mb": int(vm.used / (1024 * 1024)),
                "ram_total_mb": int(vm.total / (1024 * 1024)),
                "disk_percent": float(disk.percent),
                "disk_used_gb": round(disk.used / (1024 * 1024 * 1024), 2),
                "disk_total_gb": round(disk.total / (1024 * 1024 * 1024), 2),
                "temp_c": temp_c,
            }
        )
        telemetry["boot_time"] = psutil.boot_time()
        telemetry["uptime_s"] = max(0, int(now - float(psutil.boot_time())))
    for candidate in ("/dev/video0", "/dev/video1"):
        if Path(candidate).exists():
            sensors["camera"]["available"] = True
            break
    for candidate in ("/proc/asound/cards", "/dev/snd"):
        if Path(candidate).exists():
            sensors["microphone"]["available"] = True
            break
    return {
        "schema_version": SCHEMA_VERSION,
        "device_id": device_id,
        "label": label,
        "role": "raspberry_pi_sentinel",
        "endpoint": f"local://{socket.gethostname()}",
        "status": "online",
        "health": health,
        "sensors": sensors,
        "services": {},
        "capabilities": ["system_health", "sensor_readiness", "sentinel_events"],
        "telemetry": telemetry,
        "last_seen": now,
        "provenance": {"source": "sentinel", "origin_service": "sentinel_agent"},
    }
