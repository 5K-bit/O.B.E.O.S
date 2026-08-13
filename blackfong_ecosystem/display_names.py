"""Shared display helpers for operator surfaces."""

from __future__ import annotations

from typing import Any, Dict, Optional


def display_agent_name(agent: Optional[str]) -> str:
    normalized = str(agent or "").strip().lower()
    if normalized in {"jarvis", "daise", "d.a.i.s.e", ""}:
        return "D.A.I.S.E"
    if normalized == "coding":
        return "Coding"
    if normalized == "lilo":
        return "Lilo"
    if normalized == "stitch":
        return "Stitch"
    if normalized == "voice":
        return "Voice"
    return str(agent or "D.A.I.S.E").title()


def extract_confidence(
    *,
    result: Optional[Dict[str, Any]] = None,
    selection: Optional[Dict[str, Any]] = None,
    trace: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    if result is not None:
        value = result.get("confidence")
        if isinstance(value, (int, float)):
            return float(value)
    if selection:
        reports = selection.get("reports") or []
        if reports and isinstance(reports[0], dict):
            value = reports[0].get("confidence")
            if isinstance(value, (int, float)):
                return float(value)
    if trace:
        latency = trace.get("latency_observation") or {}
        if isinstance(latency, dict):
            value = latency.get("confidence")
            if isinstance(value, (int, float)):
                return float(value)
    return None


def format_chat_status_line(
    *,
    result: Optional[Dict[str, Any]] = None,
    selection: Optional[Dict[str, Any]] = None,
    analysis_mode: str = "standard",
    request_id: Optional[str] = None,
) -> str:
    agent = display_agent_name(
        (result or {}).get("selected_agent")
        or (result or {}).get("agent")
        or (selection or {}).get("selected_agent")
    )
    confidence = extract_confidence(result=result, selection=selection)
    confidence_text = f"{confidence:.2f}" if confidence is not None else "n/a"
    elapsed = (result or {}).get("execution_time_ms")
    elapsed_text = f"{float(elapsed) / 1000:.1f}s" if isinstance(elapsed, (int, float)) else ""
    parts = [agent, str(analysis_mode or "standard"), f"confidence {confidence_text}"]
    if elapsed_text:
        parts.append(elapsed_text)
    if request_id:
        parts.append(str(request_id))
    return " · ".join(parts)
