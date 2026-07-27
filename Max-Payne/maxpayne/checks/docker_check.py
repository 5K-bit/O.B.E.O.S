"""Docker checks."""

from __future__ import annotations

from maxpayne.core.result import CheckResult
from maxpayne.core.system import command_exists, run_command


def run_docker_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    docker_available, docker_command = command_exists("docker", "docker.exe")
    results.append(
        CheckResult(
            name="docker.available",
            status="PASS" if docker_available else "FAIL",
            message=(
                f"docker CLI is available via `{docker_command}`."
                if docker_available
                else "docker CLI was not found in PATH."
            ),
            suggestion=(
                "No action required."
                if docker_available
                else "Install Docker and ensure `docker` is available in your PATH."
            ),
        )
    )

    if not docker_available:
        return results

    daemon_check = run_command(["docker", "info"])
    daemon_running = daemon_check.returncode == 0 and not daemon_check.timed_out and not daemon_check.error

    detail_parts = []
    if daemon_check.timed_out:
        detail_parts.append("docker info timed out after 3 seconds")
    if daemon_check.error:
        detail_parts.append(daemon_check.error)
    if daemon_check.stderr:
        detail_parts.append(daemon_check.stderr)

    results.append(
        CheckResult(
            name="docker.daemon",
            status="PASS" if daemon_running else "WARN",
            message=(
                "Docker daemon is running."
                if daemon_running
                else "Docker daemon appears to be unavailable."
            ),
            suggestion=(
                "No action required."
                if daemon_running
                else "Start Docker Desktop/service and run `docker info` again."
            ),
            details=" | ".join(detail_parts) or None,
        )
    )

    return results
