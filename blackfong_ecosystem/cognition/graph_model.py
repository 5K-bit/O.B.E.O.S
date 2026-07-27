from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class CognitionNode:
    id: str
    label: str
    kind: str
    zone: str
    layers: List[str]
    state: str = "idle"
    emphasis: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CognitionEdge:
    id: str
    source: str
    target: str
    layers: List[str]
    state: str = "idle"
    confidence: float = 0.0
    urgency: float = 0.0
    freshness_s: float | None = None
    label: str = ""
    provenance: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CognitionFocus:
    mode: str = "operator"
    layer: str = "live_brain"
    selected_node: str | None = None
    selected_session: str | None = None
    selected_request: str | None = None
    opened_module: str | None = None
    zoom: float = 1.0
    orbit: float = 0.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CognitionSnapshot:
    schema_version: str
    timestamp: float
    focus: Dict[str, Any]
    layers: List[Dict[str, Any]]
    active_layer: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    overlays: Dict[str, Any]
    trace_ribbon: List[Dict[str, Any]]
    faults: List[Dict[str, Any]]
    summaries: Dict[str, Any]
    ascii_summary: str
    source_snapshots: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
