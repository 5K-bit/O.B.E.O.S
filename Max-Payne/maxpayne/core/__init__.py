"""Core domain primitives for MaxPayne."""

from .result import CheckResult
from .runner import CheckRunner
from .system import CommandResult, command_exists, detect_platform, run_command

__all__ = [
    "CheckResult",
    "CheckRunner",
    "CommandResult",
    "command_exists",
    "detect_platform",
    "run_command",
]
