"""Overlap-aware opacity for native operator panels, without focus stealing."""
from PySide6.QtCore import QEvent, QObject, QSettings, QTimer
from PySide6.QtGui import QActionGroup, QCursor
from PySide6.QtWidgets import QApplication, QDockWidget


def panel_opacity(*, floating, overlapping, active, hovered, inactive_opacity):
    if not floating or not overlapping or active or hovered:
        return 1.0
    return max(0.65, min(1.0, float(inactive_opacity)))


class FloatingPanelFocus(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.settings = QSettings("OBEOS", "PanelPresentation")
        try:
            self.opacity = max(0.65, min(1.0, float(self.settings.value("inactiveOpacity", 0.85))))
        except (TypeError, ValueError):
            self.opacity = 0.85
        self.hovered = None
        self.timer = QTimer(self)
        self.timer.setInterval(180)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QApplication.instance().installEventFilter(self)
        menu = owner.menuBar().addMenu("Panel Appearance")
        group = QActionGroup(self)
        group.setExclusive(True)
        for title, value in (("Overlap transparency: Off", 1.0), ("Subtle (85%)", 0.85), ("More transparent (70%)", 0.70)):
            action = menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(abs(self.opacity - value) < 0.01)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, opacity=value: self.set_opacity(opacity))

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Enter, QEvent.Leave, QEvent.Move, QEvent.Resize,
                            QEvent.Show, QEvent.Hide, QEvent.WindowActivate,
                            QEvent.WindowDeactivate, QEvent.ParentChange}:
            if not self.timer.isActive():
                self.timer.start()
        return False

    def set_opacity(self, value):
        self.opacity = value
        self.settings.setValue("inactiveOpacity", value)
        self.refresh()

    def refresh(self):
        if not self.owner.isVisible():
            return
        app = QApplication.instance()
        panels = list(self.owner.findChildren(QDockWidget))
        panels += [widget for widget in app.topLevelWidgets()
                   if widget.property("obeosFloatingPanel") and self.owner.isAncestorOf(widget)]
        visible = [panel for panel in panels if panel.isWindow() and panel.isVisible() and not panel.isMinimized()]
        under_pointer = app.widgetAt(QCursor.pos())
        hovered = under_pointer.window() if under_pointer is not None else None
        # Do not raise our windows while another application's window is active.
        active = app.activeWindow()
        if hovered in visible and hovered is not self.hovered and active is not None:
            hovered.setWindowOpacity(1.0)
            hovered.raise_()
        self.hovered = hovered
        surfaces = visible + ([self.owner] if not self.owner.isMinimized() else [])
        for panel in panels:
            floating = panel in visible
            overlapping = floating and any(other is not panel and panel.frameGeometry().intersects(other.frameGeometry()) for other in surfaces)
            opacity = panel_opacity(floating=floating, overlapping=overlapping,
                                    active=panel is active, hovered=panel is hovered,
                                    inactive_opacity=self.opacity)
            if abs(panel.windowOpacity() - opacity) > 0.01:
                panel.setWindowOpacity(opacity)
