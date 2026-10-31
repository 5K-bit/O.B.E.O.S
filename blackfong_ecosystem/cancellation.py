"""Cooperative request cancellation shared by UI workers and transports."""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event, Lock


class RequestCancelled(RuntimeError):
    pass


class CancellationToken:
    def __init__(self):
        self._event = Event()
        self._lock = Lock()
        self._callbacks = []

    @property
    def cancelled(self):
        return self._event.is_set()

    def raise_if_cancelled(self):
        if self._event.is_set():
            raise RequestCancelled("Request cancelled")

    def cancel(self):
        self._event.set()
        with self._lock:
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass  # Transport may already have closed.

    @contextmanager
    def resource(self, close):
        self.raise_if_cancelled()
        with self._lock:
            self._callbacks.append(close)
        try:
            self.raise_if_cancelled()
            yield
        finally:
            with self._lock:
                self._callbacks.remove(close)


_current = ContextVar("obeos_cancellation", default=None)


@contextmanager
def cancellation_scope(token):
    state = _current.set(token)
    try:
        token.raise_if_cancelled()
        yield
    finally:
        _current.reset(state)


def raise_if_cancelled():
    token = _current.get()
    if token:
        token.raise_if_cancelled()


@contextmanager
def cancellation_resource(close):
    token = _current.get()
    if token:
        with token.resource(close):
            yield
    else:
        yield
