import importlib.util
from pathlib import Path
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location("fixture_delivery", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_standalone_delivery_implementations_are_identical():
    assert (ROOT / "3000-main/three_thousand/background_delivery.py").read_bytes() == (
        ROOT / "Night-watch-OS/nightwatch/background_delivery.py"
    ).read_bytes()


def test_delivery_is_bounded_nonblocking_and_retries_same_envelope(tmp_path):
    module = load(ROOT / "3000-main/three_thousand/background_delivery.py")
    blocked = threading.Event()
    calls = []

    def sender(endpoint, payload):
        calls.append(payload)
        blocked.wait(0.2)
        if len(calls) == 1:
            raise OSError("offline")

    delivery = module.BackgroundDelivery(
        tmp_path / "delivery.sqlite3", capacity=1, sender=sender
    )
    started = time.monotonic()
    assert delivery.enqueue(
        "http://127.0.0.1", {"event_id": "one", "payload": {"value": 1}}
    )
    assert time.monotonic() - started < 0.1
    assert not delivery.enqueue("http://127.0.0.1", {"event_id": "two"})
    blocked.set()
    deadline = time.monotonic() + 3
    while delivery.status()["pending"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert delivery.status()["pending"] == 0
    delivery.close()


def test_delivery_restarts_without_new_enqueues(tmp_path):
    module = load(ROOT / "3000-main/three_thousand/background_delivery.py")
    path = tmp_path / "restart.sqlite3"
    first = module.BackgroundDelivery(
        path, sender=lambda *_: (_ for _ in ()).throw(OSError("offline"))
    )
    first.enqueue("http://127.0.0.1", {"event_id": "persisted"})
    first.close(timeout=0)
    received = []
    second = module.BackgroundDelivery(
        path, sender=lambda endpoint, payload: received.append(payload)
    )
    try:
        deadline = time.monotonic() + 3
        while second.status()["pending"] and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(received) == 1 and "persisted" in received[0]
    finally:
        second.close()


def test_queue_storage_failure_is_visible_and_does_not_escape(tmp_path):
    module = load(ROOT / "3000-main/three_thousand/background_delivery.py")
    delivery = module.BackgroundDelivery(tmp_path / "readonly.sqlite3")
    try:
        with delivery._lock:
            delivery._con.execute("PRAGMA query_only=ON")
        assert not delivery.enqueue("http://127.0.0.1", {"event_id": "one"})
        status = delivery.status()
        assert status["state"] == "degraded"
        assert status["rejected"] == 1
        assert status["last_error"] == "OperationalError"
    finally:
        delivery.close()
