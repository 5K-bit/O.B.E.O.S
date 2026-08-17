from __future__ import annotations

from blackfong_ecosystem.contracts.event_v1 import EventEnvelope
from blackfong_ecosystem.daise import DAISE
from blackfong_ecosystem.daise.reasoning_pipeline import ReasoningContext, ReasoningPipeline
from blackfong_ecosystem.event_ingestion import EventV1Ingestor


def test_daise_package_installs_v1_cutover():
    assert getattr(DAISE, "_obeos_v1_cutover_installed", False) is True
    assert hasattr(DAISE, "process_request_legacy")
    assert DAISE.process_request.__name__ == "process_request_v1"


def test_retrieval_is_context_not_answer():
    seen = {}

    def model(prompt: str, context: ReasoningContext) -> str:
        seen["memory"] = list(context.memory)
        return "4"

    pipeline = ReasoningPipeline(model=model)
    result = pipeline.answer("What is 2+2?", memory=[{"text": "irrelevant comic passage"}])
    assert result == "4"
    assert seen["memory"][0]["text"] == "irrelevant comic passage"


def test_event_contract_v1_namespaced_envelope():
    envelope = EventEnvelope(event_type="nightwatch.task.created", source="nightwatch", payload={"task_id": 7})
    payload = envelope.to_dict()
    assert payload["contract_version"] == "1.0"
    assert payload["event_type"] == "nightwatch.task.created"


def test_event_ingestor_maps_v1_without_losing_envelope(tmp_path, monkeypatch):
    class Store:
        def __init__(self): self.events = []
        def init_db(self): pass
        def add_event(self, event): self.events.append(event)

    class Queue:
        def init_db(self): pass
        def enqueue(self, event_id): return f"queue:{event_id}"

    store = Store()
    queue = Queue()
    ingestor = EventV1Ingestor(event_store=store, event_queue=queue)
    envelope = EventEnvelope(
        event_type="nightwatch.task.created",
        source="nightwatch",
        payload={"task_id": 7},
        correlation_id="corr-1",
    )
    result = ingestor.ingest(envelope)
    assert result.legacy_event.type == "operator_action_result"
    assert result.legacy_event.payload["obeos_event_v1"]["event_type"] == "nightwatch.task.created"
    assert result.legacy_event.payload["component_payload"] == {"task_id": 7}
    assert result.legacy_event.metadata["correlation_id"] == "corr-1"
    assert store.events == [result.legacy_event]
