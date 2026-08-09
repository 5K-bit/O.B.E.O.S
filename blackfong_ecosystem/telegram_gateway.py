from __future__ import annotations

import contextlib
import json
import logging
import os
import platform
import queue
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

import httpx
from sqlalchemy import text

from .config import paths, telegram_settings, TelegramSettings
from .storage import EventStore

if TYPE_CHECKING:
    from .conversation_memory import ConversationMemoryService
    from .operator_chat import OperatorChatService
    from .system_awareness import SystemAwarenessService


logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
GATEWAY_ID = "default"
DEFAULT_CAPABILITIES = ("agent_chat", "chat", "status_read")
DEFAULT_MODE = "fast"
ALLOWED_MODES = {"fast", "standard", "deep"}
TELEGRAM_THREAD_NAMESPACE = uuid.UUID("a377d71d-cdb3-4dea-b09a-4327d85df0d4")
TELEGRAM_REQUEST_NAMESPACE = uuid.UUID("6244fef2-4956-4cd4-aed1-b8d947f3b0bc")
WORKER_IDLE_TIMEOUT_S = 30.0


class TelegramGatewayError(RuntimeError):
    """Base Telegram gateway error."""


class TelegramUnauthorizedError(TelegramGatewayError):
    """Raised when the Telegram bot token is invalid."""


class TelegramConflictError(TelegramGatewayError):
    """Raised when another Telegram poller owns the bot token."""


def build_thread_key(chat_id: str, user_id: str) -> str:
    return f"telegram:{chat_id}:{user_id}"


def build_session_id(chat_id: str, user_id: str) -> str:
    return str(uuid.uuid5(TELEGRAM_THREAD_NAMESPACE, build_thread_key(chat_id, user_id)))


def build_request_id(update_id: int) -> str:
    return str(uuid.uuid5(TELEGRAM_REQUEST_NAMESPACE, f"telegram-update:{int(update_id)}"))


def read_telegram_gateway_status(
    *,
    event_store: Optional[EventStore] = None,
    settings: Optional[TelegramSettings] = None,
) -> Dict[str, Any]:
    store = event_store or EventStore()
    store.init_db()
    configured_settings = settings or telegram_settings
    with store.engine.begin() as conn:
        state_row = conn.execute(
            text(
                """
                SELECT
                    gateway_id,
                    status,
                    enabled,
                    bot_username,
                    last_update_id,
                    last_inbound_at,
                    last_outbound_at,
                    last_error,
                    lock_owner,
                    authenticated_at,
                    updated_at,
                    metadata_json
                FROM telegram_gateway_state
                WHERE gateway_id = :gateway_id
                """
            ),
            {"gateway_id": GATEWAY_ID},
        ).mappings().first()
        thread_count = int(
            conn.execute(text("SELECT COUNT(*) FROM telegram_thread_state")).scalar_one() or 0
        )
        queued_count = int(
            conn.execute(
                text("SELECT COUNT(*) FROM telegram_update_inbox WHERE status IN ('queued', 'processing', 'responded')")
            ).scalar_one()
            or 0
        )
        processed_count = int(
            conn.execute(
                text("SELECT COUNT(*) FROM telegram_update_inbox WHERE status = 'processed'")
            ).scalar_one()
            or 0
        )
    metadata: Dict[str, Any] = {}
    if state_row and state_row.get("metadata_json"):
        with contextlib.suppress(Exception):
            metadata = json.loads(str(state_row["metadata_json"]) or "{}")
    if state_row is None:
        inferred_status = "configured" if configured_settings.bot_token and configured_settings.enabled else "stopped"
        return {
            "schema_version": SCHEMA_VERSION,
            "gateway": "telegram",
            "status": inferred_status,
            "enabled": bool(configured_settings.enabled),
            "configured": bool(configured_settings.bot_token),
            "private_chat_only": bool(configured_settings.private_chat_only),
            "approved_identity_count": len(configured_settings.allowlisted_identities),
            "thread_count": thread_count,
            "queued_updates": queued_count,
            "processed_updates": processed_count,
            "last_update_id": 0,
            "last_inbound_at": None,
            "last_outbound_at": None,
            "last_error": None,
            "lock_owner": None,
            "bot_username": None,
            "metadata": {},
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "gateway": "telegram",
        "status": str(state_row["status"] or "stopped"),
        "enabled": bool(state_row["enabled"]),
        "configured": bool(configured_settings.bot_token),
        "private_chat_only": bool(configured_settings.private_chat_only),
        "approved_identity_count": len(configured_settings.allowlisted_identities),
        "thread_count": thread_count,
        "queued_updates": queued_count,
        "processed_updates": processed_count,
        "last_update_id": int(state_row["last_update_id"] or 0),
        "last_inbound_at": state_row["last_inbound_at"],
        "last_outbound_at": state_row["last_outbound_at"],
        "last_error": state_row["last_error"],
        "lock_owner": state_row["lock_owner"],
        "bot_username": state_row["bot_username"],
        "authenticated_at": state_row["authenticated_at"],
        "updated_at": state_row["updated_at"],
        "metadata": metadata,
    }


