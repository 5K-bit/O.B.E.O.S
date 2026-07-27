"""Browser-like float / dock-back / collapse for the PySide6 workspace."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from PySide6.QtCore import QByteArray, QSettings, Qt, Signal
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (
        QDockWidget,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QTabWidget,
        QToolBar,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover
    QMainWindow = object  # type: ignore[misc, assignment]
    QDockWidget = object  # type: ignore[misc, assignment]


COLLAPSED_DOCK_WIDTH = 64

DEFAULT_TAB_GROUPS: Dict[str, List[str]] = {
    "debug": ["dock_trace", "workbench_chat", "dock_files"],
    "tiny_focus": ["dock_tiny", "workbench_thoughtglass"],
    "security": ["dock_redamon", "dock_trace", "workbench_thoughtglass"],
}


@dataclass
class PanelSlot:
    panel_id: str
    title: str
    kind: str  # dock | workbench_tab
    dock: Optional[QDockWidget] = None
    tab_host: Optional[QTabWidget] = None
    widget: Optional[QWidget] = None
    tab_index: int = -1
    collapsed: bool = False
    floating: bool = False


class ManagedDockWidget(QDockWidget):
    """Dock that hides on close instead of destroying the panel."""

    panel_closed = Signal(str)

    def __init__(self, title: str, panel_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(title, parent)
        self.panel_id = panel_id
        self.setObjectName(panel_id)

    def closeEvent(self, event: Any) -> None:  # pragma: no cover - GUI
        event.ignore()
        self.panel_closed.emit(self.panel_id)
        self.hide()


class ManagedFloatWindow(QMainWindow):
    """Floating workspace panel with dock-back and collapse controls."""

    dock_back_requested = Signal(str)
    collapse_requested = Signal(str)

    def __init__(self, *, panel_id: str, title: str, content: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.panel_id = panel_id
        self.setWindowTitle(title)
        self.resize(920, 680)
        self._content = content
        self.setCentralWidget(content)

        toolbar = QToolBar("Panel", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        dock_action = QAction("Dock Back", self)
        dock_action.triggered.connect(lambda: self.dock_back_requested.emit(self.panel_id))
        toolbar.addAction(dock_action)

        collapse_action = QAction("Collapse Strip", self)
        collapse_action.triggered.connect(lambda: self.collapse_requested.emit(self.panel_id))
        toolbar.addAction(collapse_action)

        close_action = QAction("Close", self)
        close_action.triggered.connect(self.close)
        toolbar.addAction(close_action)

    def closeEvent(self, event: Any) -> None:  # pragma: no cover - GUI
        self.dock_back_requested.emit(self.panel_id)
        event.accept()


class WorkspaceWindowManager:
    """Coordinates dock float, workbench tab tear-off, collapse, and QSettings persistence."""

    def __init__(self, main_window: QMainWindow, settings: QSettings) -> None:
        self._main = main_window
        self._settings = settings
        self._slots: Dict[str, PanelSlot] = {}
        self._float_windows: Dict[str, ManagedFloatWindow] = {}

    def register_dock(self, panel_id: str, dock: ManagedDockWidget) -> None:
        dock.panel_closed.connect(self._on_panel_close_requested)
        self._slots[panel_id] = PanelSlot(
            panel_id=panel_id,
            title=dock.windowTitle(),
            kind="dock",
            dock=dock,
            widget=dock.widget(),
        )

    def register_workbench_tab(
        self,
        panel_id: str,
        *,
        tab_host: QTabWidget,
        widget: QWidget,
        title: str,
    ) -> None:
        index = tab_host.indexOf(widget)
        self._slots[panel_id] = PanelSlot(
            panel_id=panel_id,
            title=title,
            kind="workbench_tab",
            tab_host=tab_host,
            widget=widget,
            tab_index=index,
        )

    def float_panel(self, panel_id: str) -> bool:
        slot = self._slots.get(panel_id)
        if slot is None:
            return False
        if panel_id in self._float_windows:
            window = self._float_windows[panel_id]
            window.show()
            window.raise_()
            window.activateWindow()
            return True

        widget = slot.widget
        if widget is None:
            return False

        if slot.kind == "dock" and slot.dock is not None:
            placeholder = QWidget(slot.dock)
            slot.dock.setWidget(placeholder)
            slot.dock.hide()

        elif slot.kind == "workbench_tab" and slot.tab_host is not None:
            index = slot.tab_host.indexOf(widget)
            if index < 0:
                if panel_id in self._float_windows:
                    window = self._float_windows[panel_id]
                    window.show()
                    window.raise_()
                    window.activateWindow()
                    return True
                return False
            slot.tab_index = index
            slot.tab_host.removeTab(index)

        window = ManagedFloatWindow(
            panel_id=panel_id,
            title=slot.title,
            content=widget,
            parent=self._main,
        )
        window.dock_back_requested.connect(self.dock_back)
        window.collapse_requested.connect(lambda pid: self.set_collapsed(pid, True))
        self._float_windows[panel_id] = window
        slot.floating = True
        geometry = self._load_float_geometry(panel_id)
        if geometry is not None:
            window.restoreGeometry(geometry)
        window.show()
        self._persist()
        return True

    def dock_back(self, panel_id: str) -> bool:
        slot = self._slots.get(panel_id)
        window = self._float_windows.pop(panel_id, None)
        if slot is None or window is None:
            if window is not None:
                orphan = window.centralWidget()
                window.setCentralWidget(QWidget())
                window.hide()
                window.deleteLater()
                if (
                    orphan is not None
                    and slot is not None
                    and slot.kind == "workbench_tab"
                    and slot.tab_host is not None
                    and slot.tab_host.indexOf(orphan) < 0
                ):
                    index = max(0, min(slot.tab_index, slot.tab_host.count()))
                    slot.tab_host.insertTab(index, orphan, slot.title)
                    slot.floating = False
            return False

        self._save_float_geometry(panel_id, window.saveGeometry())
        widget = window.centralWidget()
        window.setCentralWidget(QWidget())
        window.deleteLater()

        if slot.kind == "dock" and slot.dock is not None:
            slot.dock.setWidget(widget)
            slot.dock.show()
            if slot.collapsed:
                self._apply_dock_collapsed(slot)
            else:
                slot.dock.setFloating(False)
                slot.dock.raise_()

        elif slot.kind == "workbench_tab" and slot.tab_host is not None:
            index = max(0, min(slot.tab_index, slot.tab_host.count()))
            slot.tab_host.insertTab(index, widget, slot.title)
            slot.tab_host.setCurrentWidget(widget)

        slot.floating = False
        self._persist()
        return True

    def set_collapsed(self, panel_id: str, collapsed: bool) -> None:
        slot = self._slots.get(panel_id)
        if slot is None:
            return
        slot.collapsed = collapsed
        if slot.kind == "dock" and slot.dock is not None and not slot.floating:
            self._apply_dock_collapsed(slot)
        self._persist()

    def toggle_collapsed(self, panel_id: str) -> None:
        slot = self._slots.get(panel_id)
        if slot is None:
            return
        self.set_collapsed(panel_id, not slot.collapsed)

    def _apply_dock_collapsed(self, slot: PanelSlot) -> None:
        dock = slot.dock
        if dock is None:
            return
        content = dock.widget()
        if slot.collapsed:
            dock.setMaximumWidth(COLLAPSED_DOCK_WIDTH)
            dock.setMinimumWidth(COLLAPSED_DOCK_WIDTH)
            if content is not None:
                content.hide()
        else:
            dock.setMaximumWidth(16777215)
            dock.setMinimumWidth(0)
            if content is not None:
                content.show()

    def show_panel(self, panel_id: str) -> None:
        slot = self._slots.get(panel_id)
        if slot is None:
            return
        if slot.floating and panel_id in self._float_windows:
            self._float_windows[panel_id].show()
            self._float_windows[panel_id].raise_()
        elif slot.dock is not None:
            slot.dock.show()
            slot.dock.raise_()

    def _on_panel_close_requested(self, panel_id: str) -> None:
        slot = self._slots.get(panel_id)
        if slot is None:
            return
        if slot.floating:
            self.dock_back(panel_id)
        elif slot.dock is not None:
            slot.dock.hide()

    def dock_all_floats(self) -> None:
        for panel_id in list(self._float_windows.keys()):
            self.dock_back(panel_id)

    def close_all_floats(self) -> None:
        for panel_id in list(self._float_windows.keys()):
            self.dock_back(panel_id)

    def restore_persisted(self) -> None:
        raw = self._settings.value("workspace/window_manager")
        if not raw:
            return
        try:
            payload = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        for panel_id in payload.get("collapsed", []):
            self.set_collapsed(str(panel_id), True)
        for panel_id in payload.get("floating", []):
            if panel_id in self._slots:
                self.float_panel(str(panel_id))
        active_group = str(payload.get("active_tab_group") or "")
        if active_group:
            self.apply_tab_group(active_group, persist=False)

    def list_tab_groups(self) -> Dict[str, List[str]]:
        raw = self._settings.value("workspace/tab_groups")
        if raw:
            try:
                stored = json.loads(str(raw))
                if isinstance(stored, dict):
                    return {str(key): list(value) for key, value in stored.items()}
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return dict(DEFAULT_TAB_GROUPS)

    def save_tab_group(self, name: str, panel_ids: List[str]) -> None:
        groups = self.list_tab_groups()
        groups[str(name)] = [str(panel_id) for panel_id in panel_ids if panel_id in self._slots]
        self._settings.setValue("workspace/tab_groups", json.dumps(groups))

    def apply_tab_group(self, name: str, *, persist: bool = True) -> bool:
        groups = self.list_tab_groups()
        panel_ids = groups.get(str(name))
        if not panel_ids:
            return False
        self.dock_all_floats()
        for panel_id in panel_ids:
            slot = self._slots.get(panel_id)
            if slot is None:
                continue
            if slot.dock is not None:
                slot.dock.show()
                slot.dock.raise_()
            elif slot.tab_host is not None and slot.widget is not None:
                index = slot.tab_host.indexOf(slot.widget)
                if index >= 0:
                    slot.tab_host.setCurrentIndex(index)
        if persist:
            payload = self._load_persist_payload()
            payload["active_tab_group"] = str(name)
            self._settings.setValue("workspace/window_manager", json.dumps(payload))
        return True

    def _load_persist_payload(self) -> Dict[str, Any]:
        raw = self._settings.value("workspace/window_manager")
        if not raw:
            return {}
        try:
            payload = json.loads(str(raw))
            return payload if isinstance(payload, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    def _load_float_geometry(self, panel_id: str) -> Optional[QByteArray]:
        payload = self._load_persist_payload()
        geometries = payload.get("float_geometries") or {}
        encoded = geometries.get(panel_id)
        if not encoded:
            return None
        try:
            return QByteArray.fromBase64(str(encoded).encode("ascii"))
        except Exception:
            return None

    def _save_float_geometry(self, panel_id: str, geometry: QByteArray) -> None:
        payload = self._load_persist_payload()
        geometries = dict(payload.get("float_geometries") or {})
        geometries[panel_id] = bytes(geometry.toBase64()).decode("ascii")
        payload["float_geometries"] = geometries
        self._settings.setValue("workspace/window_manager", json.dumps(payload))

    def _persist(self) -> None:
        payload = self._load_persist_payload()
        payload["collapsed"] = [slot.panel_id for slot in self._slots.values() if slot.collapsed]
        payload["floating"] = [slot.panel_id for slot in self._slots.values() if slot.floating]
        self._settings.setValue("workspace/window_manager", json.dumps(payload))

    def panel_ids(self) -> List[str]:
        return list(self._slots.keys())
