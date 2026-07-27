from __future__ import annotations

from typing import Any, Dict, Optional


class CognitionEventRouter:
    """Small adapter around Socket.IO emission for Thoughtglass updates."""

    def __init__(self, *, socketio: Any = None, telemetry_service: Any = None) -> None:
        self.socketio = socketio
        self.telemetry_service = telemetry_service

    def emit_snapshot(self, snapshot: Dict[str, Any]) -> None:
        if self.socketio:
            self.socketio.emit("cognition_snapshot", snapshot)

    def emit_delta(self, delta: Dict[str, Any]) -> None:
        if self.socketio:
            self.socketio.emit("cognition_delta", delta)

    def emit_focus(self, focus: Dict[str, Any]) -> None:
        if self.socketio:
            self.socketio.emit("cognition_focus", focus)

    def emit_fault(self, fault: Dict[str, Any]) -> None:
        if self.socketio:
            self.socketio.emit("cognition_fault", fault)

    def build_and_emit_snapshot(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        if not self.telemetry_service:
            return None
        snapshot = self.telemetry_service.build_snapshot(**kwargs)
        self.emit_snapshot(snapshot)
        return snapshot
