"""Shared UI event primitives for Desktop, TUI, and HUD surfaces."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class UIEvent:
    """A small, transport-neutral UI state event."""

    event_type: str
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


@dataclass(eq=False)
class UIEventSubscription:
    """Bounded subscription queue with explicit lifecycle cleanup."""

    queue: "queue.Queue[UIEvent]"
    created_at: float = field(default_factory=time.time)
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class UIEventBus:
    """In-process pub/sub for fast UI refresh without an external broker."""

    def __init__(self, *, event_store: Any = None, max_queue_size: int = 64) -> None:
        self.event_store = event_store
        self.max_queue_size = max(1, int(max_queue_size))
        self._lock = threading.Lock()
        self._subscribers: List[UIEventSubscription] = []
        self._last_event: Optional[UIEvent] = None

    def subscribe(self, *, max_queue_size: Optional[int] = None) -> UIEventSubscription:
        subscription = UIEventSubscription(queue.Queue(maxsize=max_queue_size or self.max_queue_size))
        with self._lock:
            self._subscribers.append(subscription)
        return subscription

    def unsubscribe(self, subscription: UIEventSubscription) -> None:
        subscription.close()
        with self._lock:
            self._subscribers = [item for item in self._subscribers if item is not subscription]

    def subscriber_count(self) -> int:
        with self._lock:
            return len([item for item in self._subscribers if not item.closed])

    def has_subscribers(self) -> bool:
        return self.subscriber_count() > 0

    def publish(self, event_type: str, payload: Dict[str, Any]) -> UIEvent:
        event = UIEvent(event_type=event_type, payload=dict(payload or {}))
        self._last_event = event
        stale: List[UIEventSubscription] = []
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            if subscriber.closed:
                stale.append(subscriber)
                continue
            try:
                subscriber.queue.put_nowait(event)
            except queue.Full:
                try:
                    subscriber.queue.get_nowait()
                    subscriber.queue.put_nowait(event)
                except Exception:
                    subscriber.close()
                    stale.append(subscriber)
        if stale:
            with self._lock:
                self._subscribers = [item for item in self._subscribers if item not in stale and not item.closed]
        return event

    def last_event(self) -> Optional[UIEvent]:
        return self._last_event


class DebouncedRefresh:
    """Thread-safe debouncer for refresh work that may be requested in bursts."""

    def __init__(self, delay_seconds: float = 0.2) -> None:
        self.delay_seconds = max(0.0, float(delay_seconds))
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def trigger(self, callback) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay_seconds, callback)
            self._timer.daemon = True
            self._timer.start()

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
