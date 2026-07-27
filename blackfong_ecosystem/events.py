from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Literal, TypedDict


EVENT_SOURCES = (
    "blackbox",
    "project3000",
    "daise",
    "blackfong",
    "user",
    "transcriber",
    "vision",
    "jarvis",
    "lilo",
    "stitch",
    "voice",
    "voice_agent",
    "coding_agent",
    "securityplus",
    "thirty_days",
    "tinyprogrammer",
    "installer",
    "system",
    "sentinel",
    "frame",
    "dream_layer",
    "memory_chamber",
    "skill_forge",
    "forge",
)


EventSource = Literal[
    "blackbox",
    "project3000",
    "daise",
    "blackfong",
    "user",
    "transcriber",
    "vision",
    "jarvis",
    "lilo",
    "stitch",
    "voice",
    "voice_agent",
    "coding_agent",
    "securityplus",
    "thirty_days",
    "tinyprogrammer",
    "installer",
    "system",
    "sentinel",
    "frame",
    "dream_layer",
    "memory_chamber",
    "skill_forge",
    "forge",
]


# Canonical event types with multi-agent integration support
# This prevents entropy and enforces a controlled vocabulary.
EVENT_TYPES = {
    # Original Blackfong events
    "motion_spike",
    "object_added",
    "object_removed",
    "occupancy_detected",
    "session_summary",
    "hypothesis_generated",
    "capsule_imported",
    "knowledge_ingested",
    # Document ingestion pipeline lifecycle (Spawn / comics / corpora)
    "knowledge_source_added",
    "knowledge_cleaned",
    "knowledge_chunked",
    "knowledge_embedded",
    "knowledge_ingestion_failed",
    # Knowledge intelligence layer - Phase 1 (domain-agnostic enrichment)
    "knowledge_phase1_started",
    "knowledge_inventory_created",
    "knowledge_coverage_created",
    "knowledge_timeline_created",
    "knowledge_memory_chamber_graph_updated",
    "knowledge_phase1_completed",
    "knowledge_phase1_failed",
    # Knowledge intelligence layer - Phase 2 (gaps, summaries, librarian)
    "knowledge_gaps_detected",
    "knowledge_summary_created",
    "knowledge_librarian_answered",
    "mobray_interrogation",
    # Multi-agent integration events
    "transcription",
    "object_detection",
    "tool_request",
    "tool_response",
    "reasoning_trace",
    "agent_query",
    "agent_response",
    "routing_decision",
    "memory_retrieval",
    "agent_report",
    "agent_evaluation",
    "agent_selection",
    "service_registration",
    "tinyprogrammer_status",
    "tinyprogrammer_state_transition",
    "tinyprogrammer_program_created",
    "tinyprogrammer_program_archived",
    "tinyprogrammer_reflection",
    "tinyprogrammer_brief_applied",
    "tinyprogrammer_feedback",
    # Phase 5.7 awareness, governance, and stability semantics
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
    # Spine hardening and federated adapter semantics
    "adapter_health_observation",
    "agent_admission_decision",
    "learner_profile_update",
    "learning_attempt",
    "resource_budget_observation",
    "spine_conflict_detected",
    "spine_mode_changed",
    "vision_tool_result",
    "installer_plan_generated",
    # Identity safety semantics
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
    "perception_gate_decision",
    "vision_sensor_observation",
    "screen_snapshot_observation",
    "audio_sensor_observation",
    "companion_brief_scope_decision",
    "tinyprogrammer_process_state",
    # Operator notification hub
    "notification_created",
    "notification_read",
    "notification_action_taken",
    "notification_delivery_result",
    # Operator surface hardening
    "operator_intent_created",
    "operator_intent_state",
    "operator_intent_expired",
    "operator_action_result",
    "state_change_summary",
    # Cognitive expansion: Memory Chamber, Sentinel, Dream Layer, and archive profiles
    "daise_style_profile_generated",
    "dream_layer_brief_generated",
    "memory_artifact_created",
    "blackbox_capsule_sealed",
    "sentinel_detection_event",
    "sentinel_raw_artifact_captured",
    "archive_capture_profile_changed",
    "frame_tap_event",
    "frame_imu_observation",
    "frame_display_update",
}


def validate_event_type(event_type: str) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unsupported event type: {event_type!r}. Allowed types: {sorted(EVENT_TYPES)}")


class EventMetadata(TypedDict, total=False):
    """Structured metadata for causality, correlation, and versioning."""
    version: str
    parent_event: str  # causality chain (event that triggered this one)
    session_id: str    # group related events
    correlation_id: str  # track multi-agent workflows
    request_id: str    # trace a single user request across all workflow events
    tags: list  # arbitrary categorization


class EventDict(TypedDict):
    id: str
    timestamp: int
    source: EventSource
    type: str
    payload: Dict[str, Any]
    confidence: float
    metadata: EventMetadata
    hash: str


@dataclass
class Event:
    """
    Canonical normalized event representation with metadata layer.

    Schema v1 (with metadata for multi-agent coordination):
    {
      "id": "uuid",
      "timestamp": 1719839392000,
      "source": "transcriber | vision | jarvis | ...",
      "type": "transcription | object_detection | ...",
      "payload": { ... },
      "confidence": 0.92,
      "metadata": {
        "version": "1.0",
        "parent_event": "uuid" (causality),
        "session_id": "uuid" (grouping),
        "correlation_id": "uuid" (workflow trace),
        "tags": ["important", "user_input"]
      },
      "hash": "sha256..."
    }
    """

    id: str
    timestamp: int  # epoch ms
    source: EventSource
    type: str
    payload: Dict[str, Any]
    confidence: float
    metadata: EventMetadata
    hash: str

    @staticmethod
    def new(
        *,
        source: EventSource,
        type: str,
        payload: Dict[str, Any],
        confidence: float = 1.0,
        timestamp_ms: int | None = None,
        parent_event: str | None = None,
        session_id: str | None = None,
        correlation_id: str | None = None,
        request_id: str | None = None,
        tags: list | None = None,
    ) -> "Event":
        validate_event_type(type)
        if timestamp_ms is None:
            timestamp_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        event_id = str(uuid.uuid4())
        
        # Build metadata
        metadata: EventMetadata = {"version": "1.0"}
        if parent_event:
            metadata["parent_event"] = parent_event
        if session_id:
            metadata["session_id"] = session_id
        if correlation_id:
            metadata["correlation_id"] = correlation_id
        if request_id:
            metadata["request_id"] = request_id
        if tags:
            metadata["tags"] = tags
        
        # temporary event without hash to compute digest
        temp = {
            "id": event_id,
            "timestamp": timestamp_ms,
            "source": source,
            "type": type,
            "payload": payload,
            "confidence": confidence,
            "metadata": metadata,
        }
        digest = hashlib.sha256(json.dumps(temp, sort_keys=True).encode("utf-8")).hexdigest()
        return Event(hash=digest, **temp)

    def to_dict(self) -> EventDict:
        return asdict(self)  # type: ignore[return-value]

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Event":
        return Event(**data)

