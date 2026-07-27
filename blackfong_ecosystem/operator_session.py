"""Shared operator session id across HUD, desktop, and CLI surfaces."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from .config import paths

SESSION_FILE = paths.data_root / "operator_session.json"
DEFAULT_BOOT_MODE = "resume_shared"


def _default_payload() -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "session_id": str(uuid4()),
        "updated_at": time.time(),
        "linked_surfaces": [],
    }


def load_operator_session() -> Dict[str, Any]:
    if not SESSION_FILE.exists():
        payload = _default_payload()
        save_operator_session(payload)
        return payload
    try:
        payload = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        payload = _default_payload()
        save_operator_session(payload)
        return payload
    if not str(payload.get("session_id") or "").strip():
        payload["session_id"] = str(uuid4())
    payload.setdefault("schema_version", "1.0")
    payload.setdefault("linked_surfaces", [])
    return payload


def save_operator_session(payload: Dict[str, Any]) -> Dict[str, Any]:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["updated_at"] = time.time()
    SESSION_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def normalize_session_boot_mode(mode: Optional[str]) -> str:
    normalized = str(mode or "").strip().lower().replace("-", "_")
    if normalized in {"resume", "resume_shared", "shared"}:
        return "resume_shared"
    if normalized in {"new_shared", "shared_new"}:
        return "new_shared"
    if normalized in {"fresh", "fresh_local", "local"}:
        return "fresh_local"
    return DEFAULT_BOOT_MODE


def session_boot_mode_from_env() -> str:
    return normalize_session_boot_mode(os.getenv("BLACKFONG_OPERATOR_SESSION_MODE", DEFAULT_BOOT_MODE))


def get_shared_session_id(*, surface: Optional[str] = None) -> str:
    payload = load_operator_session()
    session_id = str(payload.get("session_id") or uuid4())
    if surface:
        linked = list(payload.get("linked_surfaces") or [])
        if surface not in linked:
            linked.append(surface)
            payload["linked_surfaces"] = linked[-8:]
            payload["session_id"] = session_id
            save_operator_session(payload)
    return session_id


def link_surface(surface: str) -> Dict[str, Any]:
    payload = load_operator_session()
    linked = list(payload.get("linked_surfaces") or [])
    if surface not in linked:
        linked.append(surface)
    payload["linked_surfaces"] = linked[-8:]
    return save_operator_session(payload)


def reset_operator_session() -> Dict[str, Any]:
    payload = _default_payload()
    return save_operator_session(payload)


def create_detached_session_id() -> str:
    return str(uuid4())


def begin_operator_session(*, surface: Optional[str] = None, mode: Optional[str] = None) -> Dict[str, Any]:
    resolved_mode = normalize_session_boot_mode(mode or session_boot_mode_from_env())
    if resolved_mode == "fresh_local":
        if surface:
            link_surface(surface)
        return {
            "session_id": create_detached_session_id(),
            "mode": resolved_mode,
            "scope": "local",
            "linked": False,
            "shared_session_id": get_shared_session_id(),
        }
    if resolved_mode == "new_shared":
        payload = reset_operator_session()
        if surface:
            payload = link_surface(surface)
        return {
            "session_id": str(payload.get("session_id") or uuid4()),
            "mode": resolved_mode,
            "scope": "shared",
            "linked": True,
            "shared_session_id": str(payload.get("session_id") or uuid4()),
        }
    session_id = get_shared_session_id(surface=surface)
    if surface:
        link_surface(surface)
    return {
        "session_id": session_id,
        "mode": "resume_shared",
        "scope": "shared",
        "linked": True,
        "shared_session_id": session_id,
    }


def is_multi_surface_linked(*, required: Optional[tuple[str, ...]] = None) -> bool:
    linked = list(load_operator_session().get("linked_surfaces") or [])
    targets = required or ("desktop", "hud")
    return all(surface in linked for surface in targets)


def session_link_label() -> str:
    linked = list(load_operator_session().get("linked_surfaces") or [])
    if "desktop" in linked and "hud" in linked:
        return "Desktop + HUD linked"
    if len(linked) >= 2:
        return " + ".join(linked) + " linked"
    if linked:
        return f"{linked[-1]} session"
    return ""
