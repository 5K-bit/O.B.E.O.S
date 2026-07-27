from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .gesture_contract import DEFAULT_COGNITION_FOCUS, THOUGHTGLASS_LAYERS
from .graph_model import CognitionEdge, CognitionFocus, CognitionNode, CognitionSnapshot


class CognitionTelemetryService:
    """Translate O.B.E.O.S runtime state into a visual/terminal cognition model."""

    SCHEMA_VERSION = "1.0"

    def __init__(
        self,
        *,
        memory_service: Any,
        awareness_service: Any,
        operator_actions: Any,
        tinyprogrammer: Any,
        memory_chamber: Any = None,
    ) -> None:
        self.memory_service = memory_service
        self.awareness_service = awareness_service
        self.operator_actions = operator_actions
        self.tinyprogrammer = tinyprogrammer
        self.memory_chamber = memory_chamber

    def build_snapshot(
        self,
        *,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        layer: str = "live_brain",
        focus: Optional[Dict[str, Any]] = None,
        request_states: Optional[Dict[str, Dict[str, Any]]] = None,
        request_threads: Optional[Dict[str, Any]] = None,
        backend_bootstrap: Optional[Dict[str, Any]] = None,
        sensor_mode: str = "off",
        voice_status: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        timestamp = time.time()
        normalized_layer = self._normalize_layer(layer)
        awareness = self.awareness_service.build_snapshot(
            request_states=request_states,
            request_threads=request_threads,
            backend_bootstrap=backend_bootstrap,
            voice_status=voice_status,
            sensor_mode=sensor_mode,
            emit=False,
        )
        trace = self._resolve_trace(request_id=request_id, request_states=request_states)
        selected_session = session_id or trace.get("session_id")
        actions = self.operator_actions.list_actions(limit=24, session_id=selected_session)
        try:
            tiny_status = self.tinyprogrammer.get_status(probe_live=False)
        except TypeError:
            tiny_status = self.tinyprogrammer.get_status()
        recent_events = self.memory_service.list_recent_events(limit=40)
        sessions = self.memory_service.list_sessions(limit=10)
        expansion = self._cognitive_expansion_summary()
        startup = awareness.get("startup", {})

        active_focus = dict(DEFAULT_COGNITION_FOCUS)
        active_focus.update(focus or {})
        if not active_focus.get("selected_session"):
            active_focus["selected_session"] = selected_session
        if not active_focus.get("selected_request"):
            active_focus["selected_request"] = trace.get("request_id")
        active_focus["layer"] = normalized_layer

        nodes = self._build_nodes(
            awareness=awareness,
            trace=trace,
            actions=actions,
            tiny_status=tiny_status,
            sessions=sessions,
            layer=normalized_layer,
            expansion=expansion,
            startup=startup,
        )
        edges = self._build_edges(
            awareness=awareness,
            trace=trace,
            actions=actions,
            tiny_status=tiny_status,
            layer=normalized_layer,
            expansion=expansion,
            startup=startup,
        )
        faults = self._collect_faults(
            awareness=awareness,
            trace=trace,
            actions=actions,
            tiny_status=tiny_status,
        )
        overlays = self._build_overlays(
            awareness=awareness,
            trace=trace,
            actions=actions,
            tiny_status=tiny_status,
            faults=faults,
            layer=normalized_layer,
            startup=startup,
        )
        summaries = self._build_summaries(
            awareness=awareness,
            trace=trace,
            actions=actions,
            tiny_status=tiny_status,
            sessions=sessions,
            recent_events=recent_events,
            faults=faults,
            startup=startup,
        )
        trace_ribbon = self._build_trace_ribbon(trace)
        memory_chamber_projection: Dict[str, Any] = {}
        if self.memory_chamber is not None:
            try:
                memory_chamber_projection = self.memory_chamber.build_projection(
                    mode="thoughtglass_home",
                    layer=normalized_layer,
                    selected_node=active_focus.get("selected_node"),
                    session_id=selected_session,
                    request_id=request_id,
                    limit=22,
                )
            except Exception:
                memory_chamber_projection = {"nodes": [], "edges": [], "status": "degraded"}

        if memory_chamber_projection:
            existing_node_ids = {node.id for node in nodes}
            for node_payload in memory_chamber_projection.get("nodes", []):
                node_id = str(node_payload.get("id") or "")
                if not node_id or node_id in existing_node_ids:
                    continue
                existing_node_ids.add(node_id)
                nodes.append(
                    CognitionNode(
                        id=node_id,
                        label=str(node_payload.get("label") or node_id),
                        kind=str(node_payload.get("kind") or "concept"),
                        zone=str(node_payload.get("zone") or "memory"),
                        layers=list(node_payload.get("layers") or ["live_brain", "memory"]),
                        state=str(node_payload.get("state") or "ready"),
                        emphasis=float(node_payload.get("emphasis") or 0.2),
                        metrics=dict(node_payload.get("metrics") or {}),
                        provenance=dict(node_payload.get("provenance") or {"source": "memory_chamber"}),
                        meta=dict(node_payload.get("meta") or {}),
                    )
                )
            existing_edge_ids = {edge.id for edge in edges}
            for edge_payload in memory_chamber_projection.get("edges", []):
                edge_id = str(edge_payload.get("id") or "")
                if not edge_id or edge_id in existing_edge_ids:
                    continue
                existing_edge_ids.add(edge_id)
                edges.append(
                    CognitionEdge(
                        id=edge_id,
                        source=str(edge_payload.get("source") or ""),
                        target=str(edge_payload.get("target") or ""),
                        layers=list(edge_payload.get("layers") or ["live_brain", "memory"]),
                        state=str(edge_payload.get("state") or "ready"),
                        confidence=float(edge_payload.get("confidence") or 0.0),
                        urgency=float(edge_payload.get("urgency") or 0.0),
                        freshness_s=edge_payload.get("freshness_s"),
                        label=str(edge_payload.get("label") or ""),
                        provenance=dict(edge_payload.get("provenance") or {"source": "memory_chamber"}),
                        meta=dict(edge_payload.get("meta") or {}),
                    )
                )
            overlays["memory_chamber"] = self.memory_chamber.summary()
            summaries["memory_chamber"] = {
                "counts": (overlays.get("memory_chamber") or {}).get("counts", {}),
                "top_concepts": (overlays.get("memory_chamber") or {}).get("top_concepts", [])[:4],
            }
        active_focus = CognitionFocus(
            mode=str(active_focus.get("mode") or "operator"),
            layer=normalized_layer,
            selected_node=active_focus.get("selected_node"),
            selected_session=active_focus.get("selected_session"),
            selected_request=active_focus.get("selected_request"),
            opened_module=active_focus.get("opened_module"),
            zoom=float(active_focus.get("zoom", 1.0)),
            orbit=float(active_focus.get("orbit", 0.0)),
            pan_x=float(active_focus.get("pan_x", 0.0)),
            pan_y=float(active_focus.get("pan_y", 0.0)),
            updated_at=float(active_focus.get("updated_at") or timestamp),
        ).to_dict()

        visible_nodes = [node.to_dict() for node in nodes if normalized_layer in node.layers or "live_brain" in node.layers]
        visible_edges = [edge.to_dict() for edge in edges if normalized_layer in edge.layers or "live_brain" in edge.layers]
        snapshot = CognitionSnapshot(
            schema_version=self.SCHEMA_VERSION,
            timestamp=timestamp,
            focus=active_focus,
            layers=[{**entry, "active": entry["id"] == normalized_layer} for entry in THOUGHTGLASS_LAYERS],
            active_layer=normalized_layer,
            nodes=visible_nodes,
            edges=visible_edges,
            overlays=overlays,
            trace_ribbon=trace_ribbon,
            faults=faults,
            summaries=summaries,
            ascii_summary=self.render_ascii_from_parts(
                layer=normalized_layer,
                trace_ribbon=trace_ribbon,
                overlays=overlays,
                summaries=summaries,
                faults=faults,
                nodes=visible_nodes,
            ),
            source_snapshots={
                "awareness": awareness,
                "trace": trace,
                "actions": actions,
                "tinyprogrammer": tiny_status,
                "cognitive_expansion": expansion,
                "startup": startup,
                "memory_chamber": memory_chamber_projection,
            },
        )
        return snapshot.to_dict()

    def build_delta(self, *, reason: str, **kwargs: Any) -> Dict[str, Any]:
        snapshot = self.build_snapshot(**kwargs)
        return {
            "schema_version": self.SCHEMA_VERSION,
            "reason": reason,
            "timestamp": snapshot["timestamp"],
            "focus": snapshot["focus"],
            "active_layer": snapshot["active_layer"],
            "overlays": snapshot["overlays"],
            "trace_ribbon": snapshot["trace_ribbon"],
            "faults": snapshot["faults"],
            "nodes": snapshot["nodes"],
            "edges": snapshot["edges"],
        }

    def build_fault(
        self,
        *,
        code: str,
        message: str,
        severity: str = "warning",
        source: str = "thoughtglass_projection",
        request_id: Optional[str] = None,
        session_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "timestamp": time.time(),
            "code": code,
            "message": message,
            "severity": severity,
            "source": source,
            "request_id": request_id,
            "session_id": session_id,
            "meta": meta or {},
        }

    def render_ascii(self, *, session_id: Optional[str] = None, request_id: Optional[str] = None, layer: str = "live_brain", **kwargs: Any) -> str:
        snapshot = self.build_snapshot(session_id=session_id, request_id=request_id, layer=layer, **kwargs)
        return snapshot["ascii_summary"]

    def render_ascii_from_parts(
        self,
        *,
        layer: str,
        trace_ribbon: List[Dict[str, Any]],
        overlays: Dict[str, Any],
        summaries: Dict[str, Any],
        faults: List[Dict[str, Any]],
        nodes: List[Dict[str, Any]],
    ) -> str:
        header = "THOUGHTGLASS"
        divider = "────────────────────────────────────────"
        route_text = " -> ".join(step["label"] for step in trace_ribbon if step["status"] != "pending") or "IDLE"
        active_nodes = [node for node in nodes if node.get("state") in {"active", "warning", "fault"}][:6]
        node_lines = [f"{node['label']:<18} {node['state'].upper()}" for node in active_nodes] or ["SYSTEM             IDLE"]
        faults_text = ", ".join(fault["message"] for fault in faults[:2]) if faults else "none"
        return "\n".join(
            [
                header,
                divider,
                f"LAYER       {layer.upper()}",
                f"QUEUE       {self._gauge(overlays['queue'].get('pending_requests', 0), max_value=6)}",
                f"LATENCY     {self._gauge(overlays['latency'].get('average_response_time_s', 0), max_value=12)}",
                f"CONFIDENCE  {self._gauge(overlays.get('confidence', 0.0), max_value=1.0, width=5)}",
                "",
                "ROUTE FLOW",
                route_text,
                "",
                "ACTIVE NODES",
                *node_lines,
                "",
                f"FOCUS       {summaries.get('focus_label', 'None')}",
                f"FAULTS      {faults_text}",
            ]
        )

    def _resolve_trace(self, *, request_id: Optional[str], request_states: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
        selected_request = request_id
        if not selected_request and request_states:
            active = [
                state for state in request_states.values()
                if state.get("status") in {"pending", "complete", "error"} and state.get("request_id")
            ]
            active.sort(key=lambda item: float(item.get("updated_at") or item.get("started_at") or 0.0), reverse=True)
            if active:
                selected_request = str(active[0]["request_id"])
        if selected_request:
            return self.memory_service.get_request_trace(selected_request)
        return {
            "request_id": None,
            "session_id": None,
            "status": "idle",
            "phase": "idle",
            "events": [],
            "phases": [],
            "queue_pressure": None,
            "latency_observation": None,
            "contradictions": [],
            "diagnostic_hints": [],
            "provenance": [],
        }

    def _build_nodes(
        self,
        *,
        awareness: Dict[str, Any],
        trace: Dict[str, Any],
        actions: Dict[str, Any],
        tiny_status: Dict[str, Any],
        sessions: List[Dict[str, Any]],
        layer: str,
        expansion: Dict[str, Any],
        startup: Dict[str, Any],
    ) -> List[CognitionNode]:
        backend = awareness.get("observed", {}).get("backend", {})
        queue = awareness.get("observed", {}).get("queue", {})
        sensors = awareness.get("sensors", {})
        memory = awareness.get("observed", {}).get("memory", {})
        selected_agent = self._normalize_agent_name(
            trace.get("selection", {}).get("selected_agent")
            or trace.get("selected_agent")
            or "jarvis"
        )
        request_active = str(trace.get("status") or "idle") in {"pending", "complete"} and bool(trace.get("request_id"))
        session_count = len(sessions)
        memory_hits = self._memory_hits(trace)
        active_actions = int(actions.get("pending_actions", 0))
        file_activity = any(action.get("action", "").startswith("file_") for action in actions.get("actions", []))
        validation_activity = any("validation" in action.get("action", "") for action in actions.get("actions", []))
        startup_subsystems = (startup or {}).get("subsystems", {})
        forge_state = "active" if backend.get("bootstrap", {}).get("status") == "warming" or file_activity or validation_activity else self._startup_state(startup_subsystems, "backend_model", "warming")

        nodes = [
            CognitionNode(
                id="blackfong_core",
                label="Blackfong Core",
                kind="core",
                zone="center",
                layers=["live_brain", "tasks", "faults", "forge"],
                state="active" if request_active or active_actions else self._startup_state(startup_subsystems, "blackfong_core", awareness.get("health", {}).get("overall", "warming")),
                emphasis=0.98,
                metrics={"queue_depth": queue.get("pending_requests", 0), "sessions": session_count},
                provenance=awareness.get("provenance", {}),
            ),
            CognitionNode(
                id="daise_interface",
                label="D.A.I.S.E",
                kind="interface",
                zone="inner",
                layers=["live_brain", "tasks", "memory", "faults"],
                state="active" if request_active else self._startup_state(startup_subsystems, "daise", "warming"),
                emphasis=0.92,
                metrics={"selected_agent": selected_agent, "phase": trace.get("phase", "idle")},
            ),
            CognitionNode(
                id="memory_session",
                label="Session Memory",
                kind="memory",
                zone="memory",
                layers=["live_brain", "memory"],
                state="active" if memory_hits else self._startup_state(startup_subsystems, "session_memory", "ready"),
                emphasis=min(1.0, 0.25 + (memory_hits / 6.0)),
                metrics={"hits": memory_hits, "sessions": memory.get("sessions", session_count)},
            ),
            CognitionNode(
                id="memory_pinned",
                label="Pinned Memory",
                kind="memory",
                zone="memory",
                layers=["live_brain", "memory"],
                state="active" if memory.get("pinned_memories", 0) else self._startup_state(startup_subsystems, "pinned_memory", "ready"),
                emphasis=min(1.0, 0.2 + (memory.get("pinned_memories", 0) / 8.0)),
                metrics={"count": memory.get("pinned_memories", 0)},
            ),
            CognitionNode(
                id="memory_chamber",
                label="Memory Chamber",
                kind="memory_chamber",
                zone="memory",
                layers=["live_brain", "memory"],
                state="active" if expansion["memory_artifacts"] or memory_hits else self._startup_state(startup_subsystems, "session_memory", "ready"),
                emphasis=min(1.0, 0.35 + (expansion["memory_artifacts"] / 10.0)),
                metrics={
                    "artifacts": expansion["memory_artifacts"],
                    "sessions": session_count,
                    "latest_artifact": expansion.get("latest_memory_artifact", {}).get("title"),
                },
            ),
            CognitionNode(
                id="blackbox_archive",
                label="Black Box Archive",
                kind="archive",
                zone="memory",
                layers=["live_brain", "memory", "sensors"],
                state="active" if expansion["sealed_capsules"] else self._startup_state(startup_subsystems, "black_box_archive", "ready"),
                emphasis=min(1.0, 0.28 + (expansion["sealed_capsules"] / 8.0)),
                metrics={
                    "sealed_capsules": expansion["sealed_capsules"],
                    "capture_profile": expansion["capture_profile"],
                },
            ),
            CognitionNode(
                id="operator_queue",
                label="Operator Queue",
                kind="queue",
                zone="task",
                layers=["live_brain", "tasks", "forge"],
                state="active" if active_actions else self._startup_state(startup_subsystems, "operator_queue", "ready"),
                emphasis=min(1.0, 0.3 + (active_actions / 8.0)),
                metrics={"pending": active_actions},
            ),
            CognitionNode(
                id="backend_model",
                label="Local Model",
                kind="backend",
                zone="tool",
                layers=["live_brain", "forge", "faults"],
                state=self._startup_state(startup_subsystems, "backend_model", backend.get("status", "warming")),
                emphasis=0.7 if self._startup_state(startup_subsystems, "backend_model", backend.get("status", "warming")) in {"ready", "active"} else 0.9,
                metrics={"provider": backend.get("provider"), "model": backend.get("configured_model")},
            ),
            CognitionNode(
                id="tinyprogrammer",
                label="TinyProgrammer",
                kind="companion",
                zone="tool",
                layers=["live_brain", "tasks", "forge", "faults"],
                state="active" if tiny_status.get("online") else self._startup_state(startup_subsystems, "tinyprogrammer", str(tiny_status.get("state") or tiny_status.get("mode") or "booting")),
                emphasis=0.8 if tiny_status.get("online") else 0.46,
                metrics={"mood": tiny_status.get("mood"), "current_program": tiny_status.get("current_program_type")},
            ),
            CognitionNode(
                id="dream_layer",
                label="Dream Layer",
                kind="creative_sandbox",
                zone="tool",
                layers=["live_brain", "tasks", "memory", "forge"],
                state="active" if expansion["dream_briefs"] or expansion["style_profiles"] else self._startup_state(startup_subsystems, "dream_layer", "ready"),
                emphasis=min(1.0, 0.32 + ((expansion["dream_briefs"] + expansion["style_profiles"]) / 8.0)),
                metrics={
                    "briefs": expansion["dream_briefs"],
                    "style_profiles": expansion["style_profiles"],
                    "latest_mood": expansion.get("latest_style_profile", {}).get("mood"),
                    "latest_theme": expansion.get("latest_style_profile", {}).get("theme"),
                },
            ),
            CognitionNode(
                id="governed_files",
                label="Governed Files",
                kind="tool",
                zone="tool",
                layers=["live_brain", "tasks", "forge"],
                state="active" if file_activity else "idle",
                emphasis=0.72 if file_activity else 0.28,
                metrics={"policy": "project_roots_only"},
            ),
            CognitionNode(
                id="forge_pipeline",
                label="Forge Pipeline",
                kind="forge",
                zone="forge",
                layers=["forge", "live_brain"],
                state=forge_state,
                emphasis=0.86 if forge_state == "active" else 0.25,
                metrics={
                    "bootstrap": backend.get("bootstrap", {}).get("status"),
                    "validation": "active" if validation_activity else "idle",
                },
            ),
            CognitionNode(
                id="sensor_microphone",
                label="Microphone",
                kind="sensor",
                zone="sensor",
                layers=["live_brain", "sensors"],
                state="active" if sensors.get("microphone", {}).get("mode_enabled") else self._startup_state(startup_subsystems, "sensors", sensors.get("microphone", {}).get("can_hear_state", "manual_only")),
                emphasis=0.64 if sensors.get("microphone", {}).get("mode_enabled") else 0.22,
                metrics=sensors.get("microphone", {}),
            ),
            CognitionNode(
                id="sensor_speaker",
                label="Speaker",
                kind="sensor",
                zone="sensor",
                layers=["live_brain", "sensors"],
                state=self._startup_state(startup_subsystems, "sensors", sensors.get("speaker", {}).get("can_speak_state", "manual_only")),
                emphasis=0.5,
                metrics=sensors.get("speaker", {}),
            ),
            CognitionNode(
                id="sensor_camera",
                label="Camera",
                kind="sensor",
                zone="sensor",
                layers=["live_brain", "sensors", "faults"],
                state="active" if sensors.get("camera", {}).get("mode_enabled") else self._startup_state(startup_subsystems, "sensors", sensors.get("camera", {}).get("can_see_state", "manual_only")),
                emphasis=0.64 if sensors.get("camera", {}).get("mode_enabled") else 0.22,
                metrics=sensors.get("camera", {}),
            ),
            CognitionNode(
                id="sentinel",
                label="OBEOS Sentinel",
                kind="sentinel",
                zone="sensor",
                layers=["live_brain", "sensors", "memory", "faults"],
                state="active" if expansion["sentinel_detections"] else self._startup_state(startup_subsystems, "sensors", "manual_only"),
                emphasis=min(1.0, 0.25 + (expansion["sentinel_detections"] / 12.0)),
                metrics={
                    "detections": expansion["sentinel_detections"],
                    "raw_artifacts": expansion["sentinel_raw_artifacts"],
                    "capture_profile": expansion["capture_profile"],
                },
            ),
        ]

        for agent_name in ("jarvis", "coding", "lilo", "stitch", "voice"):
            reports = [event for event in trace.get("events", []) if event.get("event_type") == "agent_report" and str((event.get("payload") or {}).get("agent")) == agent_name]
            score = float(reports[-1].get("payload", {}).get("score", 0.0) or 0.0) if reports else 0.0
            state = "active" if selected_agent == agent_name else "warning" if reports else "idle"
            nodes.append(
                CognitionNode(
                    id=f"agent_{agent_name}",
                    label=self._display_agent_label(agent_name),
                    kind="agent",
                    zone="agent",
                    layers=["live_brain", "tasks", "faults"],
                    state=state,
                    emphasis=0.86 if selected_agent == agent_name else min(0.7, 0.2 + score),
                    metrics={"selected": selected_agent == agent_name, "score": score},
                )
            )
        return nodes

    def _build_edges(
        self,
        *,
        awareness: Dict[str, Any],
        trace: Dict[str, Any],
        actions: Dict[str, Any],
        tiny_status: Dict[str, Any],
        layer: str,
        expansion: Dict[str, Any],
        startup: Dict[str, Any],
    ) -> List[CognitionEdge]:
        trace_payloads = [event.get("payload") or {} for event in trace.get("events", [])]
        memory_hits = self._memory_hits(trace)
        selected_agent = self._normalize_agent_name(
            trace.get("selection", {}).get("selected_agent")
            or trace.get("selected_agent")
            or "jarvis"
        )
        queue = awareness.get("observed", {}).get("queue", {})
        edges = [
            CognitionEdge(
                id="edge_core_daise",
                source="blackfong_core",
                target="daise_interface",
                layers=["live_brain", "tasks", "faults"],
                state="active" if trace.get("request_id") else self._startup_overall_state(startup, "warming"),
                confidence=0.95,
                urgency=min(1.0, float(queue.get("pending_requests", 0)) / 5.0),
                label="governance",
            ),
            CognitionEdge(
                id="edge_daise_memory",
                source="daise_interface",
                target="memory_session",
                layers=["live_brain", "memory"],
                state="active" if memory_hits else self._startup_state((startup or {}).get("subsystems", {}), "session_memory", "ready"),
                confidence=0.82,
                urgency=min(1.0, memory_hits / 6.0),
                label="retrieval",
            ),
            CognitionEdge(
                id="edge_daise_pinned",
                source="daise_interface",
                target="memory_pinned",
                layers=["live_brain", "memory"],
                state="active" if memory_hits else self._startup_state((startup or {}).get("subsystems", {}), "pinned_memory", "ready"),
                confidence=0.78,
                urgency=min(1.0, memory_hits / 8.0),
                label="recall",
            ),
            CognitionEdge(
                id="edge_memory_chamber_archive",
                source="memory_chamber",
                target="blackbox_archive",
                layers=["live_brain", "memory", "sensors"],
                state="active" if expansion["sealed_capsules"] else self._startup_state((startup or {}).get("subsystems", {}), "black_box_archive", "ready"),
                confidence=0.86,
                urgency=min(1.0, expansion["sealed_capsules"] / 6.0),
                label="capsules",
            ),
            CognitionEdge(
                id="edge_sentinel_memory",
                source="sentinel",
                target="memory_chamber",
                layers=["live_brain", "sensors", "memory"],
                state="active" if expansion["sentinel_detections"] else self._startup_state((startup or {}).get("subsystems", {}), "sensors", "manual_only"),
                confidence=0.84,
                urgency=min(1.0, expansion["sentinel_detections"] / 10.0),
                label="detections",
            ),
            CognitionEdge(
                id="edge_core_backend",
                source="blackfong_core",
                target="backend_model",
                layers=["live_brain", "forge", "faults"],
                state=self._startup_state((startup or {}).get("subsystems", {}), "backend_model", awareness.get("observed", {}).get("backend", {}).get("status", "warming")),
                confidence=0.9,
                urgency=min(1.0, float(queue.get("pending_requests", 0)) / 4.0),
                label="runtime",
            ),
            CognitionEdge(
                id="edge_core_queue",
                source="blackfong_core",
                target="operator_queue",
                layers=["live_brain", "tasks", "forge"],
                state="active" if actions.get("pending_actions") else self._startup_state((startup or {}).get("subsystems", {}), "operator_queue", "ready"),
                confidence=0.84,
                urgency=min(1.0, float(actions.get("pending_actions", 0)) / 6.0),
                label="operator",
            ),
            CognitionEdge(
                id="edge_core_forge",
                source="blackfong_core",
                target="forge_pipeline",
                layers=["live_brain", "forge"],
                state="active" if awareness.get("observed", {}).get("backend", {}).get("bootstrap", {}).get("status") == "warming" else self._startup_overall_state(startup, "warming"),
                confidence=0.8,
                urgency=0.62,
                label="build",
            ),
            CognitionEdge(
                id="edge_daise_tiny",
                source="daise_interface",
                target="tinyprogrammer",
                layers=["live_brain", "tasks", "forge", "faults"],
                state="active" if tiny_status.get("online") else self._startup_state((startup or {}).get("subsystems", {}), "tinyprogrammer", "degraded"),
                confidence=0.74,
                urgency=0.52,
                label="companion",
            ),
            CognitionEdge(
                id="edge_dream_tiny",
                source="dream_layer",
                target="tinyprogrammer",
                layers=["live_brain", "tasks", "memory", "forge"],
                state="active" if expansion["dream_briefs"] or expansion["style_profiles"] else self._startup_state((startup or {}).get("subsystems", {}), "dream_layer", "ready"),
                confidence=0.88,
                urgency=min(1.0, (expansion["dream_briefs"] + expansion["style_profiles"]) / 8.0),
                label="style",
            ),
            CognitionEdge(
                id="edge_daise_files",
                source="daise_interface",
                target="governed_files",
                layers=["live_brain", "tasks", "forge"],
                state="active" if any(action.get("action", "").startswith("file_") for action in actions.get("actions", [])) else self._startup_state((startup or {}).get("subsystems", {}), "blackfong_core", "ready"),
                confidence=0.84,
                urgency=0.56,
                label="workflow",
            ),
            CognitionEdge(
                id="edge_sensor_mic",
                source="sensor_microphone",
                target="daise_interface",
                layers=["live_brain", "sensors"],
                state="active" if awareness.get("sensors", {}).get("microphone", {}).get("mode_enabled") else self._startup_state((startup or {}).get("subsystems", {}), "sensors", "manual_only"),
                confidence=float(awareness.get("sensors", {}).get("microphone", {}).get("confidence", 0.6)),
                urgency=0.36,
                label="listen",
            ),
            CognitionEdge(
                id="edge_sensor_camera",
                source="sensor_camera",
                target="daise_interface",
                layers=["live_brain", "sensors"],
                state="active" if awareness.get("sensors", {}).get("camera", {}).get("mode_enabled") else self._startup_state((startup or {}).get("subsystems", {}), "sensors", "manual_only"),
                confidence=float(awareness.get("sensors", {}).get("camera", {}).get("confidence", 0.6)),
                urgency=0.36,
                label="vision",
            ),
        ]
        edges.append(
            CognitionEdge(
                id=f"edge_daise_{selected_agent}",
                source="daise_interface",
                target=f"agent_{selected_agent}",
                layers=["live_brain", "tasks", "faults"],
                state="active",
                confidence=float(trace.get("selection", {}).get("confidence", 0.82) or 0.82),
                urgency=0.78,
                label="route",
            )
        )
        for payload in trace_payloads:
            if payload.get("agent") == "coding" and "tools_invoked" in payload:
                edges.append(
                    CognitionEdge(
                        id="edge_coding_files",
                        source="agent_coding",
                        target="governed_files",
                        layers=["live_brain", "tasks", "forge"],
                        state="active",
                        confidence=0.73,
                        urgency=0.7,
                        label="analysis",
                    )
                )
        return edges

    def _normalize_agent_name(self, agent_name: Any) -> str:
        normalized = str(agent_name or "jarvis").strip().lower()
        if normalized in {"daise", "d.a.i.s.e", "general", "fallback", "auto"}:
            return "jarvis"
        if normalized not in {"jarvis", "coding", "lilo", "stitch", "voice"}:
            return "jarvis"
        return normalized

    @staticmethod
    def _startup_state(subsystems: Dict[str, Any], key: str, fallback: str) -> str:
        entry = subsystems.get(key) or {}
        return str(entry.get("state") or fallback or "warming")

    @staticmethod
    def _startup_overall_state(startup: Dict[str, Any], fallback: str) -> str:
        return str((startup or {}).get("overall_state") or fallback or "warming")

    def _build_overlays(
        self,
        *,
        awareness: Dict[str, Any],
        trace: Dict[str, Any],
        actions: Dict[str, Any],
        tiny_status: Dict[str, Any],
        faults: List[Dict[str, Any]],
        layer: str,
        startup: Dict[str, Any],
    ) -> Dict[str, Any]:
        observed = awareness.get("observed", {})
        latency = observed.get("latency", {})
        queue = observed.get("queue", {})
        backend = observed.get("backend", {})
        memory = observed.get("memory", {})
        selection = trace.get("selection", {})
        return {
            "layer": layer,
            "queue": {
                "pending_requests": int(queue.get("pending_requests", 0)),
                "backlog_health": queue.get("backlog_health", "unknown"),
            },
            "latency": {
                "average_response_time_s": float(latency.get("average_response_time_s", 0.0)),
                "current_request_ms": (trace.get("latency_observation") or {}).get("execution_time_ms"),
            },
            "backend": {
                "provider": backend.get("provider"),
                "model": backend.get("configured_model"),
                "status": backend.get("status"),
                "retries": (trace.get("latency_observation") or {}).get("ollama_attempts"),
            },
            "memory": {
                "session_messages": self._memory_hits(trace),
                "pinned_memories": int(memory.get("pinned_memories", 0)),
                "summaries": int(memory.get("recent_summaries", 0)),
            },
            "confidence": float(selection.get("score_gap", 0.0) or selection.get("confidence", 0.0) or 0.66),
            "tinyprogrammer": {
                "online": bool(tiny_status.get("online")),
                "state": tiny_status.get("state") or tiny_status.get("mode"),
                "mood": tiny_status.get("mood"),
            },
            "startup": {
                "overall_state": startup.get("overall_state"),
                "ready_but_degraded": bool(startup.get("ready_but_degraded")),
                "counts": startup.get("counts", {}),
            },
            "fault_count": len(faults),
        }

    def _build_trace_ribbon(self, trace: Dict[str, Any]) -> List[Dict[str, Any]]:
        events = {event.get("event_type"): event for event in trace.get("events", [])}
        status = str(trace.get("status") or "idle")
        current_phase = str(trace.get("phase") or "idle")
        steps = [
            ("input", "INPUT", "accepted"),
            ("intent", "INTENT", "policy"),
            ("memory", "MEMORY", "memory"),
            ("routing", "ROUTING", "routing"),
            ("tool_agent", "TOOL/AGENT", "agent"),
            ("validation", "VALIDATION", "evaluation"),
            ("response", "RESPONSE", "complete"),
        ]
        completed = {
            "input": bool(trace.get("request_id")),
            "intent": "policy_decision" in events or "routing_decision" in events,
            "memory": "memory_retrieval" in events,
            "routing": "routing_decision" in events,
            "tool_agent": "agent_report" in events or current_phase == "agent",
            "validation": "agent_selection" in events or "agent_evaluation" in events or current_phase == "evaluation",
            "response": status == "complete" or "agent_response" in events,
        }
        if status == "error":
            completed["response"] = False

        ribbon = []
        for step_id, label, phase_name in steps:
            step_status = "pending"
            if completed[step_id]:
                step_status = "complete"
            if current_phase == phase_name and status not in {"complete", "error"}:
                step_status = "active"
            if status == "error" and step_id in {"tool_agent", "validation", "response"}:
                step_status = "fault"
            ribbon.append({"id": step_id, "label": label, "status": step_status})
        return ribbon

    def _build_summaries(
        self,
        *,
        awareness: Dict[str, Any],
        trace: Dict[str, Any],
        actions: Dict[str, Any],
        tiny_status: Dict[str, Any],
        sessions: List[Dict[str, Any]],
        recent_events: List[Dict[str, Any]],
        faults: List[Dict[str, Any]],
        startup: Dict[str, Any],
    ) -> Dict[str, Any]:
        selection = trace.get("selection", {})
        selected_agent = selection.get("selected_agent") or trace.get("selected_agent") or "D.A.I.S.E"
        focus_label = f"{selected_agent} / {trace.get('phase', 'idle')}" if trace.get("request_id") else "System idle"
        return {
            "focus_label": focus_label,
            "active_session_count": len(sessions),
            "active_action_count": int(actions.get("pending_actions", 0)),
            "latest_event": recent_events[0]["message"] if recent_events else "No recent events.",
            "top_fault": faults[0]["message"] if faults else "No active faults.",
            "tiny_label": f"{tiny_status.get('state') or tiny_status.get('mode') or 'offline'} / {tiny_status.get('mood') or 'unknown'}",
            "startup_state": startup.get("overall_state", "warming"),
            "startup_degraded": bool(startup.get("ready_but_degraded")),
        }

    def _collect_faults(
        self,
        *,
        awareness: Dict[str, Any],
        trace: Dict[str, Any],
        actions: Dict[str, Any],
        tiny_status: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        faults: List[Dict[str, Any]] = []
        backend = awareness.get("observed", {}).get("backend", {})
        if backend.get("status") not in {"available", "ready"}:
            faults.append(self.build_fault(code="backend_state", message=f"Backend is {backend.get('status', 'unknown')}.", severity="warning", source="backend"))
        for contradiction in awareness.get("health", {}).get("contradictions", []) or []:
            faults.append(self.build_fault(code="consistency_conflict", message=str(contradiction.get("message") or "Contradictory observation detected."), severity="error", source="awareness", meta=contradiction))
        if trace.get("status") == "error":
            faults.append(self.build_fault(code="request_error", message=str(trace.get("phase_message") or "Request failed."), severity="error", source="request", request_id=trace.get("request_id"), session_id=trace.get("session_id")))
        latency = trace.get("latency_observation") or {}
        if latency.get("fallback_reason") or latency.get("timeout_reason"):
            faults.append(self.build_fault(code="backend_fallback", message=str(latency.get("fallback_reason") or latency.get("timeout_reason")), severity="warning", source="latency", request_id=trace.get("request_id")))
        if tiny_status.get("last_error_code"):
            faults.append(
                self.build_fault(
                    code=str(tiny_status.get("last_error_code")),
                    message=str(tiny_status.get("last_error_summary") or "TinyProgrammer reported a degraded startup condition."),
                    severity="warning",
                    source="tinyprogrammer",
                )
            )
        for action in actions.get("actions", []):
            if action.get("status") in {"failed", "blocked", "expired"}:
                faults.append(
                    self.build_fault(
                        code=f"operator_{action.get('status')}",
                        message=str(action.get("text") or action.get("human_reason") or action.get("action")),
                        severity="warning" if action.get("status") != "failed" else "error",
                        source="operator_action",
                        request_id=action.get("request_id"),
                        session_id=action.get("session_id"),
                        meta={"action": action.get("action"), "reason_code": action.get("reason_code")},
                    )
                )
        if tiny_status.get("online") is False and tiny_status.get("stream_reason") not in {None, "available"}:
            faults.append(
                self.build_fault(
                    code="tinyprogrammer_state",
                    message=str(tiny_status.get("stream_human_reason") or tiny_status.get("live_control_human_reason") or "TinyProgrammer is offline."),
                    severity="warning",
                    source="tinyprogrammer",
                )
            )
        return faults[:8]

    def _build_summaries(
        self,
        *,
        awareness: Dict[str, Any],
        trace: Dict[str, Any],
        actions: Dict[str, Any],
        tiny_status: Dict[str, Any],
        sessions: List[Dict[str, Any]],
        recent_events: List[Dict[str, Any]],
        faults: List[Dict[str, Any]],
        startup: Dict[str, Any],
    ) -> Dict[str, Any]:
        selection = trace.get("selection", {})
        selected_agent = self._display_agent_label(selection.get("selected_agent") or trace.get("selected_agent") or "daise")
        focus_label = f"{selected_agent} / {trace.get('phase', 'idle')}" if trace.get("request_id") else "System idle"
        return {
            "focus_label": focus_label,
            "active_session_count": len(sessions),
            "active_action_count": int(actions.get("pending_actions", 0)),
            "latest_event": recent_events[0]["message"] if recent_events else "No recent events.",
            "top_fault": faults[0]["message"] if faults else "No active faults.",
            "tiny_label": f"{tiny_status.get('state') or tiny_status.get('mode') or 'offline'} / {tiny_status.get('mood') or 'unknown'}",
            "startup_state": startup.get("overall_state", "warming"),
            "startup_degraded": bool(startup.get("ready_but_degraded")),
        }

    def render_ascii_from_parts(
        self,
        *,
        layer: str,
        trace_ribbon: List[Dict[str, Any]],
        overlays: Dict[str, Any],
        summaries: Dict[str, Any],
        faults: List[Dict[str, Any]],
        nodes: List[Dict[str, Any]],
    ) -> str:
        header = "THOUGHTGLASS"
        divider = "-" * 40
        route_text = " -> ".join(step["label"] for step in trace_ribbon if step["status"] != "pending") or "IDLE"
        active_nodes = [node for node in nodes if node.get("state") in {"active", "warning", "fault"}][:6]
        node_lines = [f"{node['label']:<18} {node['state'].upper()}" for node in active_nodes] or ["SYSTEM             IDLE"]
        faults_text = ", ".join(fault["message"] for fault in faults[:2]) if faults else "none"
        zone_lines = self._zone_status_lines(nodes)
        return "\n".join(
            [
                header,
                divider,
                f"LAYER       {layer.upper()}",
                f"FOCUS       {summaries.get('focus_label', 'None')}",
                f"QUEUE       {self._ascii_gauge(overlays['queue'].get('pending_requests', 0), max_value=6)}",
                f"LATENCY     {self._ascii_gauge(overlays['latency'].get('average_response_time_s', 0), max_value=12)}",
                f"CONFIDENCE  {self._ascii_gauge(overlays.get('confidence', 0.0), max_value=1.0, width=6)}",
                "",
                "REGIONS",
                *zone_lines,
                "",
                "ROUTE FLOW",
                route_text,
                "",
                "ACTIVE NODES",
                *node_lines,
                "",
                f"TINY        {summaries.get('tiny_label', 'unknown')}",
                f"FAULTS      {faults_text}",
                f"EVENT       {summaries.get('latest_event', 'No recent events.')}",
            ]
        )

    @staticmethod
    def _display_agent_label(agent_name: Any) -> str:
        normalized = str(agent_name or "daise").strip().lower()
        mapping = {
            "jarvis": "D.A.I.S.E General",
            "daise": "D.A.I.S.E",
            "d.a.i.s.e": "D.A.I.S.E",
            "coding": "Coding",
            "lilo": "Lilo",
            "stitch": "Stitch",
            "voice": "Voice",
        }
        return mapping.get(normalized, normalized.upper() if normalized else "D.A.I.S.E")

    @staticmethod
    def _zone_status_lines(nodes: List[Dict[str, Any]]) -> List[str]:
        zones = {
            "center": "CORE",
            "memory": "MEMORY",
            "agent": "AGENTS",
            "tool": "TOOLS",
            "sensor": "SENSORS",
            "forge": "FORGE",
            "task": "TASKS",
        }
        lines: List[str] = []
        for zone, label in zones.items():
            zone_nodes = [node for node in nodes if node.get("zone") == zone]
            active_count = sum(1 for node in zone_nodes if node.get("state") in {"active", "warning", "fault"})
            lines.append(f"{label:<11}{active_count:>2} active / {max(len(zone_nodes), 1):<2} nodes")
        return lines

    @staticmethod
    def _ascii_gauge(value: float, *, max_value: float, width: int = 5) -> str:
        if max_value <= 0:
            return "[" + ("." * width) + "]"
        ratio = max(0.0, min(1.0, float(value) / float(max_value)))
        filled = int(round(ratio * width))
        return "[" + ("#" * filled) + ("." * max(0, width - filled)) + "]"

    def _memory_hits(self, trace: Dict[str, Any]) -> int:
        retrieval = next((event for event in reversed(trace.get("events", [])) if event.get("event_type") == "memory_retrieval"), None)
        payload = (retrieval or {}).get("payload") or {}
        return int(payload.get("session_messages", 0)) + int(payload.get("session_summaries", 0)) + int(payload.get("global_memories", 0))

    def _cognitive_expansion_summary(self) -> Dict[str, Any]:
        return {
            "memory_artifacts": self._event_count("memory_artifact_created"),
            "sealed_capsules": self._event_count("blackbox_capsule_sealed"),
            "sentinel_detections": self._event_count("sentinel_detection_event"),
            "sentinel_raw_artifacts": self._event_count("sentinel_raw_artifact_captured"),
            "style_profiles": self._event_count("daise_style_profile_generated"),
            "dream_briefs": self._event_count("dream_layer_brief_generated"),
            "capture_profile_changes": self._event_count("archive_capture_profile_changed"),
            "capture_profile": self._current_capture_profile(),
            "latest_style_profile": self._latest_payload("daise_style_profile_generated"),
            "latest_memory_artifact": self._latest_payload("memory_artifact_created"),
        }

    def _event_count(self, event_type: str) -> int:
        event_store = getattr(self.memory_service, "event_store", None)
        if event_store is None:
            return 0
        try:
            return int(event_store.count_events(type=event_type))
        except Exception:
            return 0

    def _latest_payload(self, event_type: str) -> Dict[str, Any]:
        event_store = getattr(self.memory_service, "event_store", None)
        if event_store is None:
            return {}
        try:
            events = event_store.list_events_by_type(event_type, limit=1)
        except Exception:
            return {}
        if not events:
            return {}
        payload = events[0].payload or {}
        if event_type == "daise_style_profile_generated":
            return payload.get("style_profile") if isinstance(payload.get("style_profile"), dict) else payload
        return payload

    def _current_capture_profile(self) -> str:
        payload = self._latest_payload("archive_capture_profile_changed")
        if payload.get("activated") is True and payload.get("profile"):
            return str(payload["profile"])
        return "bounded_raw"

    @staticmethod
    def _normalize_layer(layer: str) -> str:
        normalized = str(layer or "live_brain").strip().lower().replace(" ", "_")
        supported = {entry["id"] for entry in THOUGHTGLASS_LAYERS}
        return normalized if normalized in supported else "live_brain"

    @staticmethod
    def _gauge(value: float, *, max_value: float, width: int = 5) -> str:
        if max_value <= 0:
            return "░" * width
        ratio = max(0.0, min(1.0, float(value) / float(max_value)))
        filled = int(round(ratio * width))
        return "▓" * filled + "░" * max(0, width - filled)
