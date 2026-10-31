"""Read-only diagnostics for the Blackfong Workspace.

Reports what the Workspace can actually observe about itself: versions, the configured
Event API endpoint, last health results, and port collisions declared in
``obeos_ecosystem.toml``. It never starts, stops or repairs anything.
"""

from __future__ import annotations

import os
import platform
import sys
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

# Ports the core runtime binds by default (confirmed in Step 0). Used to catch collisions
# with ports declared by other projects in the ecosystem manifest.
CORE_PORTS: Dict[int, str] = {
    8000: "event_api (blackfong_ecosystem/event_api.py)",
    8080: "hud / legion dashboard (config.hud_settings)",
    5000: "tinyprogrammer",
}


def declared_ports(manifest_path: Path) -> Dict[int, List[str]]:
    """Map port -> project ids that declare it in the ecosystem manifest."""
    try:
        data = tomllib.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    by_port: Dict[int, List[str]] = defaultdict(list)
    for project in data.get("projects", []):
        for entry in project.get("ports", []) or []:
            port = entry.get("port")
            if isinstance(port, int):
                by_port[port].append(str(project.get("id") or project.get("path") or "unknown"))
    return dict(by_port)


def find_port_conflicts(manifest_path: Path, core_ports: Optional[Mapping[int, str]] = None) -> List[Dict[str, Any]]:
    core = dict(CORE_PORTS if core_ports is None else core_ports)
    conflicts: List[Dict[str, Any]] = []
    for port, owners in sorted(declared_ports(manifest_path).items()):
        users = list(owners)
        if port in core:
            users.append(core[port])
        if len(users) > 1:
            conflicts.append({"port": port, "claimed_by": users})
    return conflicts


def _version(module_name: str) -> str:
    try:
        module = __import__(module_name)
    except Exception as exc:  # report, never raise from diagnostics
        return f"unavailable ({type(exc).__name__})"
    return str(getattr(module, "__version__", "unknown"))


def collect_diagnostics(
    *,
    tiles: Iterable[Mapping[str, Any]] = (),
    manifest_path: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    env = os.environ if environ is None else environ
    result: Dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pyside6": _version("PySide6"),
        "httpx": _version("httpx"),
        "event_api_url": env.get("BLACKFONG_EVENT_API_URL", "http://127.0.0.1:8000"),
        "windowed_override": str(env.get("BLACKFONG_WINDOWED", "")).lower() in {"1", "true", "yes"},
        "last_health": [dict(tile) for tile in tiles],
        "port_conflicts": [],
        "notes": [],
    }
    if manifest_path is not None:
        manifest = Path(manifest_path)
        if manifest.exists():
            result["port_conflicts"] = find_port_conflicts(manifest)
        else:
            result["notes"].append(f"Ecosystem manifest not found: {manifest}")
    if sys.version_info >= (3, 13):
        result["notes"].append("Python 3.13+ is unsupported: pinned dependencies (pydantic 2.6.4) do not build.")
    if sys.platform != "win32":
        result["notes"].append("Not running on Windows: Windows Terminal launch is unavailable.")
    return result
