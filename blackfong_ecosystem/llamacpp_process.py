"""Launch and supervise the dedicated D.A.I.S.E. llama.cpp server process."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import httpx

from .config import llamacpp_settings, paths
from .model_readiness import verify_llamacpp
from .chat_backends import ChatBackendError

logger = logging.getLogger(__name__)
_shared_manager = None
_shared_manager_lock = threading.Lock()


def wait_for_local_model() -> Dict[str, Any]:
    """Wait on the request worker, never during shell/widget construction."""
    from .cancellation import raise_if_cancelled
    from .chat_backends import ChatBackendError

    global _shared_manager
    with _shared_manager_lock:
        if _shared_manager is None:
            _shared_manager = LlamaCppServerManager()
            _shared_manager.start()
        manager = _shared_manager
    deadline = time.monotonic() + llamacpp_settings.startup_timeout
    while time.monotonic() < deadline:
        raise_if_cancelled()
        state = manager.result
        if state.get("status") == "ready":
            return state
        if state.get("status") in {"error", "failed", "unconfigured", "disabled", "stopped"}:
            break
        time.sleep(0.1)
    raise ChatBackendError("D.A.I.S.E. model did not become ready within the startup deadline",
                           metadata={"provider": "llamacpp", "model": llamacpp_settings.model_alias,
                                     "error_type": "startup_failed", "lifecycle": manager.result})


def _is_server_already_listening(base_url: str, timeout: float = 1.0) -> bool:
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            verify_llamacpp(client, base_url, llamacpp_settings.model_alias)
            return True
    except ChatBackendError as exc:
        if exc.metadata.get("error_type") == "model_identity_mismatch":
            raise
        return False
    except Exception:
        return False


class LlamaCppServerManager:
    """Starts llama-server for D.A.I.S.E.'s model and keeps it running for the app lifetime.

    Unlike Ollama, llama-server keeps its one model resident for the life of the
    process, so there is no keep-alive/idle-unload concept to manage here.
    """

    def __init__(self, *, on_status: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self._on_status = on_status
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_requested = threading.Event()
        self._result: Dict[str, Any] = {"status": "idle"}
        self._start_lock = threading.Lock()

    @property
    def result(self) -> Dict[str, Any]:
        return dict(self._result)

    def _emit(self, status: Dict[str, Any]) -> None:
        self._result = status
        if self._on_status is not None:
            try:
                self._on_status(dict(status))
            except Exception:
                logger.exception("Model status observer failed")

    def start(self) -> None:
        with self._start_lock:
            self._start()

    def _start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not llamacpp_settings.enabled:
            self._emit({"status": "disabled"})
            return
        self._emit({"status": "starting", "message": "Starting local llama.cpp server for D.A.I.S.E.…"})
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        # Serialize launches across desktop, Earth and backend processes. The
        # operating system releases this lease if the supervisor crashes.
        from .earth.lease import RuntimeLease

        lease = None
        deadline = time.monotonic() + llamacpp_settings.startup_timeout
        try:
            while lease is None and not self._stop_requested.is_set():
                try:
                    lease = RuntimeLease(paths.data_root / "model-runtime" / str(llamacpp_settings.port))
                except RuntimeError:
                    if _is_server_already_listening(llamacpp_settings.base_url):
                        deadline = time.monotonic() + llamacpp_settings.startup_timeout
                        self._emit({"status": "ready", "reused_existing": True,
                                    "model": llamacpp_settings.model_alias})
                    elif time.monotonic() >= deadline:
                        self._emit({"status": "failed", "message": "Shared model startup deadline exceeded"})
                        return
                    else:
                        self._emit({"status": "loading", "message": "Waiting for shared model owner"})
                    self._stop_requested.wait(1.0)
            if lease is None:
                return
            self._launch_once()
            if not self.is_ready():
                return
            restarts = 0
            failures = 0
            recovery_deadline = None
            while not self._stop_requested.wait(2.0):
                if _is_server_already_listening(llamacpp_settings.base_url):
                    failures = 0
                    recovery_deadline = None
                    if not self.is_ready():
                        self._emit({"status": "ready", "model": llamacpp_settings.model_alias,
                                    "recovery_reason": "verified_endpoint_recovery"})
                    continue
                failures += 1
                if failures < 3:
                    continue
                # Only restart a process we launched and which actually exited.
                # A slow/busy external server must never be killed by a surface.
                if self._process is None or self._process.poll() is None:
                    if recovery_deadline is None:
                        recovery_deadline = time.monotonic() + llamacpp_settings.startup_timeout
                        self._emit({"status": "recovering", "message": "Waiting for endpoint recovery; process left untouched"})
                    elif time.monotonic() >= recovery_deadline:
                        self._emit({"status": "failed", "message": "Endpoint recovery deadline exceeded; process left untouched"})
                        return
                    continue
                if restarts:
                    self._emit({"status": "failed", "message": "Owned model restart budget exhausted"})
                    return
                restarts += 1
                self._emit({"status": "recovering", "restart_attempt": restarts})
                self._launch_once()
                if not self.is_ready():
                    return
                failures = 0
        except Exception as exc:
            logger.exception("Model supervision failed")
            self._emit({"status": "failed", "error": str(exc),
                        "error_type": getattr(exc, "metadata", {}).get("error_type", "supervision_failed")})
        finally:
            if lease is not None:
                lease.close()
            if self._stop_requested.is_set():
                self._emit({"status": "stopped"})

    def _launch_once(self) -> None:
        base_url = llamacpp_settings.base_url
        if _is_server_already_listening(base_url):
            self._emit({"status": "ready", "message": "llama.cpp server already running", "base_url": base_url, "reused_existing": True})
            return

        if not llamacpp_settings.is_configured():
            self._emit({"status": "failed", "error_type": "launch_files_missing",
                        "message": "No verified model endpoint and local launch files are unavailable."})
            return

        log_path = paths.data_root / "logs" / "llamacpp-server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            llamacpp_settings.binary_path,
            "-m", llamacpp_settings.model_path,
            "-c", str(llamacpp_settings.context_size),
            "-t", str(llamacpp_settings.threads),
            "--host", llamacpp_settings.host,
            "--port", str(llamacpp_settings.port),
            "--alias", llamacpp_settings.model_alias,
            "--reasoning-budget", str(llamacpp_settings.reasoning_budget),
        ]
        if llamacpp_settings.mmproj_path and Path(llamacpp_settings.mmproj_path).is_file():
            args += ["--mmproj", llamacpp_settings.mmproj_path]

        started = time.perf_counter()
        try:
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"\n--- launching llama-server {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                self._process = subprocess.Popen(
                    args,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
        except Exception as exc:
            logger.warning("Failed to launch llama-server: %s", exc)
            self._emit({"status": "error", "error": str(exc), "message": "Failed to launch the configured D.A.I.S.E. model server."})
            return

        deadline = started + llamacpp_settings.startup_timeout
        self._emit({"status": "loading", "model": llamacpp_settings.model_alias})
        while time.perf_counter() < deadline:
            if self._stop_requested.is_set():
                self._terminate()
                self._emit({"status": "stopped"})
                return
            if self._process.poll() is not None:
                self._emit(
                    {
                        "status": "error",
                        "message": f"llama-server exited early (code {self._process.returncode}); see {log_path}",
                        "log_path": str(log_path),
                    }
                )
                return
            if _is_server_already_listening(base_url):
                self._emit(
                    {
                        "status": "ready",
                        "base_url": base_url,
                        "model": llamacpp_settings.model_alias,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                    }
                )
                return
            self._stop_requested.wait(1.0)

        self._terminate()
        self._emit({"status": "error", "message": f"llama-server did not become ready within {llamacpp_settings.startup_timeout:.0f}s", "log_path": str(log_path)})

    def is_ready(self) -> bool:
        return self._result.get("status") == "ready"

    def _terminate(self) -> None:
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=10)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
        self._process = None

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_requested.set()
        # A ready server can be serving another surface. Detach the supervisor;
        # never tear down shared inference just because one window closes.
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)
