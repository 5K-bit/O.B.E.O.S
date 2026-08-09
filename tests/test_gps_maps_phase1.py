from __future__ import annotations

from pathlib import Path

from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.service_registry import reset_ui_services_for_tests
from blackfong_ecosystem.storage import EventStore, NullVectorStore


def _build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


def test_gps_status_and_hud_workspace_share_same_backend(tmp_path: Path) -> None:
    reset_ui_services_for_tests()
    memory = _build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8097, memory_service=memory, orchestrator_instance=None)
    client = dashboard.app.test_client()

    provider = client.post("/api/gps/provider", json={"mode": "simulator"}).get_json()
    started = client.post("/api/gps/recording/start", json={}).get_json()
    status = client.get("/api/gps/status").get_json()
    summary = client.get("/api/operator/summary").get_json()
    stopped = client.post("/api/gps/recording/stop", json={}).get_json()
    html = dashboard._get_dashboard_html()

    assert provider["provider_mode"] == "simulator"
    assert started["recording"] is True
    assert status["provider_type"] == "simulator"
    assert status["latest_fix"] is not None
    assert status["trail"]
    assert summary["gps"]["provider_type"] == "simulator"
    assert stopped["recording"] is False
    assert 'data-workspace-tab="maps"' in html
