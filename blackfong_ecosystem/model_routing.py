"""Response-mode and model routing for O.B.E.O.S chat latency."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .chat_backends import BaseChatBackend, ChatBackendError, HeuristicChatBackend, OllamaChatBackend, is_chain_retry_error
from .config import chat_settings
from .ollama_models import filter_model_chain

MAX_MODEL_CHAIN_WALL_S = 90.0
FAST_CHAIN_TIMEOUT_CAP_S = 25.0
# A single standard-model request may need time to load a cold local model.
# Multi-model chains are still divided below so retries remain bounded.
STANDARD_CHAIN_TIMEOUT_CAP_S = 75.0
DEEP_CHAIN_TIMEOUT_CAP_S = 90.0

ResponseMode = str  # "fast" | "standard" | "deep"

_CODING_KEYWORDS = (
    "code",
    "python",
    "javascript",
    "typescript",
    "bug",
    "debug",
    "refactor",
    "function",
    "class ",
    "compile",
    "syntax",
    "unittest",
    "pytest",
    "pull request",
    "diff",
    "snippet",
)

_DEEP_KEYWORDS = (
    "deep",
    "thorough",
    "comprehensive",
    "analyze deeply",
    "architecture review",
    "tradeoff",
    "multi-step reasoning",
    "prove",
    "formal",
)

_DIAGNOSTIC_KEYWORDS = (
    "/diagnose",
    "/sensors",
    "why was the response slow",
    "system diagnostics",
    "run diagnostics",
)

_FILE_KEYWORDS = (
    "create file",
    "edit file",
    "apply patch",
    "write to ",
    "open file",
    "file workflow",
)


@dataclass(frozen=True)
class DetectedIntent:
    """Lightweight intent flags used to gate expensive subsystems."""

    casual: bool = True
    coding: bool = False
    deep_reasoning: bool = False
    file_ops: bool = False
    diagnostics: bool = False
    tinyprogrammer: bool = False


def normalize_response_mode(mode: Optional[str]) -> str:
    """Map HUD/TUI modes and legacy aliases to fast | standard | deep."""
    normalized = str(mode or "fast").strip().lower()
    if normalized in {"quick", "fast"}:
        return "fast"
    if normalized in {"standard", "normal", "default"}:
        return "standard"
    if normalized in {"deep", "thorough"}:
        return "deep"
    return "fast"


def detect_intent(user_input: str) -> DetectedIntent:
    text = str(user_input or "").strip()
    lower = text.lower()
    if not text or text.startswith("/"):
        return DetectedIntent(
            casual=False,
            diagnostics=any(token in lower for token in _DIAGNOSTIC_KEYWORDS),
            tinyprogrammer=lower.startswith("/tiny"),
        )

    coding = any(token in lower for token in _CODING_KEYWORDS)
    deep_reasoning = any(token in lower for token in _DEEP_KEYWORDS)
    file_ops = any(token in lower for token in _FILE_KEYWORDS)
    diagnostics = any(token in lower for token in _DIAGNOSTIC_KEYWORDS)
    tinyprogrammer = "tinyprogrammer" in lower or "tiny programmer" in lower

    casual = not (coding or deep_reasoning or file_ops or diagnostics or tinyprogrammer)
    return DetectedIntent(
        casual=casual,
        coding=coding,
        deep_reasoning=deep_reasoning,
        file_ops=file_ops,
        diagnostics=diagnostics,
        tinyprogrammer=tinyprogrammer,
    )


def resolve_response_mode(
    user_input: str,
    explicit_mode: Optional[str],
    *,
    agent_override: Optional[str] = None,
) -> str:
    """Choose response mode from explicit HUD selection and message intent."""
    mode = normalize_response_mode(explicit_mode)
    intent = detect_intent(user_input)
    if agent_override and str(agent_override).strip().lower() == "coding":
        return "standard" if mode == "fast" else mode
    if intent.deep_reasoning and mode == "fast":
        return "deep"
    if intent.coding and mode == "fast":
        return "standard"
    return mode


def should_use_fast_path(user_input: str, context: Dict[str, Any]) -> bool:
    """Single-agent, low-latency route for casual chat."""
    if context.get("agent_override"):
        return False
    mode = normalize_response_mode(context.get("analysis_mode") or context.get("response_mode"))
    if mode != "fast":
        return False
    text = str(user_input or "").strip()
    if not text or text.startswith("/"):
        return False
    if len(text) > 240:
        return False
    intent = detect_intent(text)
    if not intent.casual:
        return False
    return True


def requires_memory_chamber(context: Dict[str, Any], intent: DetectedIntent) -> bool:
    """DreamGraph / memory chamber graph retrieval."""
    if context.get("fast_path"):
        return False
    mode = normalize_response_mode(context.get("analysis_mode") or context.get("response_mode"))
    if mode == "fast":
        return False
    if intent.diagnostics or intent.deep_reasoning:
        return True
    return mode == "deep"


def requires_deep_memory_retrieval(context: Dict[str, Any], intent: DetectedIntent) -> bool:
    """Chroma semantic search and large event scans."""
    if context.get("fast_path"):
        return False
    mode = normalize_response_mode(context.get("analysis_mode") or context.get("response_mode"))
    if mode == "fast":
        return False
    if intent.deep_reasoning or intent.diagnostics:
        return True
    return mode == "deep"


def allows_multi_agent(context: Dict[str, Any], intent: DetectedIntent) -> bool:
    if context.get("fast_path"):
        return False
    if context.get("agent_override"):
        return False
    mode = normalize_response_mode(context.get("analysis_mode") or context.get("response_mode"))
    if mode == "fast":
        return False
    if intent.casual and mode == "standard":
        return False
    return mode == "deep" or intent.deep_reasoning


def model_chain_for_agent(agent_name: Optional[str], response_mode: str) -> List[str]:
    """Ordered Ollama models to try (primary first, then fallbacks)."""
    agent = (agent_name or "jarvis").strip().lower()
    mode = normalize_response_mode(response_mode)
    if agent in {"coding", "coding_agent"}:
        raw = list(chat_settings.coding_model_chain())
    elif agent in {"voice", "voice_agent"}:
        raw = list(chat_settings.voice_model_chain())
    elif mode == "deep" or agent in {"daise", "jarvis"} and mode == "deep":
        raw = list(chat_settings.deep_model_chain())
    elif mode == "standard":
        raw = list(chat_settings.standard_model_chain())
    else:
        raw = list(chat_settings.fast_model_chain())
    return filter_model_chain(raw)


def backend_options_for_context(context: Dict[str, Any], agent_name: str = "jarvis") -> Dict[str, Any]:
    """Ollama option bundle for the active response mode."""
    mode = normalize_response_mode(context.get("analysis_mode") or context.get("response_mode"))
    is_deep = mode == "deep" or bool(context.get("long_running_task"))
    if is_deep:
        return {
            "mode": "deep",
            "timeout": chat_settings.deep_timeout,
            "max_attempts": 2,
            "retry_read_timeout": True,
            "num_predict": chat_settings.deep_num_predict,
            "num_ctx": chat_settings.deep_num_ctx,
            "keep_alive": chat_settings.deep_keep_alive,
        }
    if mode == "standard":
        return {
            "mode": "standard",
            "timeout": chat_settings.standard_timeout,
            "max_attempts": 1,
            "retry_read_timeout": False,
            "num_predict": chat_settings.standard_num_predict,
            "num_ctx": chat_settings.standard_num_ctx,
            "keep_alive": chat_settings.standard_keep_alive,
        }
    return {
        "mode": "fast",
        "timeout": chat_settings.fast_timeout,
        "max_attempts": 1,
        "retry_read_timeout": False,
        "num_predict": chat_settings.fast_num_predict,
        "num_ctx": chat_settings.fast_num_ctx,
        "keep_alive": chat_settings.fast_keep_alive,
    }


def build_routed_chat_backend(agent_name: str, response_mode: str) -> BaseChatBackend:
    """Construct an Ollama backend with model fallbacks for the given mode."""
    if chat_settings.backend.lower() != "ollama":
        return HeuristicChatBackend(model=f"{agent_name}-{chat_settings.backend}-fallback")

    chain = model_chain_for_agent(agent_name, response_mode)
    primary = chain[0]
    fallbacks = chain[1:]
    options = backend_options_for_context({"analysis_mode": response_mode}, agent_name)
    return OllamaRoutingChatBackend(
        model=primary,
        fallback_models=fallbacks,
        timeout=options["timeout"],
        keep_alive=options["keep_alive"],
        num_ctx=options["num_ctx"],
        num_predict=options["num_predict"],
    )


class OllamaRoutingChatBackend(OllamaChatBackend):
    """Try a ordered list of local models before failing."""

    def __init__(
        self,
        *,
        model: str,
        fallback_models: Sequence[str] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, **kwargs)
        self.fallback_models = [item for item in fallback_models if item and item != model]

    def _model_chain(self, options: Dict[str, Any]) -> List[str]:
        override = options.get("model")
        if override:
            return [str(override)]
        if options.get("disable_chain_fallbacks"):
            return [self.model]
        return [self.model, *self.fallback_models]

    @staticmethod
    def _capped_timeout(options: Dict[str, Any], *, chain_length: int) -> float | None:
        requested_timeout = options.get("timeout")
        if requested_timeout is None:
            return None
        try:
            timeout_value = float(requested_timeout)
        except (TypeError, ValueError):
            return None
        mode = normalize_response_mode(options.get("mode"))
        normalized_chain_length = max(1, int(chain_length or 1))
        if mode == "fast":
            cap = FAST_CHAIN_TIMEOUT_CAP_S
        elif mode == "standard":
            cap = STANDARD_CHAIN_TIMEOUT_CAP_S
        else:
            cap = DEEP_CHAIN_TIMEOUT_CAP_S
        if normalized_chain_length > 1:
            cap = max(cap / normalized_chain_length, 8.0)
        return min(timeout_value, cap)

    def generate(self, messages: Sequence[Dict[str, str]], **options: Any) -> Any:
        last_error: Exception | None = None
        attempt_models: List[str] = []
        chain_started = time.perf_counter()
        model_chain = self._model_chain(options)
        effective_timeout = self._capped_timeout(options, chain_length=len(model_chain))
        for model_name in model_chain:
            if model_name in attempt_models:
                continue
            if (time.perf_counter() - chain_started) > MAX_MODEL_CHAIN_WALL_S:
                break
            attempt_models.append(model_name)
            try:
                merged = {**options, "model": model_name}
                if effective_timeout is not None:
                    merged["timeout"] = effective_timeout
                result = super().generate(messages, **merged)
                meta = dict(result.metadata or {})
                meta["model_chain"] = attempt_models
                meta["selected_model"] = model_name
                meta["effective_timeout_s"] = effective_timeout
                return type(result)(
                    text=result.text,
                    provider=result.provider,
                    model=result.model,
                    metadata=meta,
                )
            except Exception as exc:
                last_error = exc
                if not is_chain_retry_error(exc):
                    raise
                continue
        if last_error is not None:
            raise last_error
        return super().generate(messages, **options)

    def generate_stream(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        on_token: Optional[Any] = None,
        **options: Any,
    ) -> Any:
        last_error: Exception | None = None
        attempt_models: List[str] = []
        chain_started = time.perf_counter()
        model_chain = self._model_chain(options)
        effective_timeout = self._capped_timeout(options, chain_length=len(model_chain))
        for model_name in model_chain:
            if model_name in attempt_models:
                continue
            if (time.perf_counter() - chain_started) > MAX_MODEL_CHAIN_WALL_S:
                break
            attempt_models.append(model_name)
            try:
                merged = {**options, "model": model_name}
                if effective_timeout is not None:
                    merged["timeout"] = effective_timeout
                result = super().generate_stream(messages, on_token=on_token, **merged)
                meta = dict(result.metadata or {})
                meta["model_chain"] = attempt_models
                meta["selected_model"] = model_name
                meta["streamed"] = True
                meta["effective_timeout_s"] = effective_timeout
                return type(result)(
                    text=result.text,
                    provider=result.provider,
                    model=result.model,
                    metadata=meta,
                )
            except Exception as exc:
                last_error = exc
                if not is_chain_retry_error(exc):
                    raise
                continue
        if last_error is not None:
            raise last_error
        return super().generate_stream(messages, on_token=on_token, **options)