class TelegramGatewayLock:
    """Simple singleton lock backed by a local file in the canonical data directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.owner = {
            "pid": os.getpid(),
            "hostname": socket.gethostname() or platform.node() or "unknown-host",
            "created_at": time.time(),
        }
        self._held = False

    def acquire(self) -> tuple[bool, str]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            payload = self._read_payload()
            pid = int(payload.get("pid") or 0)
            if pid and self._pid_alive(pid):
                return False, self._owner_label(payload)
            with contextlib.suppress(Exception):
                self.path.unlink()
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(str(self.path), flags)
        except FileExistsError:
            return False, self._owner_label(self._read_payload())
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.owner, handle)
        self._held = True
        return True, self._owner_label(self.owner)

    def release(self) -> None:
        if not self._held:
            return
        with contextlib.suppress(Exception):
            if self.path.exists():
                self.path.unlink()
        self._held = False

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        with contextlib.suppress(Exception):
            import psutil

            return bool(psutil.pid_exists(pid))
        return True

    def _read_payload(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _owner_label(payload: Dict[str, Any]) -> str:
        pid = payload.get("pid") or "unknown"
        host = payload.get("hostname") or "unknown-host"
        return f"pid={pid}@{host}"


class TelegramGatewayService:
    """Telegram long-poll gateway that routes everything through OperatorChatService."""

    def __init__(
        self,
        *,
        settings: Optional[TelegramSettings] = None,
        memory_service: Optional["ConversationMemoryService"] = None,
        event_store: Optional[EventStore] = None,
        operator_chat: Optional["OperatorChatService"] = None,
        awareness_service: Optional["SystemAwarenessService"] = None,
        http_client: Optional[Any] = None,
        lock_path: Optional[Path] = None,
        audio_transcriber: Optional[Callable[[Path], str]] = None,
    ) -> None:
        if memory_service is None:
            from .conversation_memory import conversation_memory_service

            memory_service = conversation_memory_service
        self.settings = settings or telegram_settings
        self.memory_service = memory_service
        self.event_store = event_store or getattr(self.memory_service, "event_store", None) or EventStore()
        self.event_store.init_db()
        if operator_chat is None:
            from .operator_chat import OperatorChatService

            operator_chat = OperatorChatService(memory_service=self.memory_service)
        self.operator_chat = operator_chat
        if awareness_service is None:
            from .system_awareness import SystemAwarenessService

            awareness_service = SystemAwarenessService(memory_service=self.memory_service)
        self.awareness_service = awareness_service
        self._http_client = http_client or httpx.Client(timeout=self.settings.request_timeout_seconds)
        self._owns_http_client = http_client is None
        self._audio_transcriber = audio_transcriber
        self._stop_event = threading.Event()
        self._queue_lock = threading.RLock()
        self._worker_queues: Dict[str, queue.Queue[int]] = {}
        self._worker_threads: Dict[str, threading.Thread] = {}
        self._lock = TelegramGatewayLock(lock_path or (paths.data_root / "telegram_gateway.lock"))
        self._bot_username: Optional[str] = None
        self._terminal_status: Optional[str] = None
        self._worker_attempt_limit = 3

    def start(self) -> Dict[str, Any]:
        if not self.settings.enabled or not self.settings.bot_token:
            reason = "telegram_disabled_or_missing_bot_token"
            self._set_gateway_state("stopped", last_error=reason, emit_event=True)
            return self.status()
        acquired, owner_label = self._lock.acquire()
        if not acquired:
            self._set_gateway_state("conflict", last_error=f"telegram_gateway_lock_held_by:{owner_label}", lock_owner=owner_label, emit_event=True)
            self._terminal_status = "conflict"
            return self.status()
        try:
            self._set_gateway_state("configured", last_error=None, lock_owner=owner_label, emit_event=True)
            self._authenticate()
            self._restore_queued_updates()
            self._set_gateway_state("polling", last_error=None, emit_event=True)
            while not self._stop_event.is_set():
                try:
                    self.poll_once()
                    if not self._stop_event.is_set():
                        self._set_gateway_state("polling", last_error=None, emit_event=False)
                except TelegramUnauthorizedError as exc:
                    self._set_gateway_state("unauthorized", last_error=self._sanitize_error(exc), emit_event=True)
                    self._terminal_status = "unauthorized"
                    break
                except TelegramConflictError as exc:
                    self._set_gateway_state("conflict", last_error=self._sanitize_error(exc), emit_event=True)
                    self._terminal_status = "conflict"
                    break
                except Exception as exc:
                    logger.exception("telegram_gateway_poll_failed")
                    self._set_gateway_state("degraded", last_error=self._sanitize_error(exc), emit_event=True)
                    time.sleep(max(0.5, float(self.settings.poll_retry_delay_seconds)))
            return self.status()
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop_event.set()
        for worker in list(self._worker_threads.values()):
            worker.join(timeout=1.5)
        self._worker_threads.clear()
        self._worker_queues.clear()
        self._lock.release()
        if self._owns_http_client:
            with contextlib.suppress(Exception):
                self._http_client.close()
        if self._terminal_status not in {"unauthorized", "conflict"}:
            self._set_gateway_state("stopped", last_error=None, emit_event=False)

    def status(self) -> Dict[str, Any]:
        return read_telegram_gateway_status(event_store=self.event_store, settings=self.settings)

    def poll_once(self) -> int:
        updates = self._get_updates(
            offset=self._next_update_offset(),
            timeout=int(max(1, float(self.settings.poll_timeout_seconds))),
            limit=int(max(1, self.settings.poll_limit)),
        )
        count = 0
        for update in updates:
            if self._accept_update(update):
                count += 1
        return count

    def _authenticate(self) -> None:
        result = self._api_call("getMe")
        self._bot_username = str(result.get("username") or "") or None
        self._set_gateway_state(
            "authenticated",
            last_error=None,
            metadata={"bot_id": result.get("id"), "bot_username": self._bot_username},
            emit_event=True,
        )

    def _get_updates(self, *, offset: int, timeout: int, limit: int) -> list[Dict[str, Any]]:
        result = self._api_call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "limit": limit,
                "allowed_updates": json.dumps(["message", "edited_message"]),
            },
        )
        return [dict(item) for item in list(result or [])]

    def _api_call(self, method: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        url = f"https://api.telegram.org/bot{self.settings.bot_token}/{method}"
        request_payload = dict(payload or {})
        try:
            if method == "getUpdates":
                response = self._http_client.get(url, params=request_payload)
            else:
                response = self._http_client.post(url, json=request_payload)
        except Exception as exc:
            raise TelegramGatewayError(self._sanitize_error(exc)) from exc
        try:
            data = response.json()
        except Exception as exc:
            raise TelegramGatewayError(f"telegram_{method}_non_json_response") from exc
        if not data.get("ok", False):
            error_code = int(data.get("error_code") or response.status_code or 0)
            description = str(data.get("description") or f"telegram_{method}_failed")
            if error_code == 401:
                raise TelegramUnauthorizedError(description)
            if error_code == 409:
                raise TelegramConflictError(description)
            raise TelegramGatewayError(description)
        return data.get("result")

    def _accept_update(self, update: Dict[str, Any]) -> bool:
        update_id = int(update.get("update_id") or 0)
        if update_id <= 0:
            return False
        payload = self._normalize_update(update)
        row_status = "ignored" if payload["route_kind"] == "unsupported_update" else "queued"
        inserted = self._insert_inbox_row(payload, row_status=row_status)
        self._set_gateway_state("polling", last_update_id=update_id, last_inbound_at=time.time(), emit_event=False)
        if not inserted:
            return False
        if row_status == "queued":
            self._dispatch_thread_queue(str(payload["thread_key"]), update_id)
        return True

    def _normalize_update(self, update: Dict[str, Any]) -> Dict[str, Any]:
        received_at = time.time()
        update_id = int(update.get("update_id") or 0)
        message = dict(update.get("message") or update.get("edited_message") or {})
        chat = dict(message.get("chat") or {})
        from_user = dict(message.get("from") or {})
        chat_id = str(chat.get("id") or "")
        user_id = str(from_user.get("id") or "")
        chat_type = str(chat.get("type") or "")
        message_id = int(message.get("message_id") or 0) or None
        text_value = str(message.get("text") or "").strip()
        caption = str(message.get("caption") or "").strip()
        voice_payload = dict(message.get("voice") or {})
        thread_key = build_thread_key(chat_id or "unknown", user_id or "unknown")
        route_kind = "unsupported_update"
        requested_agent = None
        mode_request = None
        route_text = text_value
        allowed = bool(chat_id and user_id)
        if self.settings.private_chat_only and chat_type and chat_type != "private":
            route_kind = "denied_non_private"
            allowed = False
        elif not self._identity_allowed(chat_id, user_id):
            route_kind = "denied_identity"
            allowed = False
        elif voice_payload:
            route_kind = "voice" if self.settings.voice_enabled else "voice_disabled"
        elif text_value.startswith("/"):
            route_kind, requested_agent, route_text, mode_request = self._classify_command(text_value)
        elif text_value:
            route_kind = "chat"

        thread_state = self._upsert_thread_state(
            chat_id=chat_id,
            user_id=user_id,
            message_id=message_id,
            update_id=update_id,
            create=allowed,
        )
        session_id = thread_state.get("session_id") if thread_state else None
        analysis_mode = str(thread_state.get("mode") or DEFAULT_MODE) if thread_state else DEFAULT_MODE
        return {
            "schema_version": SCHEMA_VERSION,
            "update_id": update_id,
            "message_id": message_id,
            "telegram_chat_id": chat_id,
            "telegram_user_id": user_id,
            "chat_type": chat_type,
            "thread_key": thread_key,
            "session_id": session_id,
            "request_id": build_request_id(update_id),
            "route_kind": route_kind,
            "analysis_mode": analysis_mode,
            "requested_agent": requested_agent,
            "mode_request": mode_request,
            "text": route_text,
            "caption": caption,
            "voice": {
                "file_id": voice_payload.get("file_id"),
                "duration": voice_payload.get("duration"),
                "mime_type": voice_payload.get("mime_type"),
                "file_size": voice_payload.get("file_size"),
            }
            if voice_payload
            else None,
            "received_at": received_at,
            "allowed": allowed,
            "transport": "telegram",
        }

    def _insert_inbox_row(self, payload: Dict[str, Any], *, row_status: str) -> bool:
        with self.event_store.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO telegram_update_inbox (
                        update_id,
                        message_id,
                        telegram_chat_id,
                        telegram_user_id,
                        chat_type,
                        thread_key,
                        session_id,
                        request_id,
                        route_kind,
                        status,
                        normalized_payload,
                        received_at,
                        accepted_at
                    ) VALUES (
                        :update_id,
                        :message_id,
                        :telegram_chat_id,
                        :telegram_user_id,
                        :chat_type,
                        :thread_key,
                        :session_id,
                        :request_id,
                        :route_kind,
                        :status,
                        :normalized_payload,
                        :received_at,
                        :accepted_at
                    )
                    ON CONFLICT(update_id) DO NOTHING
                    """
                ),
                {
                    "update_id": int(payload["update_id"]),
                    "message_id": payload.get("message_id"),
                    "telegram_chat_id": str(payload.get("telegram_chat_id") or ""),
                    "telegram_user_id": str(payload.get("telegram_user_id") or ""),
                    "chat_type": payload.get("chat_type"),
                    "thread_key": str(payload.get("thread_key") or ""),
                    "session_id": payload.get("session_id"),
                    "request_id": str(payload.get("request_id") or ""),
                    "route_kind": str(payload.get("route_kind") or "chat"),
                    "status": row_status,
                    "normalized_payload": json.dumps(payload, ensure_ascii=False),
                    "received_at": float(payload.get("received_at") or time.time()),
                    "accepted_at": time.time(),
                },
            )
        return bool(getattr(result, "rowcount", 0))

    def _dispatch_thread_queue(self, thread_key: str, update_id: int) -> None:
        with self._queue_lock:
            update_queue = self._worker_queues.get(thread_key)
            if update_queue is None:
                update_queue = queue.Queue()
                self._worker_queues[thread_key] = update_queue
            worker = self._worker_threads.get(thread_key)
            if worker is None or not worker.is_alive():
                worker = threading.Thread(target=self._worker_loop, args=(thread_key,), daemon=True)
                self._worker_threads[thread_key] = worker
                worker.start()
            update_queue.put(int(update_id))

    def _worker_loop(self, thread_key: str) -> None:
        while not self._stop_event.is_set():
            update_queue = self._worker_queues.get(thread_key)
            if update_queue is None:
                return
            try:
                update_id = update_queue.get(timeout=WORKER_IDLE_TIMEOUT_S)
            except queue.Empty:
                with self._queue_lock:
                    queue_ref = self._worker_queues.get(thread_key)
                    if queue_ref is update_queue and queue_ref.empty():
                        self._worker_queues.pop(thread_key, None)
                        self._worker_threads.pop(thread_key, None)
                        return
                continue
            try:
                self._process_update_with_retries(int(update_id))
            finally:
                update_queue.task_done()

    def _process_update_with_retries(self, update_id: int) -> None:
        for attempt in range(1, self._worker_attempt_limit + 1):
            row = self._get_inbox_row(update_id)
            if row is None:
                return
            payload = dict(row["payload"])
            if row["status"] == "processed":
                return
            self._set_inbox_status(update_id, status="processing", attempts=int(row["attempts"]) + 1)
            if not payload.get("response_text") and payload.get("route_kind") != "unsupported_update":
                response_text = self._build_response_text(payload)
                payload["response_text"] = self._clamp_message(response_text)
                self._update_inbox_payload(update_id, payload, status="responded", processed_at=time.time(), error=None)
                self._emit_request_outcome(payload, success=True, reason="response_ready")
            if payload.get("route_kind") == "unsupported_update":
                self._set_inbox_status(update_id, status="ignored", processed_at=time.time(), error=None)
                return
            try:
                self._send_text_reply(
                    chat_id=str(payload.get("telegram_chat_id") or ""),
                    text=str(payload.get("response_text") or "I do not have a response for that yet."),
                    reply_to_message_id=payload.get("message_id"),
                )
                self._set_inbox_status(update_id, status="processed", delivered_at=time.time(), error=None)
                self._emit_delivery_result(payload, success=True, error=None)
                return
            except Exception as exc:
                logger.exception("telegram_gateway_delivery_failed")
                self._update_inbox_payload(update_id, payload, status="responded", error=self._sanitize_error(exc))
                self._emit_delivery_result(payload, success=False, error=self._sanitize_error(exc))
                if attempt >= self._worker_attempt_limit or self._stop_event.is_set():
                    self._set_inbox_status(update_id, status="failed", error=self._sanitize_error(exc))
                    return
                time.sleep(max(0.5, float(self.settings.poll_retry_delay_seconds)))

    def _build_response_text(self, payload: Dict[str, Any]) -> str:
        route_kind = str(payload.get("route_kind") or "chat")
        if route_kind == "denied_non_private":
            return "This O.B.E.O.S Telegram gateway accepts private chats only."
        if route_kind == "denied_identity":
            return "This O.B.E.O.S Telegram gateway is private and this Telegram identity is not approved."
        if route_kind == "voice_disabled":
            return "Voice notes are disabled for this O.B.E.O.S Telegram gateway right now."
        if route_kind == "help_command":
            return self._help_text()
        if route_kind == "mode_command":
            return self._handle_mode_command(payload)
        if route_kind == "status_command":
            return self._handle_status_command(payload)
        if route_kind == "agent_command":
            return self._route_operator_chat(payload, text=str(payload.get("text") or ""), requested_agent=payload.get("requested_agent"))
        if route_kind == "voice":
            return self._handle_voice_command(payload)
        if route_kind == "chat":
            return self._route_operator_chat(payload, text=str(payload.get("text") or ""))
        return "I did not understand that Telegram update."

    def _handle_mode_command(self, payload: Dict[str, Any]) -> str:
        requested = str(payload.get("mode_request") or "").strip().lower()
        if requested not in ALLOWED_MODES:
            return "Valid Telegram modes are: fast, standard, deep."
        thread_key = str(payload.get("thread_key") or "")
        if not thread_key:
            return "I could not persist that Telegram mode because the thread identity was incomplete."
        self._set_thread_mode(thread_key, requested)
        payload["analysis_mode"] = requested
        return f"Telegram mode set to {requested} for this O.B.E.O.S thread."

    def _handle_status_command(self, payload: Dict[str, Any]) -> str:
        session_id = payload.get("session_id")
        request_id = str(payload.get("request_id") or "")
        correlation_id = self.memory_service.create_correlation_id()
        analysis_mode = str(payload.get("analysis_mode") or DEFAULT_MODE)
        query_event = self.memory_service.persist_user_turn(
            session_id=session_id or build_session_id(str(payload.get("telegram_chat_id") or ""), str(payload.get("telegram_user_id") or "")),
            correlation_id=correlation_id,
            text="/status",
            modality="text",
            routed_agent="daise",
            analysis_mode=analysis_mode,
            request_id=request_id,
        )
        awareness = self.awareness_service.build_snapshot(emit=False)
        telegram_status = self.status()
        response_text = self._render_status_message(awareness, telegram_status, analysis_mode=analysis_mode)
        response_event = self.memory_service.persist_agent_response(
            session_id=str(query_event.metadata.get("session_id") or session_id or ""),
            correlation_id=correlation_id,
            agent_name="daise",
            response_text=response_text,
            confidence=0.9,
            modality="text",
            parent_event_id=query_event.id,
            success=True,
            extras={
                "selected_agent": "daise",
                "transport": "telegram",
                "command": "/status",
                "analysis_mode": analysis_mode,
            },
            request_id=request_id,
        )
        self.memory_service.persist_workflow_event(
            source="telegram",
            event_type="request_outcome",
            payload={
                "schema_version": SCHEMA_VERSION,
                "transport": "telegram",
                "route_kind": "status_command",
                "success": True,
                "reason": "status_snapshot",
                "request_id": request_id,
            },
            confidence=0.94,
            session_id=str(query_event.metadata.get("session_id") or session_id or ""),
            correlation_id=correlation_id,
            request_id=request_id,
            parent_event_id=response_event.id,
        )
        return response_text

    def _handle_voice_command(self, payload: Dict[str, Any]) -> str:
        if not self.settings.voice_enabled:
            return "Voice notes are disabled for this O.B.E.O.S Telegram gateway right now."
        voice = dict(payload.get("voice") or {})
        duration = float(voice.get("duration") or 0.0)
        file_size = int(voice.get("file_size") or 0)
        if duration > float(self.settings.max_voice_duration_seconds):
            return "That voice note is longer than the Telegram gateway allows right now."
        if file_size and file_size > int(self.settings.max_voice_download_bytes):
            return "That voice note is larger than the Telegram gateway allows right now."
        transcript = str(payload.get("transcript") or "").strip()
        if not transcript:
            transcript = self._download_and_transcribe_voice(payload)
            payload["transcript"] = transcript
        if not transcript:
            return "I could not transcribe that voice note cleanly enough yet."
        caption = str(payload.get("caption") or "").strip()
        text = transcript if not caption else f"{caption}\n\nVoice note transcript:\n{transcript}"
        return self._route_operator_chat(payload, text=text, requested_agent=payload.get("requested_agent"), modality="voice")

    def _route_operator_chat(
        self,
        payload: Dict[str, Any],
        *,
        text: str,
        requested_agent: Optional[Any] = None,
        modality: str = "text",
    ) -> str:
        request_id = str(payload.get("request_id") or "")
        session_id = str(payload.get("session_id") or build_session_id(str(payload.get("telegram_chat_id") or ""), str(payload.get("telegram_user_id") or "")))
        analysis_mode = str(payload.get("analysis_mode") or DEFAULT_MODE)
        requested_agent_text = str(requested_agent or "").strip() or None
        result = self.operator_chat.send_message(
            message=text,
            session_id=session_id,
            request_id=request_id,
            agent_override=requested_agent_text,
            analysis_mode=analysis_mode,
            modality=modality,
            context=self._operator_context(payload, requested_agent=requested_agent_text),
        )
        if result.get("status") == "error":
            return str(result.get("error") or "I hit an internal O.B.E.O.S routing fault while handling that Telegram request.")
        result_payload = dict(result.get("result") or {})
        return str(
            result_payload.get("response")
            or result_payload.get("message")
            or result.get("error")
            or "O.B.E.O.S completed the Telegram request without a visible response."
        )

    def _operator_context(self, payload: Dict[str, Any], *, requested_agent: Optional[str]) -> Dict[str, Any]:
        return {
            "input_origin": "telegram",
            "transport": "telegram",
            "telegram_chat_id": str(payload.get("telegram_chat_id") or ""),
            "telegram_user_id": str(payload.get("telegram_user_id") or ""),
            "telegram_message_id": payload.get("message_id"),
            "telegram_update_id": payload.get("update_id"),
            "chat_type": str(payload.get("chat_type") or ""),
            "requested_agent": requested_agent,
            "capabilities": list(DEFAULT_CAPABILITIES),
        }

    def _download_and_transcribe_voice(self, payload: Dict[str, Any]) -> str:
        voice = dict(payload.get("voice") or {})
        file_id = str(voice.get("file_id") or "").strip()
        if not file_id:
            return ""
        started = time.time()
        file_info = self._api_call("getFile", {"file_id": file_id})
        file_path = str(file_info.get("file_path") or "").strip()
        if not file_path:
            return ""
        with tempfile.TemporaryDirectory(prefix="obeos_telegram_voice_") as tmp_dir:
            temp_root = Path(tmp_dir)
            suffix = Path(file_path).suffix or ".ogg"
            download_path = temp_root / f"telegram_voice{suffix}"
            normalized_path = temp_root / "telegram_voice.wav"
            raw_bytes = self._download_file(file_path)
            if len(raw_bytes) > int(self.settings.max_voice_download_bytes):
                raise TelegramGatewayError("telegram_voice_download_exceeded_size_limit")
            download_path.write_bytes(raw_bytes)
            transcript = self._transcribe_voice_file(download_path, normalized_path)
        if len(transcript) > int(self.settings.max_transcript_chars):
            transcript = transcript[: int(self.settings.max_transcript_chars)].rstrip()
        self.memory_service.persist_workflow_event(
            source="telegram",
            event_type="transcription",
            payload={
                "schema_version": SCHEMA_VERSION,
                "transport": "telegram",
                "telegram_update_id": payload.get("update_id"),
                "telegram_message_id": payload.get("message_id"),
                "transcript": transcript,
                "transcript_length": len(transcript),
                "voice_duration_s": voice.get("duration"),
                "processing_ms": round((time.time() - started) * 1000, 2),
            },
            confidence=0.86 if transcript else 0.3,
            session_id=payload.get("session_id"),
            request_id=payload.get("request_id"),
            tags=["telegram", "voice", "transcription"],
        )
        return transcript

    def _download_file(self, file_path: str) -> bytes:
        url = f"https://api.telegram.org/file/bot{self.settings.bot_token}/{file_path}"
        response = self._http_client.get(url)
        response.raise_for_status()
        return bytes(response.content)

    def _transcribe_voice_file(self, source_path: Path, normalized_path: Path) -> str:
        if self._audio_transcriber is not None:
            return str(self._audio_transcriber(source_path) or "").strip()
        if source_path.suffix.lower() == ".wav":
            normalized = source_path
        else:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise TelegramGatewayError("telegram_voice_requires_ffmpeg_for_ogg_opus_normalization")
            subprocess.run(
                [ffmpeg, "-y", "-i", str(source_path), "-ac", "1", "-ar", "16000", str(normalized_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            normalized = normalized_path
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:
            raise TelegramGatewayError("telegram_voice_transcription_unavailable") from exc
        model = WhisperModel("base.en", device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(normalized), beam_size=1, language="en")
        transcript = " ".join(str(segment.text or "").strip() for segment in segments).strip()
        return transcript

    def _send_text_reply(self, *, chat_id: str, text: str, reply_to_message_id: Optional[int]) -> None:
        clipped = self._clamp_message(text)
        self._api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": clipped,
                "reply_to_message_id": reply_to_message_id,
                "disable_web_page_preview": True,
            },
        )
        self._set_gateway_state("polling", last_outbound_at=time.time(), emit_event=False)

    def _help_text(self) -> str:
        return (
            "OBEOS Telegram commands:\n"
            "/status - bounded system status\n"
            "/mode fast|standard|deep - set this thread's default mode\n"
            "/agent <name> <message> - ask through a requested agent without bypassing D.A.I.S.E\n"
            "/help - show this help\n\n"
            "Plain messages route through the normal O.B.E.O.S chat stack."
        )

    def _render_status_message(
        self,
        awareness: Dict[str, Any],
        telegram_status: Dict[str, Any],
        *,
        analysis_mode: str,
    ) -> str:
        observed = dict(awareness.get("observed") or {})
        backend = dict(observed.get("backend") or {})
        queue_state = dict(observed.get("queue") or {})
        latency = dict(observed.get("latency") or {})
        memory = dict(observed.get("memory") or {})
        services = dict(observed.get("services") or {})
        tiny = dict(services.get("tinyprogrammer") or {})
        health = dict(awareness.get("health") or {})
        return "\n".join(
            [
                "OBEOS Status",
                f"Health: {health.get('overall', 'unknown')}",
                f"Backend: {backend.get('status', 'unknown')} | model={backend.get('configured_model', 'unknown')}",
                f"Queue: pending={queue_state.get('pending_requests', 0)} | active={queue_state.get('active_requests', 0)}",
                f"Latency: avg={latency.get('average_response_time_s', 0)}s | latest={latency.get('latest_response_time_s', 0)}s",
                f"Memory: {memory.get('status', 'unknown')} | recent_summaries={memory.get('recent_summaries', 0)}",
                f"Tiny: {'online' if tiny.get('online') else 'offline'} | state={tiny.get('state') or tiny.get('mode') or 'unknown'}",
                f"Telegram: {telegram_status.get('status', 'unknown')} | queued={telegram_status.get('queued_updates', 0)} | mode={analysis_mode}",
            ]
        )

    def _emit_delivery_result(self, payload: Dict[str, Any], *, success: bool, error: Optional[str]) -> None:
        self.memory_service.persist_workflow_event(
            source="telegram",
            event_type="notification_delivery_result",
            payload={
                "schema_version": SCHEMA_VERSION,
                "transport": "telegram",
                "telegram_update_id": payload.get("update_id"),
                "telegram_message_id": payload.get("message_id"),
                "success": success,
                "error": error,
            },
            confidence=0.96 if success else 0.52,
            session_id=payload.get("session_id"),
            request_id=payload.get("request_id"),
            tags=["telegram", "delivery"],
        )

    def _emit_request_outcome(self, payload: Dict[str, Any], *, success: bool, reason: str) -> None:
        self.memory_service.persist_workflow_event(
            source="telegram",
            event_type="request_outcome",
            payload={
                "schema_version": SCHEMA_VERSION,
                "transport": "telegram",
                "route_kind": payload.get("route_kind"),
                "success": success,
                "reason": reason,
                "telegram_update_id": payload.get("update_id"),
                "telegram_message_id": payload.get("message_id"),
            },
            confidence=0.92 if success else 0.48,
            session_id=payload.get("session_id"),
            request_id=payload.get("request_id"),
            tags=["telegram", "request_outcome"],
        )

    def _restore_queued_updates(self) -> None:
        with self.event_store.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE telegram_update_inbox
                    SET status = 'queued',
                        error = CASE
                            WHEN status = 'processing' THEN COALESCE(error, 'worker_recovered_after_restart')
                            ELSE error
                        END
                    WHERE status IN ('queued', 'processing', 'responded')
                    """
                )
            )
            rows = conn.execute(
                text(
                    """
                    SELECT update_id, thread_key
                    FROM telegram_update_inbox
                    WHERE status = 'queued'
                    ORDER BY update_id ASC
                    """
                )
            ).mappings().all()
        for row in rows:
            self._dispatch_thread_queue(str(row["thread_key"]), int(row["update_id"]))

    def _next_update_offset(self) -> int:
        state = self.status()
        return int(state.get("last_update_id") or 0) + 1

    def _identity_allowed(self, chat_id: str, user_id: str) -> bool:
        identity = (str(chat_id or "").strip(), str(user_id or "").strip())
        return identity in set(self.settings.allowlisted_identities)

    def _classify_command(self, text_value: str) -> tuple[str, Optional[str], str, Optional[str]]:
        command = text_value.strip()
        lowered = command.lower()
        if lowered.startswith("/status"):
            return "status_command", None, "/status", None
        if lowered.startswith("/start") or lowered.startswith("/help"):
            return "help_command", None, "/help", None
        if lowered.startswith("/mode"):
            parts = command.split(maxsplit=1)
            return "mode_command", None, command, parts[1].strip().lower() if len(parts) > 1 else None
        if lowered.startswith("/agent"):
            parts = command.split(maxsplit=2)
            requested_agent = parts[1].strip() if len(parts) > 1 else None
            routed_text = parts[2].strip() if len(parts) > 2 else ""
            return "agent_command", requested_agent, routed_text, None
        return "help_command", None, command, None

    def _upsert_thread_state(
        self,
        *,
        chat_id: str,
        user_id: str,
        message_id: Optional[int],
        update_id: int,
        create: bool,
    ) -> Dict[str, Any]:
        if not create or not chat_id or not user_id:
            return {}
        thread_key = build_thread_key(chat_id, user_id)
        session_id = build_session_id(chat_id, user_id)
        now = time.time()
        with self.event_store.engine.begin() as conn:
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
                        :thread_key,
                        :telegram_chat_id,
                        :telegram_user_id,
                        :session_id,
                        :mode,
                        :created_at,
                        :last_seen_at,
                        :last_message_id,
                        :last_update_id,
                        :metadata_json
                    )
                    ON CONFLICT(thread_key) DO UPDATE SET
                        last_seen_at = excluded.last_seen_at,
                        last_message_id = excluded.last_message_id,
                        last_update_id = excluded.last_update_id
                    """
                ),
                {
                    "thread_key": thread_key,
                    "telegram_chat_id": chat_id,
                    "telegram_user_id": user_id,
                    "session_id": session_id,
                    "mode": DEFAULT_MODE,
                    "created_at": now,
                    "last_seen_at": now,
                    "last_message_id": message_id,
                    "last_update_id": update_id,
                    "metadata_json": "{}",
                },
            )
            row = conn.execute(
                text(
                    """
                    SELECT thread_key, telegram_chat_id, telegram_user_id, session_id, mode, created_at, last_seen_at
                    FROM telegram_thread_state
                    WHERE thread_key = :thread_key
                    """
                ),
                {"thread_key": thread_key},
            ).mappings().first()
        return dict(row or {})

    def _set_thread_mode(self, thread_key: str, mode: str) -> None:
        with self.event_store.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE telegram_thread_state
                    SET mode = :mode,
                        last_seen_at = :last_seen_at
                    WHERE thread_key = :thread_key
                    """
                ),
                {"mode": mode, "last_seen_at": time.time(), "thread_key": thread_key},
            )

    def _get_inbox_row(self, update_id: int) -> Optional[Dict[str, Any]]:
        with self.event_store.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT update_id, status, attempts, normalized_payload, error
                    FROM telegram_update_inbox
                    WHERE update_id = :update_id
                    """
                ),
                {"update_id": update_id},
            ).mappings().first()
        if row is None:
            return None
        payload = json.loads(str(row["normalized_payload"]) or "{}")
        return {
            "update_id": int(row["update_id"]),
            "status": str(row["status"] or ""),
            "attempts": int(row["attempts"] or 0),
            "payload": payload,
            "error": row["error"],
        }

    def _set_inbox_status(
        self,
        update_id: int,
        *,
        status: str,
        attempts: Optional[int] = None,
        processed_at: Optional[float] = None,
        delivered_at: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        with self.event_store.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE telegram_update_inbox
                    SET status = :status,
                        attempts = COALESCE(:attempts, attempts),
                        processed_at = COALESCE(:processed_at, processed_at),
                        delivered_at = COALESCE(:delivered_at, delivered_at),
                        error = :error
                    WHERE update_id = :update_id
                    """
                ),
                {
                    "update_id": update_id,
                    "status": status,
                    "attempts": attempts,
                    "processed_at": processed_at,
                    "delivered_at": delivered_at,
                    "error": error,
                },
            )

    def _update_inbox_payload(
        self,
        update_id: int,
        payload: Dict[str, Any],
        *,
        status: str,
        processed_at: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        with self.event_store.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE telegram_update_inbox
                    SET normalized_payload = :normalized_payload,
                        status = :status,
                        processed_at = COALESCE(:processed_at, processed_at),
                        error = :error
                    WHERE update_id = :update_id
                    """
                ),
                {
                    "update_id": update_id,
                    "normalized_payload": json.dumps(payload, ensure_ascii=False),
                    "status": status,
                    "processed_at": processed_at,
                    "error": error,
                },
            )

    def _set_gateway_state(
        self,
        status: str,
        *,
        enabled: Optional[bool] = None,
        bot_username: Optional[str] = None,
        last_update_id: Optional[int] = None,
        last_inbound_at: Optional[float] = None,
        last_outbound_at: Optional[float] = None,
        last_error: Optional[str] = None,
        lock_owner: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        emit_event: bool = False,
    ) -> None:
        now = time.time()
        current = self.status()
        merged_metadata = dict(current.get("metadata") or {})
        merged_metadata.update(dict(metadata or {}))
        with self.event_store.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO telegram_gateway_state (
                        gateway_id,
                        status,
                        enabled,
                        bot_username,
                        last_update_id,
                        last_inbound_at,
                        last_outbound_at,
                        last_error,
                        lock_owner,
                        authenticated_at,
                        updated_at,
                        metadata_json
                    ) VALUES (
                        :gateway_id,
                        :status,
                        :enabled,
                        :bot_username,
                        :last_update_id,
                        :last_inbound_at,
                        :last_outbound_at,
                        :last_error,
                        :lock_owner,
                        :authenticated_at,
                        :updated_at,
                        :metadata_json
                    )
                    ON CONFLICT(gateway_id) DO UPDATE SET
                        status = excluded.status,
                        enabled = excluded.enabled,
                        bot_username = COALESCE(excluded.bot_username, telegram_gateway_state.bot_username),
                        last_update_id = CASE
                            WHEN excluded.last_update_id > telegram_gateway_state.last_update_id THEN excluded.last_update_id
                            ELSE telegram_gateway_state.last_update_id
                        END,
                        last_inbound_at = COALESCE(excluded.last_inbound_at, telegram_gateway_state.last_inbound_at),
                        last_outbound_at = COALESCE(excluded.last_outbound_at, telegram_gateway_state.last_outbound_at),
                        last_error = excluded.last_error,
                        lock_owner = COALESCE(excluded.lock_owner, telegram_gateway_state.lock_owner),
                        authenticated_at = COALESCE(excluded.authenticated_at, telegram_gateway_state.authenticated_at),
                        updated_at = excluded.updated_at,
                        metadata_json = excluded.metadata_json
                    """
                ),
                {
                    "gateway_id": GATEWAY_ID,
                    "status": status,
                    "enabled": int(self.settings.enabled if enabled is None else enabled),
                    "bot_username": bot_username or self._bot_username,
                    "last_update_id": int(last_update_id or current.get("last_update_id") or 0),
                    "last_inbound_at": last_inbound_at if last_inbound_at is not None else current.get("last_inbound_at"),
                    "last_outbound_at": last_outbound_at if last_outbound_at is not None else current.get("last_outbound_at"),
                    "last_error": last_error,
                    "lock_owner": lock_owner or current.get("lock_owner"),
                    "authenticated_at": now if status in {"authenticated", "polling"} else current.get("authenticated_at"),
                    "updated_at": now,
                    "metadata_json": json.dumps(merged_metadata, ensure_ascii=False),
                },
            )
        if emit_event and (status != current.get("status") or last_error != current.get("last_error")):
            self.memory_service.persist_workflow_event(
                source="telegram",
                event_type="backend_health_observation",
                payload={
                    "schema_version": SCHEMA_VERSION,
                    "transport": "telegram",
                    "gateway": "telegram",
                    "status": status,
                    "last_error": last_error,
                    "approved_identity_count": len(self.settings.allowlisted_identities),
                },
                confidence=0.98 if status in {"authenticated", "polling"} else 0.72,
                tags=["telegram", "gateway", status],
            )

    @staticmethod
    def _clamp_message(text: str, *, limit: int = 3900) -> str:
        normalized = str(text or "").strip()
        if len(normalized) <= limit:
            return normalized or "O.B.E.O.S completed the request."
        return normalized[:limit].rstrip() + "\n... [truncated for Telegram]"

    @staticmethod
    def _sanitize_error(exc: Any) -> str:
        return str(exc or "").strip() or "telegram_gateway_error"
