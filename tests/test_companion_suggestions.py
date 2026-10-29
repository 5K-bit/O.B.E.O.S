from blackfong_ecosystem.companion_suggestions import CompanionSuggestions


def test_busy_interval_deduplication_and_restart(tmp_path):
    now = [1000.0]
    path = tmp_path / "suggestions.sqlite3"
    service = CompanionSuggestions(path, clock=lambda: now[0])
    event = {"id": "e1", "type": "knowledge_ingested", "timestamp": "2026-09-13T00:00:00Z"}
    assert service.offer([event], busy=True) is None
    result = service.offer([event])
    assert result["evidence_event_id"] == "e1"
    assert result["command"] is None and result["camera_action"] is None
    second = {"id": "e2", "type": "tinyprogrammer_program_created"}
    now[0] += 299
    assert service.offer([second]) is None
    now[0] += 1
    restarted = CompanionSuggestions(path, clock=lambda: now[0])
    assert restarted.offer([event]) is None
    assert restarted.offer([second])["evidence_event_id"] == "e2"


def test_high_frequency_feed_and_assistant_statements_are_not_facts(tmp_path):
    now = [1000.0]
    service = CompanionSuggestions(tmp_path / "suggestions.sqlite3", clock=lambda: now[0])
    assert service.offer([{"id": "assistant", "type": "agent_response", "payload": {"text": "invented fact"}}]) is None
    assert service.offer([{"id": "a", "type": "spatial_observation_batch"}])
    now[0] += 301
    assert service.offer([{"id": "b", "type": "spatial_observation_batch"}]) is None
