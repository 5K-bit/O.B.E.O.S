"""Python and pip checks."""

from __future__ import annotations

import sys

from maxpayne.core.result import CheckResult
from maxpayne.core.system import command_exists

MIN_PYTHON = (3, 11)


def run_python_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    version_ok = sys.version_info >= MIN_PYTHON
    current_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    required_version = ".".join(str(part) for part in MIN_PYTHON)
    results.append(
        CheckResult(
            name="python.version",
            status="PASS" if version_ok else "FAIL",
            message=f"Python version is {current_version}.",
            suggestion=(
                "No action required."
                if version_ok
                else f"Upgrade to Python {required_version} or newer."
            ),
        )
    )

    pip_available, pip_command = command_exists("pip", "pip3", "pip.exe")
    results.append(
        CheckResult(
            name="python.pip",
            status="PASS" if pip_available else "FAIL",
            message=(
                f"pip is available via `{pip_command}`."
                if pip_available
                else "pip was not found in PATH."
            ),
            suggestion=(
                "No action required."
                if pip_available
                else "Install pip and ensure it is available in your PATH."
            ),
        )
    )

    return results
