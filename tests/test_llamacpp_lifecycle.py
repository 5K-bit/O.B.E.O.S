from types import SimpleNamespace

import pytest

from blackfong_ecosystem import llamacpp_process as lifecycle


class StopAfter:
    def __init__(self, count):
        self.count = count
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.count -= 1
        self.stopped = self.count <= 0
        return self.stopped


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "paths", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(lifecycle, "llamacpp_settings", SimpleNamespace(
        port=8091, base_url="http://local", model_alias="expected", startup_timeout=10))
    instance = lifecycle.LlamaCppServerManager()
    instance._stop_requested = StopAfter(12)
    return instance


def test_owned_crash_has_exactly_one_restart(manager, monkeypatch):
    launches = []
    def launch():
        launches.append(True)
        manager._process = SimpleNamespace(poll=lambda: 1)
        manager._emit({"status": "ready"})
    monkeypatch.setattr(manager, "_launch_once", launch)
    monkeypatch.setattr(lifecycle, "_is_server_already_listening", lambda *a: False)
    manager._run()
    assert len(launches) == 2
    assert manager.result["status"] == "failed"


def test_external_failure_is_not_restarted(manager, monkeypatch):
    clock = iter(range(0, 100, 5))
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: next(clock))
    launches = []
    def launch():
        launches.append(True)
        manager._emit({"status": "ready", "reused_existing": True})
    monkeypatch.setattr(manager, "_launch_once", launch)
    monkeypatch.setattr(lifecycle, "_is_server_already_listening", lambda *a: False)
    manager._run()
    assert len(launches) == 1
    assert manager.result["status"] == "failed"


def test_transient_external_outage_recovers_without_restart(manager, monkeypatch):
    states = []
    manager._on_status = lambda state: states.append(state["status"])
    monkeypatch.setattr(manager, "_launch_once", lambda: manager._emit({"status": "ready"}))
    probes = iter([False, False, False, True] + [True] * 20)
    monkeypatch.setattr(lifecycle, "_is_server_already_listening", lambda *a: next(probes))
    manager._run()
    assert states[:3] == ["ready", "recovering", "ready"]
    assert states[-1] == "stopped"


def test_observer_exception_does_not_break_supervisor():
    def broken(status):
        raise ValueError("UI observer failed")
    manager = lifecycle.LlamaCppServerManager(on_status=broken)
    manager._emit({"status": "ready"})
    assert manager.is_ready()


def test_closing_surface_does_not_terminate_shared_server(monkeypatch):
    manager = lifecycle.LlamaCppServerManager()
    manager._emit({"status": "ready"})
    monkeypatch.setattr(manager, "_terminate", lambda: pytest.fail("Shared server killed"))
    manager.stop()


def test_os_lease_excludes_second_owner(tmp_path):
    from blackfong_ecosystem.earth.lease import RuntimeLease
    first = RuntimeLease(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            RuntimeLease(tmp_path)
    finally:
        first.close()
    RuntimeLease(tmp_path).close()


def test_warmup_wait_is_cancellable(monkeypatch):
    from blackfong_ecosystem.cancellation import CancellationToken, RequestCancelled, cancellation_scope
    token = CancellationToken()
    monkeypatch.setattr(lifecycle, "_shared_manager", SimpleNamespace(result={"status": "loading"}))
    monkeypatch.setattr(lifecycle.time, "sleep", lambda seconds: token.cancel())
    with cancellation_scope(token):
        with pytest.raises(RequestCancelled):
            lifecycle.wait_for_local_model()


def test_failed_startup_does_not_spin(monkeypatch):
    from blackfong_ecosystem.chat_backends import ChatBackendError
    monkeypatch.setattr(lifecycle, "_shared_manager", SimpleNamespace(result={"status": "failed"}))
    with pytest.raises(ChatBackendError) as caught:
        lifecycle.wait_for_local_model()
    assert caught.value.metadata["error_type"] == "startup_failed"


def test_existing_endpoint_does_not_require_launch_files(manager, monkeypatch):
    monkeypatch.setattr(lifecycle, "_is_server_already_listening", lambda *a: True)
    monkeypatch.setattr(lifecycle.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    manager._launch_once()
    assert manager.is_ready()
    assert manager.result["reused_existing"] is True


def test_missing_files_fail_only_when_endpoint_unavailable(manager, monkeypatch):
    monkeypatch.setattr(lifecycle, "_is_server_already_listening", lambda *a: False)
    lifecycle.llamacpp_settings.is_configured = lambda: False
    manager._launch_once()
    assert manager.result["error_type"] == "launch_files_missing"


def test_wrong_endpoint_never_launches_or_terminates(manager, monkeypatch):
    from blackfong_ecosystem.chat_backends import ChatBackendError
    def mismatch(*args):
        raise ChatBackendError("Wrong model", metadata={"error_type": "model_identity_mismatch"})
    monkeypatch.setattr(lifecycle, "_is_server_already_listening", mismatch)
    monkeypatch.setattr(lifecycle.subprocess, "Popen", lambda *a, **kw: pytest.fail("Unexpected launch"))
    monkeypatch.setattr(manager, "_terminate", lambda: pytest.fail("Unexpected termination"))
    manager._run()
    assert manager.result["status"] == "failed"
    assert manager.result["error_type"] == "model_identity_mismatch"
