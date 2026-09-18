"""Chat backend that talks to a dedicated local llama.cpp server (llama-server).

Used specifically for D.A.I.S.E.'s edited Gemma model: llama-server loads one
model per process, so unlike Ollama this backend is not model-chain routed —
it always speaks to whichever single model the server was launched with.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Optional, Sequence

import httpx

from .cancellation import RequestCancelled, cancellation_resource, raise_if_cancelled
from .chat_backends import BaseChatBackend, ChatBackendError, ChatBackendResult
from .config import llamacpp_settings


class LlamaCppChatBackend(BaseChatBackend):
    """Chat backend for a local llama-server instance (OpenAI-compatible API)."""

    provider_name = "llamacpp"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        num_predict: int | None = None,
        trust_env: bool = False,
    ) -> None:
        self.trust_env = trust_env
        self.base_url = (base_url or llamacpp_settings.base_url).rstrip("/")
        self.model = model or llamacpp_settings.model_alias
        self.timeout = timeout if timeout is not None else llamacpp_settings.request_timeout
        self.num_predict = num_predict

    def _client(self, timeout: httpx.Timeout) -> httpx.Client:
        return httpx.Client(timeout=timeout, **({"trust_env": False} if not self.trust_env else {}))

    def generate(self, messages: Sequence[Dict[str, str]], **options: Any) -> ChatBackendResult:
        effective_timeout = float(options.get("timeout") or self.timeout)
        effective_model = str(options.get("model") or self.model)
        max_tokens = int(options.get("num_predict") or self.num_predict or 2048)
        messages_list = list(messages)
        payload: Dict[str, Any] = {
            "model": effective_model,
            "messages": messages_list,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if "temperature" in options:
            payload["temperature"] = options["temperature"]

        timeout = httpx.Timeout(connect=5.0, read=effective_timeout, write=30.0, pool=5.0)
        wall_started = time.perf_counter()

        def failure_metadata(error_type: str) -> Dict[str, Any]:
            return {
                "provider": self.provider_name,
                "model": effective_model,
                "error_type": error_type,
                "wall_time_ms": round((time.perf_counter() - wall_started) * 1000, 2),
                "timeout_s": effective_timeout,
            }

        try:
            with self._client(timeout) as client, cancellation_resource(getattr(client, "close", lambda: None)):
                raise_if_cancelled()
                response = client.post(f"{self.base_url}/v1/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.ReadTimeout as exc:
            raise_if_cancelled()
            raise ChatBackendError(
                f"llama.cpp request timed out after {effective_timeout:.0f}s for model {effective_model}",
                metadata=failure_metadata("read_timeout"),
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            status_code = exc.response.status_code
            raise_if_cancelled()
            raise ChatBackendError(
                f"llama.cpp backend returned HTTP {status_code} for model {effective_model}: {detail[:240]}",
                metadata=failure_metadata(f"http_{status_code}"),
            ) from exc
        except httpx.HTTPError as exc:
            raise_if_cancelled()
            raise ChatBackendError(
                f"llama.cpp backend unavailable: {exc}",
                metadata=failure_metadata("http_error"),
            ) from exc
        except RequestCancelled:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise_if_cancelled()
            raise ChatBackendError(
                f"llama.cpp backend unavailable: {exc}",
                metadata=failure_metadata("unexpected_error"),
            ) from exc

        return self._result_from_response(data, effective_model, wall_started)

    def generate_stream(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        on_token: Optional[Callable[[str], None]] = None,
        **options: Any,
    ) -> ChatBackendResult:
        effective_timeout = float(options.get("timeout") or self.timeout)
        effective_model = str(options.get("model") or self.model)
        max_tokens = int(options.get("num_predict") or self.num_predict or 2048)
        messages_list = list(messages)
        payload: Dict[str, Any] = {
            "model": effective_model,
            "messages": messages_list,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if "temperature" in options:
            payload["temperature"] = options["temperature"]

        timeout = httpx.Timeout(connect=5.0, read=effective_timeout, write=30.0, pool=5.0)
        wall_started = time.perf_counter()
        parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason: Optional[str] = None

        def failure_metadata(error_type: str) -> Dict[str, Any]:
            return {
                "provider": self.provider_name,
                "model": effective_model,
                "error_type": error_type,
                "wall_time_ms": round((time.perf_counter() - wall_started) * 1000, 2),
                "timeout_s": effective_timeout,
                "partial_stream_chars": len("".join(parts)),
            }

        try:
            with self._client(timeout) as client, cancellation_resource(getattr(client, "close", lambda: None)):
                with client.stream("POST", f"{self.base_url}/v1/chat/completions", json=payload) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        raise_if_cancelled()
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[len("data:") :].strip()
                        if not chunk or chunk == "[DONE]":
                            continue
                        try:
                            data = json.loads(chunk)
                        except ValueError:
                            continue
                        choices = data.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        finish_reason = choices[0].get("finish_reason") or finish_reason
                        token = str(delta.get("content") or "")
                        if token:
                            parts.append(token)
                            if on_token is not None:
                                on_token(token)
                        reasoning_token = str(delta.get("reasoning_content") or "")
                        if reasoning_token:
                            reasoning_parts.append(reasoning_token)
        except httpx.ReadTimeout as exc:
            raise_if_cancelled()
            raise ChatBackendError(
                f"llama.cpp request timed out after {effective_timeout:.0f}s for model {effective_model}",
                metadata=failure_metadata("read_timeout"),
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise_if_cancelled()
            raise ChatBackendError(
                f"llama.cpp backend returned HTTP {status_code} for model {effective_model}",
                metadata=failure_metadata(f"http_{status_code}"),
            ) from exc
        except httpx.HTTPError as exc:
            raise_if_cancelled()
            raise ChatBackendError(
                f"llama.cpp backend unavailable: {exc}",
                metadata=failure_metadata("http_error"),
            ) from exc
        except ChatBackendError:
            raise
        except RequestCancelled:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise_if_cancelled()
            raise ChatBackendError(
                f"llama.cpp backend unavailable: {exc}",
                metadata=failure_metadata("unexpected_error"),
            ) from exc

        content = "".join(parts).strip()
        if not content:
            raise_if_cancelled()
            raise ChatBackendError(
                "llama.cpp backend returned an empty response",
                metadata=failure_metadata("empty_response"),
            )
        wall_time_ms = round((time.perf_counter() - wall_started) * 1000, 2)
        return ChatBackendResult(
            text=content,
            provider=self.provider_name,
            model=effective_model,
            metadata={
                "streamed": True,
                "reasoning_content": "".join(reasoning_parts) or None,
                "finish_reason": finish_reason,
                "wall_time_ms": wall_time_ms,
            },
        )

    def _result_from_response(self, data: Dict[str, Any], effective_model: str, wall_started: float) -> ChatBackendResult:
        choices = data.get("choices") or []
        if not choices:
            raise_if_cancelled()
            raise ChatBackendError(
                "llama.cpp backend returned no choices",
                metadata={"provider": self.provider_name, "model": effective_model, "error_type": "empty_response"},
            )
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        reasoning_content = str(message.get("reasoning_content") or "").strip() or None
        if not content:
            raise_if_cancelled()
            raise ChatBackendError(
                "llama.cpp backend returned an empty response",
                metadata={
                    "provider": self.provider_name,
                    "model": effective_model,
                    "error_type": "empty_response",
                    "reasoning_content": reasoning_content,
                },
            )
        usage = data.get("usage") or {}
        timings = data.get("timings") or {}
        wall_time_ms = round((time.perf_counter() - wall_started) * 1000, 2)
        return ChatBackendResult(
            text=content,
            provider=self.provider_name,
            model=str(data.get("model") or effective_model),
            metadata={
                "reasoning_content": reasoning_content,
                "finish_reason": choices[0].get("finish_reason"),
                "prompt_eval_count": usage.get("prompt_tokens"),
                "eval_count": usage.get("completion_tokens"),
                "wall_time_ms": wall_time_ms,
                "llamacpp_prompt_ms": timings.get("prompt_ms"),
                "llamacpp_predicted_ms": timings.get("predicted_ms"),
                "llamacpp_predicted_per_second": timings.get("predicted_per_second"),
            },
        )
