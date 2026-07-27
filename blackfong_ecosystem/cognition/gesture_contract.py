from __future__ import annotations

import time
from typing import Any, Dict


THOUGHTGLASS_LAYERS = [
    {"id": "live_brain", "label": "Live Brain"},
    {"id": "memory", "label": "Memory"},
    {"id": "tasks", "label": "Tasks"},
    {"id": "sensors", "label": "Sensors"},
    {"id": "faults", "label": "Faults"},
    {"id": "forge", "label": "Forge"},
]

GESTURE_ACTIONS = {
    "select",
    "drag",
    "rotate",
    "zoom",
    "switch_layer",
    "open_module",
}

DEFAULT_COGNITION_FOCUS: Dict[str, Any] = {
    "mode": "operator",
    "layer": "live_brain",
    "selected_node": None,
    "selected_session": None,
    "selected_request": None,
    "opened_module": None,
    "zoom": 1.0,
    "orbit": 0.0,
    "pan_x": 0.0,
    "pan_y": 0.0,
    "updated_at": 0.0,
}


def normalize_gesture(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    data = dict(payload or {})
    action = str(data.get("action") or "").strip().lower()
    if action not in GESTURE_ACTIONS:
        raise ValueError(f"Unsupported cognition gesture: {action or 'unknown'}")

    layer = str(data.get("layer") or "").strip().lower()
    if layer and layer not in {entry["id"] for entry in THOUGHTGLASS_LAYERS}:
        raise ValueError(f"Unsupported cognition layer: {layer}")

    normalized = {
        "action": action,
        "target": data.get("target") or {},
        "delta": data.get("delta") or {},
        "layer": layer or None,
        "mode": str(data.get("mode") or "").strip().lower() or None,
        "session_id": data.get("session_id"),
        "request_id": data.get("request_id"),
        "timestamp": time.time(),
    }
    return normalized


def apply_gesture(focus: Dict[str, Any] | None, gesture: Dict[str, Any]) -> Dict[str, Any]:
    next_focus = dict(DEFAULT_COGNITION_FOCUS)
    next_focus.update(focus or {})

    action = gesture["action"]
    target = dict(gesture.get("target") or {})
    delta = dict(gesture.get("delta") or {})

    if gesture.get("mode"):
        next_focus["mode"] = gesture["mode"]
    if gesture.get("layer"):
        next_focus["layer"] = gesture["layer"]
    if gesture.get("session_id"):
        next_focus["selected_session"] = gesture["session_id"]
    if gesture.get("request_id"):
        next_focus["selected_request"] = gesture["request_id"]

    if action == "select":
        next_focus["selected_node"] = target.get("node_id")
    elif action == "open_module":
        next_focus["opened_module"] = str(target.get("module") or target.get("node_id") or "")
    elif action == "switch_layer":
        next_focus["layer"] = str(target.get("layer") or next_focus["layer"])
    elif action == "zoom":
        next_focus["zoom"] = max(0.55, min(2.6, float(next_focus.get("zoom", 1.0)) + float(delta.get("amount", 0.0))))
    elif action == "rotate":
        next_focus["orbit"] = float(next_focus.get("orbit", 0.0)) + float(delta.get("amount", 0.0))
    elif action == "drag":
        next_focus["pan_x"] = float(next_focus.get("pan_x", 0.0)) + float(delta.get("x", 0.0))
        next_focus["pan_y"] = float(next_focus.get("pan_y", 0.0)) + float(delta.get("y", 0.0))

    next_focus["updated_at"] = time.time()
    return next_focus
