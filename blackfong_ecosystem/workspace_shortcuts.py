"""Shared Windows-friendly keyboard bindings for the desktop workspace and Thoughtglass HUD."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional, Sequence

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtWidgets import QWidget
except ImportError:  # pragma: no cover
    QWidget = object  # type: ignore[misc, assignment]
    QShortcut = object  # type: ignore[misc, assignment]
    QKeySequence = object  # type: ignore[misc, assignment]
    QSettings = object  # type: ignore[misc, assignment]


@dataclass(frozen=True)
class WorkspaceShortcutSpec:
    action_id: str
    label: str
    sequence: str
    category: str = "Workspace"


WORKSPACE_SHORTCUTS: Sequence[WorkspaceShortcutSpec] = (
    WorkspaceShortcutSpec("refresh", "Refresh workspace", "F5"),
    WorkspaceShortcutSpec("command_palette", "Command palette", "Ctrl+K"),
    WorkspaceShortcutSpec("send_chat", "Send chat", "Ctrl+Return"),
    WorkspaceShortcutSpec("close_panel", "Dock back / close float", "Ctrl+W"),
    WorkspaceShortcutSpec("next_tab", "Next center tab", "Ctrl+Tab"),
    WorkspaceShortcutSpec("prev_tab", "Previous center tab", "Ctrl+Shift+Tab"),
    WorkspaceShortcutSpec("layout_brain", "Layout: Brain Home", "Ctrl+1"),
    WorkspaceShortcutSpec("layout_memory", "Layout: Memory Chamber", "Ctrl+2"),
    WorkspaceShortcutSpec("layout_chat", "Layout: Chat Focus", "Ctrl+3"),
    WorkspaceShortcutSpec("layout_tiny", "Layout: Tiny Focus", "Ctrl+4"),
    WorkspaceShortcutSpec("layout_security", "Layout: Security Review", "Ctrl+5"),
    WorkspaceShortcutSpec("open_hud", "Open Thoughtglass HUD", "Ctrl+Shift+H"),
    WorkspaceShortcutSpec("toggle_fullscreen", "Toggle fullscreen", "F11"),
    WorkspaceShortcutSpec("open_terminal", "Open Windows Terminal", "Ctrl+Shift+T"),
    WorkspaceShortcutSpec("quit_workspace", "Quit Workspace", "Ctrl+Q"),
    WorkspaceShortcutSpec("layer_live_brain", "Thoughtglass: Live Brain", "1"),
    WorkspaceShortcutSpec("layer_memory", "Thoughtglass: Memory", "2"),
    WorkspaceShortcutSpec("layer_tasks", "Thoughtglass: Tasks", "3"),
    WorkspaceShortcutSpec("layer_sensors", "Thoughtglass: Sensors", "4"),
    WorkspaceShortcutSpec("layer_faults", "Thoughtglass: Faults", "5"),
    WorkspaceShortcutSpec("layer_forge", "Thoughtglass: Forge", "6"),
)

WINDOWS_SHORTCUTS_NOTE = (
    "These shortcuts work inside OBEOS only. Windows owns system keys like Win+D, Alt+Tab, and Ctrl+Alt+Del."
)


def load_shortcut_overrides(settings: Optional[Any] = None) -> Dict[str, str]:
    if settings is None:
        return {}
    raw = settings.value("workspace/shortcut_overrides")
    if not raw:
        return {}
    try:
        payload = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if key and value}


def save_shortcut_overrides(settings: Any, overrides: Dict[str, str]) -> None:
    settings.setValue("workspace/shortcut_overrides", json.dumps(overrides))


def effective_shortcuts(overrides: Optional[Dict[str, str]] = None) -> Sequence[WorkspaceShortcutSpec]:
    mapping = dict(overrides or {})
    resolved: List[WorkspaceShortcutSpec] = []
    for spec in WORKSPACE_SHORTCUTS:
        sequence = mapping.get(spec.action_id, spec.sequence)
        resolved.append(replace(spec, sequence=sequence))
    return tuple(resolved)


def shortcuts_reference_markdown(overrides: Optional[Dict[str, str]] = None) -> str:
    lines = ["## Workspace keyboard shortcuts", "", WINDOWS_SHORTCUTS_NOTE, ""]
    for spec in effective_shortcuts(overrides):
        lines.append(f"- **{spec.sequence}** — {spec.label}")
    lines.append("")
    lines.append("Slash commands (`/tiny launch`, `/cognition`, …) remain available in the Commands pane.")
    return "\n".join(lines)


def shortcuts_reference_html(overrides: Optional[Dict[str, str]] = None) -> str:
    rows = "".join(
        f"<tr><td><code>{spec.sequence}</code></td><td>{spec.label}</td></tr>"
        for spec in effective_shortcuts(overrides)
    )
    return (
        "<h3>Workspace keyboard shortcuts</h3>"
        f"<p>{WINDOWS_SHORTCUTS_NOTE}</p>"
        "<table style='border-collapse:collapse;width:100%'>"
        "<tr><th align='left'>Keys</th><th align='left'>Action</th></tr>"
        f"{rows}</table>"
        "<p>Slash commands remain in the Commands pane. Rebind overrides persist in workspace settings.</p>"
    )


def install_desktop_shortcuts(
    host: QWidget,
    handlers: Dict[str, Callable[[], None]],
    *,
    context: Optional[Any] = None,
    overrides: Optional[Dict[str, str]] = None,
) -> List[Any]:
    """Register QShortcut instances on a QWidget host."""
    installed: List[Any] = []
    for spec in effective_shortcuts(overrides):
        handler = handlers.get(spec.action_id)
        if handler is None:
            continue
        shortcut = QShortcut(QKeySequence(spec.sequence), host)
        if context is not None:
            shortcut.setContext(context)
        shortcut.activated.connect(handler)
        installed.append(shortcut)
    return installed


def shortcuts_json_for_hud(overrides: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    return [
        {"id": spec.action_id, "label": spec.label, "sequence": spec.sequence, "category": spec.category}
        for spec in effective_shortcuts(overrides)
    ]
