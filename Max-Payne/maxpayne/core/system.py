"""System and subprocess helpers used by checks."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import platform
import shutil
import subprocess
from typing import Sequence

logger = logging.getLogger(__name__)

SUBPROCESS_TIMEOUT_SECONDS = 3


@dataclass(slots=True)
class CommandResult:
    """Normalized subprocess execution result."""

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None


def command_exists(*names: str) -> tuple[bool, str | None]:
    """Return whether any command name is available on PATH."""
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return True, name
    return False, None


def run_command(command: Sequence[str], timeout: int = SUBPROCESS_TIMEOUT_SECONDS) -> CommandResult:
    """Run a subprocess command with timeout and safe error handling."""
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
    except subprocess.TimeoutExpired as exc:
        logger.debug("Command timed out after %ss: %s", timeout, command)
        return CommandResult(
            returncode=None,
            stdout=(exc.stdout or "").strip(),
            stderr=(exc.stderr or "").strip(),
            timed_out=True,
            error=f"Timed out after {timeout} seconds.",
        )
    except FileNotFoundError:
        return CommandResult(
            returncode=None,
            stdout="",
            stderr="",
            error="Executable not found.",
        )
    except OSError as exc:
        return CommandResult(
            returncode=None,
            stdout="",
            stderr="",
            error=str(exc),
        )


def detect_platform() -> tuple[str, bool]:
    """Return platform label and whether current Linux is WSL."""
    system = platform.system()
    if system != "Linux":
        return system, False

    release = platform.release().lower()
    version = platform.version().lower()
    is_wsl = "microsoft" in release or "microsoft" in version
    return system, is_wsl
