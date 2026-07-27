from __future__ import annotations

from pathlib import Path

from blackfong_ecosystem.capability_registry import capability_snapshot
from blackfong_ecosystem.cognition.telemetry_service import CognitionTelemetryService


def test_capability_registry_marks_hud_as_secondary_surface() -> None:
    snapshot = capability_snapshot()
    hud_description = snapshot["capabilities"]["hud"]["description"]
    tui_description = snapshot["capabilities"]["tui"]["description"]

    assert "Secondary local browser cockpit" in hud_description
    assert "Terminal boot-home and fallback" in tui_description


def test_thoughtglass_ascii_summary_uses_regions_not_cortex() -> None:
    service = CognitionTelemetryService(
        memory_service=None,
        awareness_service=None,
        operator_actions=None,
        tinyprogrammer=None,
    )

    rendered = service.render_ascii_from_parts(
        layer="live_brain",
        trace_ribbon=[{"label": "route", "status": "done"}],
        overlays={
            "queue": {"pending_requests": 1},
            "latency": {"average_response_time_s": 2.5},
            "confidence": 0.75,
        },
        summaries={
            "focus_label": "D.A.I.S.E / routing",
            "tiny_label": "active / bright",
            "latest_event": "Operator opened Thoughtglass.",
        },
        faults=[{"message": "none"}],
        nodes=[
            {"label": "D.A.I.S.E", "state": "active", "zone": "center"},
            {"label": "Memory", "state": "warning", "zone": "memory"},
        ],
    )

    assert "THOUGHTGLASS" in rendered
    assert "REGIONS" in rendered
    assert "CORTEX" not in rendered


def test_hud_and_shortcut_surfaces_use_thoughtglass_hud_wording() -> None:
    root = Path(__file__).resolve().parents[1]
    cli_text = (root / "blackfong_ecosystem" / "cli.py").read_text(encoding="utf-8")
    dashboard_text = (root / "blackfong_ecosystem" / "ui" / "hud" / "dashboard.js").read_text(encoding="utf-8")
    shortcuts_text = (root / "blackfong_ecosystem" / "workspace_shortcuts.py").read_text(encoding="utf-8")

    assert "Thoughtglass HUD secondary cockpit" in cli_text
    assert "thoughtglass_projection" in dashboard_text
    assert "Open Thoughtglass HUD" in shortcuts_text
