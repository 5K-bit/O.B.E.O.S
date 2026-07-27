"""Append-only NDJSON debug logging for agent debug sessions."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

_DEBUG_LOG_PATH = Path(__file__).resolve().parents[2] / "debug-9e7edf.log"
_SESSION_ID = "9e7edf"


def debug_session_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
    run_id: str = "ui",
) -> None:
    # #region agent log
    entry = {
        "sessionId": _SESSION_ID,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
        "runId": run_id,
    }
    try:
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass
    # #endregion


def log_ui_action(
    *,
    surface: str,
    action: str,
    status: str = "ok",
    duration_ms: Optional[float] = None,
    error: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {"surface": surface, "action": action, "status": status}
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 2)
    if error:
        payload["error"] = error
    if extra:
        payload.update(extra)
    debug_session_log(
        hypothesis_id="F",
        location=f"{surface}:{action}",
        message=f"UI action {status}",
        data=payload,
    )


@contextmanager
def timed_ui_action(*, surface: str, action: str, extra: Optional[Dict[str, Any]] = None) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    except Exception as exc:
        log_ui_action(
            surface=surface,
            action=action,
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error=str(exc),
            extra=extra,
        )
        raise
    else:
        log_ui_action(
            surface=surface,
            action=action,
            status="ok",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra=extra,
        )
