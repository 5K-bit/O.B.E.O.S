from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from blackfong_ecosystem.frame_bridge import (
    FrameBridgeService,
    FrameHubClient,
    build_frame_device_report,
    build_frame_imu_payload,
    build_frame_tap_payload,
    compact_frame_text,
    frame_imu_gesture,
    frame_lua_app_path,
)
from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.operator_chat import OperatorChatService
from blackfong_ecosystem.storage import EventStore, NullVectorStore


class FakeTransport:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.lua_path: Optional[Path] = None
        self.connected = False

    async def connect(self, *, name: Optional[str] = None) -> Dict[str, Any]:
        self.connected = True
        return {
            "connected_name": name or "Frame Test",
            "battery_percent": 88.0,
            "firmware_version": "test-fw",
            "git_tag": "test-tag",
        }

    async def bootstrap_app(self, lua_path: Path) -> None:
        self.lua_path = lua_path

    async def disconnect(self) -> None:
        self.connected = False

    async def send_text(self, text: str) -> None:
        self.texts.append(text)

    async def subscribe_taps(self) -> asyncio.Queue[Any]:
        return asyncio.Queue()

    async def unsubscribe_taps(self) -> None:
        return None

    async def subscribe_imu(self) -> asyncio.Queue[Any]:
        return asyncio.Queue()

    async def unsubscribe_imu(self) -> None:
        return None

    async def request_photo(self, *, resolution: int = 720, timeout_s: float = 12.0) -> bytes:
        return b"\xff\xd8frame-photo\xff\xd9"

    async def collect_audio_phrase(self, *, timeout_s: float = 8.0) -> bytes:
        return b""


class FakeHub(FrameHubClient):
    def __init__(self) -> None:
        self.reports: list[tuple[str, Dict[str, Any]]] = []
        self.events: list[tuple[str, Dict[str, Any]]] = []
        self.gestures: list[Dict[str, Any]] = []
        self.sensor_modes: list[tuple[str, bool, str]] = []
        self.chats: list[tuple[str, str, Dict[str, Any]]] = []

    async def post_device_report(self, device_id: str, report: Dict[str, Any]) -> Dict[str, Any]:
        self.reports.append((device_id, report))
        return {"status": "ok", "device": report}

    async def post_frame_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        confidence: float = 0.9,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        self.events.append((event_type, payload))
        return {"status": "ok", "event": {"type": event_type, "payload": payload}}

    async def post_gesture(self, gesture: Dict[str, Any]) -> Dict[str, Any]:
        self.gestures.append(gesture)
        return {"status": "ok", "gesture": gesture}

    async def set_sensor_mode(self, device_id: str, *, active: bool, mode: str, consent: Dict[str, Any]) -> Dict[str, Any]:
        self.sensor_modes.append((device_id, active, mode))
        return {"status": "ok"}

    async def post_chat(self, message: str, *, modality: str, context: Dict[str, Any]) -> Dict[str, Any]:
        self.chats.append((message, modality, context))
        return {"status": "complete", "result": {"response": "Frame answer"}}

    async def status_card(self) -> Dict[str, Any]:
        return {"title": "O.B.E.O.S", "body": "Health: online\nDouble tap: talk"}

    async def iter_live_events(self) -> AsyncIterator[Dict[str, Any]]:
        if False:
            yield {}


def test_frame_payload_helpers_are_bounded_and_mapped():
    assert frame_lua_app_path().exists()
    assert "..." in compact_frame_text("x" * 300, max_chars=50)

    tap = build_frame_tap_payload(3, device_id="frame")
    assert tap["intent"] == "look_and_ask"
    assert tap["provenance"]["source"] == "frame"

    imu = build_frame_imu_payload(pitch=24, roll=2, heading=0)
    assert imu["gesture"]["action"] == "zoom"
    assert frame_imu_gesture(pitch=0, roll=28)["action"] == "rotate"

    report = build_frame_device_report(battery_percent=91)
    assert report["role"] == "wearable_frame"
    assert report["sensors"]["camera"]["active"] is False
    assert "thoughtglass_gestures" in report["capabilities"]


def test_frame_bridge_connect_reports_and_displays_ready():
    hub = FakeHub()
    transport = FakeTransport()
    service = FrameBridgeService(hub=hub, transport=transport)

    report = asyncio.run(service.connect())

    assert report["health"]["battery_percent"] == 88.0
    assert hub.reports[0][0] == "frame"
    assert transport.lua_path == frame_lua_app_path()
    assert transport.texts[-1].startswith("O.B.E.O.S")
    assert hub.events[-1][0] == "frame_display_update"


