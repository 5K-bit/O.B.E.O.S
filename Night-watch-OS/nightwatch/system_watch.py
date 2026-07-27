from __future__ import annotations

import socket
from datetime import datetime, timezone
import os
from pathlib import Path

import psutil


def _read_pi_temp_c() -> float | None:
    # Common on Raspberry Pi OS
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        raw = p.read_text(encoding="utf-8").strip()
        milli = int(raw)
        return milli / 1000.0
    except Exception:
        return None


def _read_any_temp_c() -> float | None:
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False)
        for _, entries in temps.items():
            for e in entries:
                if e.current is not None:
                    return float(e.current)
    except Exception:
        pass
    return _read_pi_temp_c()


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _local_interface_up() -> bool:
    try:
        if_stats = psutil.net_if_stats()
        return any(st.isup for st in if_stats.values())
    except Exception:
        return False


def _network_up(timeout_s: float = 0.6, offline_mode: bool | None = None) -> bool:
    # Offline mode reports local interface state and never performs route checks.
    if offline_mode is None:
        offline_mode = _truthy(os.environ.get("NIGHTWATCH_OFFLINE_MODE"))
    interface_up = _local_interface_up()
    if not interface_up or offline_mode:
        return interface_up

    # UDP "connect" doesn't send packets but validates route.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout_s)
        s.connect(("1.1.1.1", 53))
        s.close()
        return True
    except Exception:
        return False


def read_system_snapshot(offline_mode: bool | None = None) -> dict:
    resolved_offline_mode = _truthy(os.environ.get("NIGHTWATCH_OFFLINE_MODE")) if offline_mode is None else offline_mode
    cpu = float(psutil.cpu_percent(interval=0.15))
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")

    return {
        "at": datetime.now(timezone.utc),
        "cpu_percent": cpu,
        "ram_percent": float(vm.percent),
        "ram_used_mb": int(vm.used / (1024 * 1024)),
        "ram_total_mb": int(vm.total / (1024 * 1024)),
        "disk_percent": float(du.percent),
        "disk_used_gb": round(du.used / (1024 * 1024 * 1024), 2),
        "disk_total_gb": round(du.total / (1024 * 1024 * 1024), 2),
        "temp_c": _read_any_temp_c(),
        "network_up": _network_up(offline_mode=resolved_offline_mode),
        "network_mode": "offline_local_interface" if resolved_offline_mode else "route_check",
    }
