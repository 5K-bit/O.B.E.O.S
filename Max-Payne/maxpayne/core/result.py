"""Result type returned by health checks."""

from dataclasses import dataclass
from typing import Literal

Status = Literal["PASS", "WARN", "FAIL"]


@dataclass(slots=True)
class CheckResult:
    """A single diagnostic check outcome."""

    name: str
    status: Status
    message: str
    suggestion: str
    details: str | None = None
