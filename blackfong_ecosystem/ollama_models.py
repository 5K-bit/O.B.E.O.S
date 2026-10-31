"""Ollama model discovery and chain filtering for chat routing."""

from __future__ import annotations

import logging
import time
from typing import List, Optional, Sequence, Set

import httpx

from .config import chat_settings
from .http_clients import client_for

logger = logging.getLogger(__name__)

_installed_cache: Optional[Set[str]] = None
_installed_cache_at: float = 0.0
_CACHE_TTL_S = 300.0


def _normalize_model_name(name: str) -> str:
    return str(name or "").strip().lower()


def fetch_installed_models(*, force_refresh: bool = False) -> Set[str]:
    """Return normalized model names reported by the local Ollama server."""
    global _installed_cache, _installed_cache_at
    now = time.perf_counter()
    if (
        not force_refresh
        and _installed_cache is not None
        and (now - _installed_cache_at) < _CACHE_TTL_S
    ):
        return set(_installed_cache)

    base_url = chat_settings.ollama_url.rstrip("/")
    installed: Set[str] = set()
    try:
        with client_for(base_url, timeout=httpx.Timeout(connect=3.0, read=8.0, write=5.0, pool=3.0)) as client:
            response = client.get(f"{base_url}/api/tags")
            response.raise_for_status()
            for item in response.json().get("models", []):
                raw = str(item.get("name") or item.get("model") or "").strip()
                if raw:
                    installed.add(_normalize_model_name(raw))
                    if ":" in raw:
                        installed.add(_normalize_model_name(raw.split(":", 1)[0]))
    except Exception as exc:
        logger.debug("Could not list Ollama models: %s", exc)
        if _installed_cache is not None:
            return set(_installed_cache)
        return set()

    _installed_cache = installed
    _installed_cache_at = now
    return set(installed)


def model_is_installed(model_name: str, installed: Optional[Set[str]] = None) -> bool:
    """Check whether a model name matches an installed Ollama tag."""
    normalized = _normalize_model_name(model_name)
    if not normalized:
        return False
    tags = installed if installed is not None else fetch_installed_models()
    if normalized in tags:
        return True
    base = normalized.split(":", 1)[0]
    return any(tag == base or tag.startswith(f"{base}:") for tag in tags)


def filter_model_chain(chain: Sequence[str], *, installed: Optional[Set[str]] = None) -> List[str]:
    """Keep only models that appear to be installed; fall back to primary if filter empties chain."""
    tags = installed if installed is not None else fetch_installed_models()
    if not tags:
        return list(dict.fromkeys(chain))
    filtered = [name for name in chain if model_is_installed(name, tags)]
    if filtered:
        return filtered
    return list(dict.fromkeys(chain))[:1]
