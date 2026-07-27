"""Port occupancy checks."""

from __future__ import annotations

from maxpayne.core.result import CheckResult

COMMON_PORTS = [3000, 5000, 8000, 8037, 8080, 11434]


def _scan_ports() -> tuple[dict[int, tuple[int | None, str | None]], str | None]:
    try:
        import psutil
    except Exception as exc:  # pragma: no cover - dependency failure path
        return {}, f"psutil unavailable: {exc}"

    by_port: dict[int, tuple[int | None, str | None]] = {}
    try:
        connections = psutil.net_connections(kind="inet")
    except Exception as exc:  # pragma: no cover - system permission/runtime path
        return {}, str(exc)

    for connection in connections:
        if connection.status != psutil.CONN_LISTEN or not connection.laddr:
            continue

        port = connection.laddr.port
        if port not in COMMON_PORTS or port in by_port:
            continue

        pid = connection.pid
        process_name: str | None = None
        if pid:
            try:
                process_name = psutil.Process(pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                process_name = "unknown"

        by_port[port] = (pid, process_name)

    return by_port, None


def run_ports_checks() -> list[CheckResult]:
    listening, scan_error = _scan_ports()

    results: list[CheckResult] = []
    for port in COMMON_PORTS:
        occupancy = listening.get(port)
        occupied = occupancy is not None

        if scan_error:
            results.append(
                CheckResult(
                    name=f"ports.{port}",
                    status="WARN",
                    message=f"Port {port} could not be inspected.",
                    suggestion="Install/enable psutil permissions, then rerun `maxpayne ports`.",
                    details=scan_error,
                )
            )
            continue

        if not occupied:
            results.append(
                CheckResult(
                    name=f"ports.{port}",
                    status="PASS",
                    message=f"Port {port} is available.",
                    suggestion="No action required.",
                )
            )
            continue

        pid, process_name = occupancy
        results.append(
            CheckResult(
                name=f"ports.{port}",
                status="WARN",
                message=f"Port {port} is occupied.",
                suggestion="Stop the process using this port or change your app port.",
                details=f"PID: {pid or 'unknown'}, Process: {process_name or 'unknown'}",
            )
        )

    return results
