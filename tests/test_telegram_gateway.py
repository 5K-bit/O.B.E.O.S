from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import text

from blackfong_ecosystem.config import TelegramSettings
from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.operator_summary import OperatorSummaryService
from blackfong_ecosystem.policy_engine import PolicyEngine
from blackfong_ecosystem.storage import EventStore, NullVectorStore
from blackfong_ecosystem.system_awareness import SystemAwarenessService
from blackfong_ecosystem.telegram_gateway import TelegramGatewayLock, TelegramGatewayService, read_telegram_gateway_status


class FakeTelegramResponse:
    def __init__(self, payload: Optional[Dict[str, Any]] = None, *, content: bytes = b"", status_code: int = 200) -> None:
        self._payload = payload or {"ok": True, "result": True}
        self.content = content
        self.status_code = status_code

    def json(self) -> Dict[str, Any]:
        return dict(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http_status_{self.status_code}")


class FakeTelegramClient:
    def __init__(self) -> None:
        self.sent_messages: list[Dict[str, Any]] = []
        self.downloads: list[str] = []
        self.closed = False

    def post(self, url: str, json: Optional[Dict[str, Any]] = None) -> FakeTelegramResponse:
        if url.endswith("/sendMessage"):
            payload = dict(json or {})
            self.sent_messages.append(payload)
            return FakeTelegramResponse({"ok": True, "result": {"message_id": 9000}})
        if url.endswith("/getMe"):
            return FakeTelegramResponse({"ok": True, "result": {"id": 42, "username": "obeos_bot"}})
        if url.endswith("/getFile"):
            return FakeTelegramResponse({"ok": True, "result": {"file_path": "voice/test.ogg"}})
        return FakeTelegramResponse()

    def get(self, url: str, params: Optional[Dict[str, Any]] = None) -> FakeTelegramResponse:
        if "/file/bot" in url:
            self.downloads.append(url)
            return FakeTelegramResponse(content=b"fake-telegram-voice")
        if url.endswith("/getUpdates"):
            return FakeTelegramResponse({"ok": True, "result": []})
        return FakeTelegramResponse({"ok": True, "result": []})

    def close(self) -> None:
        self.closed = True


class StubOperatorChat:
    def __init__(self, *, sleep_map: Optional[Dict[str, float]] = None) -> None:
        self.calls: list[Dict[str, Any]] = []
        self.lock = threading.Lock()
        self.started: Dict[str, float] = {}
        self.finished: Dict[str, float] = {}
        self.sleep_map = dict(sleep_map or {})

    def send_message(
        self,
        *,
        message: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        agent_override: Optional[str] = None,
        analysis_mode: str = "standard",
        stream_callback: Optional[Any] = None,
        include_memory: bool = False,
        include_messages: bool = True,
        include_trace: bool = True,
        modality: str = "text",
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        started = time.time()
        with self.lock:
            self.calls.append(
                {
                    "message": message,
                    "session_id": session_id,
                    "request_id": request_id,
                    "agent_override": agent_override,
                    "analysis_mode": analysis_mode,
                    "include_memory": include_memory,
                    "include_messages": include_messages,
                    "include_trace": include_trace,
                    "modality": modality,
                    "context": dict(context or {}),
                }
            )
            self.started[message] = started
        time.sleep(float(self.sleep_map.get(message, 0.0)))
        finished = time.time()
        with self.lock:
            self.finished[message] = finished
        return {
            "status": "complete",
            "result": {
                "response": f"handled:{message}",
                "selected_agent": agent_override or "daise",
            },
        }


class FakeTinyProgrammer:
    def get_status(self, require_stream: bool = True) -> Dict[str, Any]:
        return {"online": False, "mode": "offline_snapshot"}


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


def build_settings(*, allowlist: tuple[tuple[str, str], ...], voice_enabled: bool = True) -> TelegramSettings:
    return TelegramSettings(
        enabled=True,
        bot_token="123456:TESTTOKEN",
        allowlisted_identities=allowlist,
        private_chat_only=True,
        poll_timeout_seconds=1.0,
        poll_retry_delay_seconds=0.01,
        poll_limit=10,
        request_timeout_seconds=2.0,
        voice_enabled=voice_enabled,
        max_voice_download_bytes=1_000_000,
        max_voice_duration_seconds=120.0,
        max_voice_processing_seconds=60.0,
        max_transcript_chars=1000,
    )


def message_update(
    update_id: int,
    *,
    chat_id: str = "100",
    user_id: str = "200",
    text: str = "hello",
    chat_type: str = "private",
) -> Dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": user_id, "username": f"user_{user_id}"},
            "text": text,
        },
    }


