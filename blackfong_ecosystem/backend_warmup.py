"""Warm local Ollama models during workspace/HUD boot."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Sequence

import httpx

from .config import chat_settings
from .cancellation import CancellationToken, cancellation_scope, cancellation_resource, raise_if_cancelled, RequestCancelled

logger = logging.getLogger(__name__)


def warm_ollama_model(
    *,
    model: Optional[str] = None,
    keep_alive: Optional[str] = None,
    timeout: float = 45.0,
) -> Dict[str, Any]:
    """Ping Ollama to load a model into memory."""
    fast_chain = chat_settings.fast_model_chain()
    target_model = model or (fast_chain[0] if fast_chain else chat_settings.model_for_agent("daise"))
    target_keep_alive = keep_alive or chat_settings.fast_keep_alive
    base_url = chat_settings.ollama_url.rstrip("/")
    payload = {
        "model": target_model,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "keep_alive": target_keep_alive,
        "options": {"num_predict": 8, "num_ctx": 2048},
    }
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=5.0)) as client, cancellation_resource(client.close):
            raise_if_cancelled()
            response = client.post(f"{base_url}/api/chat", json=payload)
            response.raise_for_status()
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "status": "ready",
            "model": target_model,
            "elapsed_ms": elapsed_ms,
            "keep_alive": target_keep_alive,
        }
    except Exception as exc:
        logger.debug("Ollama warmup failed for %s: %s", target_model, exc)
        return {
            "status": "error",
            "model": target_model,
            "error": str(exc),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }


def warm_model_chain(
    models: Optional[Sequence[str]] = None,
    *,
    keep_alive: Optional[str] = None,
    timeout: float = 45.0,
) -> Dict[str, Any]:
    """Warm the fast chat model chain (primary + fallbacks)."""
    chain = list(models or chat_settings.fast_model_chain())
    results: list[Dict[str, Any]] = []
    for model_name in chain[:2]:
        raise_if_cancelled()
        results.append(
            warm_ollama_model(model=model_name, keep_alive=keep_alive, timeout=timeout)
        )
        if results[-1].get("status") == "ready":
            break
    ready = [item for item in results if item.get("status") == "ready"]
    if ready:
        primary = ready[0]
        return {
            "status": "ready",
            "model": primary.get("model"),
            "elapsed_ms": primary.get("elapsed_ms"),
            "keep_alive": primary.get("keep_alive"),
            "warmed_models": [item.get("model") for item in ready],
        }
    return results[-1] if results else {"status": "error", "error": "no models configured"}


class BackendWarmupWorker:
    """Background Ollama warmup with status callback."""

    def __init__(self, *, on_status: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self._on_status = on_status
        self._cancel = CancellationToken()
        self._thread: Optional[threading.Thread] = None
        self._result: Dict[str, Any] = {"status": "idle"}

    @property
    def result(self) -> Dict[str, Any]:
        return dict(self._result)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._result = {"status": "warming", "message": "Warming fast chat model…"}
        if self._on_status is not None:
            self._on_status(self._result)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            with cancellation_scope(self._cancel):
                outcome = warm_model_chain()
        except RequestCancelled:
            return
        if self._cancel.cancelled:
            return
        self._result = outcome
        if self._on_status is not None:
            self._on_status(outcome)

    def stop(self, timeout=0):
        self._cancel.cancel()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)
