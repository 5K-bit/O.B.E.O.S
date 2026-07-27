"""Git checks."""

from __future__ import annotations

from maxpayne.core.result import CheckResult
from maxpayne.core.system import command_exists, run_command


def _read_git_config(key: str) -> tuple[str | None, str | None]:
    completed = run_command(["git", "config", "--global", "--get", key])
    if completed.timed_out:
        return None, "Timed out reading git config."
    if completed.error:
        return None, completed.error
    if completed.returncode != 0:
        return None, completed.stderr or None

    value = completed.stdout.strip()
    return (value or None), None


def run_git_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    git_available, git_command = command_exists("git", "git.exe")
    results.append(
        CheckResult(
            name="git.available",
            status="PASS" if git_available else "FAIL",
            message=(
                f"git is available via `{git_command}`."
                if git_available
                else "git was not found in PATH."
            ),
            suggestion=(
                "No action required."
                if git_available
                else "Install git and ensure it is available in your PATH."
            ),
        )
    )

    if not git_available:
        return results

    git_name, git_name_error = _read_git_config("user.name")
    git_email, git_email_error = _read_git_config("user.email")
    identity_configured = bool(git_name and git_email)

    details_parts = [f"user.name={git_name or '<unset>'}", f"user.email={git_email or '<unset>'}"]
    if git_name_error:
        details_parts.append(f"name_error={git_name_error}")
    if git_email_error:
        details_parts.append(f"email_error={git_email_error}")

    results.append(
        CheckResult(
            name="git.identity",
            status="PASS" if identity_configured else "WARN",
            message=(
                "Global git identity is configured."
                if identity_configured
                else "Global git identity is incomplete or unavailable."
            ),
            suggestion=(
                "No action required."
                if identity_configured
                else "Run `git config --global user.name \"Your Name\"` and "
                "`git config --global user.email \"you@example.com\"`."
            ),
            details=", ".join(details_parts),
        )
    )

    return results
