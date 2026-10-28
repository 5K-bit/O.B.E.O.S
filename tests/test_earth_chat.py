"""POST /api/chat on the Earth app: auth, validation, session identity, and the
promise that Earth's boot path never imports the chat stack."""
import sys
from types import SimpleNamespace
import pytest
from earthglass.spatial.engine import SpatialEngine
from blackfong_ecosystem.earth.control import EarthControl
from blackfong_ecosystem.earth.credentials import CredentialBroker
from blackfong_ecosystem.earth.server import create_app
from fastapi.testclient import TestClient

ORIGIN = "http://127.0.0.1:8765"
AUTH = {"Authorization": "Bearer test-token", "Origin": ORIGIN}


@pytest.fixture
def client(tmp_path):
    engine = SpatialEngine(tmp_path / "data")
    control = EarthControl(engine, tmp_path / "scene.sqlite3")
    app = create_app(
        engine, control, CredentialBroker(tmp_path / "keys.json"),
        tmp_path / "assets", tmp_path, "test-token", origin=ORIGIN,
    )
    with TestClient(app, base_url=ORIGIN) as c:
        yield c, app
    control.close()
    engine.stop()


def test_creating_the_app_does_not_import_the_chat_stack(client, tmp_path):
    _, app = client
    assert getattr(app.state, "operator_chat", None) is None
    # Checked in a clean interpreter, because any earlier test that actually sends
    # a message imports the module for the rest of the session. conversation_memory
    # builds a ConversationMemoryService at module scope, so finding it in a fresh
    # boot would mean Earth's startup had paid for the whole chat stack.
    import subprocess
    probe = (
        "import sys;"
        "from pathlib import Path;"
        "from earthglass.spatial.engine import SpatialEngine;"
        "from blackfong_ecosystem.earth.control import EarthControl;"
        "from blackfong_ecosystem.earth.credentials import CredentialBroker;"
        "from blackfong_ecosystem.earth.server import create_app;"
        f"root = Path({str(tmp_path / 'probe')!r});"
        "engine = SpatialEngine(root / 'data');"
        "control = EarthControl(engine, root / 'scene.sqlite3');"
        "create_app(engine, control, CredentialBroker(root / 'k.json'),"
        " root / 'assets', root, 't', origin='http://127.0.0.1:8765');"
        "print('LEAKED' if 'blackfong_ecosystem.operator_chat' in sys.modules else 'CLEAN');"
        "control.close(); engine.stop()"
    )
    # No cwd pinned here: the probe only imports already-installed packages
    # and works entirely from the absolute tmp_path it's given, so it has no
    # dependency on the working directory. A hardcoded Linux CI path here
    # previously broke this test on Windows dev machines.
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         timeout=180)
    assert "CLEAN" in out.stdout, out.stdout + out.stderr


def test_requires_a_local_session(client):
    c, _ = client
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_browser_sessions_are_origin_checked(client):
    c, app = client

    class FakeChat:
        def send_message(self, **kwargs):
            return {"status": "complete"}

    app.state.operator_chat = FakeChat()
    # A Bearer token deliberately bypasses the origin check - that is how the
    # Qt-embedded view, which sends no Origin header, authenticates. Browser
    # sessions ride the cookie instead, and those are origin-checked. Establish
    # the cookie, then post cross-origin without the token.
    assert c.get("/api/scene", headers=AUTH).status_code == 200
    assert c.post("/api/chat", json={"message": "hi"},
                  headers={"Origin": "https://example.org"}).status_code == 403
    # Same cookie, correct origin: accepted. A stub stands in so the assertion is
    # about the boundary, not about loading a model.
    assert c.post("/api/chat", json={"message": "hi"},
                  headers={"Origin": ORIGIN}).status_code != 403


def test_rejects_malformed_requests(client):
    c, _ = client
    assert c.post("/api/chat", json={}, headers=AUTH).status_code == 422
    assert c.post("/api/chat", json={"message": ""}, headers=AUTH).status_code == 422
    assert c.post("/api/chat", json={"message": "x" * 4001}, headers=AUTH).status_code == 422
    # extra="forbid" keeps the surface closed, as every other Earth model does.
    assert c.post("/api/chat", json={"message": "hi", "nope": 1}, headers=AUTH).status_code == 422
    assert c.post("/api/chat", json={"message": "hi", "analysis_mode": "turbo"},
                  headers=AUTH).status_code == 422


def test_sends_through_the_operator_chat_service(client, monkeypatch):
    c, app = client
    seen = {}

    class FakeChat:
        def send_message(self, **kwargs):
            seen.update(kwargs)
            return {"status": "complete", "session_id": "s1", "request_id": "r1",
                    "result": {"response": "hello"}, "messages": []}

    app.state.operator_chat = FakeChat()
    body = c.post("/api/chat", json={"message": "what am I looking at?",
                                     "session_id": "s1", "analysis_mode": "deep"},
                  headers=AUTH)
    assert body.status_code == 200
    assert body.json()["result"]["response"] == "hello"
    assert seen["message"] == "what am I looking at?"
    assert seen["session_id"] == "s1"
    assert seen["analysis_mode"] == "deep"
    # Earth is identified as the surface so the event spine can attribute the turn.
    assert seen["context"]["input_origin"] == "earth"
    # Trace is suppressed; it is large and the panel renders from messages.
    assert seen["include_trace"] is False


def test_service_is_built_once_and_reused(client, monkeypatch):
    c, app = client
    built = []

    class FakeChat:
        def __init__(self):
            built.append(1)

        def send_message(self, **kwargs):
            return {"status": "complete"}

    module = SimpleNamespace(OperatorChatService=FakeChat)
    monkeypatch.setitem(sys.modules, "blackfong_ecosystem.operator_chat", module)
    for _ in range(3):
        assert c.post("/api/chat", json={"message": "hi"}, headers=AUTH).status_code == 200
    assert len(built) == 1


def test_unserialisable_reply_still_returns_the_turn(client):
    c, app = client

    class Opaque:
        pass

    class FakeChat:
        def send_message(self, **kwargs):
            return {"status": "complete", "result": {"response": "ok", "handle": Opaque()}}

    app.state.operator_chat = FakeChat()
    response = c.post("/api/chat", json={"message": "hi"}, headers=AUTH)
    # Without make_json_compatible this is a 500 and the whole turn is lost.
    assert response.status_code == 200
    assert response.json()["result"]["response"] == "ok"
