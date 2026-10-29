from blackfong_ecosystem.agents.jarvis_agent import JARVISAgent
from blackfong_ecosystem.chat_backends import ChatBackendResult


def test_incomplete_output_is_preserved_but_not_success(monkeypatch):
    agent = object.__new__(JARVISAgent)
    class Backend:
        model = "test"
        provider_name = "llamacpp"
        def generate(self, messages, **options):
            return ChatBackendResult(text="Partial answer", provider=self.provider_name,
                model=self.model, metadata={"finish_reason": "length", "response_complete": False,
                                           "termination": "output_limit"})
    agent.chat_backend = Backend()
    agent._chat_backend_explicit = True
    monkeypatch.setattr(agent, "_grounded_memory_response", lambda context: None)
    monkeypatch.setattr(agent, "_grounded_knowledge_response", lambda *a, **kw: None)
    monkeypatch.setattr(agent, "_build_chat_messages", lambda *a: ([], {}))
    monkeypatch.setattr(agent, "_backend_options", lambda *a: {})
    text, info = agent._generate_response("hello", {"analysis_mode": "standard"})
    assert text == "Partial answer"
    assert "incomplete" in info["backend_error"]
    assert info["metadata"]["recovery_probe_succeeded"] is False
