from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .events import Event
from .storage import EventStore


ADAPTER_CONTRACT_VERSION = "1.0"


class AuthoritySource(str, Enum):
    """Ordered truth sources for D.A.I.S.E conflict resolution."""

    EVENT_MEMORY = "event_memory"
    UNIFIED_LEARNER_PROFILE = "unified_learner_profile"
    LIVE_ADAPTER_RESPONSE = "live_adapter_response"
    COMPANION_PROPOSAL = "companion_proposal"
    CACHED_LOCAL_PROJECT_STATE = "cached_local_project_state"


AUTHORITY_ORDER: List[AuthoritySource] = [
    AuthoritySource.EVENT_MEMORY,
    AuthoritySource.UNIFIED_LEARNER_PROFILE,
    AuthoritySource.LIVE_ADAPTER_RESPONSE,
    AuthoritySource.COMPANION_PROPOSAL,
    AuthoritySource.CACHED_LOCAL_PROJECT_STATE,
]


class SpineMode(str, Enum):
    MINIMAL = "minimal"
    LEARNING = "learning"
    CREATIVE = "creative"
    FULL = "full"


SPINE_MODE_AGENTS: Dict[SpineMode, List[str]] = {
    SpineMode.MINIMAL: ["jarvis"],
    SpineMode.LEARNING: ["jarvis", "lilo", "stitch", "securityplus", "thirty_days"],
    SpineMode.CREATIVE: ["jarvis", "tinyprogrammer"],
    SpineMode.FULL: [
        "jarvis",
        "lilo",
        "stitch",
        "securityplus",
        "thirty_days",
        "tinyprogrammer",
        "vision",
    ],
}


def authority_rank(source: AuthoritySource | str) -> int:
    normalized = source if isinstance(source, AuthoritySource) else AuthoritySource(str(source))
    return AUTHORITY_ORDER.index(normalized)


def normalize_spine_mode(mode: str | SpineMode) -> SpineMode:
    try:
        return mode if isinstance(mode, SpineMode) else SpineMode(str(mode).strip().lower())
    except ValueError as exc:
        valid = ", ".join(item.value for item in SpineMode)
        raise ValueError(f"Unknown spine mode {mode!r}. Valid modes: {valid}") from exc


@dataclass(frozen=True)
class SpineSignal:
    """A single claim from one subsystem about the same logical fact."""

    key: str
    value: Any
    source: AuthoritySource | str
    confidence: float = 1.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        source = self.source if isinstance(self.source, AuthoritySource) else AuthoritySource(str(self.source))
        data["source"] = source.value
        return data


@dataclass(frozen=True)
class ConflictResolution:
    key: str
    winner: SpineSignal
    candidates: List[SpineSignal]
    conflict_detected: bool
    authority_order: List[str]
    event_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "winner": self.winner.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "conflict_detected": self.conflict_detected,
            "authority_order": self.authority_order,
            "event_id": self.event_id,
        }


def resolve_authoritative_signal(
    signals: Iterable[SpineSignal],
    *,
    event_store: Optional[EventStore] = None,
    context: Optional[Mapping[str, Any]] = None,
) -> ConflictResolution:
    """Resolve conflicting claims by the spine authority order."""

    candidates = list(signals)
    if not candidates:
        raise ValueError("At least one spine signal is required")

    keys = {candidate.key for candidate in candidates}
    if len(keys) != 1:
        raise ValueError(f"Signals must share one key; got {sorted(keys)}")

    winner = sorted(
        candidates,
        key=lambda item: (authority_rank(item.source), -float(item.confidence)),
    )[0]
    values = {repr(candidate.value) for candidate in candidates}
    conflict_detected = len(values) > 1
    event_id: Optional[str] = None

    if conflict_detected and event_store is not None:
        event_store.init_db()
        event = Event.new(
            source="daise",
            type="spine_conflict_detected",
            payload={
                "adapter_contract_version": ADAPTER_CONTRACT_VERSION,
                "key": winner.key,
                "winner": winner.to_dict(),
                "candidates": [candidate.to_dict() for candidate in candidates],
                "authority_order": [source.value for source in AUTHORITY_ORDER],
                "context": dict(context or {}),
            },
            confidence=winner.confidence,
            tags=["spine", "authority", "conflict"],
        )
        event_store.add_event(event)
        event_id = event.id

    return ConflictResolution(
        key=winner.key,
        winner=winner,
        candidates=candidates,
        conflict_detected=conflict_detected,
        authority_order=[source.value for source in AUTHORITY_ORDER],
        event_id=event_id,
    )


