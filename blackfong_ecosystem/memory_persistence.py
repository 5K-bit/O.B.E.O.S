"""Conversation persistence operations, preserving the public service interface."""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from .events import Event

WORKFLOW_EVENT_TYPES = {
    "routing_decision",
    "memory_retrieval",
    "agent_report",
    "agent_evaluation",
    "agent_selection",
    "tinyprogrammer_status",
    "tinyprogrammer_state_transition",
    "tinyprogrammer_program_created",
    "tinyprogrammer_program_archived",
    "tinyprogrammer_reflection",
    "tinyprogrammer_brief_applied",
    "tinyprogrammer_feedback",
    "awareness_snapshot",
    "performance_snapshot",
    "latency_observation",
    "routing_quality_observation",
    "memory_retrieval_observation",
    "backend_health_observation",
    "environment_snapshot",
    "sensor_capability_snapshot",
    "audio_device_observation",
    "camera_device_observation",
    "queue_pressure_observation",
    "consistency_conflict",
    "request_outcome",
    "agent_trust_update",
    "policy_decision",
    "capability_snapshot",
    "adapter_health_observation",
    "agent_admission_decision",
    "learner_profile_update",
    "learning_attempt",
    "resource_budget_observation",
    "spine_conflict_detected",
    "spine_mode_changed",
    "vision_tool_result",
    "installer_plan_generated",
    "identity_resolution_event",
    "operator_consent",
    "file_workflow_state",
    "file_proposal_created",
    "file_proposal_expired",
    "file_pre_apply_validation",
    "file_change_applied",
    "file_post_apply_validation",
    "file_scope_discovery",
    "file_scope_root_approved",
    "file_scope_map",
    "file_scope_read",
    "knowledge_source_added",
    "knowledge_cleaned",
    "knowledge_chunked",
    "knowledge_embedded",
    "knowledge_ingestion_failed",
    "knowledge_phase1_started",
    "knowledge_inventory_created",
    "knowledge_coverage_created",
    "knowledge_timeline_created",
    "knowledge_memory_chamber_graph_updated",
    "knowledge_phase1_completed",
    "knowledge_phase1_failed",
    "knowledge_gaps_detected",
    "knowledge_summary_created",
    "knowledge_librarian_answered",
    "perception_gate_decision",
    "vision_sensor_observation",
    "screen_snapshot_observation",
    "audio_sensor_observation",
    "companion_brief_scope_decision",
    "tinyprogrammer_process_state",
    "notification_created",
    "notification_read",
    "notification_action_taken",
    "notification_delivery_result",
    "operator_intent_created",
    "operator_intent_state",
    "operator_intent_expired",
    "operator_action_result",
    "state_change_summary",
    "frame_tap_event",
    "frame_imu_observation",
    "frame_display_update",
}


class MemoryPersistence:
    def persist_user_turn(
        self,
        *,
        session_id: str,
        correlation_id: str,
        text: str,
        modality: str = "text",
        routed_agent: str = "daise",
        analysis_mode: str = "standard",
        agent_override: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Event:
        event = Event.new(
            source="user",
            type="agent_query",
            payload={
                "text": text,
                "modality": modality,
                "agent": routed_agent,
                "analysis_mode": analysis_mode,
                "agent_override": agent_override,
            },
            confidence=1.0,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["conversation", "user_turn", modality],
        )
        self.event_store.add_event(event)
        self._store_lifecycle_item(event, "event")
        self._capture_derived_user_memories(
            session_id=session_id,
            source_event_id=event.id,
            text=text,
        )
        return event

    def persist_agent_response(
        self,
        *,
        session_id: str,
        correlation_id: str,
        agent_name: str,
        response_text: str,
        confidence: float,
        modality: str = "text",
        parent_event_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        execution_time_ms: Optional[float] = None,
        success: bool = True,
        extras: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> Event:
        payload = {
            "response": response_text,
            "text": response_text,
            "agent": agent_name,
            "modality": modality,
            "success": success,
            "provider": provider or "",
            "model": model or "",
            "execution_time_ms": execution_time_ms or 0.0,
        }
        if extras:
            payload.update(extras)
        backend_metadata = payload.get("backend_metadata")
        if not payload.get("response_path") and isinstance(backend_metadata, dict):
            response_path = str(backend_metadata.get("response_path") or "").strip()
            if response_path:
                payload["response_path"] = response_path

        event = Event.new(
            source=agent_name,  # type: ignore[arg-type]
            type="agent_response",
            payload=payload,
            confidence=confidence,
            parent_event=parent_event_id,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["conversation", "agent_response", agent_name],
        )
        self.event_store.add_event(event)
        self._store_lifecycle_item(event, "event")
        return event

    def persist_workflow_event(
        self,
        *,
        source: str,
        event_type: str,
        payload: Dict[str, Any],
        confidence: float = 1.0,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Event:
        event = Event.new(
            source=source,  # type: ignore[arg-type]
            type=event_type,
            payload=payload,
            confidence=confidence,
            parent_event=parent_event_id,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=tags or ["workflow", event_type],
        )
        self.event_store.add_event(event)
        lifecycle_type = "trace" if event_type in WORKFLOW_EVENT_TYPES else "event"
        self._store_lifecycle_item(event, lifecycle_type)
        return event
