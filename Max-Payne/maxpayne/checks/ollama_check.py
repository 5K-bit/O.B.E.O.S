"""Ollama checks."""

from __future__ import annotations

import socket

from maxpayne.core.result import CheckResult
from maxpayne.core.system import command_exists

OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434
SOCKET_TIMEOUT_SECONDS = 1.0


def _is_ollama_server_reachable() -> tuple[bool, str | None]:
    try:
        with socket.create_connection((OLLAMA_HOST, OLLAMA_PORT), timeout=SOCKET_TIMEOUT_SECONDS):
            return True, None
    except TimeoutError:
        return False, "Connection timed out while checking localhost:11434."
    except OSError as exc:
        return False, str(exc)


def run_ollama_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    ollama_available, ollama_command = command_exists("ollama", "ollama.exe")
    results.append(
        CheckResult(
            name="ollama.available",
            status="PASS" if ollama_available else "FAIL",
            message=(
                f"ollama CLI is available via `{ollama_command}`."
                if ollama_available
                else "ollama CLI was not found in PATH."
            ),
            suggestion=(
                "No action required."
                if ollama_available
                else "Install Ollama and ensure `ollama` is available in your PATH."
            ),
        )
    )

    server_running, error = _is_ollama_server_reachable()
    results.append(
        CheckResult(
            name="ollama.server",
            status="PASS" if server_running else "WARN",
            message=(
                f"Ollama server is reachable at {OLLAMA_HOST}:{OLLAMA_PORT}."
                if server_running
                else f"Ollama server is not reachable at {OLLAMA_HOST}:{OLLAMA_PORT}."
            ),
            suggestion=(
                "No action required."
                if server_running
                else "Start Ollama (`ollama serve`) before running local models."
            ),
            details=error,
        )
    )

    return results
