from __future__ import annotations

import platform
import time
from typing import Any, Dict, List, Optional

from .config import hud_settings
from .conversation_memory import ConversationMemoryService, conversation_memory_service
from .storage import EventStore


class NotificationService:
    """Event-backed operator notifications for HUD, TUI, and desktop delivery."""

    def __init__(
        self,
        *,
        memory_service: Optional[ConversationMemoryService] = None,
        windows_toasts_enabled: Optional[bool] = None,
    ) -> None:
        self.memory_service = memory_service or conversation_memory_service
        self.event_store: EventStore = self.memory_service.event_store
        self.windows_toasts_enabled = hud_settings.windows_toasts_enabled if windows_toasts_enabled is None else windows_toasts_enabled

    def create(
        self,
        *,
        title: str,
        message: str,
        level: str = "info",
        category: str = "system",
        action: Optional[str] = None,
        target: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        desktop: bool = False,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "schema_version": "1.0",
            "title": title,
            "message": message,
            "level": level,
            "category": category,
            "action": action,
            "target": target,
            "read": False,
            "created_at": time.time(),
            "metadata": metadata or {},
            "provenance": {
                "source": "operator_runtime",
                "origin_service": "notification_service",
                "derived_from": [],
                "collection_method": "notification_create",
                "observed_at": time.time(),
                "schema_version": "1.0",
            },
        }
        event = self.memory_service.persist_workflow_event(
            source="system",
            event_type="notification_created",
            payload=payload,
            confidence=0.98,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["notification", category, level],
        )
        notification = {"id": event.id, **payload}
        if desktop:
            notification["delivery"] = self._deliver_windows_toast(notification)
        return notification

    def list_notifications(self, *, limit: int = 50, unread_only: bool = False) -> Dict[str, Any]:
        created = self.event_store.list_events_by_type("notification_created", limit=max(limit * 4, 100))
        read_ids = {
            str(event.payload.get("notification_id"))
            for event in self.event_store.list_events_by_type("notification_read", limit=1000)
            if event.payload.get("notification_id")
        }
        notifications: List[Dict[str, Any]] = []
        for event in created:
            item = {"id": event.id, **dict(event.payload or {})}
            item["read"] = event.id in read_ids or bool(item.get("read"))
            item["timestamp"] = event.timestamp / 1000.0
            if unread_only and item["read"]:
                continue
            notifications.append(item)
            if len(notifications) >= limit:
                break
        unread_count = sum(1 for item in notifications if not item["read"])
        return {
            "schema_version": "1.0",
            "notifications": notifications,
            "unread_count": unread_count,
            "total": len(notifications),
        }

    def mark_read(self, notification_id: str) -> Dict[str, Any]:
        event = self.memory_service.persist_workflow_event(
            source="user",
            event_type="notification_read",
            payload={
                "schema_version": "1.0",
                "notification_id": notification_id,
                "read_at": time.time(),
            },
            confidence=1.0,
            tags=["notification", "read"],
        )
        return {"status": "read", "notification_id": notification_id, "event_id": event.id}

    def mark_all_read(self) -> Dict[str, Any]:
        listed = self.list_notifications(limit=500)
        unread = [item for item in listed["notifications"] if not item.get("read")]
        event_ids = [self.mark_read(item["id"])["event_id"] for item in unread]
        return {"status": "read_all", "read_count": len(event_ids), "event_ids": event_ids}

    def record_action(self, *, notification_id: str, action: str, result: Dict[str, Any]) -> Dict[str, Any]:
        event = self.memory_service.persist_workflow_event(
            source="user",
            event_type="notification_action_taken",
            payload={
                "schema_version": "1.0",
                "notification_id": notification_id,
                "action": action,
                "result": result,
                "acted_at": time.time(),
            },
            confidence=0.95,
            tags=["notification", "action"],
        )
        return {"status": "recorded", "event_id": event.id}

    def _deliver_windows_toast(self, notification: Dict[str, Any]) -> Dict[str, Any]:
        status = "skipped"
        reason = "Windows toast delivery is disabled."
        if self.windows_toasts_enabled:
            if platform.system().lower() != "windows":
                status = "unavailable"
                reason = "Windows toast delivery only runs on Windows."
            else:
                try:
                    from winotify import Notification

                    toast = Notification(
                        app_id="Blackfong O.B.E.O.S",
                        title=str(notification.get("title") or "Blackfong"),
                        msg=str(notification.get("message") or ""),
                    )
                    toast.show()
                    status = "delivered"
                    reason = "Windows toast delivered."
                except Exception as exc:  # pragma: no cover - depends on OS shell integration
                    status = "unavailable"
                    reason = str(exc)
        payload = {
            "schema_version": "1.0",
            "notification_id": notification.get("id"),
            "channel": "windows_toast",
            "status": status,
            "reason": reason,
            "delivered_at": time.time(),
        }
        event = self.memory_service.persist_workflow_event(
            source="system",
            event_type="notification_delivery_result",
            payload=payload,
            confidence=0.9 if status == "delivered" else 0.7,
            tags=["notification", "delivery", "windows_toast"],
        )
        return {"event_id": event.id, **payload}


notification_service = NotificationService()
