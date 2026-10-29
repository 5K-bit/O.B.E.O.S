"""Poll the first-party OBEOS pieces and aggregate them via MaxPayne's OBEOS Health Contract v1.

MaxPayne's ``OBEOSHealthAdapter`` (Max-Payne/maxpayne/integrations/obeos.py) already
implements the aggregation contract; nothing in OBEOS actually called it. This module
is the missing caller: it runs each project's declared ``health_checks`` from
``obeos_ecosystem.toml`` and hands the results to MaxPayne to aggregate.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ecosystem_manifest import iter_ecosystem_projects
from .http_clients import client_for


def _subprocess_env() -> Dict[str, str]:
    """Env with this interpreter's own Scripts/bin dir on PATH.

    The submodule console scripts (``3000``, ``quietdrop``, ``nightwatch``, ...) are
    installed into this venv; a caller that reached this code without activating the
    venv first would otherwise not find them on PATH.
    """
    env = dict(os.environ)
    bin_dir = str(Path(sys.executable).parent)
    env["PATH"] = os.pathsep.join([bin_dir, env.get("PATH", "")])
    return env


def _run_command_check(check: Dict[str, Any], *, workspace_root: Path) -> bool:
    command = str(check.get("command") or "")
    if not command:
        return False
    cwd = workspace_root / str(check.get("cwd") or ".")
    timeout = float(check.get("timeout_seconds") or 10)
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            env=_subprocess_env(),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return False


def _run_path_exists_check(check: Dict[str, Any], *, workspace_root: Path) -> bool:
    rel_path = str(check.get("path") or "")
    if not rel_path:
        return False
    return (workspace_root / rel_path).exists()


def _run_http_check(check: Dict[str, Any]) -> Optional[bool]:
    url = str(check.get("url") or "")
    if not url:
        return False
    timeout = float(check.get("timeout_seconds") or 2)
    try:
        with client_for(url, timeout=timeout, trust_env=False) as client:
            response = client.get(url)
            return response.status_code < 500
    except Exception:
        return None if check.get("optional") else False


def _project_health_payload(project: Dict[str, Any], *, workspace_root: Path) -> Dict[str, Any]:
    name = str(project.get("id") or project.get("path") or "unknown")
    if not project.get("available"):
        return {"name": name, "status": "down", "details": {"reason": "project directory not found"}}

    checks = project.get("health_checks") or []
    results: List[Dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        check_type = str(check.get("type") or "")
        check_name = str(check.get("name") or check_type)
        if check_type == "command":
            ok: Optional[bool] = _run_command_check(check, workspace_root=workspace_root)
        elif check_type == "path_exists":
            ok = _run_path_exists_check(check, workspace_root=workspace_root)
        elif check_type == "http":
            ok = _run_http_check(check)
        else:
            continue
        results.append({"name": check_name, "ok": ok})

    if not results:
        status = "unknown"
    elif all(item["ok"] is True for item in results):
        status = "ok"
    elif any(item["ok"] is False for item in results):
        status = "degraded"
    else:
        status = "unknown"

    return {
        "name": name,
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "details": {"checks": results},
        "warnings": [item["name"] for item in results if item["ok"] is False],
    }


def poll_ecosystem_components(*, workspace_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Run every non-core project's declared health_checks and shape the results."""
    root = Path(workspace_root) if workspace_root else None
    from .config import paths

    root = root or paths.root
    payloads = []
    for project in iter_ecosystem_projects(root):
        if project.get("id") == "maxpayne":
            continue  # MaxPayne reports its own health directly in aggregate().
        payloads.append(_project_health_payload(project, workspace_root=root))
    return payloads


def aggregate_ecosystem_health(*, workspace_root: Optional[Path] = None, publish: bool = False) -> Dict[str, Any]:
    """Poll the ecosystem and aggregate through MaxPayne's OBEOS Health Contract v1."""
    try:
        from maxpayne.integrations.obeos import OBEOSHealthAdapter
    except ImportError as exc:
        raise RuntimeError(
            "MaxPayne is not installed in this environment; run "
            "`pip install -e ./Max-Payne` to enable ecosystem health aggregation."
        ) from exc

    component_payloads = poll_ecosystem_components(workspace_root=workspace_root)
    adapter = OBEOSHealthAdapter()
    return adapter.aggregate(component_payloads, include_maxpayne=True, record=False, publish=publish)
