"""Native 3D Thoughtglass brain scene (OpenGL) for the desktop workspace."""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Tuple

try:
    from PySide6.QtCore import QPointF, Qt, QTimer, Signal
    from PySide6.QtGui import QColor, QMouseEvent, QWheelEvent
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
except ImportError:  # pragma: no cover
    QOpenGLWidget = QWidget  # type: ignore[misc, assignment]
    Signal = object  # type: ignore[misc, assignment]

try:
    from OpenGL.GL import (  # type: ignore[import-untyped]
        GL_COLOR_BUFFER_BIT,
        GL_DEPTH_BUFFER_BIT,
        GL_DEPTH_TEST,
        GL_LINES,
        GL_POINTS,
        GL_QUADS,
        glBegin,
        glClear,
        glClearColor,
        glColor3f,
        glEnable,
        glEnd,
        glLoadIdentity,
        glMatrixMode,
        glPointSize,
        glRotatef,
        glTranslatef,
        glVertex3f,
        glViewport,
        GL_PROJECTION,
        GL_MODELVIEW,
    )
    from OpenGL.GLU import gluPerspective  # type: ignore[import-untyped]

    OPENGL_AVAILABLE = True
except Exception:  # pragma: no cover
    OPENGL_AVAILABLE = False


STATE_COLORS = {
    "active": (0.89, 0.76, 0.46),
    "ready": (0.44, 0.88, 0.72),
    "warming": (0.48, 0.82, 0.86),
    "booting": (0.48, 0.65, 0.79),
    "degraded": (1.0, 0.5, 0.46),
    "fault": (1.0, 0.5, 0.46),
    "warning": (1.0, 0.62, 0.45),
    "offline": (0.48, 0.52, 0.57),
    "idle": (0.35, 0.4, 0.45),
}


