"""Honest service-health tiles for the Blackfong Workspace.

Maps raw probe results and StartupCoordinator subsystem states onto one small
vocabulary: online / degraded / offline / unknown / stale. Every tile carries the
time it was checked. A failed, malformed or missing check never maps to ``online``.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional

import httpx

from .contracts.health_v1 import HealthRecord

TILE_STATES = ("online", "degraded", "offline", "unknown", "stale")
DEFAULT_MAX_AGE_SECONDS = 60.0
DEFAULT_PROBE_TIMEOUT_SECONDS = 2.0

_SUBSYSTEM_TO_TILE = {
    "ready": "online",
    "active": "online",
    "degraded": "degraded",
    "offline": "offline",
    "missing": "offline",
    "manual_only": "offline",
    "booting": "unknown",
    "warming": "unknown",
}
_HEALTH_TO_TILE = {"ok": "online", "degraded": "degraded", "down": "offline", "unknown": "unknown"}


@dataclass(frozen=True)
class HealthTile:
    key: str
    label: str
    state: str
    checked_at: Optional[float]
    detail: str = ""

    def __post_init__(self) -> None:
        if self.state not in TILE_STATES:
            raise ValueError(f"Unsupported tile state: {self.state}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state,
            "checked_at": self.checked_at,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class HttpCheck:
    key: str
    label: str
    url: str
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS


def tile_from_subsystem(key: str, entry: Mapping[str, Any], *, now: Optional[float] = None) -> HealthTile:
    """Map one StartupCoordinator subsystem dict to a tile; unknown values stay ``unknown``."""
    raw_state = str(entry.get("state") or "").lower()
    state = _SUBSYSTEM_TO_TILE.get(raw_state, "unknown")
    checked_at = entry.get("updated_at")
    if not isinstance(checked_at, (int, float)):
        checked_at = None
    detail = str(entry.get("human_reason") or entry.get("summary") or "")
    if raw_state == "manual_only":
        detail = detail or "Manual start only."
    return HealthTile(
        key=key,
        label=str(entry.get("label") or key),
        state=state,
        checked_at=float(checked_at) if checked_at is not None else None,
        detail=detail,
    )


def tile_from_health_payload(check: HttpCheck, payload: Any, *, now: Optional[float] = None) -> HealthTile:
    """Map a decoded JSON health body; anything that is not a mapping is ``unknown``."""
    checked_at = time.time() if now is None else now
    if not isinstance(payload, dict):
        return HealthTile(check.key, check.label, "unknown", checked_at, "Malformed health response.")
    try:
        record = HealthRecord.from_payload(payload, fallback_name=check.key)
    except (ValueError, TypeError) as exc:
        return HealthTile(check.key, check.label, "unknown", checked_at, f"Unreadable health response: {exc}")
    state = _HEALTH_TO_TILE.get(record.status, "unknown")
    detail = "; ".join(record.warnings) if record.warnings else ""
    return HealthTile(check.key, check.label, state, checked_at, detail)


def probe_http(check: HttpCheck, *, client: Optional[httpx.Client] = None, now: Optional[float] = None) -> HealthTile:
    """Probe one HTTP health endpoint. Never raises and never reports success on failure."""
    checked_at = time.time() if now is None else now
    owns_client = client is None
    http = client or httpx.Client(timeout=check.timeout)
    try:
        response = http.get(check.url, timeout=check.timeout)
    except httpx.TimeoutException:
        return HealthTile(check.key, check.label, "unknown", checked_at, "Health check timed out.")
    except httpx.ConnectError:
        return HealthTile(check.key, check.label, "offline", checked_at, "Connection refused or unreachable.")
    except httpx.HTTPError as exc:
        return HealthTile(check.key, check.label, "unknown", checked_at, f"Health check failed: {type(exc).__name__}")
    finally:
        if owns_client:
            http.close()
    if response.status_code >= 500:
        return HealthTile(check.key, check.label, "degraded", checked_at, f"HTTP {response.status_code}.")
    if response.status_code >= 400:
        return HealthTile(check.key, check.label, "unknown", checked_at, f"HTTP {response.status_code}.")
    try:
        payload = response.json()
    except ValueError:
        return HealthTile(check.key, check.label, "unknown", checked_at, "Health response was not JSON.")
    return tile_from_health_payload(check, payload, now=checked_at)


def apply_staleness(tile: HealthTile, *, now: float, max_age: float = DEFAULT_MAX_AGE_SECONDS) -> HealthTile:
    """Downgrade a tile that claims online/degraded but whose check is old or untimed."""
    if tile.state not in {"online", "degraded"}:
        return tile
    if tile.checked_at is None or now - tile.checked_at > max_age:
        return HealthTile(tile.key, tile.label, "stale", tile.checked_at, "Last check is too old to trust.")
    return tile


def tiles_from_startup(
    startup: Mapping[str, Any],
    *,
    now: Optional[float] = None,
    max_age: float = DEFAULT_MAX_AGE_SECONDS,
) -> List[HealthTile]:
    current = time.time() if now is None else now
    subsystems = startup.get("subsystems") or {}
    order: Iterable[str] = startup.get("subsystem_order") or list(subsystems)
    tiles = [
        apply_staleness(tile_from_subsystem(key, subsystems[key]), now=current, max_age=max_age)
        for key in order
        if key in subsystems
    ]
    return tiles


def probe_all(checks: Iterable[HttpCheck], *, client: Optional[httpx.Client] = None) -> List[HealthTile]:
    return [probe_http(check, client=client) for check in checks]


def default_http_checks() -> List[HttpCheck]:
    """HTTP checks for services whose /health route is confirmed in this repo (Event API only)."""
    base = os.getenv("BLACKFONG_EVENT_API_URL", "http://127.0.0.1:8000").rstrip("/")
    return [HttpCheck(key="event_api", label="Event API", url=f"{base}/health")]


def format_tile_line(tile: HealthTile, *, now: float) -> str:
    """One display line: state, label, when it was checked, and any detail."""
    if tile.checked_at is None:
        when = "never checked"
    else:
        when = f"checked {time.strftime('%H:%M:%S', time.localtime(tile.checked_at))} ({max(0, int(now - tile.checked_at))}s ago)"
    line = f"[{tile.state.upper():<8}] {tile.label}  {when}"
    return f"{line}  {tile.detail}" if tile.detail else line
