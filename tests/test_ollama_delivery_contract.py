import json

import httpx
import pytest

from blackfong_ecosystem.chat_backends import ChatBackendError, OllamaChatBackend


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("reason", ["stop", "length"])
def test_finish_reason_and_sampling_preserved(monkeypatch, stream, reason):
    original_client = httpx.Client
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        data = {"model": "fixture:latest", "message": {"content": "Answer"},
                "done": True, "done_reason": reason,
                "prompt_eval_duration": 12000000, "eval_duration": 34000000}
        return httpx.Response(200, text=json.dumps(data) + "\n")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(respond)))
    backend = OllamaChatBackend(model="fixture:latest")
    result = (backend.generate_stream if stream else backend.generate)([], temperature=0.2)
    assert result.text == "Answer"
    assert result.metadata["finish_reason"] == reason
    assert result.metadata["response_complete"] is (reason == "stop")
    assert requests[0]["options"]["temperature"] == 0.2
    assert result.metadata["prompt_eval_ms"] == 12.0
    assert result.metadata["generation_ms"] == 34.0
    assert result.metadata["num_ctx"] == backend.num_ctx


def test_interrupted_ollama_stream_is_not_success(monkeypatch):
    original_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200,
        text=json.dumps({"message": {"content": "Partial"}, "done": False}) + "\n"))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=transport))
    with pytest.raises(ChatBackendError) as caught:
        OllamaChatBackend().generate_stream([])
    assert caught.value.metadata["error_type"] == "incomplete_stream"


@pytest.mark.parametrize("stream", [False, True])
def test_wrong_model_rejected_before_delivery(monkeypatch, stream):
    original_client = httpx.Client
    data = {"model": "other:latest", "message": {"content": "Wrong model answer"},
            "done": True, "done_reason": "stop"}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=json.dumps(data) + "\n"))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=transport))
    backend = OllamaChatBackend(model="fixture:latest")
    delivered = []
    with pytest.raises(ChatBackendError) as caught:
        (backend.generate_stream if stream else backend.generate)([], on_token=delivered.append)
    assert caught.value.metadata["error_type"] == "model_identity_mismatch"
    assert delivered == []


def test_default_latest_tag_is_same_model():
    OllamaChatBackend._verify_response_model("fixture", "fixture:latest")
