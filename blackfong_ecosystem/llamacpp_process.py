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

logger = logging.getLogger(__name__)


def _is_server_already_listening(base_url: str, timeout: float = 1.0) -> bool:
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(f"{base_url}/health")
            return response.status_code == 200
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

    @property
    def result(self) -> Dict[str, Any]:
        return dict(self._result)

    def _emit(self, status: Dict[str, Any]) -> None:
        self._result = status
        if self._on_status is not None:
            self._on_status(status)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not llamacpp_settings.enabled:
            self._emit({"status": "disabled"})
            return
        if not llamacpp_settings.is_configured():
            self._emit(
                {
                    "status": "unconfigured",
                    "message": "llama-server binary or model GGUF not found; D.A.I.S.E. will use Ollama instead.",
                    "binary_path": llamacpp_settings.binary_path,
                    "model_path": llamacpp_settings.model_path,
                }
            )
            return
        self._emit({"status": "starting", "message": "Starting local llama.cpp server for D.A.I.S.E.…"})
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        base_url = llamacpp_settings.base_url
        if _is_server_already_listening(base_url):
            self._emit({"status": "ready", "message": "llama.cpp server already running", "base_url": base_url, "reused_existing": True})
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
            self._emit({"status": "error", "error": str(exc), "message": "Failed to launch llama-server; D.A.I.S.E. will use Ollama instead."})
            return

        deadline = started + llamacpp_settings.startup_timeout
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
            time.sleep(1.0)

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
        self._terminate()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)
