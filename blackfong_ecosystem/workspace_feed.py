"""Read-only notification feed formatting for the Blackfong Workspace.

Pure functions over the payload returned by ``NotificationService.list_notifications``.
Nothing here calls a service, runs a command, or changes state, so the feed can be shown
without widening what the Workspace is allowed to do. The original record is always
available verbatim through ``raw_detail``.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Mapping

SEVERITY_ORDER = ("critical", "error", "warning", "info")
_SEVERITY_ALIASES = {"warn": "warning", "err": "error", "fatal": "critical", "notice": "info", "debug": "info"}
FEED_LIMIT = 50


def normalize_severity(level: Any) -> str:
    value = str(level or "").strip().lower()
    value = _SEVERITY_ALIASES.get(value, value)
    return value if value in SEVERITY_ORDER else "info"


def format_item(item: Mapping[str, Any], *, now: float) -> str:
    severity = normalize_severity(item.get("level"))
    stamp = item.get("timestamp") or item.get("created_at")
    if isinstance(stamp, (int, float)):
        when = time.strftime("%H:%M:%S", time.localtime(stamp))
        age = f"{max(0, int(now - stamp))}s ago"
    else:
        when, age = "--:--:--", "time unknown"
    marker = " " if item.get("read") else "*"
    title = str(item.get("title") or "(untitled)")
    category = str(item.get("category") or "system")
    short_id = str(item.get("id") or "")[:8] or "no-id"
    line = f"{marker} [{severity.upper():<8}] {when} ({age}) {category}: {title}  #{short_id}"
    message = str(item.get("message") or "").strip()
    return f"{line}\n    {message}" if message else line


def format_feed(payload: Any, *, now: float, limit: int = FEED_LIMIT) -> str:
    """Render the feed, most severe first within the delivered order, with an honest truncation notice."""
    if not isinstance(payload, Mapping):
        return "Feed unavailable: unexpected response from the notification service."
    items: List[Mapping[str, Any]] = [i for i in (payload.get("notifications") or []) if isinstance(i, Mapping)]
    if not items:
        return "No notifications."
    ordered = sorted(items, key=lambda i: SEVERITY_ORDER.index(normalize_severity(i.get("level"))))
    header = f"{len(items)} shown, {int(payload.get('unread_count') or 0)} unread  (* = unread)"
    lines = [header, ""]
    lines += [format_item(item, now=now) for item in ordered]
    if len(items) >= limit:
        lines += ["", f"Showing the newest {limit}. Older notifications exist and are not displayed here."]
    return "\n".join(lines)


def raw_detail(payload: Any) -> str:
    """The unmodified record(s) as received, for evidence inspection."""
    try:
        return json.dumps(payload, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(payload)


def summarize_counts(payload: Any) -> Dict[str, int]:
    counts = {severity: 0 for severity in SEVERITY_ORDER}
    if isinstance(payload, Mapping):
        for item in payload.get("notifications") or []:
            if isinstance(item, Mapping):
                counts[normalize_severity(item.get("level"))] += 1
    return counts
