"""Thoughtglass renderer adapter contract (snapshot + focus in, visuals out)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class CognitionRenderer(Protocol):
    """Apply a cognition snapshot to a surface-specific renderer."""

    def apply_snapshot(
        self,
        *,
        snapshot: Dict[str, Any],
        graph_payload: Dict[str, Any],
        focus: Optional[Dict[str, Any]] = None,
    ) -> None:
        ...


class BaseCognitionRenderer(ABC):
    """Base adapter with normalized inputs."""

    @abstractmethod
    def apply_snapshot(
        self,
        *,
        snapshot: Dict[str, Any],
        graph_payload: Dict[str, Any],
        focus: Optional[Dict[str, Any]] = None,
    ) -> None:
        raise NotImplementedError


def normalize_renderer_inputs(
    *,
    snapshot: Dict[str, Any],
    graph_payload: Dict[str, Any],
    focus: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Shared normalization for HUD JS, native 2D, and 3D renderers."""
    cognition = dict(snapshot or {})
    graph = dict((graph_payload or {}).get("graph") or graph_payload or {})
    merged_focus = dict(cognition.get("focus") or {})
    if focus:
        merged_focus.update(focus)
    active_layer = str(cognition.get("active_layer") or merged_focus.get("layer") or "live_brain")
    merged_focus.setdefault("layer", active_layer)
    cognition.setdefault("active_layer", active_layer)
    return {
        "snapshot": cognition,
        "graph": graph,
        "graph_payload": {"graph": graph, **{k: v for k, v in (graph_payload or {}).items() if k != "graph"}},
        "focus": merged_focus,
        "nodes": list(graph.get("visible_nodes") or graph.get("nodes") or []),
        "edges": list(graph.get("visible_edges") or graph.get("edges") or []),
        "active_layer": active_layer,
        "zoom": float(merged_focus.get("zoom") or 1.0),
        "orbit": float(merged_focus.get("orbit") or 0.0),
    }


class NativeGraphCognitionRenderer(BaseCognitionRenderer):
    """Adapter for the desktop NativeCognitionGraph widget."""

    def __init__(self, graph_widget: Any, *, mode_label_provider: Optional[Any] = None) -> None:
        self._graph = graph_widget
        self._mode_label_provider = mode_label_provider or (lambda: "Live Brain")

    def apply_snapshot(
        self,
        *,
        snapshot: Dict[str, Any],
        graph_payload: Dict[str, Any],
        focus: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized = normalize_renderer_inputs(snapshot=snapshot, graph_payload=graph_payload, focus=focus)
        self._graph.apply_graph(
            snapshot=normalized["snapshot"],
            graph_payload=normalized["graph_payload"],
            mode_label=str(self._mode_label_provider()),
        )


class Thoughtglass3DCognitionRenderer(BaseCognitionRenderer):
    """Adapter for the OpenGL Thoughtglass scene host."""

    def __init__(self, scene_host: Any) -> None:
        self._scene_host = scene_host

    def apply_snapshot(
        self,
        *,
        snapshot: Dict[str, Any],
        graph_payload: Dict[str, Any],
        focus: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized = normalize_renderer_inputs(snapshot=snapshot, graph_payload=graph_payload, focus=focus)
        if hasattr(self._scene_host, "apply_graph"):
            self._scene_host.apply_graph(
                snapshot=normalized["snapshot"],
                graph_payload=normalized["graph_payload"],
            )
