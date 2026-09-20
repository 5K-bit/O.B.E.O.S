from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, Optional, Sequence

import httpx
from .cancellation import RequestCancelled, cancellation_resource, raise_if_cancelled

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

    @staticmethod
    def _verify_response_model(expected: str, reported: Any) -> None:
        if not reported:
            return
        def normalized(name: str) -> str:
            return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"
        if normalized(str(reported)) != normalized(expected):
            raise ChatBackendError(
                "Ollama returned a different model than requested",
                metadata={"error_type": "model_identity_mismatch", "provider": "ollama",
                          "model": expected, "reported_model": str(reported)},
            )

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        keep_alive: str | None = None,
        num_ctx: int | None = None,
        num_predict: int | None = None,
        trust_env: bool = True,
    ) -> None:
        self.trust_env = trust_env
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
        if "think" in options:
            payload["think"] = bool(options["think"])
        if "temperature" in options:
            payload["options"]["temperature"] = float(options["temperature"])

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
            raise_if_cancelled()
            attempt_started = time.perf_counter()
            try:
                with httpx.Client(timeout=timeout, **({"trust_env": False} if not self.trust_env else {})) as client, cancellation_resource(getattr(client, "close", lambda: None)):
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
                raise_if_cancelled()
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
                    raise_if_cancelled()
                    raise ChatBackendError(
                        f"Ollama model not found: {effective_model}",
                        metadata=failure_metadata("model_not_found"),
                    ) from exc
                if attempt < max_attempts - 1 and status_code >= 500:
                    time.sleep(0.5)
                    continue
                raise_if_cancelled()
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
                raise_if_cancelled()
                raise ChatBackendError(
                    f"Ollama backend unavailable: {exc}",
                    metadata=failure_metadata("http_error"),
                ) from exc
            except RequestCancelled:
                raise
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
                raise_if_cancelled()
                raise ChatBackendError(
                    f"Ollama backend unavailable: {exc}",
                    metadata=failure_metadata("unexpected_error"),
                ) from exc
        else:  # pragma: no cover - defensive fallback
            raise_if_cancelled()
            raise ChatBackendError(
                f"Ollama backend unavailable: {last_error}",
                metadata=failure_metadata("unavailable"),
            )

        data = data or {}
        self._verify_response_model(effective_model, data.get("model"))
        content = (data.get("message") or {}).get("content", "").strip()
        if not content:
            raise_if_cancelled()
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
            model=data.get("model") or effective_model,
            metadata={
                "done": data.get("done"),
                "finish_reason": data.get("done_reason"),
                "response_complete": data.get("done") is True and data.get("done_reason") == "stop",
                "termination": "output_limit" if data.get("done_reason") == "length" else data.get("done_reason"),
                "total_duration": data.get("total_duration"),
                "load_duration": data.get("load_duration"),
                "ollama_total_duration_ms": round(float(total_duration) / 1_000_000, 2) if total_duration else None,
                "load_duration_ms": round(float(load_duration) / 1_000_000, 2) if load_duration else None,
                "prompt_eval_ms": round(float(data["prompt_eval_duration"]) / 1_000_000, 2) if data.get("prompt_eval_duration") is not None else None,
                "generation_ms": round(float(data["eval_duration"]) / 1_000_000, 2) if data.get("eval_duration") is not None else None,
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
        if "think" in options:
            payload["think"] = bool(options["think"])
        if "temperature" in options:
            payload["options"]["temperature"] = float(options["temperature"])
        timeout = httpx.Timeout(connect=5.0, read=effective_timeout, write=30.0, pool=5.0)
        wall_started = time.perf_counter()
        parts: list[str] = []
        first_token_ms: float | None = None
        model_name = str(payload["model"])
        final_data: Dict[str, Any] = {}
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
            with httpx.Client(timeout=timeout, **({"trust_env": False} if not self.trust_env else {})) as client, cancellation_resource(getattr(client, "close", lambda: None)):
                with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    if response.status_code == 404:
                        raise_if_cancelled()
                        raise ChatBackendError(
                            f"Ollama model not found: {model_name}",
                            metadata=failure_metadata("model_not_found"),
                        )
                    response.raise_for_status()
                    for line in response.iter_lines():
                        raise_if_cancelled()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except ValueError:
                            continue
                        self._verify_response_model(str(payload["model"]), data.get("model"))
                        final_data = data
                        token = str((data.get("message") or {}).get("content") or "")
                        if token:
                            if first_token_ms is None:
                                first_token_ms = round((time.perf_counter() - wall_started) * 1000, 2)
                            parts.append(token)
                            if on_token is not None:
                                on_token(token)
                        if data.get("done"):
                            model_name = str(data.get("model") or model_name)
        except httpx.ReadTimeout as exc:  # pragma: no cover - network failure path
            raise_if_cancelled()
            raise ChatBackendError(
                f"Ollama request timed out after {effective_timeout:.0f}s for model {model_name}",
                metadata=failure_metadata("read_timeout", timeout_reason=f"read_timeout_after_{effective_timeout:.0f}s"),
            ) from exc
        except httpx.HTTPStatusError as exc:  # pragma: no cover - network failure path
            detail = exc.response.text.strip()
            status_code = exc.response.status_code
            if status_code == 404:
                raise_if_cancelled()
                raise ChatBackendError(
                    f"Ollama model not found: {model_name}",
                    metadata=failure_metadata("model_not_found"),
                ) from exc
            raise_if_cancelled()
            raise ChatBackendError(
                f"Ollama backend returned HTTP {status_code} for model {model_name}: {detail[:240]}",
                metadata=failure_metadata(f"http_{status_code}"),
            ) from exc
        except httpx.HTTPError as exc:  # pragma: no cover - network failure path
            raise_if_cancelled()
            raise ChatBackendError(
                f"Ollama backend unavailable: {exc}",
                metadata=failure_metadata("http_error"),
            ) from exc
        except ChatBackendError:
            raise
        except RequestCancelled:
            raise
        except Exception as exc:  # pragma: no cover - network failure path
            raise_if_cancelled()
            raise ChatBackendError(
                f"Ollama backend unavailable: {exc}",
                metadata=failure_metadata("unexpected_error"),
            ) from exc
        raise_if_cancelled()
        if not final_data.get("done"):
            raise ChatBackendError(
                "Ollama stream ended before provider completion",
                metadata=failure_metadata("incomplete_stream"),
            )
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
                "first_token_ms": first_token_ms,
                "done": final_data.get("done"),
                "finish_reason": final_data.get("done_reason"),
                "response_complete": final_data.get("done_reason") == "stop",
                "termination": "output_limit" if final_data.get("done_reason") == "length" else final_data.get("done_reason"),
                "total_duration": final_data.get("total_duration"),
                "load_duration": final_data.get("load_duration"),
                "ollama_total_duration_ms": round(float(final_data.get("total_duration")) / 1_000_000, 2) if final_data.get("total_duration") else None,
                "load_duration_ms": round(float(final_data.get("load_duration")) / 1_000_000, 2) if final_data.get("load_duration") else None,
                "prompt_eval_ms": round(float(final_data["prompt_eval_duration"]) / 1_000_000, 2) if final_data.get("prompt_eval_duration") is not None else None,
                "generation_ms": round(float(final_data["eval_duration"]) / 1_000_000, 2) if final_data.get("eval_duration") is not None else None,
                "num_ctx": effective_num_ctx,
                "num_predict": effective_num_predict,
                "eval_count": final_data.get("eval_count"),
                "prompt_eval_count": final_data.get("prompt_eval_count"),
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
        if self._contains_any_phrase(
            lowered,
            "good evening",
            "good morning",
            "good afternoon",
            "how are you",
            "are you still there",
            "are you there",
            "still there",
            "are you hungry",
        ):
            return "I am here inside O.B.E.O.S., and I can still answer from local memory while the live model lane recovers."

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

        if fallback_reason:
            return (
                "The live model lane did not complete this turn, so I do not want to invent an answer. "
                f"Falling back to local O.B.E.O.S. memory because: {fallback_reason}. "
                "I can retry the live model when it is available."
            )

        return (
            "I can still help from local O.B.E.O.S. memory and stored knowledge while the live model lane recovers. "
            "If you want, ask again directly and I will keep the answer grounded."
        )

    def _grounded_context_fallback(self, lowered: str, context_text: str) -> str:
        if not context_text:
            return ""
        conversational_match = re.search(
            r"Broad conversational grounding for this turn:\n(?P<body>.+?)(?:\nUse this as a memory-backed backbone|\Z)",
            context_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if conversational_match:
            excerpt = " ".join(str(conversational_match.group("body") or "").split()).strip(" ,.;:-")
            if excerpt:
                return excerpt.rstrip(".") + "."
        continuity_match = re.search(
            r"Conversation continuity:\n(?P<body>.+?)(?:\n\n|\Z)",
            context_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        specialist_match = re.search(
            r"Specialist continuity:\n(?P<body>.+?)(?:\n\n|\Z)",
            context_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        specialist_body = str(specialist_match.group("body") or "") if specialist_match else ""
        specialist_name_match = re.search(
            r"Last selected specialist:\s*(?P<agent>.+?)(?:\.|\n|$)",
            specialist_body,
            flags=re.IGNORECASE,
        )
        specialist_name = " ".join(str((specialist_name_match.group("agent") if specialist_name_match else "") or "").split()).strip(" ,.;:-")
        task_focus_match = re.search(
            r"Last task focus:\s*(?P<focus>.+?)(?:\.|\n|$)",
            specialist_body,
            flags=re.IGNORECASE,
        )
        task_focus = " ".join(str((task_focus_match.group("focus") if task_focus_match else "") or "").split()).strip(" ,.;:-")
        if continuity_match:
            continuity_body = str(continuity_match.group("body") or "")
            thread_match = re.search(r"Ongoing thread:\s*(?P<thread>.+?)(?:\.|\n|$)", continuity_body, flags=re.IGNORECASE)
            thread = " ".join(str((thread_match.group("thread") if thread_match else "") or "").split()).strip(" ,.;:-")
            last_context_match = re.search(
                r"Last stable assistant context:\s*(?P<context>.+?)(?:\.|\n|$)",
                continuity_body,
                flags=re.IGNORECASE,
            )
            last_context = " ".join(str((last_context_match.group("context") if last_context_match else "") or "").split()).strip(" ,.;:-")
            if last_context.lower().startswith("more specifically, i mean "):
                last_context = last_context[len("more specifically, i mean ") :].strip(" ,.;:-")
            from .conversation_memory import conversation_memory_service

            continuity_flags = conversation_memory_service._conversational_followup_flags(lowered)
            if thread and any(
                token in lowered
                for token in (
                    "keep talking",
                    "talk more",
                    "for a minute",
                    "still with me",
                    "continue",
                    "keep going",
                    "about the system",
                    "about obeos",
                    "about the project",
                    "what's going on",
                )
            ):
                return (
                    f"We are still in the middle of {thread}. "
                    "That is the thread I would keep carrying forward as we continue."
                )
            if any(
                token in lowered
                for token in (
                    "tell me more",
                    "say more",
                    "go on",
                    "keep going",
                    "what do you mean",
                    "what makes you say that",
                    "how so",
                    "why is that",
                    "why though",
                    "expand on that",
                    "elaborate",
                    "continue that",
                    "what else",
                    "and then",
                    "stay with that thought",
                    "stay with that",
                    "stay with this thought",
                    "stay with this",
                    "same for",
                    "same with",
                    "same here",
                    "for the python one",
                    "for the code one",
                    "for that one",
                    "on the hud too",
                    "on telegram",
                    "does that work there too",
                    "what about that case",
                    "same thing there",
                    "would that still be true there",
                    "does that line up with the hud",
                    "over there too",
                    "same on that side",
                    "locally too",
                    "same in browser",
                    "same in the cloud",
                    "on desktop too",
                    "same on this machine",
                    "what about in the browser hud",
                    "does that also work locally",
                    "same thing in telegram",
                    "does it still work over there",
                    "what about in that path",
                    "does that also fit there",
                    "is that still how it works there",
                    "same for that route",
                    "what about that flow",
                    "does that still hold here",
                    "does this still hold here",
                    "does that carry over",
                    "does this carry over",
                    "so that still applies",
                    "so this still applies",
                    "does that apply here too",
                    "does this apply here too",
                    "keep talking about that",
                    "keep talking about this",
                    "that feels important",
                    "this feels important",
                    "that seems important",
                    "this seems important",
                    "is that what worries you",
                    "is this what worries you",
                    "does that still worry you",
                    "does this still worry you",
                    "is that what you mean",
                    "is this what you mean",
                    "is that still the problem",
                    "is this still the problem",
                    "is that still the main thing",
                    "is this still the main thing",
                    "what about the other part",
                    "what about that part",
                    "what about this side of it",
                    "what about that side of it",
                    "what part matters most",
                    "which part matters most",
                    "what part is still fragile",
                    "which part is still fragile",
                )
            ) or (
                continuity_flags.get("ultra_short_followup")
                and bool(last_context or thread)
            ) or (
                continuity_flags.get("referential_followup")
            ) or (
                continuity_flags.get("short_scope_followup")
            ) or (
                continuity_flags.get("environment_followup")
            ):
                if last_context:
                    if any(token in lowered for token in ("why", "how so", "what makes you say")):
                        return (
                            f"Because {last_context[:1].lower() + last_context[1:] if last_context else last_context}. "
                            + (f"That still connects back to {thread}." if thread else "That is the thread I am still carrying forward.")
                        )
                    if any(token in lowered for token in ("what else", "and then")):
                        return (
                            f"Another part of it is {last_context[:1].lower() + last_context[1:] if last_context else last_context}. "
                            + (
                                f"That still connects back to {thread}."
                                if thread
                                else "That is the thread I am still carrying forward."
                            )
                            + (
                                f" I am still treating this as part of the {specialist_name} lane."
                                if specialist_name
                                else ""
                            )
                        )
                    return (
                        f"More specifically, I mean {last_context}. "
                        + (
                            f"That still connects back to {thread}."
                            if thread
                            else "That is the thread I am still carrying forward."
                        )
                        + (
                            f" I am still treating this as part of the {specialist_name} lane"
                            f"{' focused on ' + task_focus if task_focus else ''}."
                            if specialist_name
                            else ""
                        )
                    )
                if thread:
                    return (
                        f"More specifically, I am still talking about {thread}. "
                        "That is the thread I would keep carrying forward from here."
                        + (
                            f" I am still treating it as part of the {specialist_name} lane"
                            f"{' focused on ' + task_focus if task_focus else ''}."
                            if specialist_name
                            else ""
                        )
                    )
        stance_match = re.search(
            r"Conversational stance:\n(?P<body>.+?)(?:\n\n|\Z)",
            context_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if stance_match:
            stance_body = str(stance_match.group("body") or "")
            focus_match = re.search(r"Current shared focus:\s*(?P<focus>.+?)(?:\.|\n|$)", stance_body, flags=re.IGNORECASE)
            focus = " ".join(str((focus_match.group("focus") if focus_match else "") or "").split()).strip(" ,.;:-")
            if focus and any(
                token in lowered
                for token in (
                    "project",
                    "system",
                    "work",
                    "us",
                    "going",
                    "heading",
                    "direction",
                    "risk",
                    "fragile",
                    "think",
                    "feel",
                    "progress",
                    "overall",
                )
            ):
                return (
                    f"What stands out most to me right now is {focus}. "
                    "That is the work most directly shaping whether O.B.E.O.S. feels grounded, coherent, and dependable."
                )
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
        structured_summary = bool(passage_snippet) and len(re.findall(r"[A-Za-z]{4,}", cleaned)) >= 8
        if (
            "[page" in cleaned.lower()
            or "ufg " in cleaned.lower()
            or " j! " in cleaned.lower()
            or (not structured_summary and HeuristicChatBackend._looks_like_ocr_noise(cleaned))
        ):
            return ""
        if len(cleaned) > 320:
            cleaned = cleaned[:317].rstrip(" ,.;:-") + "..."
        return cleaned

    @staticmethod
    def _looks_like_ocr_noise(text: str) -> bool:
        tokens = re.findall(r"[A-Za-z]{2,}", str(text or ""))
        if not tokens:
            return True
        uppercase_tokens = [token for token in tokens if token.isupper() and len(token) >= 3]
        short_tokens = [token for token in tokens if len(token) <= 3]
        return len(uppercase_tokens) >= 4 or (len(short_tokens) >= 8 and len(tokens) >= 10)

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
