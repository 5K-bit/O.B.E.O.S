from __future__ import annotations

import socket
from datetime import datetime, timezone
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


def _network_up(timeout_s: float = 0.6) -> bool:
    # Fast, local-ish signal: any interface up + can open a UDP socket.
    try:
        if_stats = psutil.net_if_stats()
        if not any(st.isup for st in if_stats.values()):
            return False
    except Exception:
        pass

    # UDP "connect" doesn't send packets but validates route.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout_s)
        s.connect(("1.1.1.1", 53))
        s.close()
        return True
    except Exception:
        return False


def read_system_snapshot() -> dict:
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
        "network_up": _network_up(),
    }

