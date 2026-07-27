"""
Hybrid offline-first compute policy for the knowledge intelligence layer.

Deterministic services are the source of truth and never call a model. The local
Ollama LLM is optional, used only for natural-language synthesis, always grounded
in caller-supplied context, and degrades safely when no model is available.

This module centralizes three things so every Phase 2+ feature behaves identically:

- ``ComputeMode``: ``deterministic`` | ``local_llm`` | ``hybrid`` (default ``hybrid``).
- ``local_llm_available()``: whether a usable local model is actually installed.
- ``synthesize(...)``: grounded synthesis with a guaranteed deterministic fallback.

The golden rule enforced here: the model may only rephrase/organize the supplied
grounding. It must never invent facts. When the model is unavailable, disabled by
mode, or fails for any reason, the caller-supplied deterministic ``fallback`` is
returned verbatim and ``used_llm`` is ``False`` with a ``fallback_reason``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_COMPUTE_MODE_ENV = "BLACKFONG_KNOWLEDGE_COMPUTE_MODE"


class ComputeMode(str, Enum):
    """How a knowledge feature is allowed to compute its natural-language output."""

    DETERMINISTIC = "deterministic"
    LOCAL_LLM = "local_llm"
    HYBRID = "hybrid"

    @classmethod
    def from_value(cls, value: Any) -> "ComputeMode":
        """Coerce a string/enum to a ComputeMode, defaulting to HYBRID."""
        if isinstance(value, ComputeMode):
            return value
        normalized = str(value or "").strip().lower().replace("-", "_")
        for member in cls:
            if member.value == normalized:
                return member
        if normalized in {"llm", "local", "ollama"}:
            return cls.LOCAL_LLM
        if normalized in {"deterministic", "offline", "rules", "rule"}:
            return cls.DETERMINISTIC
        return cls.HYBRID


DEFAULT_COMPUTE_MODE = ComputeMode.HYBRID


def resolve_compute_mode(mode: Any = None) -> ComputeMode:
    """Resolve the effective compute mode.

    Priority: an explicit ``mode`` argument, then ``BLACKFONG_KNOWLEDGE_COMPUTE_MODE``,
    then the default (``hybrid``).
    """
    if mode is not None and str(mode).strip():
        return ComputeMode.from_value(mode)
    env_value = os.getenv(_COMPUTE_MODE_ENV)
    if env_value and env_value.strip():
        return ComputeMode.from_value(env_value)
    return DEFAULT_COMPUTE_MODE


# --- local LLM availability ------------------------------------------------

_GROUNDING_SYSTEM_RULE = (
    "You are the OBEOS knowledge librarian's synthesis layer. You will be given a "
    "GROUNDING block containing facts already extracted deterministically from a "
    "local knowledge base, and a QUESTION or task. Write a concise, well-organized "
    "answer using ONLY information present in the GROUNDING block. Do not invent, "
    "assume, or add outside knowledge. If the grounding does not contain enough "
    "information to answer, say so plainly and state what is missing. Never fabricate "
    "source names, numbers, dates, or citations."
)


def local_llm_available(*, force_refresh: bool = False) -> bool:
    """Return True only if a usable local model is actually installed.

    Wraps the Ollama model discovery used by the chat router. Returns False when the
    configured backend is not Ollama, when the server is unreachable, or when none of
    the configured fast/standard models are installed. Never raises.
    """
    try:
        from ..config import chat_settings

        if chat_settings.backend.lower() != "ollama":
            return False

        from ..ollama_models import fetch_installed_models, model_is_installed

        installed = fetch_installed_models(force_refresh=force_refresh)
        if not installed:
            return False

        candidates: List[str] = []
        for chain in (chat_settings.standard_model_chain(), chat_settings.fast_model_chain()):
            candidates.extend(chain)
        return any(model_is_installed(name, installed) for name in candidates if name)
    except Exception as exc:  # noqa: BLE001 - availability probe must never raise
        logger.debug("local_llm_available probe failed: %s", exc)
        return False


# --- synthesis -------------------------------------------------------------


@dataclass
class SynthesisResult:
    """Result of a grounded synthesis attempt.

    ``used_llm`` is True only when the local model produced the returned text. When
    False, ``text`` is the caller-supplied deterministic fallback and
    ``fallback_reason`` explains why (``mode_deterministic``, ``llm_unavailable``,
    ``llm_error:<detail>``, or ``empty_grounding``).
    """

    text: str
    used_llm: bool
    fallback_reason: str = ""
    model: str = ""
    mode: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "used_llm": self.used_llm,
            "fallback_reason": self.fallback_reason,
            "model": self.model,
            "mode": self.mode,
            "metadata": dict(self.metadata),
        }


def _grounding_to_text(grounding: Any) -> str:
    """Render arbitrary grounding (str/list/dict) into a stable text block."""
    if grounding is None:
        return ""
    if isinstance(grounding, str):
        return grounding.strip()
    if isinstance(grounding, (list, tuple)):
        parts: List[str] = []
        for item in grounding:
            rendered = _grounding_to_text(item)
            if rendered:
                parts.append(f"- {rendered}" if not rendered.startswith("- ") else rendered)
        return "\n".join(parts)
    if isinstance(grounding, dict):
        parts = []
        for key, value in grounding.items():
            rendered = _grounding_to_text(value)
            if rendered:
                parts.append(f"{key}: {rendered}")
        return "\n".join(parts)
    return str(grounding).strip()


def synthesize(
    *,
    system: str,
    grounding: Any,
    question: str,
    mode: Any = None,
    fallback: Callable[[], str],
    agent: str = "daise",
    response_mode: str = "standard",
    max_grounding_chars: int = 8000,
) -> SynthesisResult:
    """Produce grounded natural-language text, with a guaranteed deterministic fallback.

    Args:
        system: Feature-specific system guidance (prepended to the global grounding rule).
        grounding: Deterministic facts/context the model may use (str, list, or dict).
        question: The user question or synthesis task.
        mode: ComputeMode or string; defaults to the env/global setting (hybrid).
        fallback: Zero-arg callable returning the deterministic/extractive text to use
            when the LLM is not used. Always invoked when ``used_llm`` is False.
        agent / response_mode: routing hints for ``build_routed_chat_backend``.
        max_grounding_chars: hard cap on grounding sent to the model.

    The ``fallback`` is never allowed to be skipped: any LLM problem returns it.
    """
    effective_mode = resolve_compute_mode(mode)
    grounding_text = _grounding_to_text(grounding)

    def _fallback(reason: str) -> SynthesisResult:
        try:
            text = fallback() or ""
        except Exception as exc:  # noqa: BLE001 - fallback must not crash callers
            logger.error("knowledge synthesize fallback raised: %s", exc)
            text = ""
        return SynthesisResult(
            text=text,
            used_llm=False,
            fallback_reason=reason,
            mode=effective_mode.value,
        )

    if effective_mode is ComputeMode.DETERMINISTIC:
        return _fallback("mode_deterministic")

    if not grounding_text:
        # Nothing for the model to ground in -> never let it free-associate.
        return _fallback("empty_grounding")

    if not local_llm_available():
        return _fallback("llm_unavailable")

    if len(grounding_text) > max_grounding_chars:
        grounding_text = grounding_text[:max_grounding_chars] + "\n[grounding truncated]"

    system_prompt = f"{system.strip()}\n\n{_GROUNDING_SYSTEM_RULE}" if system and system.strip() else _GROUNDING_SYSTEM_RULE
    user_prompt = f"GROUNDING:\n{grounding_text}\n\nQUESTION:\n{question.strip()}"

    try:
        from ..model_routing import (
            backend_options_for_context,
            build_routed_chat_backend,
            normalize_response_mode,
        )

        normalized_mode = normalize_response_mode(response_mode)
        backend = build_routed_chat_backend(agent, normalized_mode)
        options = backend_options_for_context({"analysis_mode": normalized_mode}, agent)
        result = backend.generate(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **options,
        )
        text = (getattr(result, "text", "") or "").strip()
        if not text:
            return _fallback("llm_error:empty_response")
        meta = dict(getattr(result, "metadata", {}) or {})
        return SynthesisResult(
            text=text,
            used_llm=True,
            fallback_reason="",
            model=getattr(result, "model", "") or "",
            mode=effective_mode.value,
            metadata={
                "provider": getattr(result, "provider", ""),
                "selected_model": meta.get("selected_model"),
                "wall_time_ms": meta.get("wall_time_ms"),
            },
        )
    except Exception as exc:  # noqa: BLE001 - any LLM failure degrades to fallback
        detail = f"{type(exc).__name__}: {exc}"
        logger.warning("knowledge synthesize LLM path failed, using fallback: %s", detail)
        return _fallback(f"llm_error:{detail[:160]}")


__all__ = [
    "ComputeMode",
    "DEFAULT_COMPUTE_MODE",
    "resolve_compute_mode",
    "local_llm_available",
    "SynthesisResult",
    "synthesize",
]
