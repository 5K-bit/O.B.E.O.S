import json

import httpx
import pytest

from blackfong_ecosystem.chat_backends import ChatBackendError
from blackfong_ecosystem.llamacpp_backend import LlamaCppChatBackend


def backend_for(monkeypatch, finish):
    chunks = [{"choices": [{"delta": {"content": "A complete answer."}}]}]
    if finish is not None:
        chunks.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    body = "\n\n".join("data: " + json.dumps(chunk) for chunk in chunks)
    body += "\n\ndata: [DONE]\n\n"
    backend = LlamaCppChatBackend()
    def respond(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": backend.model}]})
        return httpx.Response(200, text=body)
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(backend, "_client", lambda timeout: httpx.Client(transport=transport))
    return backend


def test_unfinished_stream_is_not_success(monkeypatch):
    backend = backend_for(monkeypatch, None)
    with pytest.raises(ChatBackendError) as caught:
        backend.generate_stream([])
    assert caught.value.metadata["error_type"] == "incomplete_stream"


@pytest.mark.parametrize("finish,complete,termination", [
    ("stop", True, "stop"), ("length", False, "output_limit"),
])
def test_finish_reason_and_latency_preserved(monkeypatch, finish, complete, termination):
    backend = backend_for(monkeypatch, finish)
    tokens = []
    result = backend.generate_stream([], on_token=tokens.append)
    assert result.text == "".join(tokens)
    assert result.metadata["response_complete"] is complete
    assert result.metadata["termination"] == termination
    assert result.metadata["first_token_ms"] >= 0
