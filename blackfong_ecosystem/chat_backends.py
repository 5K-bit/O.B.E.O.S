from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, Optional, Sequence

import httpx

from .config import chat_settings
from .ollama_models import filter_model_chain


def is_chain_retry_error(exc: Exception) -> bool:
    """True when the routing layer should try the next model in the chain."""
    if isinstance(exc, ChatBackendError):
        meta = getattr(exc, "metadata", {}) or {}
        error_type = str(meta.get("error_type") or "")
        if error_type in {"http_404", "model_not_found", "empty_stream", "read_timeout"}:
            return True
        message = str(exc).lower()
        if "404" in message or "not found" in message or "timed out" in message:
            return True
    return False


def is_missing_model_error(exc: Exception) -> bool:
    """Backward-compatible alias."""
    return is_chain_retry_error(exc)


class ChatBackendError(RuntimeError):
    """Raised when a local chat backend cannot produce a response."""

    def __init__(self, message: str, *, metadata: Dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.metadata = metadata or {}


@dataclass
class ChatBackendResult:
    """Normalized chat backend result."""

    text: str
    provider: str
    model: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseChatBackend(ABC):
    """Abstract interface for local chat backends."""

    provider_name = "local"

    @abstractmethod
    def generate(self, messages: Sequence[Dict[str, str]], **options: Any) -> ChatBackendResult:
        raise NotImplementedError


class OllamaChatBackend(BaseChatBackend):
    """Chat backend that talks to a local Ollama server."""

    provider_name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        keep_alive: str | None = None,
        num_ctx: int | None = None,
        num_predict: int | None = None,
    ) -> None:
        self.base_url = (base_url or chat_settings.ollama_url).rstrip("/")
        self.model = model or chat_settings.ollama_model
        self.timeout = timeout if timeout is not None else chat_settings.ollama_timeout
        self.keep_alive = keep_alive or chat_settings.ollama_keep_alive
        self.num_ctx = num_ctx if num_ctx is not None else chat_settings.ollama_num_ctx
        self.num_predict = num_predict if num_predict is not None else chat_settings.ollama_num_predict

    def generate(self, messages: Sequence[Dict[str, str]], **options: Any) -> ChatBackendResult:
        effective_timeout = float(options.get("timeout") or self.timeout)
        effective_keep_alive = str(options.get("keep_alive") or self.keep_alive)
        effective_num_ctx = int(options.get("num_ctx") or self.num_ctx)
        effective_num_predict = int(options.get("num_predict") or self.num_predict)
        max_attempts = max(1, int(options.get("max_attempts") or 2))
        retry_read_timeout = bool(options.get("retry_read_timeout", True))
        invocation_mode = str(options.get("mode") or "default")
        messages_list = list(messages)
        prompt_chars = sum(len(str(message.get("content", ""))) for message in messages_list)
        approx_prompt_tokens = max(1, prompt_chars // 4) if prompt_chars else 0
        effective_model = str(options.get("model") or self.model)
        payload = {
            "model": effective_model,
            "messages": messages_list,
            "stream": False,
            "keep_alive": effective_keep_alive,
            "options": {
                "num_ctx": effective_num_ctx,
                "num_predict": effective_num_predict,
            },
        }

        timeout = httpx.Timeout(connect=5.0, read=effective_timeout, write=30.0, pool=5.0)
        last_error: Exception | None = None
        attempt_log = []
        wall_started = time.perf_counter()
        data: Dict[str, Any] | None = None

        def failure_metadata(error_type: str, *, timeout_reason: str | None = None) -> Dict[str, Any]:
            return {
                "provider": self.provider_name,
                "model": effective_model,
                "mode": invocation_mode,
                "ollama_attempts": len(attempt_log),
                "attempt_log": attempt_log,
                "wall_time_ms": round((time.perf_counter() - wall_started) * 1000, 2),
                "timeout_s": effective_timeout,
                "timeout_reason": timeout_reason,
                "error_type": error_type,
                "retry_read_timeout": retry_read_timeout,
                "prompt_message_count": len(messages_list),
                "prompt_chars": prompt_chars,
                "approx_prompt_tokens": approx_prompt_tokens,
                "keep_alive": effective_keep_alive,
                "num_ctx": effective_num_ctx,
                "num_predict": effective_num_predict,
            }

        for attempt in range(max_attempts):
            attempt_started = time.perf_counter()
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(f"{self.base_url}/api/chat", json=payload)
                    response.raise_for_status()
                    data = response.json()
                attempt_log.append(
                    {
                        "attempt": attempt + 1,
                        "status": "success",
                        "elapsed_ms": round((time.perf_counter() - attempt_started) * 1000, 2),
                    }
                )
                break
            except httpx.ReadTimeout as exc:  # pragma: no cover - network failure path
                last_error = exc
                attempt_log.append(
                    {
                        "attempt": attempt + 1,
                        "status": "read_timeout",
                        "elapsed_ms": round((time.perf_counter() - attempt_started) * 1000, 2),
                        "timeout_s": effective_timeout,
                    }
                )
                if retry_read_timeout and attempt < max_attempts - 1:
                    time.sleep(0.5)
                    continue
                raise ChatBackendError(
                    f"Ollama request timed out after {effective_timeout:.0f}s for model {effective_model}",
                    metadata=failure_metadata("read_timeout", timeout_reason=f"read_timeout_after_{effective_timeout:.0f}s"),
                ) from exc
            except httpx.HTTPStatusError as exc:  # pragma: no cover - network failure path
                last_error = exc
                detail = exc.response.text.strip()
                status_code = exc.response.status_code
                attempt_log.append(
                    {
                        "attempt": attempt + 1,
                        "status": f"http_{status_code}",
                        "elapsed_ms": round((time.perf_counter() - attempt_started) * 1000, 2),
                    }
                )
                if status_code == 404:
                    raise ChatBackendError(
                        f"Ollama model not found: {effective_model}",
                        metadata=failure_metadata("model_not_found"),
                    ) from exc
                if attempt < max_attempts - 1 and status_code >= 500:
                    time.sleep(0.5)
                    continue
                raise ChatBackendError(
                    f"Ollama backend returned HTTP {status_code} for model {effective_model}: {detail[:240]}",
                    metadata=failure_metadata(f"http_{status_code}"),
                ) from exc
            except httpx.HTTPError as exc:  # pragma: no cover - network failure path
                last_error = exc
                attempt_log.append(
                    {
                        "attempt": attempt + 1,
                        "status": "http_error",
                        "elapsed_ms": round((time.perf_counter() - attempt_started) * 1000, 2),
                        "error": str(exc)[:240],
                    }
                )
                if attempt < max_attempts - 1:
                    time.sleep(0.5)
                    continue
                raise ChatBackendError(
                    f"Ollama backend unavailable: {exc}",
                    metadata=failure_metadata("http_error"),
                ) from exc
            except Exception as exc:  # pragma: no cover - network failure path
                last_error = exc
                attempt_log.append(
                    {
                        "attempt": attempt + 1,
                        "status": "unexpected_error",
                        "elapsed_ms": round((time.perf_counter() - attempt_started) * 1000, 2),
                        "error": str(exc)[:240],
                    }
                )
                raise ChatBackendError(
                    f"Ollama backend unavailable: {exc}",
                    metadata=failure_metadata("unexpected_error"),
                ) from exc
        else:  # pragma: no cover - defensive fallback
            raise ChatBackendError(
                f"Ollama backend unavailable: {last_error}",
                metadata=failure_metadata("unavailable"),
            )

        data = data or {}
        content = (data.get("message") or {}).get("content", "").strip()
        if not content:
            raise ChatBackendError(
                "Ollama backend returned an empty response",
                metadata=failure_metadata("empty_response"),
            )

        wall_time_ms = round((time.perf_counter() - wall_started) * 1000, 2)
        total_duration = data.get("total_duration")
        load_duration = data.get("load_duration")
        return ChatBackendResult(
            text=content,
            provider=self.provider_name,
            model=data.get("model", self.model),
            metadata={
                "done": data.get("done"),
                "total_duration": data.get("total_duration"),
                "load_duration": data.get("load_duration"),
                "ollama_total_duration_ms": round(float(total_duration) / 1_000_000, 2) if total_duration else None,
                "load_duration_ms": round(float(load_duration) / 1_000_000, 2) if load_duration else None,
                "eval_count": data.get("eval_count"),
                "prompt_eval_count": data.get("prompt_eval_count"),
                "keep_alive": effective_keep_alive,
                "num_ctx": effective_num_ctx,
                "num_predict": effective_num_predict,
                "timeout_s": effective_timeout,
                "retry_read_timeout": retry_read_timeout,
                "ollama_attempts": len(attempt_log),
                "attempt_log": attempt_log,
                "wall_time_ms": wall_time_ms,
                "mode": invocation_mode,
                "prompt_message_count": len(messages_list),
                "prompt_chars": prompt_chars,
                "approx_prompt_tokens": approx_prompt_tokens,
            },
        )

    def generate_stream(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        on_token: Optional[Callable[[str], None]] = None,
        **options: Any,
    ) -> ChatBackendResult:
        """Stream tokens from Ollama, invoking on_token for each delta."""
        effective_timeout = float(options.get("timeout") or self.timeout)
        effective_keep_alive = str(options.get("keep_alive") or self.keep_alive)
        effective_num_ctx = int(options.get("num_ctx") or self.num_ctx)
        effective_num_predict = int(options.get("num_predict") or self.num_predict)
        invocation_mode = str(options.get("mode") or "stream")
        messages_list = list(messages)
        prompt_chars = sum(len(str(message.get("content", ""))) for message in messages_list)
        approx_prompt_tokens = max(1, prompt_chars // 4) if prompt_chars else 0
        payload = {
            "model": options.get("model") or self.model,
            "messages": messages_list,
            "stream": True,
            "keep_alive": effective_keep_alive,
            "options": {
                "num_ctx": effective_num_ctx,
                "num_predict": effective_num_predict,
            },
        }
        timeout = httpx.Timeout(connect=5.0, read=effective_timeout, write=30.0, pool=5.0)
        wall_started = time.perf_counter()
        parts: list[str] = []
        model_name = str(payload["model"])
        def failure_metadata(error_type: str, *, timeout_reason: str | None = None) -> Dict[str, Any]:
            return {
                "provider": self.provider_name,
                "model": model_name,
                "mode": invocation_mode,
                "wall_time_ms": round((time.perf_counter() - wall_started) * 1000, 2),
                "timeout_s": effective_timeout,
                "timeout_reason": timeout_reason,
                "error_type": error_type,
                "prompt_message_count": len(messages_list),
                "prompt_chars": prompt_chars,
                "approx_prompt_tokens": approx_prompt_tokens,
                "keep_alive": effective_keep_alive,
                "num_ctx": effective_num_ctx,
                "num_predict": effective_num_predict,
                "partial_stream_chars": len("".join(parts)),
            }

        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    if response.status_code == 404:
                        raise ChatBackendError(
                            f"Ollama model not found: {model_name}",
                            metadata=failure_metadata("model_not_found"),
                        )
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except ValueError:
                            continue
                        token = str((data.get("message") or {}).get("content") or "")
                        if token:
                            parts.append(token)
                            if on_token is not None:
                                on_token(token)
                        if data.get("done"):
                            model_name = str(data.get("model") or model_name)
        except httpx.ReadTimeout as exc:  # pragma: no cover - network failure path
            raise ChatBackendError(
                f"Ollama request timed out after {effective_timeout:.0f}s for model {model_name}",
                metadata=failure_metadata("read_timeout", timeout_reason=f"read_timeout_after_{effective_timeout:.0f}s"),
            ) from exc
        except httpx.HTTPStatusError as exc:  # pragma: no cover - network failure path
            detail = exc.response.text.strip()
            status_code = exc.response.status_code
            if status_code == 404:
                raise ChatBackendError(
                    f"Ollama model not found: {model_name}",
                    metadata=failure_metadata("model_not_found"),
                ) from exc
            raise ChatBackendError(
                f"Ollama backend returned HTTP {status_code} for model {model_name}: {detail[:240]}",
                metadata=failure_metadata(f"http_{status_code}"),
            ) from exc
        except httpx.HTTPError as exc:  # pragma: no cover - network failure path
            raise ChatBackendError(
                f"Ollama backend unavailable: {exc}",
                metadata=failure_metadata("http_error"),
            ) from exc
        except ChatBackendError:
            raise
        except Exception as exc:  # pragma: no cover - network failure path
            raise ChatBackendError(
                f"Ollama backend unavailable: {exc}",
                metadata=failure_metadata("unexpected_error"),
            ) from exc
        content = "".join(parts).strip()
        if not content:
            # Some local Ollama builds accept the stream route but return no deltas.
            fallback = self.generate(messages_list, **options)
            metadata = dict(fallback.metadata or {})
            metadata.setdefault("mode", invocation_mode)
            metadata["streamed"] = False
            metadata["stream_fallback"] = "generate"
            metadata["stream_fallback_reason"] = "empty_stream"
            metadata["wall_time_ms"] = round((time.perf_counter() - wall_started) * 1000, 2)
            return ChatBackendResult(
                text=fallback.text,
                provider=fallback.provider,
                model=fallback.model,
                metadata=metadata,
            )
        return ChatBackendResult(
            text=content,
            provider=self.provider_name,
            model=model_name,
            metadata={
                "mode": invocation_mode,
                "streamed": True,
                "wall_time_ms": round((time.perf_counter() - wall_started) * 1000, 2),
            },
        )


class HeuristicChatBackend(BaseChatBackend):
    """Deterministic fallback backend for when Ollama is offline."""

    provider_name = "heuristic"

    def __init__(self, model: str = "fallback-heuristic") -> None:
        self.model = model

    def generate(self, messages: Sequence[Dict[str, str]], **options: Any) -> ChatBackendResult:
        latest_user = ""
        context_blob = []
        for message in messages:
            role = message.get("role", "")
            content = message.get("content", "")
            if role == "user":
                latest_user = content
            if role in {"system", "assistant", "user"}:
                context_blob.append(content)

        response = self._generate_reply(
            latest_user,
            "\n".join(context_blob),
            backend_error=options.get("backend_error"),
            fallback_reason=options.get("fallback_reason"),
            provider=options.get("provider"),
            model=options.get("model"),
            backend_health=options.get("backend_health"),
            primary_backend_metadata=options.get("primary_backend_metadata"),
        )
        metadata = dict(options.get("metadata") or {})
        metadata.update(
            {
                "backend_error": options.get("backend_error"),
                "fallback_reason": options.get("fallback_reason"),
                "provider": options.get("provider"),
                "model": options.get("model"),
                "backend_health": options.get("backend_health"),
                "primary_backend_metadata": options.get("primary_backend_metadata"),
            }
        )
        return ChatBackendResult(
            text=response,
            provider=self.provider_name,
            model=self.model,
            metadata=metadata,
        )

    def _generate_reply(
        self,
        user_input: str,
        context_text: str,
        *,
        backend_error: str | None = None,
        fallback_reason: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        backend_health: Any | None = None,
        primary_backend_metadata: Any | None = None,
    ) -> str:
        lowered = user_input.lower().strip()

        favorite_color_match = re.search(
            r"favorite color(?: is|:)\s+([a-zA-Z]+)",
            context_text,
            flags=re.IGNORECASE,
        )
        if "favorite color" in lowered and favorite_color_match:
            return f"Your favorite color is {favorite_color_match.group(1)}."

        if self._matches_memory_commitment_prompt(lowered):
            return (
                "Yes. If we keep that in our shared session or promote it into pinned O.B.E.O.S. memory, "
                "D.A.I.S.E. can carry it forward across the HUD, desktop, and Telegram."
            )

        if self._matches_chat_rendering_prompt(lowered):
            return (
                "That usually points to the active chat surface rather than lost memory. "
                "When a reply looks cut off in O.B.E.O.S., the usual causes are the renderer, "
                "scroll container, or message layout while the live model path is recovering."
            )

        if self._matches_milestone_prompt(lowered):
            return (
                "Thank you. That does matter. "
                "Remembering your identity and carrying forward shared O.B.E.O.S. context is one of the milestones "
                "we're actively strengthening."
            )

        if self._contains_any_word(lowered, "hello", "hi", "hey"):
            return (
                "Hello. D.A.I.S.E. is here with local O.B.E.O.S. memory, recent session context, "
                "and stored knowledge still available."
            )

        if fallback_reason:
            fallback_source = "live model" if provider == "ollama" else provider or "the unavailable backend"
            return (
                f"The live model lane is unavailable right now. "
                f"Falling back to local O.B.E.O.S. memory because: {fallback_reason}. "
                "I’m answering from stored context while the primary backend recovers."
            )

        if self._contains_any_phrase(lowered, "system status") or self._contains_any_word(lowered, "status", "health"):
            return "O.B.E.O.S is online locally. Use the HUD for sessions, memory, commands, and voice controls."

        if self._contains_any_phrase(lowered, "what can you do") or self._contains_any_word(lowered, "help", "capabilities"):
            return (
                "I can chat as D.A.I.S.E, recall prior sessions, pin durable memory, surface session history, "
                "and route specialized requests to coding, Lilo, Stitch, or voice controls."
            )

        if self._contains_any_word(lowered, "memory"):
            return "Session and global memory are available through the shared conversation layer. Use /memory or pin important context from the HUD."

        grounded = self._grounded_context_fallback(lowered, context_text)
        if grounded:
            return grounded

        return (
            "I can still help from local O.B.E.O.S. memory and stored knowledge while the live model lane recovers. "
            "If you want, ask again directly and I will keep the answer grounded."
        )

    def _grounded_context_fallback(self, lowered: str, context_text: str) -> str:
        if not context_text:
            return ""
        source_match = re.search(
            r"\[Source\s+\d+\][^\n]*\n(?P<body>.+?)(?:\n---\n|\Z)",
            context_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not source_match:
            return ""
        excerpt = self._clean_local_fallback_excerpt(source_match.group("body"))
        if not excerpt:
            return ""
        if any(
            token in lowered
            for token in (
                "tell me about",
                "what is",
                "what's",
                "who is",
                "who's",
                "explain",
                "remember",
                "recall",
            )
        ):
            return (
                "From local O.B.E.O.S. knowledge, "
                f"{excerpt}. "
                "I'm answering from stored context while the live model lane recovers."
            )
        return (
            "I can still ground this from local O.B.E.O.S. context: "
            f"{excerpt}. "
            "I'm using the stored local context for this turn."
        )

    @staticmethod
    def _clean_local_fallback_excerpt(text: str) -> str:
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return ""

        passage_snippet = ""
        passage_match = re.search(
            r"Representative passages:\s*(.+?)(?:Structured timeline entries:|Open questions:|Known gaps:|\[truncated\]|\Z)",
            cleaned,
            flags=re.IGNORECASE,
        )
        if passage_match:
            passage_text = passage_match.group(1).strip(" ,.;:-")
            if passage_text:
                passage_snippet = re.split(r"(?<=[.!?])\s+", passage_text, maxsplit=1)[0].strip(" ,.;:-")

        for stopper in (
            "Representative passages:",
            "Structured timeline entries:",
            "Open questions:",
            "Known gaps:",
            "[truncated]",
        ):
            index = cleaned.lower().find(stopper.lower())
            if index > 0:
                cleaned = cleaned[:index].rstrip(" ,.;:-")
                break

        if passage_snippet and passage_snippet.lower() not in cleaned.lower():
            cleaned = f"{cleaned}. {passage_snippet}".strip(" ,.;:-")

        cleaned = re.sub(r"[_*#~`|]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:-")
        if len(cleaned) > 320:
            cleaned = cleaned[:317].rstrip(" ,.;:-") + "..."
        return cleaned

    @staticmethod
    def _contains_any_word(text: str, *words: str) -> bool:
        return any(re.search(rf"\b{re.escape(word)}\b", text, flags=re.IGNORECASE) for word in words)

    @staticmethod
    def _contains_any_phrase(text: str, *phrases: str) -> bool:
        lowered = str(text or "").lower()
        return any(str(phrase).lower() in lowered for phrase in phrases)

    def _matches_memory_commitment_prompt(self, lowered: str) -> bool:
        return self._contains_any_phrase(
            lowered,
            "will you remember",
            "remember that in the future",
            "remember this in the future",
            "remember to do that in the future",
            "remember that later",
        )

    def _matches_chat_rendering_prompt(self, lowered: str) -> bool:
        if not self._contains_any_phrase(lowered, "cut off", "clipped", "truncated", "not showing all"):
            return False
        return self._contains_any_word(lowered, "text", "reply", "message", "messages", "render", "rendering", "chat")

    def _matches_milestone_prompt(self, lowered: str) -> bool:
        if not self._contains_any_word(lowered, "congratulate", "congratulations", "milestone"):
            return False
        return self._contains_any_word(lowered, "remembered", "remember", "name", "identity")


def build_default_chat_backend() -> BaseChatBackend:
    """Build the configured default chat backend."""
    if chat_settings.backend.lower() == "ollama":
        return OllamaChatBackend()
    return HeuristicChatBackend(model=f"{chat_settings.backend}-fallback")


def build_agent_chat_backend(agent_name: str, response_mode: str = "fast") -> BaseChatBackend:
    """Build a chat backend with per-agent model routing and fallbacks."""
    from .model_routing import build_routed_chat_backend, normalize_response_mode

    if chat_settings.backend.lower() == "ollama":
        return build_routed_chat_backend(agent_name, normalize_response_mode(response_mode))
    return HeuristicChatBackend(model=f"{agent_name}-{chat_settings.backend}-fallback")
