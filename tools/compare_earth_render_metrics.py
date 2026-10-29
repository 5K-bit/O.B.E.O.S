"""Compare browser and Qt Earth metrics only when their render surfaces match."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("renderer"), dict):
        raise ValueError(f"{path} must contain a renderer object")
    return value


def compare(browser: dict[str, Any], qt: dict[str, Any], *, scene: str, workload: str) -> dict[str, Any]:
    browser_renderer = browser["renderer"]
    qt_renderer = qt["renderer"]
    dimensions = {
        "browser": [browser_renderer.get("canvasWidth"), browser_renderer.get("canvasHeight")],
        "qt": [qt_renderer.get("canvasWidth"), qt_renderer.get("canvasHeight")],
    }
    if dimensions["browser"] != dimensions["qt"]:
        raise ValueError(
            "browser and Qt canvas dimensions differ: "
            f"{dimensions['browser']} != {dimensions['qt']}"
        )

    def delta(field: str) -> float | None:
        browser_value = browser_renderer.get(field)
        qt_value = qt_renderer.get(field)
        if not isinstance(browser_value, (int, float)) or not isinstance(qt_value, (int, float)):
            return None
        return round(float(qt_value) - float(browser_value), 3)

    browser_memory = browser.get("owned_process_memory_mib")
    qt_memory = qt.get("owned_process_memory_mib")
    memory_delta = (
        round(float(qt_memory) - float(browser_memory), 3)
        if isinstance(browser_memory, (int, float)) and isinstance(qt_memory, (int, float))
        else None
    )
    return {
        "schema_version": "1.0",
        "scene": scene,
        "workload": workload,
        "dimensions": dimensions,
        "browser": {
            "frame_p50_ms": browser_renderer.get("frameP50Ms"),
            "frame_p95_ms": browser_renderer.get("frameP95Ms"),
            "resolution": browser_renderer.get("resolution"),
            "memory_mib": browser_memory,
            "graphics_acceleration": browser.get("graphics_acceleration", "unrecorded"),
        },
        "qt": {
            "frame_p50_ms": qt_renderer.get("frameP50Ms"),
            "frame_p95_ms": qt_renderer.get("frameP95Ms"),
            "resolution": qt_renderer.get("resolution"),
            "memory_mib": qt_memory,
            "graphics_acceleration": qt.get("graphics_acceleration", "unrecorded"),
        },
        "delta_qt_minus_browser": {
            "frame_p50_ms": delta("frameP50Ms"),
            "frame_p95_ms": delta("frameP95Ms"),
            "resolution": delta("resolution"),
            "memory_mib": memory_delta,
        },
        "limitations": [
            "Graphics acceleration is reported as unrecorded unless each input artifact provides it.",
            "Equal canvas dimensions do not prove identical scene contents or machine load.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--qt", type=Path, required=True)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compare(load(args.browser), load(args.qt), scene=args.scene, workload=args.workload)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