def voice_update(
    update_id: int,
    *,
    chat_id: str = "100",
    user_id: str = "200",
    chat_type: str = "private",
    caption: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": user_id, "username": f"user_{user_id}"},
            "voice": {
                "file_id": "voice-file-1",
                "duration": 3,
                "mime_type": "audio/ogg",
                "file_size": 128,
            },
        },
    }
    if caption:
        payload["message"]["caption"] = caption
    return payload


def wait_for_processed(memory: ConversationMemoryService, *, expected: int, timeout_s: float = 3.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with memory.event_store.engine.begin() as conn:
            processed = int(
                conn.execute(
                    text("SELECT COUNT(*) FROM telegram_update_inbox WHERE status = 'processed'")
                ).scalar_one()
                or 0
            )
        if processed >= expected:
            return
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {expected} processed Telegram updates.")


def stabilize_awareness(service: SystemAwarenessService, monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "observe_backend",
        lambda *, backend_bootstrap=None: {
            "schema_version": "1.0",
            "provider": "ollama",
            "status": "available",
            "configured_model": "llama3.2:3b",
            "model_available": True,
            "bootstrap": backend_bootstrap or {},
        },
    )
    monkeypatch.setattr(service, "_probe_audio_devices", lambda: {"status": "available", "input_count": 1, "output_count": 1})
    monkeypatch.setattr(service, "_module_available", lambda name: True)


def test_telegram_policy_boundary_rejects_privileged_remote_actions() -> None:
    engine = PolicyEngine()
    denied = engine.evaluate(
        "request",
        {
            "text": "have CodingAgent restart the server and install the new plugin",
            "transport": "telegram",
            "capabilities": ["chat", "status_read", "agent_chat"],
        },
    ).to_dict()
    allowed = engine.evaluate(
        "request",
        {
            "text": "what do you think about the memory chamber graph right now?",
            "transport": "telegram",
            "capabilities": ["chat", "status_read", "agent_chat"],
        },
    ).to_dict()

    assert denied["decision_state"] == "reject"
    assert "telegram_capability_boundary" in denied["risk_flags"]
    assert allowed["decision_state"] == "approve"


def test_telegram_gateway_replays_are_idempotent_and_mode_persists(tmp_path: Path) -> None:
    memory = build_memory(tmp_path)
    client = FakeTelegramClient()
    service = TelegramGatewayService(
        settings=build_settings(allowlist=(("100", "200"),)),
        memory_service=memory,
        operator_chat=StubOperatorChat(),
        http_client=client,
        lock_path=tmp_path / "telegram.lock",
    )
    service._dispatch_thread_queue = lambda thread_key, update_id: None  # type: ignore[method-assign]

    first = service._accept_update(message_update(1, text="/mode deep"))
    second = service._accept_update(message_update(1, text="/mode deep"))
    service._process_update_with_retries(1)

    with memory.event_store.engine.begin() as conn:
        inbox_count = int(conn.execute(text("SELECT COUNT(*) FROM telegram_update_inbox")).scalar_one() or 0)
        stored_mode = conn.execute(
            text("SELECT mode FROM telegram_thread_state WHERE thread_key = 'telegram:100:200'")
        ).scalar_one()

    assert first is True
    assert second is False
    assert inbox_count == 1
    assert stored_mode == "deep"
    assert "Telegram mode set to deep" in client.sent_messages[-1]["text"]


def test_telegram_gateway_lock_reclaims_stale_pid_reuse(tmp_path: Path) -> None:
    lock_path = tmp_path / "telegram.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "created_at": time.time() - 60.0,
                "process_started_at": 1.0,
            }
        ),
        encoding="utf-8",
    )

    lock = TelegramGatewayLock(lock_path)
    acquired, owner_label = lock.acquire()

    assert acquired is True
    assert f"pid={os.getpid()}@" in owner_label
    lock.release()


def test_telegram_gateway_default_mode_is_standard(tmp_path: Path) -> None:
    memory = build_memory(tmp_path)
    client = FakeTelegramClient()
    operator_chat = StubOperatorChat()
    service = TelegramGatewayService(
        settings=build_settings(allowlist=(("100", "200"),)),
        memory_service=memory,
        operator_chat=operator_chat,
        http_client=client,
        lock_path=tmp_path / "telegram.lock",
    )
    service._dispatch_thread_queue = lambda thread_key, update_id: None  # type: ignore[method-assign]

    accepted = service._accept_update(message_update(21, text="hello there"))
    service._process_update_with_retries(21)

    assert accepted is True
    assert operator_chat.calls[0]["analysis_mode"] == "standard"
    assert operator_chat.calls[0]["include_messages"] is False
    assert operator_chat.calls[0]["include_trace"] is False


