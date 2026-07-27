from __future__ import annotations

import json
from pathlib import Path

from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.notification_service import NotificationService
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.storage import EventStore, NullVectorStore
from blackfong_ecosystem.tinyprogrammer_companion import TinyProgrammerCompanion


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


def test_notification_service_persists_inbox_read_state_and_delivery(tmp_path: Path):
    memory = build_memory(tmp_path)
    service = NotificationService(memory_service=memory, windows_toasts_enabled=False)

    created = service.create(
        title="TinyProgrammer offline",
        message="Companion state changed to offline_snapshot.",
        level="warning",
        category="tinyprogrammer",
        desktop=True,
    )
    inbox = service.list_notifications()

    assert created["id"]
    assert inbox["unread_count"] == 1
    assert inbox["notifications"][0]["title"] == "TinyProgrammer offline"
    assert created["delivery"]["status"] == "skipped"
    assert memory.event_store.count_events(type="notification_created") == 1
    assert memory.event_store.count_events(type="notification_delivery_result") == 1

    read = service.mark_read(created["id"])
    refreshed = service.list_notifications()

    assert read["status"] == "read"
    assert refreshed["unread_count"] == 0
    assert refreshed["notifications"][0]["read"] is True


class FakeTinyProgrammer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.auto_stream = True
        self.adopt_external = True
        self.autostart_enabled = True
        self.shutdown_policy = "ask"
        self.log_path = Path("C:/tmp/tinyprogrammer.log")

    def get_status(self, *, require_stream=None):
        self.calls.append(("status", {"require_stream": require_stream}))
        return {
            "online": False,
            "mode": "offline_snapshot",
            "state": "offline",
            "stream_required": require_stream,
            "stream_reason": "not_started_with_stream",
            "bbs_reason": "feature_disabled",
            "log_path": str(self.log_path),
            "process": {
                "ownership": "offline",
                "management_state": "offline_snapshot",
                "pid": None,
            },
        }

    def process_status(self, *, require_stream=None):
        self.calls.append(("process", {"require_stream": require_stream}))
        return {"state": "not_started", "online": False, "stream_required": require_stream}

    def ensure_running(self, *, require_stream=True, adopt_external=True, open_browser=False, consent=None):
        self.calls.append(
            (
                "ensure_running",
                {
                    "require_stream": require_stream,
                    "adopt_external": adopt_external,
                    "open_browser": open_browser,
                    "consent": consent,
                },
            )
        )
        return {
            "success": True,
            "status": "already_live",
            "process": {"state": "running", "ownership": "managed", "management_state": "running"},
            "tinyprogrammer": {"online": True, "stream_available": True},
        }

    def open(self, *, target="dashboard", consent=None, open_browser=True):
        self.calls.append(("open", {"target": target, "consent": consent, "open_browser": open_browser}))
        return {"success": True, "status": "opened", "url": f"http://tiny/{target}"}

    def open_live(self, *, consent=None, open_browser=True):
        self.calls.append(("open_live", {"consent": consent, "open_browser": open_browser}))
        return {"success": True, "status": "opened", "url": "http://tiny/stream"}

    def stop(self):
        self.calls.append(("stop", None))
        return {"success": True, "status": "stopped"}

    def get_recent(self, limit=10):
        self.calls.append(("recent", limit))
        return {"online": False, "programs": [{"filename": "sketch.py", "code_preview": "print('tiny')"}], "reflections": []}

    def get_log_tail(self, *, lines=40, max_chars=12000):
        self.calls.append(("log", {"lines": lines, "max_chars": max_chars}))
        return {
            "success": True,
            "log_path": str(self.log_path),
            "line_count": 2,
            "content": "tiny boot ok\nstream ready",
            "human_reason": "Managed TinyProgrammer log tail loaded.",
        }

    def send_brief(self, brief, *, metadata=None):
        self.calls.append(("brief", {"brief": brief, "metadata": metadata}))
        return {"success": True, "mode": "offline_override", "brief": brief}

    def send_control(self, action, value=None):
        self.calls.append(("control", {"action": action, "value": value}))
        return {"success": True, "action": action, "value": value}

    def rate_last_creation(self, rating, *, feedback=""):
        self.calls.append(("feedback", {"rating": rating, "feedback": feedback}))
        return {"success": True, "rating": rating, "feedback": feedback}


