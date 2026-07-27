"""Project and environment checks."""

from __future__ import annotations

from pathlib import Path

from maxpayne.core.result import CheckResult
from maxpayne.core.system import detect_platform


def run_env_checks() -> list[CheckResult]:
    cwd = Path.cwd()
    env_file = cwd / ".env"
    env_example_file = cwd / ".env.example"
    pyproject_file = cwd / "pyproject.toml"
    requirements_file = cwd / "requirements.txt"

    system_name, is_wsl = detect_platform()
    platform_suffix = " (WSL)" if is_wsl else ""

    env_example_ok = not env_file.exists() or env_example_file.exists()
    dependencies_file_ok = pyproject_file.exists() or requirements_file.exists()

    results = [
        CheckResult(
            name="system.platform",
            status="PASS",
            message=f"Detected platform: {system_name}{platform_suffix}.",
            suggestion="No action required.",
        ),
        CheckResult(
            name="env.example",
            status="PASS" if env_example_ok else "WARN",
            message=(
                ".env.example is present (or no .env detected)."
                if env_example_ok
                else ".env exists but .env.example is missing."
            ),
            suggestion=(
                "No action required."
                if env_example_ok
                else "Add a sanitized `.env.example` for onboarding and CI clarity."
            ),
        ),
        CheckResult(
            name="env.dependencies_file",
            status="PASS" if dependencies_file_ok else "FAIL",
            message=(
                "Project dependency file found."
                if dependencies_file_ok
                else "Neither pyproject.toml nor requirements.txt was found."
            ),
            suggestion=(
                "No action required."
                if dependencies_file_ok
                else "Add `pyproject.toml` or `requirements.txt` to define dependencies."
            ),
        ),
    ]

    return results
