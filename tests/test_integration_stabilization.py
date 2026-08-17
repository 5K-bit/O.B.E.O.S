import json
from pathlib import Path

import pytest

from blackfong_ecosystem.component_registry import ComponentManifest, ComponentRegistry, ManifestError
from blackfong_ecosystem.contracts import EventEnvelope, HealthRecord, aggregate_health
from blackfong_ecosystem.daise.reasoning_pipeline import ReasoningPipeline


def test_root_manifest_is_discoverable() -> None:
    registry = ComponentRegistry().discover(Path(__file__).resolve().parents[1])
    assert "obeos-core" in registry.components
    assert not registry.errors


def test_manifest_rejects_invalid_component_type() -> None:
    with pytest.raises(ManifestError):
        ComponentManifest.from_dict({
            "schema_version": 1,
            "name": "bad",
            "component_type": "anything",
            "version": "1",
            "entrypoint": "bad",
        })


def test_health_contract_aggregates_degraded_state() -> None:
    overall = aggregate_health([
        HealthRecord(name="daise", version="1", status="ok"),
        HealthRecord(name="nightwatch", version="1", status="degraded"),
    ])
    assert overall.status == "degraded"
    assert overall.dependencies["daise"] == "ok"


def test_event_contract_requires_namespaced_type() -> None:
    event = EventEnvelope(event_type="system.health.degraded", source="maxpayne", payload={"component": "ollama"})
    assert event.to_dict()["contract_version"] == "1.0"
    with pytest.raises(ValueError):
        EventEnvelope(event_type="health", source="maxpayne", payload={})


def test_daise_reasoning_pipeline_uses_memory_as_context_not_answer() -> None:
    seen = {}

    def model(prompt, context):
        seen["prompt"] = prompt
        seen["memory"] = context.memory
        return "model-generated answer"

    pipeline = ReasoningPipeline(model=model)
    result = pipeline.answer("What is two plus two?", memory=({"retrieved": "irrelevant chunk"},))
    assert result == "model-generated answer"
    assert seen["memory"][0]["retrieved"] == "irrelevant chunk"