@dataclass(frozen=True)
class ResourceBudget:
    model_name: str
    model_footprint_mb: int
    average_latency_ms: int
    timeout_seconds: float
    memory_budget_mb: int
    token_budget: int
    load_cost: str
    failure_threshold: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_RESOURCE_BUDGETS: Dict[str, ResourceBudget] = {
    "jarvis": ResourceBudget("llama3.2:3b", 2048, 1800, 120.0, 3072, 512, "warm", 3),
    "lilo": ResourceBudget("llama3.2:3b", 2048, 2500, 120.0, 3072, 640, "warm", 3),
    "stitch": ResourceBudget("deepseek-coder:latest", 776, 2500, 120.0, 2048, 640, "warm", 3),
    "securityplus": ResourceBudget("local-offline", 0, 250, 15.0, 512, 0, "none", 5),
    "thirty_days": ResourceBudget("local-offline", 0, 250, 15.0, 512, 0, "none", 5),
    "tinyprogrammer": ResourceBudget("deepseek-coder:latest", 776, 4000, 180.0, 2048, 1024, "cold", 3),
    "vision": ResourceBudget("torch/yolov5/opencv", 5200, 3500, 180.0, 8192, 0, "cold", 2),
}


@dataclass(frozen=True)
class ResourceUsage:
    latency_ms: float = 0.0
    memory_mb: int = 0
    tokens_used: int = 0
    model_loaded: bool = False
    degraded: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterRequest:
    agent: str
    action: str
    payload: Dict[str, Any] = field(default_factory=dict)
    adapter_contract_version: str = ADAPTER_CONTRACT_VERSION
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterResponse:
    agent: str
    source_project: str
    action: str
    success: bool
    response: str
    observations: List[Dict[str, Any]] = field(default_factory=list)
    proposals: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    trust_score: float = 0.5
    resource_usage: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    adapter_contract_version: str = ADAPTER_CONTRACT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterHealthCheck:
    agent: str
    source_project: str
    ok: bool
    status: str
    checks: List[Dict[str, Any]]
    resource_budget: Dict[str, Any]
    adapter_contract_version: str = ADAPTER_CONTRACT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentAdmissionChecklist:
    agent: str
    adapter_exists: bool
    event_mapping_exists: bool
    trust_scoring_exists: bool
    health_checks_exist: bool
    failure_handling_exists: bool
    resource_budget_exists: bool
    tests_exist: bool

    @property
    def admitted(self) -> bool:
        return all(
            [
                self.adapter_exists,
                self.event_mapping_exists,
                self.trust_scoring_exists,
                self.health_checks_exist,
                self.failure_handling_exists,
                self.resource_budget_exists,
                self.tests_exist,
            ]
        )

    def missing_items(self) -> List[str]:
        return [
            field_name
            for field_name, value in asdict(self).items()
            if field_name != "agent" and not bool(value)
        ]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["admitted"] = self.admitted
        data["missing_items"] = self.missing_items()
        return data


@dataclass(frozen=True)
class AgentAdmissionDecision:
    agent: str
    admitted: bool
    checklist: AgentAdmissionChecklist
    health: AdapterHealthCheck
    reason: str
    adapter_contract_version: str = ADAPTER_CONTRACT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adapter_contract_version": self.adapter_contract_version,
            "agent": self.agent,
            "admitted": self.admitted,
            "checklist": self.checklist.to_dict(),
            "health": self.health.to_dict(),
            "reason": self.reason,
        }


REQUIRED_ADAPTER_RESPONSE_FIELDS = {
    "adapter_contract_version",
    "agent",
    "source_project",
    "action",
    "success",
    "response",
    "observations",
    "proposals",
    "metrics",
    "trust_score",
    "resource_usage",
    "error",
}


def validate_adapter_response(payload: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    missing = sorted(REQUIRED_ADAPTER_RESPONSE_FIELDS - set(payload.keys()))
    if missing:
        errors.append(f"Missing required fields: {', '.join(missing)}")
    if payload.get("adapter_contract_version") != ADAPTER_CONTRACT_VERSION:
        errors.append(
            f"Unsupported adapter_contract_version: {payload.get('adapter_contract_version')!r}"
        )
    if "trust_score" in payload:
        try:
            trust = float(payload["trust_score"])
            if not 0.0 <= trust <= 1.0:
                errors.append("trust_score must be between 0.0 and 1.0")
        except (TypeError, ValueError):
            errors.append("trust_score must be numeric")
    return errors


def now_ms() -> int:
    return int(time.time() * 1000)
