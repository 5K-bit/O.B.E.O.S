from __future__ import annotations

import time
import uuid
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from .conversation_memory import ConversationMemoryService, conversation_memory_service
from .storage import EventStore


SCHEMA_VERSION = "1.0"
ACTIVE_INTENT_STATUSES = {"pending", "executing", "blocked", "waiting_for_consent"}
TERMINAL_INTENT_STATUSES = {"completed", "expired", "failed", "cancelled"}
OPERATOR_INTENT_EVENT_TYPES = {
    "operator_intent_created",
    "operator_intent_state",
    "operator_intent_expired",
    "operator_action_result",
    "state_change_summary",
}


class OperatorActionService:
    """Event-backed operator intent queue, action history, and change summaries."""

    def __init__(self, *, memory_service: Optional[ConversationMemoryService] = None) -> None:
        self.memory_service = memory_service or conversation_memory_service
        self.event_store: EventStore = self.memory_service.event_store

    def create_intent(
        self,
        *,
        action: str,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        target: Optional[str] = None,
        priority: str = "normal",
        owned_by: str = "daise",
        governed_by: str = "blackfong",
        proposed_by: str = "operator",
        reason_code: str = "accepted",
        human_reason: str = "",
        expires_at: Optional[float] = None,
        ttl_seconds: float = 300.0,
        metadata: Optional[Dict[str, Any]] = None,
        initial_status: str = "pending",
    ) -> Dict[str, Any]:
        created_at = time.time()
        intent_id = str(uuid.uuid4())[:12]
        payload = {
            "schema_version": SCHEMA_VERSION,
            "intent_id": intent_id,
            "action": action,
            "status": initial_status,
            "priority": priority,
            "owned_by": owned_by,
            "governed_by": governed_by,
            "proposed_by": proposed_by,
            "created_at": created_at,
            "updated_at": created_at,
            "expires_at": expires_at or (created_at + ttl_seconds),
            "target": target,
            "reason_code": reason_code,
            "human_reason": human_reason or self._default_reason(action, initial_status),
            "metadata": metadata or {},
        }
        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="operator_intent_created",
            payload=payload,
            confidence=0.96,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["operator_intent", action, initial_status],
        )
        return self.get_action(intent_id, expire_stale=False) or payload

    def transition(
        self,
        intent_id: str,
        *,
        status: str,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        reason_code: str = "",
        human_reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        owned_by: Optional[str] = None,
        governed_by: Optional[str] = None,
        proposed_by: Optional[str] = None,
        expires_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        current = self.get_action(intent_id, expire_stale=False)
        if current is None:
            return {"status": "failed", "error": "Unknown operator intent", "intent_id": intent_id}
        now = time.time()
        event_type = "operator_intent_expired" if status == "expired" else "operator_intent_state"
        payload = {
            "schema_version": SCHEMA_VERSION,
            "intent_id": intent_id,
            "action": current.get("action"),
            "status": status,
            "updated_at": now,
            "reason_code": reason_code or self._reason_for_status(status),
            "human_reason": human_reason or self._default_reason(str(current.get("action") or "action"), status),
            "owned_by": owned_by or current.get("owned_by") or "daise",
            "governed_by": governed_by or current.get("governed_by") or "blackfong",
            "proposed_by": proposed_by or current.get("proposed_by") or "operator",
            "expires_at": expires_at if expires_at is not None else current.get("expires_at"),
            "metadata": metadata or {},
        }
        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type=event_type,
            payload=payload,
            confidence=0.92 if status in TERMINAL_INTENT_STATUSES else 0.88,
            session_id=session_id or current.get("session_id"),
            correlation_id=correlation_id or current.get("correlation_id"),
            request_id=request_id or current.get("request_id"),
            tags=["operator_intent", str(current.get("action") or "action"), status],
        )
        return self.get_action(intent_id, expire_stale=False) or payload

    def record_result(
        self,
        intent_id: str,
        *,
        result: Dict[str, Any],
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        text: Optional[str] = None,
        status: Optional[str] = None,
        reason_code: str = "",
        human_reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        owned_by: Optional[str] = None,
        governed_by: Optional[str] = None,
        proposed_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        current = self.get_action(intent_id, expire_stale=False)
        if current is None:
            return {"status": "failed", "error": "Unknown operator intent", "intent_id": intent_id}
        normalized_status = status or self._result_status(result)
        summary_text = text or self._result_text(str(current.get("action") or "action"), result, normalized_status)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "intent_id": intent_id,
            "action": current.get("action"),
            "status": normalized_status,
            "result": result,
            "text": summary_text,
            "reason_code": reason_code or self._reason_for_status(normalized_status),
            "human_reason": human_reason or self._default_reason(str(current.get("action") or "action"), normalized_status, result=result),
            "owned_by": owned_by or current.get("owned_by") or "daise",
            "governed_by": governed_by or current.get("governed_by") or "blackfong",
            "proposed_by": proposed_by or current.get("proposed_by") or "operator",
            "created_at": time.time(),
            "expires_at": current.get("expires_at"),
            "metadata": metadata or {},
        }
        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="operator_action_result",
            payload=payload,
            confidence=0.95 if normalized_status == "completed" else 0.78,
            session_id=session_id or current.get("session_id"),
            correlation_id=correlation_id or current.get("correlation_id"),
            request_id=request_id or current.get("request_id"),
            tags=["operator_intent", str(current.get("action") or "action"), normalized_status],
        )
        self.transition(
            intent_id,
            status=normalized_status,
            session_id=session_id or current.get("session_id"),
            correlation_id=correlation_id or current.get("correlation_id"),
            request_id=request_id or current.get("request_id"),
            reason_code=payload["reason_code"],
            human_reason=payload["human_reason"],
            metadata=metadata,
            owned_by=payload["owned_by"],
            governed_by=payload["governed_by"],
            proposed_by=payload["proposed_by"],
        )
        return self.get_action(intent_id, expire_stale=False) or payload

    def cancel_intent(
        self,
        intent_id: str,
        *,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        human_reason: str = "Operator cancelled the action.",
    ) -> Dict[str, Any]:
        return self.transition(
            intent_id,
            status="cancelled",
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code="cancelled_by_operator",
            human_reason=human_reason,
        )

    def list_actions(
        self,
        *,
        limit: int = 50,
        session_id: Optional[str] = None,
        statuses: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        self.expire_stale_intents()
        grouped = self._group_events(limit=max(limit * 6, 250))
        items = [self._compose_action(intent_id, events) for intent_id, events in grouped.items()]
        if session_id is not None:
            items = [item for item in items if item.get("session_id") == session_id]
        if statuses is not None:
            allowed = set(statuses)
            items = [item for item in items if item.get("status") in allowed]
        items.sort(key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0.0), reverse=True)
        items = items[:limit]
        pending_count = sum(1 for item in items if item.get("status") in ACTIVE_INTENT_STATUSES)
        return {
            "schema_version": SCHEMA_VERSION,
            "actions": items,
            "pending_actions": pending_count,
            "action_priority": Counter(str(item.get("priority") or "normal") for item in items),
        }

    def get_action(self, intent_id: str, *, expire_stale: bool = True) -> Optional[Dict[str, Any]]:
        if expire_stale:
            self.expire_stale_intents()
        grouped = self._group_events(limit=1500)
        events = grouped.get(str(intent_id))
        if not events:
            return None
        return self._compose_action(str(intent_id), events)

    def expire_stale_intents(self) -> int:
        grouped = self._group_events(limit=1500)
        expired = 0
        now = time.time()
        for intent_id, events in grouped.items():
            action = self._compose_action(intent_id, events)
            expires_at = float(action.get("expires_at") or 0.0)
            if expires_at and expires_at < now and action.get("status") in ACTIVE_INTENT_STATUSES:
                self.transition(
                    intent_id,
                    status="expired",
                    session_id=action.get("session_id"),
                    correlation_id=action.get("correlation_id"),
                    request_id=action.get("request_id"),
                    reason_code="stale_action",
                    human_reason=f"{action.get('action', 'Action')} expired before it finished.",
                )
                expired += 1
        return expired

    def summarize_changes(
        self,
        *,
        session_id: Optional[str] = None,
        since_timestamp: Optional[float] = None,
        limit: int = 40,
    ) -> Dict[str, Any]:
        now = time.time()
        baseline = since_timestamp or self._last_summary_timestamp(session_id=session_id) or (now - 300)
        events = [
            event
            for event in self.event_store.recent_events(limit=500)
            if (event.timestamp / 1000.0) >= baseline
        ]
        if session_id is not None:
            events = [event for event in events if event.metadata.get("session_id") == session_id]
        events.sort(key=lambda event: event.timestamp)
        changes: List[Dict[str, Any]] = []
        counts = Counter()
        for event in events:
            category = self._event_category(event.type)
            if not category:
                continue
            counts[category] += 1
            changes.append(
                {
                    "event_id": event.id,
                    "timestamp": event.timestamp / 1000.0,
                    "category": category,
                    "event_type": event.type,
                    "message": self.memory_service._summarize_recent_event(event),  # type: ignore[attr-defined]
                }
            )
        changes = changes[-limit:]
        summary = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "since_timestamp": baseline,
            "until_timestamp": now,
            "changes": changes,
            "counts": dict(counts),
            "summary_text": self._summary_text(counts, changes),
        }
        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="state_change_summary",
            payload=summary,
            confidence=0.86,
            session_id=session_id,
            tags=["operator_intent", "summary"],
        )
        return summary

    def _group_events(self, *, limit: int) -> Dict[str, List[Any]]:
        events = [
            event
            for event in self.event_store.recent_events(limit=limit)
            if event.type in OPERATOR_INTENT_EVENT_TYPES
        ]
        events.sort(key=lambda event: event.timestamp)
        grouped: Dict[str, List[Any]] = {}
        for event in events:
            intent_id = str((event.payload or {}).get("intent_id") or "")
            if not intent_id:
                continue
            grouped.setdefault(intent_id, []).append(event)
        return grouped

    def _compose_action(self, intent_id: str, events: List[Any]) -> Dict[str, Any]:
        created = events[0]
        payload = dict(created.payload or {})
        action = {
            "intent_id": intent_id,
            "action": payload.get("action"),
            "status": payload.get("status", "pending"),
            "priority": payload.get("priority", "normal"),
            "owned_by": payload.get("owned_by", "daise"),
            "governed_by": payload.get("governed_by", "blackfong"),
            "proposed_by": payload.get("proposed_by", "operator"),
            "created_at": payload.get("created_at", created.timestamp / 1000.0),
            "updated_at": payload.get("updated_at", created.timestamp / 1000.0),
            "expires_at": payload.get("expires_at"),
            "target": payload.get("target"),
            "reason_code": payload.get("reason_code", "accepted"),
            "human_reason": payload.get("human_reason", ""),
            "metadata": dict(payload.get("metadata") or {}),
            "session_id": created.metadata.get("session_id"),
            "correlation_id": created.metadata.get("correlation_id"),
            "request_id": created.metadata.get("request_id"),
            "history": [],
            "result": None,
            "text": "",
        }
        for event in events:
            event_payload = dict(event.payload or {})
            action["history"].append(
                {
                    "event_id": event.id,
                    "event_type": event.type,
                    "status": event_payload.get("status"),
                    "reason_code": event_payload.get("reason_code"),
                    "human_reason": event_payload.get("human_reason"),
                    "timestamp": event.timestamp / 1000.0,
                }
            )
            if event.type in {"operator_intent_state", "operator_intent_expired"}:
                action["status"] = event_payload.get("status", action["status"])
                action["updated_at"] = event_payload.get("updated_at", event.timestamp / 1000.0)
                action["reason_code"] = event_payload.get("reason_code", action["reason_code"])
                action["human_reason"] = event_payload.get("human_reason", action["human_reason"])
                action["expires_at"] = event_payload.get("expires_at", action["expires_at"])
                action["owned_by"] = event_payload.get("owned_by", action["owned_by"])
                action["governed_by"] = event_payload.get("governed_by", action["governed_by"])
                action["proposed_by"] = event_payload.get("proposed_by", action["proposed_by"])
            if event.type == "operator_action_result":
                action["status"] = event_payload.get("status", action["status"])
                action["updated_at"] = event.timestamp / 1000.0
                action["result"] = event_payload.get("result")
                action["text"] = event_payload.get("text", "")
                action["reason_code"] = event_payload.get("reason_code", action["reason_code"])
                action["human_reason"] = event_payload.get("human_reason", action["human_reason"])
                action["owned_by"] = event_payload.get("owned_by", action["owned_by"])
                action["governed_by"] = event_payload.get("governed_by", action["governed_by"])
                action["proposed_by"] = event_payload.get("proposed_by", action["proposed_by"])
        return action

    def _last_summary_timestamp(self, *, session_id: Optional[str]) -> Optional[float]:
        summaries = self.event_store.list_events_by_type("state_change_summary", limit=50)
        for event in summaries:
            if session_id is not None and event.metadata.get("session_id") != session_id:
                continue
            payload = event.payload or {}
            until_timestamp = payload.get("until_timestamp")
            if until_timestamp:
                return float(until_timestamp)
        return None

    @staticmethod
    def _event_category(event_type: str) -> Optional[str]:
        if event_type in {"request_outcome", "agent_selection", "agent_response"}:
            return "request"
        if event_type in {"awareness_snapshot", "performance_snapshot", "backend_health_observation", "consistency_conflict"}:
            return "health"
        if event_type.startswith("tinyprogrammer_"):
            return "tinyprogrammer"
        if event_type.startswith("notification_"):
            return "notification"
        if event_type in {"knowledge_ingested", "session_summary"}:
            return "memory"
        if event_type.startswith("file_") or event_type == "file_workflow_state":
            return "file_workflow"
        if event_type.startswith("operator_intent") or event_type == "operator_action_result":
            return "operator_action"
        return None

    @staticmethod
    def _reason_for_status(status: str) -> str:
        mapping = {
            "pending": "queued",
            "executing": "in_progress",
            "blocked": "blocked",
            "waiting_for_consent": "needs_consent",
            "completed": "completed",
            "expired": "stale_action",
            "failed": "action_failed",
            "cancelled": "cancelled_by_operator",
        }
        return mapping.get(status, status or "unknown")

    def _default_reason(self, action: str, status: str, *, result: Optional[Dict[str, Any]] = None) -> str:
        if status == "pending":
            return f"{action} is queued for D.A.I.S.E and Blackfong."
        if status == "executing":
            return f"{action} is currently executing."
        if status == "waiting_for_consent":
            return f"{action} is waiting for operator consent."
        if status == "blocked":
            return f"{action} is blocked by policy or a missing dependency."
        if status == "completed":
            return f"{action} completed successfully."
        if status == "expired":
            return f"{action} expired before completion."
        if status == "cancelled":
            return f"{action} was cancelled."
        if result and result.get("error"):
            return str(result["error"])
        return f"{action} failed."

    @staticmethod
    def _result_status(result: Dict[str, Any]) -> str:
        status = str(result.get("status") or "")
        if status == "requires_consent":
            return "waiting_for_consent"
        if result.get("success") is False or result.get("error"):
            if status == "blocked":
                return "blocked"
            return "failed"
        return "completed"

    @staticmethod
    def _result_text(action: str, result: Dict[str, Any], status: str) -> str:
        if result.get("text"):
            return str(result["text"])
        if result.get("message"):
            return str(result["message"])
        if result.get("error"):
            return f"{action} failed: {result['error']}"
        if status == "waiting_for_consent":
            return f"{action} is waiting for consent."
        if status == "completed":
            return f"{action} completed."
        return f"{action} finished with status {status}."

    @staticmethod
    def _summary_text(counts: Counter, changes: List[Dict[str, Any]]) -> str:
        if not changes:
            return "No state changes were recorded since the last checkpoint."
        fragments = []
        for category in ("request", "health", "tinyprogrammer", "notification", "memory", "file_workflow", "operator_action"):
            count = counts.get(category, 0)
            if count:
                fragments.append(f"{count} {category.replace('_', ' ')}")
        latest = changes[-1]["message"] if changes else ""
        return f"Recent changes: {', '.join(fragments)}. Latest: {latest}"


operator_action_service = OperatorActionService()
