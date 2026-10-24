import httpx
import pytest

from blackfong_ecosystem.chat_backends import ChatBackendError
from blackfong_ecosystem.model_readiness import verify_llamacpp
from blackfong_ecosystem.llamacpp_backend import LlamaCppChatBackend


@pytest.mark.parametrize("status,identity,error", [
    (200, "expected", None),
    (503, "expected", "endpoint_not_ready"),
    (200, "wrong", "model_identity_mismatch"),
])
def test_readiness_requires_health_and_identity(status, identity, error):
    def respond(request):
        if request.url.path == "/health":
            return httpx.Response(status, json={})
        return httpx.Response(200, json={"data": [{"id": identity}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        if error:
            with pytest.raises(ChatBackendError) as caught:
                verify_llamacpp(client, "http://local", "expected")
            assert caught.value.metadata["error_type"] == error
        else:
            verify_llamacpp(client, "http://local", "expected")


@pytest.mark.parametrize("stream", [False, True])
def test_wrong_model_never_receives_prompt(monkeypatch, stream):
    requests = []
    def respond(request):
        requests.append(request.method)
        return httpx.Response(200, json={"data": [{"id": "wrong"}]})
    backend = LlamaCppChatBackend(model="expected")
    monkeypatch.setattr(backend, "_client", lambda timeout: httpx.Client(
        transport=httpx.MockTransport(respond)))
    with pytest.raises(ChatBackendError) as caught:
        (backend.generate_stream if stream else backend.generate)([])
    assert caught.value.metadata["error_type"] == "model_identity_mismatch"
    assert "POST" not in requests


def test_missing_local_files_do_not_substitute_backend(monkeypatch):
    from types import SimpleNamespace
    from blackfong_ecosystem import config, model_routing
    monkeypatch.setattr(config, "chat_settings", SimpleNamespace(daise_backend="llamacpp"))
    monkeypatch.setattr(config, "llamacpp_settings", SimpleNamespace(
        enabled=True, is_configured=lambda: False, base_url="http://local",
        model_alias="expected", request_timeout=10))
    backend = model_routing.build_daise_llamacpp_backend()
    assert isinstance(backend, LlamaCppChatBackend)
    assert backend.model == "expected"


def test_daise_ollama_has_no_automatic_model_chain(monkeypatch):
    from types import SimpleNamespace
    from blackfong_ecosystem import model_routing
    monkeypatch.setattr(model_routing, "chat_settings", SimpleNamespace(backend="ollama"))
    monkeypatch.setattr(model_routing, "build_daise_llamacpp_backend", lambda: None)
    monkeypatch.setattr(model_routing, "model_chain_for_agent", lambda *args: ["primary", "other"])
    monkeypatch.setattr(model_routing, "backend_options_for_context", lambda *args: {
        "timeout": 10, "keep_alive": "5m", "num_ctx": 2048, "num_predict": 100})
    assert model_routing.build_routed_chat_backend("daise", "standard").fallback_models == []
    assert model_routing.build_routed_chat_backend("coding", "standard").fallback_models == ["other"]
