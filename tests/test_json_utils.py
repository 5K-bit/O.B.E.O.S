from __future__ import annotations

import json

from blackfong_ecosystem.json_utils import dump_pretty_json, make_json_compatible


def test_make_json_compatible_converts_tuple_keys_recursively() -> None:
    payload = {
        ("agent", "state"): {
            ("backend", "health"): "ok",
            "nested": {("queue", 1): ["pending", {("a", "b"): "value"}]},
        }
    }

    normalized = make_json_compatible(payload)

    assert "('agent', 'state')" in normalized
    assert normalized["('agent', 'state')"]["('backend', 'health')"] == "ok"
    assert normalized["('agent', 'state')"]["nested"]["('queue', 1)"][1]["('a', 'b')"] == "value"


def test_dump_pretty_json_handles_tuple_keys_and_sets() -> None:
    payload = {
        ("agent", "state"): {"seen": {"alpha", "beta"}},
    }

    rendered = dump_pretty_json(payload)
    parsed = json.loads(rendered)

    assert "('agent', 'state')" in parsed
    assert sorted(parsed["('agent', 'state')"]["seen"]) == ["alpha", "beta"]
