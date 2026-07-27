"""AI and heuristic explanation helpers for crash logs and tracebacks."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import socket
import urllib.error
import urllib.request

from maxpayne.core.system import command_exists


@dataclass(slots=True)
class ExplanationResult:
    source: str
    explanation: str


def _extract_error_line(content: str) -> str:
    for line in reversed(content.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if "error" in stripped.lower() or "exception" in stripped.lower() or "traceback" in stripped.lower():
            return stripped
    return "No explicit traceback line found."


def _heuristic_explanation(content: str) -> str:
    error_line = _extract_error_line(content)
    lowered = content.lower()

    if "modulenotfounderror" in lowered:
        return (
            f"I found a missing module error: '{error_line}'. It usually means a dependency is not installed "
            "in the active environment. Install the package listed in the traceback and rerun your command."
        )
    if "connectionrefusederror" in lowered or "failed to establish a new connection" in lowered:
        return (
            f"This looks like a service connectivity issue: '{error_line}'. Start the required local service "
            "(database/API/model server) and confirm host/port settings."
        )
    if "address already in use" in lowered:
        return (
            f"Port conflict detected: '{error_line}'. Another process is already bound to the same port. "
            "Run `maxpayne ports` or `maxpayne heal port <port>` to free it."
        )
    if "permissionerror" in lowered or "permission denied" in lowered:
        return (
            f"Permission issue detected: '{error_line}'. Check file permissions, shell elevation, and ownership "
            "for the path shown in the traceback."
        )
    if "filenotfounderror" in lowered:
        return (
            f"A required file was not found: '{error_line}'. Verify file paths, working directory, and generated "
            "artifacts before retrying."
        )

    return (
        f"Likely failure point: '{error_line}'. Review the last frames in the traceback, then validate dependencies, "
        "environment variables, and service availability."
    )


def _ollama_available() -> bool:
    exists, _ = command_exists("ollama", "ollama.exe")
    if not exists:
        return False

    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=1.0):
            return True
    except OSError:
        return False


def _query_ollama(prompt: str, model: str) -> str | None:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            body = json.loads(response.read().decode("utf-8"))
            text = body.get("response", "").strip()
            return text or None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def explain_text(content: str, model: str = "llama3.2") -> ExplanationResult:
    condensed = "\n".join(content.splitlines()[-120:])

    if _ollama_available():
        prompt = (
            "You are a senior developer assistant. Explain this traceback/log in plain English, "
            "identify likely root cause, and provide a concise fix plan.\n\n"
            f"Log:\n{condensed}\n"
        )
        ai_text = _query_ollama(prompt, model=model)
        if ai_text:
            return ExplanationResult(source="ollama", explanation=ai_text)

    return ExplanationResult(source="heuristic", explanation=_heuristic_explanation(content))


def explain_file(path: Path, model: str = "llama3.2") -> ExplanationResult:
    text = path.read_text(encoding="utf-8", errors="replace")
    return explain_text(text, model=model)
