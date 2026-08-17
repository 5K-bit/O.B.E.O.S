from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

HEALTH_CONTRACT_VERSION = "1.0"
HEALTH_STATES = {"ok", "degraded", "down", "unknown"}


@dataclass(frozen=True, slots=True)
class HealthRecord:
    name: str
    version: str
    status: str
    node: str = "unknown"
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dependencies: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("health.name is required")
        if self.status not in HEALTH_STATES:
            raise ValueError(f"Unsupported health status: {self.status}")

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, fallback_name: str = "unknown") -> "HealthRecord":
        raw_status = str(payload.get("status") or ("ok" if payload.get("ok") is True else "unknown")).lower()
        aliases = {"healthy": "ok", "up": "ok", "warning": "degraded", "error": "down", "failed": "down"}
        status = aliases.get(raw_status, raw_status)
        if status not in HEALTH_STATES:
            status = "unknown"
        warnings = payload.get("warnings") or []
        if isinstance(warnings, str):
            warnings = [warnings]
        return cls(
            name=str(payload.get("name") or fallback_name),
            version=str(payload.get("version") or "unknown"),
            status=status,
            node=str(payload.get("node") or "unknown"),
            checked_at=str(payload.get("checked_at") or datetime.now(timezone.utc).isoformat()),
            dependencies=dict(payload.get("dependencies") or {}),
            warnings=tuple(str(item) for item in warnings),
            details=dict(payload.get("details") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": HEALTH_CONTRACT_VERSION,
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "node": self.node,
            "checked_at": self.checked_at,
            "dependencies": self.dependencies,
            "warnings": list(self.warnings),
            "details": self.details,
        }


def aggregate_health(records: Iterable[HealthRecord], *, name: str = "obeos") -> HealthRecord:
    items = list(records)
    if not items:
        return HealthRecord(name=name, version="unknown", status="unknown", warnings=("no health records",))
    statuses = {item.status for item in items}
    if "down" in statuses:
        overall = "down"
    elif "degraded" in statuses or "unknown" in statuses:
        overall = "degraded"
    else:
        overall = "ok"
    return HealthRecord(
        name=name,
        version="1",
        status=overall,
        dependencies={item.name: item.status for item in items},
        warnings=tuple(warning for item in items for warning in item.warnings),
        details={"components": [item.to_dict() for item in items]},
    )