def test_frame_bridge_maps_single_tap_to_status_and_imu_to_gesture():
    hub = FakeHub()
    transport = FakeTransport()
    service = FrameBridgeService(hub=hub, transport=transport)

    tap = asyncio.run(service.handle_tap(1))
    imu = asyncio.run(service.handle_imu({"pitch": 0, "roll": 30, "heading": 0}))

    assert tap["intent"] == "status_card"
    assert transport.texts[-1].startswith("O.B.E.O.S")
    assert ("frame_tap_event", tap) in hub.events
    assert imu["gesture"]["action"] == "rotate"
    assert hub.gestures[-1]["action"] == "rotate"


def test_frame_bridge_double_tap_uses_consent_gated_audio_lane():
    hub = FakeHub()
    transport = FakeTransport()
    service = FrameBridgeService(hub=hub, transport=transport)

    asyncio.run(service.handle_tap(2))

    assert ("frame", True, "tap_to_talk") in hub.sensor_modes
    assert ("frame", False, "tap_to_talk") in hub.sensor_modes
    event_types = [event_type for event_type, _payload in hub.events]
    assert "frame_tap_event" in event_types
    assert "audio_sensor_observation" in event_types
    assert transport.texts[-1].startswith("D.A.I.S.E")


def test_operator_chat_accepts_frame_origin_modality_and_context(tmp_path: Path):
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    memory = ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())

    class StubOrchestrator:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any, Dict[str, Any]]] = []

        def process_input(self, text, modality, context=None):
            self.calls.append((text, modality, dict(context or {})))
            return {"response": "voice routed", "confidence": 0.8, "agent": "daise"}

    orchestrator = StubOrchestrator()
    service = OperatorChatService(memory_service=memory, orchestrator=orchestrator)

    result = service.send_message(
        message="frame voice command",
        modality="voice",
        context={"input_origin": "frame", "device_id": "frame"},
    )

    assert result["status"] == "complete"
    text, modality, context = orchestrator.calls[0]
    assert text == "frame voice command"
    assert getattr(modality, "value", modality) == "voice"
    assert context["input_origin"] == "frame"
    assert context["transport"] == "frame"
    assert context["device_id"] == "frame"
    assert context["analysis_mode"] == "standard"
    assert result["analysis_mode"] == "standard"


def test_operator_chat_keeps_grounded_followup_continuity_for_shared_session(tmp_path: Path):
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    memory = ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())

    class RecallOrchestrator:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Dict[str, Any]]] = []

        def process_input(self, text, modality, context=None):
            context = dict(context or {})
            self.calls.append((text, context))
            session_id = str(context.get("session_id") or memory.create_session_id())
            correlation_id = str(context.get("correlation_id") or memory.create_correlation_id())
            request_id = str(context.get("request_id") or memory.create_correlation_id()[:8])
            analysis_mode = str(context.get("analysis_mode") or "standard")

            query_event = memory.persist_user_turn(
                session_id=session_id,
                correlation_id=correlation_id,
                text=text,
                modality="text",
                routed_agent="daise",
                analysis_mode=analysis_mode,
                request_id=request_id,
            )
            memory_context = memory.build_context(
                session_id=session_id,
                query=text,
                agent_name="daise",
                analysis_mode=analysis_mode,
                request_id=request_id,
                transport=str(context.get("transport") or "operator_chat"),
            )
            grounded = (
                memory_context.get("grounded_memory_answer")
                or memory_context.get("grounded_conversation_answer")
                or {}
            )
            response_text = str(grounded.get("answer") or "").strip() or f"stored:{text}"
            memory.persist_agent_response(
                session_id=session_id,
                correlation_id=correlation_id,
                agent_name="daise",
                response_text=response_text,
                confidence=0.9,
                modality="text",
                parent_event_id=query_event.id,
                provider="conversation_memory" if grounded else "heuristic",
                model="deterministic-memory-grounded" if grounded else "operator-chat-test-model",
                success=True,
                request_id=request_id,
            )
            return {
                "response": response_text,
                "confidence": 0.9,
                "agent": "daise",
                "selected_agent": "daise",
                "provider": "conversation_memory" if grounded else "heuristic",
                "model": "deterministic-memory-grounded" if grounded else "operator-chat-test-model",
                "analysis_mode": analysis_mode,
                "request_id": request_id,
                "session_id": session_id,
            }

    orchestrator = RecallOrchestrator()
    service = OperatorChatService(memory_service=memory, orchestrator=orchestrator)

    first = service.send_message(
        message="We have been working on response latency, Telegram grounding, and HUD improvements.",
        session_id="shared-followup-session",
        context={"input_origin": "hud", "transport": "hud"},
    )
    second = service.send_message(
        message="and telegram?",
        session_id="shared-followup-session",
        context={"input_origin": "hud", "transport": "hud"},
    )

    assert first["status"] == "complete"
    assert second["status"] == "complete"
    assert second["analysis_mode"] == "standard"
    assert "response latency" in second["result"]["response"] or "Telegram grounding" in second["result"]["response"]
    assert orchestrator.calls[1][1]["transport"] == "hud"
