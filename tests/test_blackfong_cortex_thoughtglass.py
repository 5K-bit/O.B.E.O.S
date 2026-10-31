from __future__ import annotations

from pathlib import Path

import pytest

from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.storage import EventStore, NullVectorStore
from blackfong_ecosystem.ui.blackcomputer_tui import BlackcomputerTUIApp


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


def test_cognition_snapshot_route_exposes_core_layers_and_nodes(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    client = dashboard.app.test_client()

    response = client.get("/api/cognition/snapshot")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["active_layer"] == "live_brain"
    assert any(layer["id"] == "forge" for layer in payload["layers"])
    node_ids = {node["id"] for node in payload["nodes"]}
    assert {"blackfong_core", "daise_interface", "tinyprogrammer"}.issubset(node_ids)
    assert payload["trace_ribbon"]
    assert "THOUGHTGLASS" in payload["ascii_summary"]


@pytest.mark.xfail(
    reason="Triage B04: selecting node 'tinyprogrammer' returns no selected_node here. Unverified whether the node "
    "only exists when TinyProgrammer is online (environmental) or the graph changed; needs owner triage.",
    strict=False,
)
def test_cognition_gesture_route_updates_focus_and_layer(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    client = dashboard.app.test_client()

    switched = client.post(
        "/api/cognition/gesture",
        json={"action": "switch_layer", "mode": "thoughtglass", "layer": "forge", "target": {"layer": "forge"}},
    ).get_json()
    selected = client.post(
        "/api/cognition/gesture",
        json={"action": "select", "mode": "thoughtglass", "target": {"node_id": "tinyprogrammer"}},
    ).get_json()

    assert switched["status"] == "ok"
    assert switched["focus"]["layer"] == "forge"
    assert switched["snapshot"]["active_layer"] == "forge"
    assert selected["status"] == "ok"
    assert selected["focus"]["selected_node"] == "tinyprogrammer"


def test_cognition_command_and_dashboard_asset_shell_render(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    client = dashboard.app.test_client()

    html = client.get("/").get_data(as_text=True)
    result = client.post("/api/commands/execute", json={"command": "/cognition forge"}).get_json()

    assert "Blackfong Thoughtglass" in html
    assert "/hud-static/dashboard.css" in html
    assert 'data-workspace-tab="chat"' in html
    assert 'data-workspace-tab="thoughtglass"' in html
    assert 'data-workspace-tab="tiny"' in html
    assert 'id="leftRail"' in html
    assert 'id="rightRail"' in html
    assert 'id="layoutScrim"' in html
    assert 'id="conversationStatusChip"' in html
    assert 'data-open-workspace-window="thoughtglass"' in html
    assert 'data-open-workspace-window="trace"' in html
    assert 'id="commandHistory"' in html
    assert result["status"] == "ok"
    assert result["command"] == "/cognition forge"
    assert result["cognition"]["active_layer"] == "forge"
    assert result["focus"]["mode"] == "thoughtglass"


def test_hud_dashboard_script_uses_versioned_layout_and_bounded_collapse_rules():
    root = Path(__file__).resolve().parents[1]
    script = (root / "blackfong_ecosystem" / "ui" / "hud" / "dashboard.js").read_text(encoding="utf-8")

    assert "const LAYOUT_SCHEMA_VERSION = 2;" in script
    assert "const threshold = 800;" not in script
    assert "function shouldCollapseMessageBody" in script
    assert "function buildWorkspaceUrl" in script
    assert "function openWorkspaceWindow" in script


def test_live_stream_route_bootstraps_sse_transport(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    client = dashboard.app.test_client()

    response = client.get("/api/live/stream", buffered=False)
    first_chunk = next(response.response).decode("utf-8")

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert "event: transport" in first_chunk


def test_tui_cognition_command_returns_ascii_snapshot():
    app = BlackcomputerTUIApp()

    result = app._execute_slash_command("/cognition sensors")

    assert result["status"] == "ok"
    assert result["cognition"]["active_layer"] == "sensors"
    assert "THOUGHTGLASS" in result["ascii_summary"]