def test_hud_notification_and_tiny_routes_share_real_action_handlers(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    fake_tiny = FakeTinyProgrammer()
    dashboard.tinyprogrammer = fake_tiny
    client = dashboard.app.test_client()

    commands = client.get("/api/commands").get_json()["commands"]
    assert any(command["command"] == "/notifications" for command in commands)
    assert any(command["command"] == "/tiny launch" for command in commands)

    notification = client.post("/api/notifications/test", json={"desktop": False}).get_json()["notification"]
    inbox = client.get("/api/notifications").get_json()
    assert notification["title"] == "Blackfong notification test"
    assert inbox["unread_count"] >= 1

    launch = client.post("/api/tinyprogrammer/launch", json={"open_browser": False}).get_json()
    control = client.post("/api/tinyprogrammer/control", json={"action": "restart"}).get_json()
    recent = client.post("/api/commands/execute", json={"command": "/tiny recent"}).get_json()
    read_all = client.post("/api/commands/execute", json={"command": "/notifications read-all"}).get_json()

    assert launch["success"] is True
    assert control["success"] is True
    assert recent["tinyprogrammer"]["programs"][0]["filename"] == "sketch.py"
    assert read_all["status"] == "read_all"
    assert (
        "ensure_running",
        {
            "require_stream": True,
            "adopt_external": True,
            "open_browser": False,
            "consent": {"approved": True, "level": 1},
        },
    ) in fake_tiny.calls
    assert ("control", {"action": "restart", "value": None}) in fake_tiny.calls


def test_operator_summary_and_tiny_log_routes_share_backend_state(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    fake_tiny = FakeTinyProgrammer()
    dashboard.tinyprogrammer = fake_tiny
    dashboard.operator_summary.tinyprogrammer = fake_tiny
    client = dashboard.app.test_client()

    summary = client.get("/api/operator/summary").get_json()
    tiny_log = client.get("/api/tinyprogrammer/log?lines=12").get_json()

    assert summary["doctrine"]["session_authority"] == "OBEOS owns the session."
    assert summary["tinyprogrammer"]["log_path"] == str(fake_tiny.log_path)
    assert "files" in summary
    assert tiny_log["success"] is True
    assert "stream ready" in tiny_log["content"]
    assert ("log", {"lines": 12, "max_chars": 12000}) in fake_tiny.calls


def test_tinyprogrammer_offline_recent_creations_emit_events_and_notifications(tmp_path: Path):
    memory = build_memory(tmp_path)
    notifications = NotificationService(memory_service=memory, windows_toasts_enabled=False)
    project = tmp_path / "TinyProgrammer-main"
    programs = project / "programs"
    programs.mkdir(parents=True)
    (programs / "sketch.py").write_text("print('tiny creation')\n", encoding="utf-8")
    (programs / "index.json").write_text(
        json.dumps(
            [
                {
                    "id": "creation-1",
                    "filename": "sketch.py",
                    "program_type": "pattern",
                    "mood": "curious",
                    "success": True,
                    "thought_process": "Testing gallery rendering.",
                }
            ]
        ),
        encoding="utf-8",
    )
    (project / "lessons.md").write_text("- Keep the companion bounded and playful.\n", encoding="utf-8")
    companion = TinyProgrammerCompanion(
        memory_service=memory,
        base_url="http://127.0.0.1:1",
        project_path=project,
        timeout=0.01,
        notification_service_instance=notifications,
    )

    recent = companion.get_recent(limit=5)

    assert recent["online"] is False
    assert recent["programs"][0]["exists"] is True
    assert "tiny creation" in recent["programs"][0]["code_preview"]
    assert recent["reflections"][0]["text"] == "Keep the companion bounded and playful."
    assert memory.event_store.count_events(type="tinyprogrammer_program_created") == 1
    assert memory.event_store.count_events(type="tinyprogrammer_program_archived") == 1
    assert memory.event_store.count_events(type="notification_created") == 1


def test_tinyprogrammer_recent_lessons_tolerate_windows_encoded_notes(tmp_path: Path):
    memory = build_memory(tmp_path)
    project = tmp_path / "tiny"
    (project / "programs").mkdir(parents=True)
    (project / "lessons.md").write_bytes(b"- Don\x92t crash on imported notes.\n")
    companion = TinyProgrammerCompanion(
        memory_service=memory,
        base_url="http://127.0.0.1:1",
        project_path=project,
        timeout=0.01,
        notification_service_instance=NotificationService(memory_service=memory, windows_toasts_enabled=False),
    )

    recent = companion.get_recent(limit=5)

    assert recent["online"] is False
    assert recent["reflections"]
    assert "crash on imported notes" in recent["reflections"][0]["text"]


def test_tinyprogrammer_live_controls_map_to_real_routes(monkeypatch, tmp_path: Path):
    memory = build_memory(tmp_path)
    companion = TinyProgrammerCompanion(
        memory_service=memory,
        base_url="http://127.0.0.1:8765",
        project_path=tmp_path,
        timeout=0.01,
        notification_service_instance=NotificationService(memory_service=memory, windows_toasts_enabled=False),
    )
    calls: list[tuple[str, dict]] = []

    def fake_post_json(path: str, payload: dict):
        calls.append((path, payload))
        return {"success": True, "path": path, "payload": payload}

    monkeypatch.setattr(companion, "_post_json", fake_post_json)

    restart = companion.send_control("restart")
    screen_on = companion.send_control("screensaver", "on")
    mood = companion.send_control("mood", "luminous")

    assert restart["path"] == "/api/restart"
    assert screen_on["path"] == "/api/screensaver/on"
    assert mood["path"] == "/api/control"
    assert calls == [
        ("/api/restart", {}),
        ("/api/screensaver/on", {}),
        ("/api/control", {"action": "mood", "value": "luminous"}),
    ]


def test_tinyprogrammer_launch_records_consent_process_and_notification(monkeypatch, tmp_path: Path):
    import blackfong_ecosystem.tinyprogrammer_companion as tiny_module

    memory = build_memory(tmp_path)
    project = tmp_path / "TinyProgrammer-main"
    project.mkdir()
    (project / "main.py").write_text("print('tiny')\n", encoding="utf-8")
    companion = TinyProgrammerCompanion(
        memory_service=memory,
        base_url="http://127.0.0.1:8765",
        project_path=project,
        timeout=0.01,
        notification_service_instance=NotificationService(memory_service=memory, windows_toasts_enabled=False),
    )
    companion.launch_timeout = 0.05
    status_calls = {"count": 0}

    class FakePopen:
        pid = 4242

        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    def fake_fetch_live_status():
        status_calls["count"] += 1
        if status_calls["count"] == 1:
            return None
        return {"state": "running", "online": True}

    monkeypatch.setattr(tiny_module.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(companion, "_fetch_live_status", fake_fetch_live_status)

    result = companion.launch(consent={"approved": True, "level": 1}, open_browser=False)

    assert result["success"] is True
    assert result["process"]["pid"] == 4242
    assert result["process"]["log_path"].endswith("tinyprogrammer.log")
    assert companion.log_path.exists()
    assert memory.event_store.count_events(type="operator_consent") == 1
    assert memory.event_store.count_events(type="tinyprogrammer_process_state") == 1
    assert memory.event_store.count_events(type="notification_created") >= 1


def test_tinyprogrammer_ensure_running_adopts_external_live_instance(monkeypatch, tmp_path: Path):
    memory = build_memory(tmp_path)
    companion = TinyProgrammerCompanion(
        memory_service=memory,
        base_url="http://127.0.0.1:8765",
        project_path=tmp_path,
        timeout=0.01,
        notification_service_instance=NotificationService(memory_service=memory, windows_toasts_enabled=False),
    )
    monkeypatch.setattr(
        companion,
        "_fetch_live_status",
        lambda: {"state": "running", "online": True, "stream_enabled": True, "mood": "steady"},
    )

    result = companion.ensure_running(require_stream=True, adopt_external=True, open_browser=False)

    assert result["success"] is True
    assert result["status"] == "already_live"
    assert result["process"]["ownership"] == "external"


def test_tinyprogrammer_ensure_running_flags_external_instance_without_stream(monkeypatch, tmp_path: Path):
    memory = build_memory(tmp_path)
    companion = TinyProgrammerCompanion(
        memory_service=memory,
        base_url="http://127.0.0.1:8765",
        project_path=tmp_path,
        timeout=0.01,
        notification_service_instance=NotificationService(memory_service=memory, windows_toasts_enabled=False),
    )
    monkeypatch.setattr(
        companion,
        "_fetch_live_status",
        lambda: {"state": "running", "online": True, "stream_enabled": False, "mood": "steady"},
    )

    result = companion.ensure_running(require_stream=True, adopt_external=True, open_browser=False)

    assert result["success"] is False
    assert result["reason_code"] == "external_no_stream"


def test_dashboard_autostart_uses_shared_tiny_ensure_running(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    fake_tiny = FakeTinyProgrammer()
    dashboard.tinyprogrammer = fake_tiny

    dashboard._autostart_tiny_if_enabled(sync=True)

    assert (
        "ensure_running",
        {
            "require_stream": True,
            "adopt_external": True,
            "open_browser": False,
            "consent": {"approved": True, "level": 1},
        },
    ) in fake_tiny.calls


def test_tui_tiny_launch_uses_stream_ready_ensure_running(monkeypatch):
    import blackfong_ecosystem.ui.blackcomputer_tui as tui_module

    fake_tiny = FakeTinyProgrammer()
    monkeypatch.setattr(tui_module, "tinyprogrammer_companion", fake_tiny)
    app = tui_module.BlackcomputerTUIApp()

    result = app._tiny_command("/tiny launch")

    assert result["success"] is True
    assert any(call[0] == "ensure_running" and call[1]["require_stream"] is True for call in fake_tiny.calls)


def test_tui_imports_with_richlog_tabs():
    from blackfong_ecosystem.ui.blackcomputer_tui import BlackcomputerTUIApp

    assert BlackcomputerTUIApp.__name__ == "BlackcomputerTUIApp"
    assert any(binding[0] == "ctrl+r" for binding in BlackcomputerTUIApp.BINDINGS)
