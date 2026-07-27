from __future__ import annotations

from pathlib import Path

from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.daise import DAISE
from desktop_shell import workspace_probe
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.service_registry import get_ui_services, reset_ui_services_for_tests
from blackfong_ecosystem.storage import EventStore, NullVectorStore
from blackfong_ecosystem.ui_commands import CommandContext, get_command_registry, reset_command_registry_for_tests


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


def test_ui_service_container_reuses_instances_for_same_memory(tmp_path: Path):
    reset_ui_services_for_tests()
    memory = build_memory(tmp_path)

    first = get_ui_services(memory_service=memory)
    second = get_ui_services(memory_service=memory)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory)
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    assert first is second
    assert dashboard.services is first
    assert dashboard.operator_actions is first.operator_actions
    assert dashboard.file_scope_service is first.file_scope_service
    assert dashboard.notification_service is first.notification_service
    assert daise.memory_chamber is first.memory_chamber


def test_command_registry_catalog_and_shared_handlers(tmp_path: Path):
    reset_ui_services_for_tests()
    reset_command_registry_for_tests()
    services = get_ui_services(memory_service=build_memory(tmp_path))
    registry = get_command_registry()

    catalog = registry.catalog()
    assert any(item["command"] == "/desktop workspace" and item["category"] == "Desktop" for item in catalog)
    assert any("/status" in item["autocomplete"] for item in catalog if item["command"] == "/status")

    context = CommandContext(services=services, source="test")
    unknown = registry.execute("/does-not-exist", context)
    notifications = registry.execute("/notifications", context)

    assert unknown["status"] == "error"
    assert notifications["status"] == "ok"
    assert "notifications" in notifications


def test_ui_event_bus_bounds_and_cleans_subscribers(tmp_path: Path):
    reset_ui_services_for_tests()
    services = get_ui_services(memory_service=build_memory(tmp_path))
    subscription = services.event_bus.subscribe(max_queue_size=2)

    services.event_bus.publish("one", {"n": 1})
    services.event_bus.publish("two", {"n": 2})
    services.event_bus.publish("three", {"n": 3})

    assert subscription.queue.qsize() == 2
    assert services.event_bus.subscriber_count() == 1

    services.event_bus.unsubscribe(subscription)

    assert services.event_bus.subscriber_count() == 0
    assert subscription.closed is True


def test_hud_routes_expose_pagination_and_registry_catalog(tmp_path: Path):
    reset_ui_services_for_tests()
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=build_memory(tmp_path))
    client = dashboard.app.test_client()

    commands = client.get("/api/commands").get_json()["commands"]
    events = client.get("/api/events?limit=5&cursor=0").get_json()
    sessions = client.get("/api/sessions?limit=5&cursor=0").get_json()

    assert any(command["command"] == "/cognition" and command["category"] == "Thoughtglass" for command in commands)
    assert events["limit"] == 5
    assert events["cursor"] == 0
    assert "next_cursor" in events
    assert sessions["limit"] == 5
    assert sessions["cursor"] == 0


def test_workspace_probe_reports_split_graph_defaults() -> None:
    result = workspace_probe()

    assert result["success"] is True
    assert result["thoughtglass_home_default"] is True
    assert result["thoughtglass_default_mode"] == "live_brain"
    assert result["memory_chamber_default_mode"] == "cluster_map"
    assert result["native_graph_available"] is True
