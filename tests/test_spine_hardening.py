from __future__ import annotations

from click.testing import CliRunner

from blackfong_ecosystem.cli import cli
from blackfong_ecosystem.learner_profile import LearnerProfileService
from blackfong_ecosystem.spine_adapters import SpineAdapterRegistry
from blackfong_ecosystem.spine_contract import (
    ADAPTER_CONTRACT_VERSION,
    AuthoritySource,
    SpineSignal,
    resolve_authoritative_signal,
    validate_adapter_response,
)
from blackfong_ecosystem.storage import EventStore


def test_authority_order_logs_conflict(tmp_path):
    store = EventStore(tmp_path / "events.db")
    resolution = resolve_authoritative_signal(
        [
            SpineSignal("python.functions", "weak", AuthoritySource.LIVE_ADAPTER_RESPONSE, 0.95),
            SpineSignal("python.functions", "complete", AuthoritySource.CACHED_LOCAL_PROJECT_STATE, 0.9),
            SpineSignal("python.functions", "needs_review", AuthoritySource.EVENT_MEMORY, 0.7),
        ],
        event_store=store,
        context={"test": True},
    )

    assert resolution.conflict_detected is True
    assert resolution.winner.value == "needs_review"
    assert resolution.event_id is not None
    assert store.count_events(type="spine_conflict_detected") == 1


def test_adapter_response_contract_is_versioned():
    registry = SpineAdapterRegistry()
    response = registry.run("jarvis", "status").to_dict()

    assert response["adapter_contract_version"] == ADAPTER_CONTRACT_VERSION
    assert validate_adapter_response(response) == []


def test_agent_admission_requires_checklist_and_health(tmp_path):
    registry = SpineAdapterRegistry(event_store=EventStore(tmp_path / "events.db"))
    decision = registry.admit("jarvis")

    assert decision.admitted is True
    assert decision.checklist.admitted is True
    assert registry.event_store.count_events(type="agent_admission_decision") == 1


def test_spine_modes_limit_enabled_agents():
    registry = SpineAdapterRegistry()

    assert registry.list_agents("minimal") == ["jarvis"]
    assert "stitch" in registry.list_agents("learning")
    assert "tinyprogrammer" in registry.list_agents("creative")
    assert "vision" in registry.list_agents("full")


def test_learner_profile_is_federated_not_replacement(tmp_path):
    service = LearnerProfileService(event_store=EventStore(tmp_path / "events.db"))
    profile = service.synthesize(persist=True).to_dict()

    assert profile["profile_type"] == "federated_synthesis"
    assert {"lilo", "stitch", "securityplus", "thirty_days"}.issubset(profile["source_profiles"])
    assert service.event_store.count_events(type="learner_profile_update") == 1


def test_cli_spine_and_agent_surfaces_work():
    runner = CliRunner()

    spine_result = runner.invoke(cli, ["spine", "status", "--mode", "minimal"])
    agent_result = runner.invoke(cli, ["agent", "run", "jarvis", "status"])

    assert spine_result.exit_code == 0, spine_result.output
    assert '"jarvis"' in spine_result.output
    assert agent_result.exit_code == 0, agent_result.output
    assert ADAPTER_CONTRACT_VERSION in agent_result.output
