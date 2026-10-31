from types import SimpleNamespace

from blackfong_ecosystem import backend_warmup, llamacpp_process


def test_llamacpp_warmup_never_loads_ollama(monkeypatch):
    monkeypatch.setattr(backend_warmup, "chat_settings", SimpleNamespace(daise_backend="llamacpp"))
    monkeypatch.setattr(llamacpp_process, "wait_for_local_model", lambda: {"status": "ready", "model": "selected"})
    assert backend_warmup.warm_selected_model() == {"status": "ready", "model": "selected"}


def test_ollama_warmup_uses_primary_only(monkeypatch):
    calls = []
    monkeypatch.setattr(backend_warmup, "chat_settings", SimpleNamespace(
        daise_backend="ollama", model_for_agent=lambda agent: "selected", ollama_timeout=120))
    def warm(**kwargs):
        calls.append(kwargs)
        return {"status": "error", "error": "unavailable"}
    monkeypatch.setattr(backend_warmup, "warm_ollama_model", warm)
    assert backend_warmup.warm_selected_model()["status"] == "error"
    assert calls == [{"model": "selected", "timeout": 120}]
