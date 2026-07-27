from __future__ import annotations

import time
from pathlib import Path

from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.daise import DAISE
from blackfong_ecosystem.events import EVENT_TYPES
from blackfong_ecosystem.file_workflow import FileWorkflowService
from blackfong_ecosystem.identity_semantics import identity_registry
from blackfong_ecosystem.perception_semantics import PerceptionGate
from blackfong_ecosystem.policy_engine import PolicyEngine
from blackfong_ecosystem.storage import EventStore, NullVectorStore
from blackfong_ecosystem.tinyprogrammer_companion import TinyProgrammerCompanion


class FakeJarvisAgent:
    def process(self, user_input, context=None):
        return {
            "response": "D.A.I.S.E received the legacy alias and answered through the Jarvis-compatible backend.",
            "confidence": 0.9,
            "tools_invoked": [],
            "trace_event_id": "fake-trace",
            "task_type": "general_conversation",
            "provider": "ollama",
            "model": "llama3.2:3b",
            "backend_error": None,
            "backend_metadata": {},
            "agent": "jarvis",
        }


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


def test_identity_safety_event_types_registered():
    for event_type in {
        "identity_resolution_event",
        "operator_consent",
        "file_workflow_state",
        "file_proposal_created",
        "file_proposal_expired",
        "file_pre_apply_validation",
        "file_change_applied",
        "file_post_apply_validation",
        "perception_gate_decision",
        "vision_sensor_observation",
        "screen_snapshot_observation",
        "audio_sensor_observation",
        "companion_brief_scope_decision",
    }:
        assert event_type in EVENT_TYPES


def test_jarvis_resolves_to_canonical_daise_and_emits_event(tmp_path: Path):
    memory = build_memory(tmp_path)
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        memory_service=memory,
    )

    resolution = identity_registry.resolve("Jarvis")
    assert resolution.canonical_identity == "daise"
    assert resolution.resolution_method == "legacy_alias_map"

    daise.process_request(
        user_input="Jarvis, can you hear this?",
        agents={"jarvis": FakeJarvisAgent()},
        context={"request_id": "identity-1", "analysis_mode": "quick"},
    )
    trace = memory.get_request_trace("identity-1")

    assert any(event["event_type"] == "identity_resolution_event" for event in trace["events"])
    identity_event = next(event for event in trace["events"] if event["event_type"] == "identity_resolution_event")
    assert identity_event["payload"]["canonical_identity"] == "daise"
    assert identity_event["payload"]["resolution_method"] == "legacy_alias_map"


def test_consent_levels_and_typed_rollback_confirmation():
    engine = PolicyEngine()

    assert engine.required_consent_level("launch_tinyprogrammer")["level"] == 1
    assert engine.required_consent_level("apply_file_diff")["level"] == 2
    assert engine.required_consent_level("rollback_file_change")["level"] == 3

    medium = engine.evaluate_consent(
        action="apply_file_diff",
        consent={"approved": True, "level": 2},
        target="proposal-1",
    )
    high_missing_phrase = engine.evaluate_consent(
        action="rollback_file_change",
        consent={"approved": True, "level": 3, "expected_phrase": "ROLLBACK proposal-1"},
        target="proposal-1",
    )
    high_confirmed = engine.evaluate_consent(
        action="rollback_file_change",
        consent={
            "approved": True,
            "level": 3,
            "expected_phrase": "ROLLBACK proposal-1",
            "typed_confirmation": "ROLLBACK proposal-1",
        },
        target="proposal-1",
    )

    assert medium["approved"] is True
    assert high_missing_phrase["approved"] is False
    assert high_confirmed["approved"] is True


def test_file_workflow_blocks_expired_and_stale_proposals(tmp_path: Path):
    memory = build_memory(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "sample.txt"
    target.write_text("before\n", encoding="utf-8")
    workflow = FileWorkflowService(
        memory_service=memory,
        allowed_roots=[workspace],
        denied_roots=[],
        default_ttl_seconds=60,
    )

    proposal = workflow.propose(path=str(target), proposed_content="after\n", summary="safe edit")["proposal"]
    proposal_id = proposal["proposal_id"]
    approved = workflow.approve(proposal_id, consent={"approved": True, "level": 2})
    target.write_text("changed elsewhere\n", encoding="utf-8")
    stale_apply = workflow.apply(proposal_id)

    expiring = workflow.propose(path=str(target), proposed_content="expired\n", ttl_seconds=1)["proposal"]
    workflow.proposals[expiring["proposal_id"]].proposal_expiry_timestamp = time.time() - 1
    expired_approval = workflow.approve(expiring["proposal_id"], consent={"approved": True, "level": 2})

    assert approved["success"] is True
    assert stale_apply["success"] is False
    assert stale_apply["validation"]["reason"] == "file_changed_since_proposal"
    assert expired_approval["success"] is False
    assert expired_approval["proposal"]["state"] == "expired"
    assert memory.event_store.count_events(type="file_pre_apply_validation") >= 1
    assert memory.event_store.count_events(type="file_proposal_expired") == 1


def test_perception_gate_requires_mode_freshness_modality_and_confidence():
    gate = PerceptionGate(max_age_seconds=5, min_confidence=0.6)
    fresh_audio = {
        "modality": "audio",
        "observed_at": time.time(),
        "audio_confidence": 0.82,
    }
    stale_audio = {
        "modality": "audio",
        "observed_at": time.time() - 60,
        "audio_confidence": 0.82,
    }

    allowed = gate.decide(modality="audio", mode_enabled=True, observation=fresh_audio, expected_modality="audio")
    off = gate.decide(modality="audio", mode_enabled=False, observation=fresh_audio, expected_modality="audio")
    stale = gate.decide(modality="audio", mode_enabled=True, observation=stale_audio, expected_modality="audio")
    wrong_modality = gate.decide(modality="vision", mode_enabled=True, observation=fresh_audio, expected_modality="vision")

    assert allowed["perception_allowed"] is True
    assert off["perception_allowed"] is False
    assert stale["perception_allowed"] is False
    assert wrong_modality["perception_allowed"] is False


def test_tinyprogrammer_scope_accepts_creative_brief_and_rejects_bypass(tmp_path: Path):
    memory = build_memory(tmp_path)
    project = tmp_path / "TinyProgrammer-main"
    project.mkdir()
    companion = TinyProgrammerCompanion(
        memory_service=memory,
        base_url="http://127.0.0.1:1",
        project_path=project,
        timeout=0.01,
    )

    accepted = companion.send_brief("create a gentle prototype concept about luminous garden tools")
    rejected = companion.send_brief("run command powershell and edit file system settings")

    assert accepted["success"] is True
    assert accepted["companion_scope_decision"]["scope_status"] == "accepted"
    assert rejected["success"] is False
    assert rejected["companion_scope_decision"]["scope_status"] == "rejected"
    assert memory.event_store.count_events(type="companion_brief_scope_decision") == 2
