from types import SimpleNamespace

import pytest

from blackfong_ecosystem.tinyprogrammer_companion import TinyProgrammerCompanion


@pytest.fixture
def companion(tmp_path):
    memory = SimpleNamespace(event_store=object())
    instance = TinyProgrammerCompanion(
        memory_service=memory,
        project_path=tmp_path,
        notification_service_instance=SimpleNamespace(),
        policy_engine=SimpleNamespace(),
    )
    instance.calls = 0
    instance.response = None

    def fake_get_json(path):
        instance.calls += 1
        return None if instance.response is None else dict(instance.response)

    instance._get_json = fake_get_json
    return instance


def test_offline_probe_is_reused_within_ttl(companion):
    for _ in range(10):
        assert companion._fetch_live_status() is None
    assert companion.calls == 1


def test_fresh_probe_bypasses_cache(companion):
    companion._fetch_live_status()
    companion._fetch_live_status_fresh()
    assert companion.calls == 2


def test_cache_expires(companion, monkeypatch):
    companion._fetch_live_status()
    companion.live_probe_ttl_s = 0.0
    companion._fetch_live_status()
    assert companion.calls == 2


def test_live_result_is_marked_and_callers_cannot_corrupt_cache(companion):
    companion.response = {"state": "idle"}
    first = companion._fetch_live_status()
    assert first["online"] is True and first["mode"] == "live"
    first["state"] = "mutated"
    assert companion._fetch_live_status()["state"] == "idle"
    assert companion.calls == 1


def test_invalidate_forces_next_probe(companion):
    companion._fetch_live_status()
    companion._invalidate_live_probe()
    companion.response = {"state": "idle"}
    assert companion._fetch_live_status() is not None
    assert companion.calls == 2
