from types import SimpleNamespace

from blackfong_ecosystem.conversation_memory import ConversationMemoryService


def health_for(monkeypatch, failed_at):
    monkeypatch.setattr("blackfong_ecosystem.conversation_memory.time.time", lambda: 1000.0)
    payload = {"transport": "hud", "provider": "ollama", "model": "fixture",
               "execution_time_ms": 75000, "backend_error": "Model timed out",
               "fallback_used": True}
    events = [SimpleNamespace(timestamp=failed_at * 1000, payload={**payload,
              "backend_metadata": {"live_lane_attempted": True, "fallback_mode": "reactive"}})]
    events += [SimpleNamespace(timestamp=(990 + i) * 1000, payload={**payload,
               "backend_metadata": {"live_lane_attempted": False, "fallback_mode": "preemptive"}})
               for i in range(8)]
    service = object.__new__(ConversationMemoryService)
    service.event_store = SimpleNamespace(list_events_by_type=lambda kind, **kw:
                                          events if kind == "latency_observation" else [])
    return service.recent_backend_health(transport="hud")


def test_skipped_turns_do_not_multiply_provider_timeouts(monkeypatch):
    health = health_for(monkeypatch, 980)
    assert health["recent_timeout_count"] == 1
    assert health["recent_fallback_count"] == 9
    assert health["latest_failure_at"] == 980
    assert health["allow_recovery_probe"] is True


def test_skipped_turns_cannot_refresh_stale_failure(monkeypatch):
    health = health_for(monkeypatch, 700)
    assert health["recent_timeout_count"] == 0
    assert health["latest_failure_at"] == 700
    assert health["state"] == "healthy"
    assert health["recovery_reason"] == "stale_timeout_window_expired"
