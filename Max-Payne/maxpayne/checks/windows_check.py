"""Windows-focused environment checks."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from maxpayne.core.result import CheckResult
from maxpayne.core.system import command_exists, detect_platform, run_command


def _non_windows_result(name: str) -> CheckResult:
    return CheckResult(
        name=name,
        status="PASS",
        message="Check skipped on non-Windows platform.",
        suggestion="No action required.",
    )


def _check_wsl_installed() -> CheckResult:
    available, _ = command_exists("wsl", "wsl.exe")
    return CheckResult(
        name="windows.wsl_installed",
        status="PASS" if available else "WARN",
        message="WSL command is available." if available else "WSL command was not found.",
        suggestion=(
            "No action required." if available else "Install WSL (`wsl --install`) if you use Linux toolchains."
        ),
    )


def _check_wsl_distro_status() -> CheckResult:
    status = run_command(["wsl", "-l", "-v"])
    if status.error:
        return CheckResult(
            name="windows.wsl_distro_status",
            status="WARN",
            message="Unable to inspect WSL distro status.",
            suggestion="Run `wsl -l -v` manually and verify your distro is running.",
            details=status.error,
        )

    if status.timed_out:
        return CheckResult(
            name="windows.wsl_distro_status",
            status="WARN",
            message="WSL distro status check timed out.",
            suggestion="Retry `wsl -l -v` and verify WSL service health.",
        )

    running = "running" in status.stdout.lower()
    return CheckResult(
        name="windows.wsl_distro_status",
        status="PASS" if running else "WARN",
        message=("At least one WSL distro is running." if running else "No running WSL distro detected."),
        suggestion=(
            "No action required." if running else "Start a distro (`wsl`) or set a default distro (`wsl --set-default <name>`)."
        ),
        details=status.stdout or None,
    )


def _check_path_pollution() -> CheckResult:
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    normalized: set[str] = set()
    duplicate_count = 0
    missing_count = 0

    for entry in path_entries:
        clean = entry.strip()
        if not clean:
            continue
        normalized_entry = clean.casefold()
        if normalized_entry in normalized:
            duplicate_count += 1
        else:
            normalized.add(normalized_entry)

        if not Path(clean).exists():
            missing_count += 1

    healthy = duplicate_count == 0 and missing_count == 0
    return CheckResult(
        name="windows.path_pollution",
        status="PASS" if healthy else "WARN",
        message=(
            "PATH entries look healthy."
            if healthy
            else "PATH contains duplicate or missing entries."
        ),
        suggestion=(
            "No action required."
            if healthy
            else "Remove duplicate PATH entries and clean non-existent directories."
        ),
        details=f"duplicates={duplicate_count}, missing={missing_count}",
    )


def _check_visual_cpp_runtime() -> CheckResult:
    windir = Path(os.environ.get("WINDIR", "C:/Windows"))
    required = [windir / "System32" / "vcruntime140.dll", windir / "System32" / "msvcp140.dll"]
    missing = [str(path) for path in required if not path.exists()]

    runtime_ok = not missing
    return CheckResult(
        name="windows.visual_cpp_runtime",
        status="PASS" if runtime_ok else "WARN",
        message=(
            "Visual C++ runtime DLLs were found."
            if runtime_ok
            else "Visual C++ runtime appears incomplete."
        ),
        suggestion=(
            "No action required."
            if runtime_ok
            else "Install or repair the Microsoft Visual C++ Redistributable package."
        ),
        details=(", ".join(missing) if missing else None),
    )


def _check_powershell_execution_policy() -> CheckResult:
    policy = run_command(["powershell", "-NoProfile", "-Command", "Get-ExecutionPolicy", "-Scope", "CurrentUser"])
    if policy.error:
        return CheckResult(
            name="windows.powershell_execution_policy",
            status="WARN",
            message="Unable to read PowerShell execution policy.",
            suggestion="Run PowerShell as user and inspect `Get-ExecutionPolicy -Scope CurrentUser`.",
            details=policy.error,
        )

    current = policy.stdout.strip() or "Unknown"
    restricted = current.lower() == "restricted"
    return CheckResult(
        name="windows.powershell_execution_policy",
        status="PASS" if not restricted else "WARN",
        message=f"CurrentUser execution policy is {current}.",
        suggestion=(
            "No action required."
            if not restricted
            else "Set a less restrictive policy for dev scripts, e.g. `RemoteSigned`."
        ),
    )


def _check_side_by_side_runtime() -> CheckResult:
    probe = run_command([sys.executable, "-c", "import ctypes; ctypes.CDLL('vcruntime140.dll')"])
    healthy = probe.returncode == 0 and not probe.error and not probe.timed_out
    return CheckResult(
        name="windows.side_by_side_runtime",
        status="PASS" if healthy else "WARN",
        message=(
            "Side-by-side runtime probe succeeded."
            if healthy
            else "Side-by-side runtime probe failed."
        ),
        suggestion=(
            "No action required." if healthy else "Repair Visual C++ runtime installation and rerun diagnostics."
        ),
        details=(probe.stderr or probe.error or None),
    )


def _check_broken_symlinks() -> CheckResult:
    root = Path.cwd()
    broken_links: list[str] = []

    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            if not path.resolve(strict=True).exists():
                broken_links.append(str(path))
        except FileNotFoundError:
            broken_links.append(str(path))
        except OSError:
            continue

    healthy = not broken_links
    return CheckResult(
        name="windows.broken_symlinks",
        status="PASS" if healthy else "WARN",
        message=("No broken symlinks found in workspace." if healthy else "Broken symlinks detected in workspace."),
        suggestion=(
            "No action required." if healthy else "Remove or repair broken symlinks to avoid toolchain confusion."
        ),
        details=(", ".join(broken_links[:5]) if broken_links else None),
    )


def _check_pip_launcher() -> CheckResult:
    pip_status = run_command(["pip", "--version"])
    healthy = pip_status.returncode == 0 and not pip_status.error and not pip_status.timed_out
    return CheckResult(
        name="windows.pip_launcher",
        status="PASS" if healthy else "WARN",
        message=("pip launcher appears healthy." if healthy else "pip launcher appears unhealthy."),
        suggestion=(
            "No action required." if healthy else "Repair Python install or run `python -m ensurepip --upgrade` if available."
        ),
        details=(pip_status.stderr or pip_status.error or None),
    )


def _check_python_launcher_mismatch() -> CheckResult:
    launcher = run_command(["py", "-0p"])
    if launcher.error:
        return CheckResult(
            name="windows.python_launcher_mismatch",
            status="WARN",
            message="Python launcher `py` is unavailable.",
            suggestion="Install or repair Python launcher on Windows.",
            details=launcher.error,
        )

    current = Path(sys.executable).as_posix().lower()
    mismatch = current not in launcher.stdout.replace("\\", "/").lower()
    return CheckResult(
        name="windows.python_launcher_mismatch",
        status="PASS" if not mismatch else "WARN",
        message=(
            "Python launcher matches active interpreter."
            if not mismatch
            else "Python launcher may target a different interpreter."
        ),
        suggestion=(
            "No action required." if not mismatch else "Align `py` defaults with the interpreter used by this project."
        ),
    )


def run_windows_checks() -> list[CheckResult]:
    system_name, _ = detect_platform()
    if system_name != "Windows":
        return [
            _non_windows_result("windows.wsl_installed"),
            _non_windows_result("windows.wsl_distro_status"),
            _non_windows_result("windows.path_pollution"),
            _non_windows_result("windows.visual_cpp_runtime"),
            _non_windows_result("windows.powershell_execution_policy"),
            _non_windows_result("windows.side_by_side_runtime"),
            _non_windows_result("windows.broken_symlinks"),
            _non_windows_result("windows.pip_launcher"),
            _non_windows_result("windows.python_launcher_mismatch"),
        ]

    checks = [
        _check_wsl_installed,
        _check_wsl_distro_status,
        _check_path_pollution,
        _check_visual_cpp_runtime,
        _check_powershell_execution_policy,
        _check_side_by_side_runtime,
        _check_broken_symlinks,
        _check_pip_launcher,
        _check_python_launcher_mismatch,
    ]
    return [check() for check in checks]