def test_telegram_gateway_upgrades_legacy_non_explicit_fast_threads_to_standard(tmp_path: Path) -> None:
    memory = build_memory(tmp_path)
    client = FakeTelegramClient()
    operator_chat = StubOperatorChat()
    service = TelegramGatewayService(
        settings=build_settings(allowlist=(("100", "200"),)),
        memory_service=memory,
        operator_chat=operator_chat,
        http_client=client,
        lock_path=tmp_path / "telegram.lock",
    )
    with memory.event_store.engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO telegram_thread_state (
                    thread_key,
                    telegram_chat_id,
                    telegram_user_id,
                    session_id,
                    mode,
                    created_at,
                    last_seen_at,
                    last_message_id,
                    last_update_id,
                    metadata_json
                ) VALUES (
                    'telegram:100:200',
                    '100',
                    '200',
                    'legacy-session',
                    'fast',
                    :now,
                    :now,
                    1,
                    1,
                    '{}'
                )
                """
            ),
            {"now": time.time()},
        )
    service._dispatch_thread_queue = lambda thread_key, update_id: None  # type: ignore[method-assign]

    accepted = service._accept_update(message_update(22, text="hello again"))
    service._process_update_with_retries(22)

    with memory.event_store.engine.begin() as conn:
        stored_mode = conn.execute(
            text("SELECT mode FROM telegram_thread_state WHERE thread_key = 'telegram:100:200'")
        ).scalar_one()

    assert accepted is True
    assert operator_chat.calls[0]["analysis_mode"] == "standard"
    assert stored_mode == "standard"


def test_telegram_gateway_normalizes_legacy_fast_threads_on_startup(tmp_path: Path) -> None:
    memory = build_memory(tmp_path)
    with memory.event_store.engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO telegram_thread_state (
                    thread_key,
                    telegram_chat_id,
                    telegram_user_id,
                    session_id,
                    mode,
                    created_at,
                    last_seen_at,
                    last_message_id,
                    last_update_id,
                    metadata_json
                ) VALUES (
                    'telegram:100:200',
                    '100',
                    '200',
                    'legacy-session',
                    'fast',
                    :now,
                    :now,
                    1,
                    1,
                    '{}'
                )
                """
            ),
            {"now": time.time()},
        )

    TelegramGatewayService(
        settings=build_settings(allowlist=(("100", "200"),)),
        memory_service=memory,
        operator_chat=StubOperatorChat(),
        http_client=FakeTelegramClient(),
        lock_path=tmp_path / "telegram.lock",
    )

    with memory.event_store.engine.begin() as conn:
        stored_mode = conn.execute(
            text("SELECT mode FROM telegram_thread_state WHERE thread_key = 'telegram:100:200'")
        ).scalar_one()

    assert stored_mode == "standard"