class ThoughtglassScene3D(QOpenGLWidget):
    """Orbital 3D cognition graph with dust particles and growth scaling."""

    node_selected = Signal(str)
    node_activated = Signal(dict)
    zoom_changed = Signal(float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._orbit_yaw = 24.0
        self._orbit_pitch = -18.0
        self._zoom = 1.0
        self._growth = 1.0
        self._drag_origin: Optional[QPointF] = None
        self._nodes: List[Dict[str, Any]] = []
        self._edges: List[Dict[str, Any]] = []
        self._positions: Dict[str, Tuple[float, float, float]] = {}
        self._particles: List[Tuple[float, float, float, float]] = []
        self._selected_id: Optional[str] = None
        self._fx_tick = 0.0
        self._press_origin: Optional[QPointF] = None
        self._active_layer = "live_brain"
        self._layer_blend = 1.0
        self._fault_density = 0.0

        self.setMinimumHeight(320)
        self._seed_particles(52)
        self._anim = QTimer(self)
        self._anim.setInterval(33)
        self._anim.timeout.connect(self._advance_fx)
        self._sync_animation_timer()

    def _seed_particles(self, count: int) -> None:
        rng = random.Random(42)
        self._particles = [
            (rng.uniform(-1.4, 1.4), rng.uniform(-1.0, 1.0), rng.uniform(-1.2, 1.2), rng.uniform(0.2, 1.0))
            for _ in range(count)
        ]

    def _sync_animation_timer(self, active: Optional[bool] = None) -> None:
        should_run = bool(active) if active is not None else bool(self.isVisible())
        if should_run and not self._anim.isActive():
            self._anim.start()
        if not should_run and self._anim.isActive():
            self._anim.stop()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._sync_animation_timer()

    def hideEvent(self, event: Any) -> None:
        self._sync_animation_timer(active=False)
        super().hideEvent(event)

    def _advance_fx(self) -> None:
        self._fx_tick += 0.018
        if self._layer_blend < 1.0:
            self._layer_blend = min(1.0, self._layer_blend + 0.045)
        self.update()

    def apply_graph(self, *, snapshot: Dict[str, Any], graph_payload: Dict[str, Any]) -> None:
        from .cognition_renderer import normalize_renderer_inputs

        normalized = normalize_renderer_inputs(snapshot=snapshot, graph_payload=graph_payload)
        new_layer = str(normalized.get("active_layer") or "live_brain")
        if new_layer != self._active_layer:
            self._layer_blend = 0.0
            self._active_layer = new_layer
        self._nodes = list(normalized["nodes"])
        self._edges = list(normalized["edges"])
        self.apply_focus(normalized["focus"])
        metrics = dict((snapshot or {}).get("summaries") or {})
        node_count = len(self._nodes)
        event_count = int(metrics.get("event_count") or node_count)
        self._growth = 0.85 + min(0.65, (node_count + event_count) / 80.0)
        fault_nodes = sum(
            1
            for node in self._nodes
            if str(node.get("state") or "") in {"fault", "degraded", "warning"}
        )
        self._fault_density = min(1.0, fault_nodes / max(len(self._nodes), 1))
        target_particles = 52 + int(self._fault_density * 48)
        if len(self._particles) != target_particles:
            self._seed_particles(target_particles)
        self._layout_nodes()
        self.update()

    def apply_focus(self, focus: Dict[str, Any]) -> None:
        self._zoom = max(0.35, min(3.5, float(focus.get("zoom") or self._zoom)))
        self._orbit_yaw = float(focus.get("orbit") or self._orbit_yaw) * 57.2958
        selected = focus.get("selected_node")
        if selected:
            self._selected_id = str(selected)
            self.node_selected.emit(self._selected_id)
        self.update()

    def _layout_nodes(self) -> None:
        positions: Dict[str, Tuple[float, float, float]] = {}
        count = max(len(self._nodes), 1)
        for index, node in enumerate(self._nodes):
            node_id = str(node.get("id") or index)
            layer = float(node.get("layer_index") or 0)
            angle = (index / count) * math.tau + self._fx_tick * 0.15
            radius = (0.55 + layer * 0.08) * self._growth
            y = math.sin(angle * 1.7) * 0.25 * self._growth
            positions[node_id] = (
                math.cos(angle) * radius,
                y,
                math.sin(angle) * radius,
            )
        self._positions = positions

    def initializeGL(self) -> None:  # pragma: no cover - GL
        if not OPENGL_AVAILABLE:
            return
        glClearColor(0.04, 0.06, 0.08, 1.0)
        glEnable(GL_DEPTH_TEST)
        glPointSize(3.0)

    def resizeGL(self, width: int, height: int) -> None:  # pragma: no cover - GL
        if not OPENGL_AVAILABLE:
            return
        glViewport(0, 0, max(width, 1), max(height, 1))

    def paintGL(self) -> None:  # pragma: no cover - GL
        if not OPENGL_AVAILABLE:
            return
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        fade = max(0.35, self._layer_blend)
        glClearColor(0.04 * fade, 0.06 * fade, 0.08 * fade, 1.0)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        aspect = self.width() / max(self.height(), 1)
        gluPerspective(42.0, aspect, 0.1, 40.0)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glTranslatef(0.0, 0.0, -3.2 / max(self._zoom, 0.35))
        glRotatef(self._orbit_pitch, 1.0, 0.0, 0.0)
        glRotatef(self._orbit_yaw, 0.0, 1.0, 0.0)

        self._draw_particles()
        self._draw_shells()
        self._draw_edges()
        self._draw_nodes()

    def _draw_particles(self) -> None:
        glBegin(GL_POINTS)
        for index, (x, y, z, weight) in enumerate(self._particles):
            phase = self._fx_tick * (0.8 + self._fault_density * 0.6) + index * 0.11
            px = x + math.sin(phase) * (0.08 + self._fault_density * 0.05)
            py = y + math.cos(phase * 0.7) * 0.06
            pz = z + math.sin(phase * 1.3) * 0.08
            if index % 5 == 0 and self._fault_density > 0.2:
                glColor3f(0.95, 0.45 + self._fault_density * 0.2, 0.35)
            else:
                tone = 0.55 if index % 5 == 0 else 0.25
                glColor3f(tone * 0.9, tone * 0.75, tone * 0.45)
            glVertex3f(px, py, pz)
        glEnd()

    def _draw_shells(self) -> None:
        for ring, radius in enumerate((0.35, 0.7, 1.05)):
            scaled = radius * self._growth
            glColor3f(0.12 + ring * 0.04, 0.18 + ring * 0.03, 0.22)
            segments = 36
            glBegin(GL_LINES)
            for step in range(segments):
                a0 = (step / segments) * math.tau
                a1 = ((step + 1) / segments) * math.tau
                glVertex3f(math.cos(a0) * scaled, 0.0, math.sin(a0) * scaled)
                glVertex3f(math.cos(a1) * scaled, 0.0, math.sin(a1) * scaled)
            glEnd()

    def _draw_edges(self) -> None:
        glBegin(GL_LINES)
        for edge in self._edges:
            source = self._positions.get(str(edge.get("source")))
            target = self._positions.get(str(edge.get("target")))
            if not source or not target:
                continue
            active = str(edge.get("state") or "") == "active"
            if active:
                glColor3f(0.89, 0.76, 0.46)
            else:
                glColor3f(0.25, 0.32, 0.38)
            glVertex3f(*source)
            glVertex3f(*target)
        glEnd()

    def _draw_nodes(self) -> None:
        for node in self._nodes:
            node_id = str(node.get("id") or "")
            pos = self._positions.get(node_id)
            if not pos:
                continue
            state = str(node.get("state") or "idle")
            color = STATE_COLORS.get(state, STATE_COLORS["idle"])
            emphasis = float(node.get("emphasis") or 0.2)
            size = 0.05 + emphasis * 0.05
            if node_id == self._selected_id:
                size *= 1.35
                glColor3f(1.0, 0.86, 0.5)
            else:
                glColor3f(*color)
            self._draw_cube(pos, size)

    @staticmethod
    def _draw_cube(center: Tuple[float, float, float], size: float) -> None:
        x, y, z = center
        s = size
        glBegin(GL_QUADS)
        for face in (
            ((x - s, y - s, z + s), (x + s, y - s, z + s), (x + s, y + s, z + s), (x - s, y + s, z + s)),
            ((x - s, y - s, z - s), (x + s, y - s, z - s), (x + s, y + s, z - s), (x - s, y + s, z - s)),
        ):
            for vertex in face:
                glVertex3f(*vertex)
        glEnd()

    def wheelEvent(self, event: QWheelEvent) -> None:  # pragma: no cover - GUI
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 0.9
        self._zoom = max(0.35, min(3.5, self._zoom * factor))
        self.zoom_changed.emit(self._zoom)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # pragma: no cover - GUI
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.position()
            self._press_origin = event.position()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # pragma: no cover - GUI
        if self._drag_origin is None:
            return
        delta = event.position() - self._drag_origin
        self._drag_origin = event.position()
        self._orbit_yaw += float(delta.x()) * 0.35
        self._orbit_pitch = max(-80.0, min(20.0, self._orbit_pitch + float(delta.y()) * 0.25))
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # pragma: no cover - GUI
        if (
            event.button() == Qt.LeftButton
            and self._press_origin is not None
            and (event.position() - self._press_origin).manhattanLength() < 8
        ):
            picked = self._pick_node(event.position())
            if picked is not None:
                self._selected_id = picked.get("id")
                self.node_selected.emit(str(self._selected_id))
                self.node_activated.emit(dict(picked))
        self._drag_origin = None
        self._press_origin = None

    def _pick_node(self, point: QPointF) -> Optional[Dict[str, Any]]:
        if not self._positions:
            return None
        center_x = self.width() / 2.0
        center_y = self.height() / 2.0
        best: Optional[Dict[str, Any]] = None
        best_distance = float("inf")
        for node in self._nodes:
            node_id = str(node.get("id") or "")
            position = self._positions.get(node_id)
            if not position:
                continue
            sx = center_x + position[0] * 120.0 * self._zoom
            sy = center_y - position[1] * 120.0 * self._zoom
            distance = math.hypot(float(point.x()) - sx, float(point.y()) - sy)
            if distance < best_distance:
                best_distance = distance
                best = node
        if best is not None and best_distance <= 42.0:
            return best
        return None

    def set_selected_node(self, node_id: Optional[str]) -> None:
        self._selected_id = node_id
        self.update()


class Thoughtglass3DHost(QWidget):
    """Wrapper with 2D/3D toggle around the native cognition graph and 3D scene."""

    drill_requested = Signal(dict)

    def __init__(self, graph_2d: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.graph_2d = graph_2d
        self._use_3d = OPENGL_AVAILABLE
        self.scene_3d = ThoughtglassScene3D(self) if OPENGL_AVAILABLE else None
        if self.scene_3d is not None:
            self.scene_3d.node_activated.connect(self._on_node_activated)

        controls = QHBoxLayout()
        self.mode_label = QLabel("3D Brain" if self._use_3d else "2D Graph (install PyOpenGL for 3D)", self)
        self.toggle_button = QPushButton("Switch to 2D", self)
        self.toggle_button.setEnabled(self._use_3d)
        self.toggle_button.clicked.connect(self._toggle_mode)
        controls.addWidget(self.mode_label)
        controls.addStretch(1)
        controls.addWidget(self.toggle_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        layout.addWidget(self._active_widget(), 1)
        self._stack_target = layout

    def _active_widget(self) -> QWidget:
        if self._use_3d and self.scene_3d is not None:
            return self.scene_3d
        return self.graph_2d

    def _toggle_mode(self) -> None:
        layout = self.layout()
        if layout is None or self.scene_3d is None:
            return
        while layout.count() > 1:
            item = layout.takeAt(1)
            if item.widget():
                item.widget().setParent(None)
        self._use_3d = not self._use_3d
        layout.addWidget(self._active_widget(), 1)
        self.toggle_button.setText("Switch to 2D" if self._use_3d else "Switch to 3D")
        self.mode_label.setText("3D Brain" if self._use_3d else "2D Graph")

    def _on_node_activated(self, node: Dict[str, Any]) -> None:
        self.drill_requested.emit(
            {
                "selected_node": node.get("id"),
                "mode": "neighborhood",
                "layer": node.get("layer") or "live_brain",
            }
        )

    def apply_graph(self, *, snapshot: Dict[str, Any], graph_payload: Dict[str, Any]) -> None:
        from .cognition_renderer import normalize_renderer_inputs

        normalized = normalize_renderer_inputs(snapshot=snapshot, graph_payload=graph_payload)
        if self.scene_3d is not None:
            self.scene_3d.apply_graph(snapshot=snapshot, graph_payload=graph_payload)
            self.scene_3d.apply_focus(normalized["focus"])
        if hasattr(self.graph_2d, "apply_graph"):
            graph = dict(graph_payload or {})
            self.graph_2d.apply_graph(
                snapshot=snapshot,
                graph_payload=graph,
                mode_label=str(graph.get("mode") or graph_payload.get("mode_label") or ""),
            )
