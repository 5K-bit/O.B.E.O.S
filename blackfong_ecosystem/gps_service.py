from __future__ import annotations

import contextlib
import math
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import GPSSettings, gps_settings
from .events import Event
from .storage import EventStore


def _utc_iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class GPSService:
    """Shared GPS/GNSS adapter with simulator fallback and event-backed route history."""

    def __init__(
        self,
        *,
        event_store: Optional[EventStore] = None,
        settings: Optional[GPSSettings] = None,
    ) -> None:
        self.event_store = event_store or EventStore()
        self.event_store.init_db()
        self.settings = settings or gps_settings
        self._provider_override: Optional[str] = None
        self._lock = threading.RLock()
        self._started_at = time.time()
        self._last_fix_event_at = 0.0
        self._latest_fix: Optional[Dict[str, Any]] = None
        self._provider_error: Optional[str] = None
        self._serial_thread: Optional[threading.Thread] = None
        self._serial_stop = threading.Event()
        self._simulator_route = self._build_simulator_route()

    def shutdown(self) -> None:
        self._serial_stop.set()
        thread = self._serial_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def snapshot(self, *, emit: bool = False) -> Dict[str, Any]:
        with self._lock:
            provider_type = self._resolve_provider_type()
            self._provider_error = None
            if provider_type == "simulator":
                self._latest_fix = self._simulator_fix()
            elif provider_type == "nmea_serial":
                self._ensure_serial_reader()
            else:
                self._latest_fix = None

            recording_state = self._recording_state()
            if emit or (recording_state.get("recording") and provider_type in {"simulator", "nmea_serial"}):
                self._maybe_emit_fix_event(provider_type=provider_type, recording_state=recording_state)
            trail = self._recent_trail(
                route_session_id=str(recording_state.get("route_session_id") or ""),
                limit=self.settings.trail_limit,
            )
            active_route = self._active_route_summary(recording_state=recording_state, trail=trail)
            latest_fix = self._latest_fix_from_events() or self._latest_fix
            status = "ready" if latest_fix else "waiting_for_fix" if provider_type in {"simulator", "nmea_serial"} else "offline"
            if provider_type == "unavailable":
                status = "degraded"
            if provider_type == "off":
                status = "disabled"
            return {
                "status": status,
                "available": provider_type in {"simulator", "nmea_serial"} and self.settings.enabled,
                "provider_type": provider_type,
                "recording": bool(recording_state.get("recording")),
                "latest_fix": latest_fix,
                "accuracy_m": (latest_fix or {}).get("accuracy_m"),
                "last_update_at": (latest_fix or {}).get("timestamp"),
                "offline_state": "offline_ready" if provider_type == "simulator" else "device" if provider_type == "nmea_serial" else "offline",
                "active_route": active_route,
                "trail": trail,
                "provider_error": self._provider_error,
                "recording_state": recording_state,
                "simulator": {
                    "enabled": bool(self.settings.simulator_enabled),
                    "step_seconds": self.settings.simulator_step_seconds,
                },
            }

    def set_provider_mode(
        self,
        mode: str,
        *,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized = (mode or "").strip().lower() or "auto"
        if normalized not in {"auto", "serial", "simulator", "off"}:
            raise ValueError("Provider mode must be auto, serial, simulator, or off.")
        with self._lock:
            self._provider_override = normalized
            if normalized != "serial":
                self._serial_stop.set()
        return {
            "status": "ok",
            "provider_mode": normalized,
            "snapshot": self.snapshot(),
            "session_id": session_id,
            "request_id": request_id,
        }

    def start_recording(
        self,
        *,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._recording_state()
        if state.get("recording"):
            return {"status": "ok", "recording": True, "route_session_id": state.get("route_session_id"), "snapshot": self.snapshot()}
        route_session_id = str(uuid.uuid4())
        event = Event.new(
            source="system",
            type="gps_route_recording_state",
            payload={
                "recording": True,
                "route_session_id": route_session_id,
                "provider_type": self._resolve_provider_type(),
                "changed_at": _utc_iso_now(),
            },
            confidence=0.93,
            session_id=session_id,
            request_id=request_id,
            tags=["gps", "route", "recording"],
        )
        self.event_store.add_event(event)
        snapshot = self.snapshot(emit=True)
        return {
            "status": "ok",
            "recording": True,
            "route_session_id": route_session_id,
            "event_id": event.id,
            "snapshot": snapshot,
        }

    def stop_recording(
        self,
        *,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        state = self._recording_state()
        event = Event.new(
            source="system",
            type="gps_route_recording_state",
            payload={
                "recording": False,
                "route_session_id": state.get("route_session_id"),
                "provider_type": self._resolve_provider_type(),
                "changed_at": _utc_iso_now(),
            },
            confidence=0.93,
            session_id=session_id,
            request_id=request_id,
            tags=["gps", "route", "recording"],
        )
        self.event_store.add_event(event)
        snapshot = self.snapshot()
        return {
            "status": "ok",
            "recording": False,
            "route_session_id": state.get("route_session_id"),
            "event_id": event.id,
            "snapshot": snapshot,
        }

    def _resolve_provider_type(self) -> str:
        if not self.settings.enabled:
            return "off"
        mode = self._provider_override or self.settings.provider_mode
        if mode == "off":
            return "off"
        if mode in {"auto", "serial"} and self.settings.serial_port:
            return "nmea_serial"
        if mode == "serial":
            self._provider_error = "serial_port_not_configured"
            return "unavailable"
        if self.settings.simulator_enabled:
            return "simulator"
        return "unavailable"

    def _ensure_serial_reader(self) -> None:
        if self._serial_thread is not None and self._serial_thread.is_alive():
            return
        self._serial_stop.clear()
        self._serial_thread = threading.Thread(target=self._serial_loop, daemon=True, name="obeos-gps-serial")
        self._serial_thread.start()

    def _serial_loop(self) -> None:
        try:
            import serial  # type: ignore
        except Exception:
            self._provider_error = "pyserial_unavailable"
            return
        port = self.settings.serial_port
        if not port:
            self._provider_error = "serial_port_not_configured"
            return
        try:
            with serial.Serial(port, self.settings.baud_rate, timeout=1.0) as handle:
                while not self._serial_stop.is_set():
                    raw = handle.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="ignore").strip()
                    fix = self._parse_nmea_line(line)
                    if fix:
                        with self._lock:
                            self._latest_fix = fix
        except Exception as exc:
            self._provider_error = str(exc)

    def _parse_nmea_line(self, line: str) -> Optional[Dict[str, Any]]:
        if not line.startswith("$") or "," not in line:
            return None
        parts = line.split(",")
        if parts[0] not in {"$GPRMC", "$GNRMC", "$GPGGA", "$GNGGA"}:
            return None
        try:
            if parts[0].endswith("RMC"):
                status = parts[2] if len(parts) > 2 else ""
                if status != "A":
                    return None
                latitude = self._parse_nmea_coord(parts[3], parts[4])
                longitude = self._parse_nmea_coord(parts[5], parts[6])
                speed_knots = float(parts[7] or 0.0) if len(parts) > 7 and parts[7] else 0.0
                return {
                    "latitude": latitude,
                    "longitude": longitude,
                    "accuracy_m": None,
                    "speed_kmh": round(speed_knots * 1.852, 2),
                    "timestamp": _utc_iso_now(),
                    "provider_type": "nmea_serial",
                }
            latitude = self._parse_nmea_coord(parts[2], parts[3])
            longitude = self._parse_nmea_coord(parts[4], parts[5])
            hdop = float(parts[8] or 0.0) if len(parts) > 8 and parts[8] else 0.0
            return {
                "latitude": latitude,
                "longitude": longitude,
                "accuracy_m": round(max(3.0, hdop * 5.0), 2) if hdop else None,
                "speed_kmh": None,
                "timestamp": _utc_iso_now(),
                "provider_type": "nmea_serial",
            }
        except Exception:
            return None

    @staticmethod
    def _parse_nmea_coord(value: str, hemisphere: str) -> float:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("missing coordinate")
        if "." not in raw:
            raise ValueError("invalid coordinate")
        dot = raw.index(".")
        degree_digits = 3 if len(raw[:dot]) > 4 else 2
        degrees = float(raw[:degree_digits])
        minutes = float(raw[degree_digits:])
        decimal = degrees + (minutes / 60.0)
        if hemisphere in {"S", "W"}:
            decimal *= -1.0
        return round(decimal, 6)

    def _simulator_fix(self) -> Optional[Dict[str, Any]]:
        if not self._simulator_route:
            return None
        elapsed = max(0.0, time.time() - self._started_at)
        step = max(1, int(elapsed / self.settings.simulator_step_seconds))
        point = self._simulator_route[step % len(self._simulator_route)]
        return {
            **point,
            "provider_type": "simulator",
            "timestamp": _utc_iso_now(),
        }

    def _maybe_emit_fix_event(self, *, provider_type: str, recording_state: Dict[str, Any]) -> None:
        latest_fix = self._latest_fix
        if latest_fix is None or provider_type not in {"simulator", "nmea_serial"}:
            return
        now = time.time()
        if (now - self._last_fix_event_at) < self.settings.emit_interval_seconds:
            return
        route_session_id = str(recording_state.get("route_session_id") or "")
        event = Event.new(
            source="system",
            type="gps_fix_observation",
            payload={
                "provider_type": provider_type,
                "recording": bool(recording_state.get("recording")),
                "route_session_id": route_session_id or None,
                "fix": dict(latest_fix),
            },
            confidence=0.91 if provider_type == "nmea_serial" else 0.82,
            tags=["gps", "route", provider_type],
        )
        self.event_store.add_event(event)
        self._last_fix_event_at = now

    def _recording_state(self) -> Dict[str, Any]:
        events = self.event_store.list_events_by_type("gps_route_recording_state", limit=1)
        if not events:
            return {"recording": False, "route_session_id": None, "changed_at": None}
        payload = dict(events[0].payload or {})
        return {
            "recording": bool(payload.get("recording")),
            "route_session_id": payload.get("route_session_id"),
            "changed_at": payload.get("changed_at"),
            "provider_type": payload.get("provider_type"),
        }

    def _recent_trail(self, *, route_session_id: str, limit: int) -> List[Dict[str, Any]]:
        events = self.event_store.list_events_by_type("gps_fix_observation", limit=max(limit * 3, limit))
        trail: List[Dict[str, Any]] = []
        for event in reversed(events):
            payload = dict(event.payload or {})
            if route_session_id and str(payload.get("route_session_id") or "") != route_session_id:
                continue
            fix = dict(payload.get("fix") or {})
            if not fix:
                continue
            trail.append(fix)
            if len(trail) >= limit:
                break
        return trail

    def _latest_fix_from_events(self) -> Optional[Dict[str, Any]]:
        events = self.event_store.list_events_by_type("gps_fix_observation", limit=1)
        if not events:
            return None
        return dict((events[0].payload or {}).get("fix") or {})

    def _active_route_summary(self, *, recording_state: Dict[str, Any], trail: List[Dict[str, Any]]) -> Dict[str, Any]:
        route_session_id = recording_state.get("route_session_id")
        distance_km = 0.0
        for previous, current in zip(trail, trail[1:]):
            distance_km += self._haversine_km(
                float(previous.get("latitude") or 0.0),
                float(previous.get("longitude") or 0.0),
                float(current.get("latitude") or 0.0),
                float(current.get("longitude") or 0.0),
            )
        return {
            "route_session_id": route_session_id,
            "point_count": len(trail),
            "distance_km": round(distance_km, 3),
            "started_at": recording_state.get("changed_at"),
            "recording": bool(recording_state.get("recording")),
        }

    @staticmethod
    def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius_km = 6371.0
        lat1_r = math.radians(lat1)
        lat2_r = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lon = math.radians(lon2 - lon1)
        a = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(delta_lon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(max(1e-12, 1 - a)))
        return radius_km * c

    @staticmethod
    def _build_simulator_route() -> List[Dict[str, Any]]:
        points = [
            (40.758896, -73.985130),
            (40.759410, -73.983220),
            (40.760112, -73.981540),
            (40.760821, -73.979680),
            (40.761880, -73.978150),
            (40.763101, -73.976420),
            (40.764280, -73.974980),
            (40.765210, -73.973200),
            (40.766010, -73.971480),
            (40.766802, -73.969910),
        ]
        route: List[Dict[str, Any]] = []
        for latitude, longitude in points:
            route.append(
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "accuracy_m": 6.0,
                    "speed_kmh": 4.8,
                }
            )
        return route