def test_telegram_gateway_normalizes_legacy_explicit_fast_threads_without_command_source(tmp_path: Path) -> None:
    memory = build_memory(tmp_path)
    with memory.event_store.engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO telegram_thread_state (
                    thread_key,
                    telegram_chat_id,
                    telegram_user_id,
                    session_id,
                    mode,
                    created_at,
                    last_seen_at,
                    last_message_id,
                    last_update_id,
                    metadata_json
                ) VALUES (
                    'telegram:100:200',
                    '100',
                    '200',
                    'legacy-session',
                    'fast',
                    :now,
                    :now,
                    1,
                    1,
                    '{"mode_explicit": true}'
                )
                """
            ),
            {"now": time.time()},
        )

    TelegramGatewayService(
        settings=build_settings(allowlist=(("100", "200"),)),
        memory_service=memory,
        operator_chat=StubOperatorChat(),
        http_client=FakeTelegramClient(),
        lock_path=tmp_path / "telegram.lock",
    )

    with memory.event_store.engine.begin() as conn:
        row = conn.execute(
            text("SELECT mode, metadata_json FROM telegram_thread_state WHERE thread_key = 'telegram:100:200'")
        ).mappings().one()

    assert row["mode"] == "standard"
    metadata = json.loads(row["metadata_json"])
    assert metadata["mode_explicit"] is False
    assert metadata["mode_source"] == "normalized_default"


def test_telegram_gateway_denies_non_private_or_unapproved_identities(tmp_path: Path) -> None:
    memory = build_memory(tmp_path)
    client = FakeTelegramClient()
    operator_chat = StubOperatorChat()
    service = TelegramGatewayService(
        settings=build_settings(allowlist=(("100", "200"),)),
        memory_service=memory,
        operator_chat=operator_chat,
        http_client=client,
        lock_path=tmp_path / "telegram.lock",
    )
    service._dispatch_thread_queue = lambda thread_key, update_id: None  # type: ignore[method-assign]

    service._accept_update(message_update(2, chat_id="100", user_id="200", chat_type="group", text="hello"))
    service._accept_update(message_update(3, chat_id="999", user_id="999", text="hello"))
    service._process_update_with_retries(2)
    service._process_update_with_retries(3)

    assert operator_chat.calls == []
    assert "private chats only" in client.sent_messages[0]["text"].lower()
    assert "not approved" in client.sent_messages[1]["text"].lower()


def test_telegram_gateway_voice_notes_route_through_transcription_and_chat(tmp_path: Path) -> None:
    memory = build_memory(tmp_path)
    client = FakeTelegramClient()
    operator_chat = StubOperatorChat()
    service = TelegramGatewayService(
        settings=build_settings(allowlist=(("100", "200"),)),
        memory_service=memory,
        operator_chat=operator_chat,
        http_client=client,
        audio_transcriber=lambda path: "hello from telegram voice",
        lock_path=tmp_path / "telegram.lock",
    )
    service._dispatch_thread_queue = lambda thread_key, update_id: None  # type: ignore[method-assign]

    accepted = service._accept_update(voice_update(4, caption="summarize this"))
    service._process_update_with_retries(4)
    transcription_events = memory.event_store.list_events_by_type("transcription", limit=5)

    assert accepted is True
    assert transcription_events
    assert transcription_events[0].payload["transport"] == "telegram"
    assert "Voice note transcript" in operator_chat.calls[0]["message"]
    assert operator_chat.calls[0]["modality"] == "voice"
    assert "handled:summarize this" in client.sent_messages[-1]["text"]


def test_telegram_gateway_orders_each_thread_but_allows_parallel_identities(tmp_path: Path) -> None:
    memory = build_memory(tmp_path)
    client = FakeTelegramClient()
    operator_chat = StubOperatorChat(sleep_map={"A1": 0.25, "A2": 0.01, "B1": 0.08})
    service = TelegramGatewayService(
        settings=build_settings(allowlist=(("100", "200"), ("101", "201"))),
        memory_service=memory,
        operator_chat=operator_chat,
        http_client=client,
        lock_path=tmp_path / "telegram.lock",
    )

    service._accept_update(message_update(10, chat_id="100", user_id="200", text="A1"))
    service._accept_update(message_update(11, chat_id="100", user_id="200", text="A2"))
    service._accept_update(message_update(12, chat_id="101", user_id="201", text="B1"))
    wait_for_processed(memory, expected=3)
    service.stop()

    assert operator_chat.started["A2"] >= operator_chat.finished["A1"]
    assert operator_chat.started["B1"] < operator_chat.finished["A1"]


def test_telegram_status_surfaces_into_awareness_and_summary(tmp_path: Path, monkeypatch) -> None:
    memory = build_memory(tmp_path)
    service = TelegramGatewayService(
        settings=build_settings(allowlist=(("100", "200"),)),
        memory_service=memory,
        operator_chat=StubOperatorChat(),
        http_client=FakeTelegramClient(),
        lock_path=tmp_path / "telegram.lock",
    )
    service._set_gateway_state("polling", emit_event=True)

    awareness = SystemAwarenessService(memory_service=memory, tinyprogrammer=FakeTinyProgrammer())
    stabilize_awareness(awareness, monkeypatch)
    observed_services = awareness.observe_services()
    summary = OperatorSummaryService(
        memory_service=memory,
        awareness_service=awareness,
        tinyprogrammer=FakeTinyProgrammer(),
    ).build_summary(include_awareness=True)
    gateway_status = read_telegram_gateway_status(event_store=memory.event_store, settings=service.settings)

    assert gateway_status["status"] == "polling"
    assert observed_services["telegram"]["status"] == "polling"
    assert summary["telegram"]["status"] == "polling"
