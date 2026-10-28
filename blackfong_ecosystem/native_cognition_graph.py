from __future__ import annotations

import json
import math
from html import escape
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


STATE_COLORS = {
    "active": QColor("#e4c276"),
    "ready": QColor("#6fe0b7"),
    "warming": QColor("#7bd0da"),
    "booting": QColor("#7ba6c9"),
    "degraded": QColor("#ff8076"),
    "fault": QColor("#ff8076"),
    "warning": QColor("#ff9d72"),
    "manual_only": QColor("#9db2c6"),
    "offline": QColor("#7a8591"),
    "missing": QColor("#596470"),
    "idle": QColor("#465564"),
}
EDGE_CATEGORY_COLORS = {
    "agent": QColor("#dcb96a"),
    "concept": QColor("#5fc7bb"),
    "document": QColor("#9dc5ff"),
    "fault": QColor("#ff7f7f"),
    "event": QColor("#c799ff"),
    "sequence": QColor("#7ba6c9"),
}
REGION_COLORS = {
    "operator_core": QColor("#193549"),
    "memory_region": QColor("#142c2f"),
    "system_ring": QColor("#242231"),
    "fault_region": QColor("#311d21"),
    "documents_region": QColor("#162637"),
    "agents_region": QColor("#2c2421"),
    "tiny_region": QColor("#1e2834"),
    "sensor_region": QColor("#172124"),
}
BUCKET_ORDER = {"overview": 0, "mid": 1, "close": 2}
DETAIL_LEVEL_ORDER = {"overview": 0, "mid": 1, "close": 2}
TIER_RADII = {1: 34.0, 2: 28.0, 3: 20.0, 4: 15.0}


def _pretty_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True)


def _zoom_bucket(scale: float) -> str:
    if scale < 0.92:
        return "overview"
    if scale < 1.7:
        return "mid"
    return "close"


def _detail_visible(detail_level: str, zoom_bucket: str) -> bool:
    return DETAIL_LEVEL_ORDER.get(detail_level, 1) <= BUCKET_ORDER.get(zoom_bucket, 1)


def _label_should_render(node: Dict[str, Any], *, selected: bool, hovered: bool, zoom_bucket: str) -> bool:
    if selected or hovered or node.get("always_show_label"):
        return True
    if float(node.get("label_priority") or 0.0) >= 0.9:
        return True
    if node.get("low_signal_label"):
        return False
    if zoom_bucket == "overview":
        return bool(node.get("anchor")) or float(node.get("label_priority") or 0.0) >= 0.88
    if zoom_bucket == "mid":
        return float(node.get("label_priority") or 0.0) >= 0.7
    return float(node.get("label_priority") or 0.0) >= 0.35


class GraphView(QGraphicsView):
    zoomChanged = Signal(float, str)

    def __init__(self, scene: QGraphicsScene, parent: Optional[QWidget] = None) -> None:
        super().__init__(scene, parent)
        self._zoom_scale = 1.0
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(QColor("#070b10")))
        self.setFrameShape(QGraphicsView.NoFrame)

    def wheelEvent(self, event: Any) -> None:
        delta = event.angleDelta().y()
        factor = 1.12 if delta > 0 else 0.89
        self.scale(factor, factor)
        self._zoom_scale = max(0.4, min(3.5, self._zoom_scale * factor))
        self.zoomChanged.emit(self._zoom_scale, _zoom_bucket(self._zoom_scale))
        event.accept()

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._zoom_scale = 1.0
        self.zoomChanged.emit(self._zoom_scale, _zoom_bucket(self._zoom_scale))

    def viewport_scene_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()


class MiniMapView(QGraphicsView):
    def __init__(self, scene: QGraphicsScene, source_view: GraphView, parent: Optional[QWidget] = None) -> None:
        super().__init__(scene, parent)
        self._source_view = source_view
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setBackgroundBrush(QBrush(QColor("#0b1219")))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(220)
        self.setMaximumHeight(280)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawForeground(painter, rect)
        viewport_rect = self._source_view.viewport_scene_rect()
        painter.setPen(QPen(QColor("#4fe0f5"), 2.0))
        painter.setBrush(QBrush(Qt.NoBrush))
        painter.drawRect(viewport_rect)

    def mousePressEvent(self, event: Any) -> None:
        scene_point = self.mapToScene(event.position().toPoint()) if hasattr(event, "position") else self.mapToScene(event.pos())
        self._source_view.centerOn(scene_point)
        self.viewport().update()
        event.accept()


class GraphRegionItem(QGraphicsPathItem):
    def __init__(self, region: Dict[str, Any]) -> None:
        super().__init__()
        self.region = region
        self.label_item = QGraphicsSimpleTextItem(str(region.get("label") or ""), self)
        self.label_item.setBrush(QColor("#cfd9e2"))
        self.label_item.setZValue(-17)
        self.setZValue(-20)

    def update_geometry(self, rect: QRectF) -> None:
        path = QPainterPath()
        path.addEllipse(rect)
        self.setPath(path)
        self.label_item.setPos(rect.center().x() - (self.label_item.boundingRect().width() / 2.0), rect.top() + 8.0)

    def set_display_context(self, *, zoom_bucket: str, selected: bool, visible: bool) -> None:
        self.setVisible(visible)
        color = QColor(REGION_COLORS.get(str(self.region.get("id") or ""), QColor("#1c2731")))
        color.setAlpha(74 if selected else 46)
        pen_color = QColor("#4fe0f5" if selected else "#2c4357")
        pen_color.setAlpha(150 if selected else 80)
        self.setBrush(QBrush(color))
        self.setPen(QPen(pen_color, 2.2 if selected else 1.2, Qt.DashLine))
        self.label_item.setVisible(zoom_bucket != "close" or selected)


class GraphNodeItem(QGraphicsObject):
    def __init__(self, node: Dict[str, Any], position: QPointF, on_selected: Callable[[str, Dict[str, Any]], None]) -> None:
        super().__init__()
        self.node = node
        self.on_selected = on_selected
        self.hovered = False
        self.selected_for_focus = False
        self.neighbor_for_focus = False
        self.dimmed = False
        self.zoom_bucket = "mid"
        self.phase = 0.0
        self.radius = max(12.0, TIER_RADII.get(int(node.get("tier") or 3), 18.0) + float(node.get("emphasis") or 0.0) * 10.0)
        self.setPos(position)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setAcceptHoverEvents(True)
        self.setToolTip(f"{node.get('label') or node.get('id')} | {node.get('state') or 'ready'}")

    def boundingRect(self) -> QRectF:
        width = self.radius * 7.0
        height = self.radius * 5.0
        return QRectF(-width / 2.0, -self.radius - 12.0, width, height)

    def set_display_context(
        self,
        *,
        zoom_bucket: str,
        selected_id: Optional[str],
        neighbor_ids: set[str],
        dimmed: bool,
    ) -> None:
        self.zoom_bucket = zoom_bucket
        node_id = str(self.node.get("id") or "")
        self.selected_for_focus = node_id == str(selected_id or "")
        self.neighbor_for_focus = node_id in neighbor_ids
        self.dimmed = dimmed and not self.selected_for_focus and not self.neighbor_for_focus and not self.node.get("anchor")
        visible = _detail_visible(str(self.node.get("detail_level") or "mid"), zoom_bucket) or self.selected_for_focus or self.node.get("anchor")
        self.setVisible(visible)
        self.update()

    def hoverEnterEvent(self, event: Any) -> None:
        self.hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: Any) -> None:
        self.hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event: Any) -> None:
        self.on_selected(str(self.node.get("id") or ""), self.node)
        super().mousePressEvent(event)

    def set_phase(self, phase: float) -> None:
        self.phase = phase
        if self.selected_for_focus or self.node.get("anchor") or str(self.node.get("state") or "") in {"active", "warming", "fault", "warning"}:
            self.update()

    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        state = str(self.node.get("state") or "ready")
        color = QColor(STATE_COLORS.get(state, QColor("#7ba6c9")))
        if self.dimmed:
            color.setAlpha(95)
        halo = QColor(color)
        pulse = (math.sin(self.phase) + 1.0) * 0.5
        halo.setAlpha(
            110 + int(pulse * 40)
            if self.selected_for_focus
            else 62 + int(pulse * 28)
            if self.neighbor_for_focus or self.node.get("anchor") or state in {"active", "warming", "fault", "warning"}
            else 0
        )
        tier = int(self.node.get("tier") or 3)
        if self.selected_for_focus or self.neighbor_for_focus or state in {"active", "fault", "warning"}:
            painter.setPen(QPen(halo if halo.alpha() else QColor("#274258"), 2.4 if self.selected_for_focus else 1.4))
            painter.setBrush(QBrush(Qt.NoBrush))
            painter.drawEllipse(QPointF(0, 0), self.radius + (14.0 if tier == 1 else 9.0), self.radius + (14.0 if tier == 1 else 9.0))
        fill = QColor(color)
        fill.setAlpha(235 if not self.dimmed else 100)
        painter.setPen(QPen(QColor("#15212d"), 1.4))
        painter.setBrush(QBrush(fill))
        painter.drawEllipse(QPointF(0, 0), self.radius, self.radius)

        if self.node.get("anchor"):
            ring_pen = QPen(QColor("#4fe0f5" if self.selected_for_focus else "#1fb5cc"), 1.4 if tier > 1 else 2.0)
            painter.setPen(ring_pen)
            painter.setBrush(QBrush(Qt.NoBrush))
            painter.drawEllipse(QPointF(0, 0), self.radius + 4.0, self.radius + 4.0)

        if _label_should_render(self.node, selected=self.selected_for_focus, hovered=self.hovered, zoom_bucket=self.zoom_bucket):
            painter.setPen(QPen(QColor("#f5efe6") if self.selected_for_focus else QColor("#d7e2ea"), 1.0))
            label = str(self.node.get("label") or self.node.get("id") or "")
            text_rect = QRectF(-self.radius * 3.0, self.radius + 8.0, self.radius * 6.0, 40.0)
            painter.drawText(text_rect, Qt.AlignHCenter | Qt.TextWordWrap, label)


class GraphEdgeItem(QGraphicsPathItem):
    def __init__(self, edge: Dict[str, Any], source: QPointF, target: QPointF) -> None:
        super().__init__()
        self.edge = edge
        self.phase = 0.0
        self.source = source
        self.target = target
        self.zoom_bucket = "mid"
        self.selected = False
        self.highlighted = False
        self.dimmed = False
        self.setZValue(-10)
        self._rebuild_path()
        self.setToolTip(f"{edge.get('label') or 'edge'} | {edge.get('source')} -> {edge.get('target')}")

    def _rebuild_path(self) -> None:
        path = QPainterPath(self.source)
        dx = self.target.x() - self.source.x()
        dy = self.target.y() - self.source.y()
        control_offset = max(18.0, math.hypot(dx, dy) * 0.18)
        ctrl1 = QPointF(self.source.x() + dx * 0.35, self.source.y() - control_offset)
        ctrl2 = QPointF(self.target.x() - dx * 0.35, self.target.y() + control_offset * 0.25)
        path.cubicTo(ctrl1, ctrl2, self.target)
        self.setPath(path)

    def set_phase(self, phase: float) -> None:
        self.phase = phase
        self.update()

    def set_display_context(
        self,
        *,
        zoom_bucket: str,
        selected_id: Optional[str],
        neighbor_ids: set[str],
    ) -> None:
        self.zoom_bucket = zoom_bucket
        source = str(self.edge.get("source") or "")
        target = str(self.edge.get("target") or "")
        self.selected = bool(selected_id and selected_id in {source, target})
        self.highlighted = self.selected or (source in neighbor_ids and target in neighbor_ids)
        self.dimmed = bool(selected_id) and not self.highlighted
        detail_level = str(self.edge.get("detail_level") or "mid")
        visible = _detail_visible(detail_level, zoom_bucket) or self.highlighted
        self.setVisible(visible)
        self.update()

    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        category = str(self.edge.get("edge_category") or "concept")
        color = QColor(EDGE_CATEGORY_COLORS.get(category, QColor("#4d6072")))
        if self.dimmed:
            color.setAlpha(35)
        elif self.highlighted:
            color.setAlpha(210)
        else:
            color.setAlpha(110 if self.zoom_bucket != "overview" else 62)
        pen = QPen(color, 2.6 if self.selected else 1.6 if self.highlighted else 1.0)
        if self.selected:
            pen.setDashPattern([7.0, 6.0])
            pen.setDashOffset(self.phase)
        elif category in {"fault", "event"}:
            pen.setDashPattern([3.0, 5.0])
        painter.setPen(pen)
        painter.setBrush(QBrush(Qt.NoBrush))
        painter.drawPath(self.path())


class NativeCognitionGraph(QWidget):
    drillRequested = Signal(dict)
    searchRequested = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._selected_node_id: Optional[str] = None
        self._node_items: Dict[str, GraphNodeItem] = {}
        self._edge_items: List[GraphEdgeItem] = []
        self._region_items: List[GraphRegionItem] = []
        self._graph_payload: Dict[str, Any] = {}
        self._snapshot: Dict[str, Any] = {}
        self._zoom_bucket = "mid"

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(8)

        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self.banner = QLabel("BOOTING | Graph warming in the background.", header)
        self.banner.setProperty("variant", "status")
        self.mode_badge = QLabel("Mode: Live Brain", header)
        self.mode_badge.setProperty("variant", "status")
        self.zoom_badge = QLabel("Zoom: Mid", header)
        self.zoom_badge.setProperty("variant", "status")
        self.focus_badge = QLabel("No focus", header)
        self.focus_badge.setProperty("variant", "status")
        self.search_input = QLineEdit(header)
        self.search_input.setPlaceholderText("Search concepts, files, agents, or sessions...")
        self.search_input.returnPressed.connect(self.focus_search)
        header_layout.addWidget(self.banner, 3)
        header_layout.addWidget(self.mode_badge, 1)
        header_layout.addWidget(self.zoom_badge, 1)
        header_layout.addWidget(self.focus_badge, 1)
        header_layout.addWidget(self.search_input, 2)
        root_layout.addWidget(header)

        self.status_deck = QWidget(self)
        self.status_deck_layout = QHBoxLayout(self.status_deck)
        self.status_deck_layout.setContentsMargins(0, 0, 0, 0)
        self.status_deck_layout.setSpacing(10)
        self.status_cards: List[QLabel] = []
        for index in range(5):
            card = QLabel(self.status_deck)
            card.setWordWrap(True)
            card.setMinimumWidth(230 if index == 0 else 150)
            card.setStyleSheet(
                "QLabel {"
                "background:#101922;"
                "border:1px solid #24384a;"
                "border-radius:12px;"
                "padding:10px 12px;"
                "color:#e7efe9;"
                "}"
            )
            self.status_deck_layout.addWidget(card, 2 if index == 0 else 1)
            self.status_cards.append(card)
        root_layout.addWidget(self.status_deck)

        self.scene = QGraphicsScene(self)
        self.view = GraphView(self.scene, self)
        self.view.zoomChanged.connect(self._handle_zoom_change)
        self.minimap = MiniMapView(self.scene, self.view, self)

        self.inspector_summary = QTextEdit(self)
        self.inspector_summary.setReadOnly(True)
        self.inspector_toggle = QToolButton(self)
        self.inspector_toggle.setText("Developer Details")
        self.inspector_toggle.setCheckable(True)
        self.inspector_toggle.toggled.connect(self._toggle_developer_details)
        self.developer_output = QTextEdit(self)
        self.developer_output.setReadOnly(True)
        self.developer_output.hide()

        inspector_container = QWidget(self)
        inspector_layout = QVBoxLayout(inspector_container)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(6)
        inspector_layout.addWidget(self.inspector_summary, 1)
        inspector_layout.addWidget(self.inspector_toggle, 0)
        inspector_layout.addWidget(self.developer_output, 1)

        graph_stack = QWidget(self)
        graph_stack_layout = QVBoxLayout(graph_stack)
        graph_stack_layout.setContentsMargins(0, 0, 0, 0)
        graph_stack_layout.setSpacing(10)
        graph_stack_layout.addWidget(self.view, 1)
        graph_stack_layout.addWidget(self.minimap, 0)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(graph_stack)
        splitter.addWidget(inspector_container)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([980, 360])
        root_layout.addWidget(splitter, 1)

        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(70)
        self._animation_timer.timeout.connect(self._animate_edges)
        self._sync_animation_timer()

    @property
    def selected_node_id(self) -> Optional[str]:
        return self._selected_node_id

    @property
    def zoom_bucket(self) -> str:
        return self._zoom_bucket

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._sync_animation_timer()

    def hideEvent(self, event: Any) -> None:
        self._sync_animation_timer(active=False)
        super().hideEvent(event)

    def show_developer_payload(self, payload: Dict[str, Any], *, title: str = "Developer Details") -> None:
        self.focus_badge.setText(title)
        self.inspector_summary.setHtml(
            f"<h3 style='margin:0 0 8px 0'>{escape(title)}</h3>"
            "<p style='margin:0 0 12px 0; color:#d4e1ea'>Raw operator payload exposed for debugging and inspection.</p>"
        )
        self.developer_output.setPlainText(_pretty_json(payload))
        self.inspector_toggle.setVisible(True)
        self.inspector_toggle.setChecked(True)
        self.developer_output.show()
        self.mode_badge.setText("Mode: Developer")

    def focus_search(self) -> None:
        text = self.search_input.text().strip()
        if not text:
            return
        lowered = text.lower()
        for node_id, item in self._node_items.items():
            label = str(item.node.get("label") or item.node.get("id") or "").lower()
            if lowered in label or lowered in node_id.lower():
                self._select_node(node_id, reveal=True)
                return
        self.searchRequested.emit(text)

    def clear_focus(self) -> None:
        self._select_node(None)

    def apply_graph(
        self,
        *,
        snapshot: Dict[str, Any],
        graph_payload: Dict[str, Any],
        mode_label: str,
    ) -> None:
        self._snapshot = dict(snapshot or {})
        self._graph_payload = dict(graph_payload or {})
        graph = dict((graph_payload or {}).get("graph") or graph_payload or {})
        nodes = list(graph.get("visible_nodes") or graph.get("nodes") or [])
        edges = list(graph.get("visible_edges") or graph.get("edges") or [])
        regions = list(graph.get("regions") or [])
        self.scene.clear()
        self._node_items.clear()
        self._edge_items.clear()
        self._region_items.clear()

        if not nodes:
            self.banner.setText("WARMING | No graph nodes yet. Background ingestion is still building the cognition substrate.")
            self.focus_badge.setText("No focus")
            self.mode_badge.setText(f"Mode: {mode_label}")
            self.zoom_badge.setText("Zoom: Mid")
            self.inspector_summary.setPlainText("Memory Chamber is warming. The native graph will appear as soon as the first projection is ready.")
            self.developer_output.clear()
            self.scene.setSceneRect(QRectF(0, 0, 1200, 840))
            self._render_status_deck(snapshot=snapshot, graph=graph, mode_label=mode_label)
            self._sync_animation_timer(active=False)
            return

        positions = self._layout(nodes, regions=regions, mode=str(graph.get("mode") or "live_brain"))
        self.scene.setSceneRect(QRectF(0, 0, 1200, 840))
        self._render_regions(regions, positions)

        for edge in edges:
            source = positions.get(str(edge.get("source") or ""))
            target = positions.get(str(edge.get("target") or ""))
            if source is None or target is None:
                continue
            item = GraphEdgeItem(edge, source, target)
            self.scene.addItem(item)
            self._edge_items.append(item)

        for node in nodes:
            node_id = str(node.get("id") or "")
            position = positions.get(node_id)
            if position is None:
                continue
            item = GraphNodeItem(node, position, self._handle_node_selected)
            self.scene.addItem(item)
            self._node_items[node_id] = item

        startup = (snapshot.get("overlays", {}) or {}).get("startup", {})
        state_label = "READY BUT DEGRADED" if startup.get("ready_but_degraded") else str(startup.get("overall_state") or "warming").upper()
        counts = (graph_payload or {}).get("summary", {}).get("counts", {}) if isinstance(graph_payload, dict) else {}
        self.banner.setText(
            f"{state_label} | {mode_label} | Nodes {len(nodes)} of {counts.get('nodes', len(nodes))} | Edges {len(edges)} | Clusters {counts.get('clusters', 0)}"
        )
        self.mode_badge.setText(f"Mode: {mode_label}")

        chosen = str(graph.get("selected_node") or "")
        if not chosen and str(graph.get("mode") or "") == "live_brain":
            chosen = next(
                (
                    str(node.get("id") or "")
                    for node in nodes
                    if str(node.get("id") or "") == "anchor:blackfong"
                ),
                "",
            )
        if not chosen and str(graph.get("mode") or "") == "cluster_map":
            chosen = next(
                (
                    str(node.get("id") or "")
                    for node in nodes
                    if str(node.get("kind") or "") == "cluster" or str(node.get("region_id") or "").startswith("cluster:")
                ),
                "",
            )
        if not chosen:
            chosen = str(nodes[0].get("id") or "")
        self.view.reset_zoom()
        self._zoom_bucket = str(graph.get("zoom_bucket") or "mid")
        self.zoom_badge.setText(f"Zoom: {self._zoom_bucket.title()}")
        self._select_node(chosen, reveal=False)
        self._render_status_deck(snapshot=snapshot, graph=graph, mode_label=mode_label)
        self.minimap.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        self._sync_animation_timer()

    def _handle_zoom_change(self, scale: float, zoom_bucket: str) -> None:
        self._zoom_bucket = zoom_bucket
        self.zoom_badge.setText(f"Zoom: {zoom_bucket.title()}")
        self._apply_display_state()
        self.minimap.viewport().update()

    def _handle_node_selected(self, node_id: str, node: Dict[str, Any]) -> None:
        self._select_node(node_id, reveal=True)
        if node.get("synthetic") and node.get("drill"):
            self.drillRequested.emit(dict(node.get("drill") or {}))

    def _animate_edges(self) -> None:
        for index, item in enumerate(self._edge_items):
            item.set_phase((index * 1.05 + (item.phase + 0.85)) % 12.0)
        for index, item in enumerate(self._node_items.values()):
            item.set_phase((index * 0.42 + (item.phase + 0.18)) % (math.pi * 2.0))

    def _select_node(self, node_id: Optional[str], *, reveal: bool = True) -> None:
        self._selected_node_id = node_id
        self._apply_display_state()
        graph = dict((self._graph_payload.get("graph") or self._graph_payload) or {})
        inspector = dict(graph.get("inspector_card") or graph.get("inspector") or {})
        selected_graph_node = graph.get("selected_node")
        if node_id and inspector and node_id == selected_graph_node:
            self._render_inspector(inspector)
            self.focus_badge.setText(str(inspector.get("title") or node_id))
            if reveal:
                self._gently_focus_node(node_id)
            self._render_status_deck(snapshot=self._snapshot, graph=graph, mode_label=self.mode_badge.text().replace("Mode: ", ""))
            return

        if node_id and node_id in self._node_items:
            node = self._node_items[node_id].node
            fallback = {
                "title": str(node.get("label") or node_id),
                "summary": str(node.get("why") or f"{node.get('kind')} in {node.get('zone')}"),
                "fields": [
                    {"label": "Type", "value": str(node.get("kind") or "node")},
                    {"label": "Zone", "value": str(node.get("zone") or "memory")},
                    {"label": "State", "value": str(node.get("state") or "ready")},
                ],
                "details": [],
                "developer_details": {"meta": node.get("meta") or {}},
            }
            self._render_inspector(fallback)
            self.focus_badge.setText(str(node.get("label") or node_id))
            if reveal:
                self._gently_focus_node(node_id)
            self._render_status_deck(snapshot=self._snapshot, graph=graph, mode_label=self.mode_badge.text().replace("Mode: ", ""))
            return

        self.focus_badge.setText("No focus")
        self.inspector_summary.setPlainText("Select a node to inspect its neighborhood, meaning, and recent graph deltas.")
        self.developer_output.clear()
        self._render_status_deck(snapshot=self._snapshot, graph=graph, mode_label=self.mode_badge.text().replace("Mode: ", ""))

    def _gently_focus_node(self, node_id: Optional[str]) -> None:
        if not node_id or node_id not in self._node_items:
            return
        item = self._node_items[node_id]
        viewport = self.view.viewport_scene_rect()
        if viewport.isNull():
            self.view.centerOn(item)
            self.minimap.viewport().update()
            return
        target = item.sceneBoundingRect().adjusted(-46.0, -46.0, 46.0, 62.0)
        inset_x = max(40.0, viewport.width() * 0.18)
        inset_y = max(36.0, viewport.height() * 0.18)
        comfortable = viewport.adjusted(inset_x, inset_y, -inset_x, -inset_y)
        if comfortable.contains(target.center()):
            self.minimap.viewport().update()
            return
        if viewport.intersects(target):
            self.view.ensureVisible(target, 56, 48)
        else:
            self.view.centerOn(item)
        self.minimap.viewport().update()

    def _sync_animation_timer(self, active: Optional[bool] = None) -> None:
        should_run = bool(active) if active is not None else bool(self.isVisible() and (self._node_items or self._edge_items))
        if should_run and not self._animation_timer.isActive():
            self._animation_timer.start()
        if not should_run and self._animation_timer.isActive():
            self._animation_timer.stop()

    def _apply_display_state(self) -> None:
        selected = self._selected_node_id
        neighbor_ids: set[str] = set()
        for edge_item in self._edge_items:
            source = str(edge_item.edge.get("source") or "")
            target = str(edge_item.edge.get("target") or "")
            if selected and selected in {source, target}:
                neighbor_ids.update({source, target})

        for node_id, item in self._node_items.items():
            dimmed = bool(selected) and node_id not in neighbor_ids and node_id != selected
            item.set_display_context(
                zoom_bucket=self._zoom_bucket,
                selected_id=selected,
                neighbor_ids=neighbor_ids,
                dimmed=dimmed,
            )

        for item in self._edge_items:
            item.set_display_context(
                zoom_bucket=self._zoom_bucket,
                selected_id=selected,
                neighbor_ids=neighbor_ids,
            )

        for item in self._region_items:
            region_nodes = set(item.region.get("node_ids") or [])
            region_selected = bool(selected and selected in region_nodes)
            visible = self._zoom_bucket != "close" or region_selected
            item.set_display_context(zoom_bucket=self._zoom_bucket, selected=region_selected, visible=visible)

    def _render_regions(self, regions: Sequence[Dict[str, Any]], positions: Dict[str, QPointF]) -> None:
        for region in regions:
            region_nodes = [positions[node_id] for node_id in region.get("node_ids") or [] if node_id in positions]
            if len(region_nodes) < 2:
                continue
            xs = [point.x() for point in region_nodes]
            ys = [point.y() for point in region_nodes]
            rect = QRectF(min(xs) - 90.0, min(ys) - 70.0, (max(xs) - min(xs)) + 180.0, (max(ys) - min(ys)) + 140.0)
            item = GraphRegionItem(region)
            item.update_geometry(rect)
            self.scene.addItem(item)
            self._region_items.append(item)

    def _card_field_map(self, card: Dict[str, Any]) -> Dict[str, str]:
        fields = card.get("fields") or []
        mapped: Dict[str, str] = {}
        for field in fields:
            key = str(field.get("label") or "").strip().lower().replace(" ", "_")
            if key:
                mapped[key] = str(field.get("value") or "").strip()
        return mapped

    def _split_card_values(self, value: str) -> List[str]:
        text = str(value or "").strip()
        if not text or text.lower() == "none":
            return []
        return [item.strip() for item in text.split(",") if item.strip()]

    def _accent_for_state(self, state: str) -> str:
        return QColor(STATE_COLORS.get(str(state or "idle").lower(), QColor("#7ba6c9"))).name()

    def _pill_row_html(self, items: Sequence[str], *, accent: str, empty: Optional[str] = None) -> str:
        cleaned = [escape(str(item)) for item in items if str(item).strip()]
        if not cleaned:
            return f"<div style='color:#90a1af'>{escape(empty or 'No linked items in this projection.')}</div>"
        pills = []
        for item in cleaned[:6]:
            pills.append(
                "<span style='display:inline-block; margin:0 8px 8px 0; padding:5px 9px; "
                f"border-radius:999px; border:1px solid {accent}; background:#101922; color:#e9f0f6'>{item}</span>"
            )
        return "".join(pills)

    def _section_html(self, title: str, body: str) -> str:
        return (
            "<div style='margin-top:14px; padding:12px 14px; background:#0f1821; border:1px solid #233544; border-radius:12px'>"
            f"<div style='color:#7ba6c9; font-size:11px; font-weight:600; letter-spacing:0.08em; text-transform:uppercase'>{escape(title)}</div>"
            f"<div style='margin-top:8px'>{body}</div>"
            "</div>"
        )

    def _render_inspector(self, card: Dict[str, Any]) -> None:
        field_map = self._card_field_map(card)
        details = card.get("details") or []
        recent_deltas = card.get("recent_deltas") or []
        actions = card.get("actions") or []
        kind = field_map.get("type", "Node")
        zone = field_map.get("zone", "Memory")
        state = field_map.get("state", "Ready")
        weight = field_map.get("weight", "0.00")
        centrality = field_map.get("centrality", "0.00")
        top_neighbors = self._split_card_values(field_map.get("top_neighbors", ""))
        related_documents = self._split_card_values(field_map.get("related_documents", ""))
        related_agents = self._split_card_values(field_map.get("related_agents", ""))
        accent = self._accent_for_state(state)
        status_pills = [
            f"Type {kind}",
            f"Zone {zone}",
            f"State {state}",
            f"Weight {weight}",
            f"Centrality {centrality}",
        ]
        html = [
            "<div style='padding:14px 16px; background:#101922; border:1px solid #24384a; border-radius:14px'>",
            f"<div style='color:{accent}; font-size:11px; font-weight:700; letter-spacing:0.12em; text-transform:uppercase'>{escape(kind)} | {escape(zone)} | {escape(state)}</div>",
            f"<div style='font-size:22px; font-weight:700; margin-top:6px; color:#f6efe3'>{escape(str(card.get('title') or 'Thoughtglass'))}</div>",
            f"<div style='margin-top:10px; color:#d4e1ea'>{escape(str(card.get('summary') or ''))}</div>",
            f"<div style='margin-top:12px'>{self._pill_row_html(status_pills, accent=accent)}</div>",
            "</div>",
        ]
        if details:
            detail_items = "".join(f"<li style='margin-bottom:6px'>{escape(str(detail))}</li>" for detail in details[:4])
            html.append(self._section_html("Why It Matters", f"<ul style='margin:0; padding-left:18px; color:#ebf1f5'>{detail_items}</ul>"))
        html.append(
            self._section_html(
                "Signal",
                "<div style='display:flex; gap:10px; flex-wrap:wrap'>"
                f"<div style='min-width:120px'><div style='color:#90a1af; font-size:11px'>Weight</div><div style='font-size:19px; font-weight:700; color:#f6efe3'>{escape(weight)}</div></div>"
                f"<div style='min-width:120px'><div style='color:#90a1af; font-size:11px'>Centrality</div><div style='font-size:19px; font-weight:700; color:#f6efe3'>{escape(centrality)}</div></div>"
                "</div>",
            )
        )
        if top_neighbors:
            html.append(self._section_html("Top Neighbors", self._pill_row_html(top_neighbors, accent="#5fc7bb")))
        if related_documents:
            html.append(self._section_html("Related Documents", self._pill_row_html(related_documents, accent="#9dc5ff")))
        if related_agents:
            html.append(self._section_html("Related Agents", self._pill_row_html(related_agents, accent="#dcb96a")))
        if actions:
            html.append(self._section_html("Suggested Actions", self._pill_row_html(actions[:6], accent="#f0c66d")))
        if recent_deltas:
            delta_rows: List[str] = []
            for delta in recent_deltas[:3]:
                label = escape(str(delta.get("label") or delta.get("id") or "snapshot"))
                nodes = escape(str(delta.get("node_count") or 0))
                edges = escape(str(delta.get("edge_count") or 0))
                delta_rows.append(
                    "<div style='padding:8px 10px; margin-bottom:8px; background:#111b24; border:1px solid #24384a; border-radius:10px'>"
                    f"<div style='font-weight:600; color:#f6efe3'>{label}</div>"
                    f"<div style='color:#90a1af; margin-top:4px'>Nodes {nodes} | Edges {edges}</div>"
                    "</div>"
                )
            html.append(self._section_html("Recent Deltas", "".join(delta_rows)))
        self.inspector_summary.setHtml("".join(html))
        developer_details = card.get("developer_details") or {}
        self.developer_output.setPlainText(_pretty_json(developer_details) if developer_details else "")
        self.inspector_toggle.setVisible(bool(developer_details))
        if not developer_details:
            self.inspector_toggle.setChecked(False)
            self.developer_output.hide()

    def _render_status_deck(self, *, snapshot: Dict[str, Any], graph: Dict[str, Any], mode_label: str) -> None:
        cards = self._build_status_cards(snapshot=snapshot, graph=graph, mode_label=mode_label)
        for index, label in enumerate(self.status_cards):
            if index < len(cards):
                card = cards[index]
                accent = str(card.get("accent") or "#7ba6c9")
                priority = str(card.get("priority") or ("hero" if index == 0 else "standard"))
                if priority == "hero":
                    label.setMinimumWidth(240)
                    label.setStyleSheet(
                        "QLabel {"
                        f"background:#111b24; border:1px solid {accent}; border-radius:14px; padding:14px 16px; color:#f4efe6;"
                        "}"
                    )
                    value_size = 22
                    summary_color = "#d9e2ea"
                else:
                    label.setMinimumWidth(150)
                    label.setStyleSheet(
                        "QLabel {"
                        f"background:#101922; border:1px solid {accent}; border-radius:12px; padding:10px 12px; color:#e7efe9;"
                        "}"
                    )
                    value_size = 16
                    summary_color = "#aebdcc"
                label.setText(
                    f"<div style='color:{accent}; font-size:11px; font-weight:700; letter-spacing:0.08em; text-transform:uppercase'>{escape(str(card.get('title') or 'Status'))}</div>"
                    f"<div style='font-size:{value_size}px; font-weight:700; margin-top:6px'>{escape(str(card.get('value') or ''))}</div>"
                    f"<div style='color:{summary_color}; margin-top:7px'>{escape(str(card.get('summary') or ''))}</div>"
                    + (
                        f"<div style='color:#7c92a3; margin-top:6px; font-size:11px'>{escape(str(card.get('meta') or ''))}</div>"
                        if card.get("meta")
                        else ""
                    )
                )
                label.show()
            else:
                label.hide()

    def _build_status_cards(self, *, snapshot: Dict[str, Any], graph: Dict[str, Any], mode_label: str) -> List[Dict[str, Any]]:
        nodes = list(graph.get("visible_nodes") or graph.get("nodes") or [])
        mode = str(graph.get("mode") or "").lower()
        startup = (snapshot.get("overlays", {}) or {}).get("startup", {})
        counts = (self._graph_payload.get("summary") or {}).get("counts", {}) if isinstance(self._graph_payload, dict) else {}
        selected_id = str(graph.get("selected_node") or "")
        visible_edges = len(graph.get("visible_edges") or graph.get("edges") or [])
        overall_state = "ready but degraded" if startup.get("ready_but_degraded") else str(startup.get("overall_state") or "warming").replace("_", " ")

        def accent_for_state(state: str) -> str:
            return {
                "active": "#e4c276",
                "ready": "#6fe0b7",
                "warming": "#7bd0da",
                "booting": "#7ba6c9",
                "degraded": "#ff8076",
                "fault": "#ff8076",
                "warning": "#ff9d72",
                "manual_only": "#9db2c6",
                "offline": "#7a8591",
                "missing": "#596470",
            }.get(state, "#7ba6c9")

        def node_by_id(node_id: str) -> Optional[Dict[str, Any]]:
            return next((item for item in nodes if str(item.get("id") or "") == node_id), None)

        selected = node_by_id(selected_id) if selected_id else None

        if mode == "live_brain":
            blackfong = node_by_id("anchor:blackfong")
            daise = node_by_id("anchor:daise")
            tiny = node_by_id("anchor:tinyprogrammer")
            active_cluster = node_by_id("anchor:active_cluster")
            faults = node_by_id("anchor:faults")
            focus_node = selected or blackfong
            return [
                {
                    "priority": "hero",
                    "title": "Workspace State",
                    "value": overall_state.title(),
                    "summary": f"{mode_label} is foregrounding the active cognition surface.",
                    "meta": f"Focus: {str(focus_node.get('label') or 'Blackfong') if focus_node else 'Blackfong'}",
                    "accent": accent_for_state(str((focus_node or {}).get("state") or startup.get("overall_state") or "ready")),
                },
                {
                    "title": "Selected Focus",
                    "value": str((focus_node or {}).get("label") or "Blackfong"),
                    "summary": str(((focus_node or {}).get("meta") or {}).get("summary") or (focus_node or {}).get("why") or "Primary operator anchor."),
                    "accent": accent_for_state(str((focus_node or {}).get("state") or "ready")),
                },
                {
                    "title": "Active Cluster",
                    "value": str((active_cluster or {}).get("label") or "Stabilizing"),
                    "summary": str(((active_cluster or {}).get("meta") or {}).get("summary") or (active_cluster or {}).get("why") or "Memory mass nearest the live operator view."),
                    "accent": accent_for_state(str((active_cluster or {}).get("state") or "warming")),
                },
                {
                    "title": "Agent Rhythm",
                    "value": f"DAISE {str((daise or {}).get('state') or 'ready').replace('_', ' ').title()}",
                    "summary": str(((tiny or {}).get("meta") or {}).get("summary") or (faults or {}).get("why") or "Tiny and fault lanes remain visible at a glance."),
                    "meta": f"Tiny {str((tiny or {}).get('state') or 'ready').replace('_', ' ').title()} | Faults {str((faults or {}).get('state') or 'ready').replace('_', ' ').title()}",
                    "accent": accent_for_state(str((daise or {}).get("state") or "ready")),
                },
                {
                    "title": "Graph Scale",
                    "value": f"{len(nodes)} / {counts.get('nodes', len(nodes))}",
                    "summary": f"{visible_edges} visible edges | {counts.get('clusters', 0)} clusters tracked.",
                    "accent": "#7ba6c9",
                },
            ]

        if mode == "cluster_map":
            cluster_nodes = [
                item for item in nodes
                if str(item.get("kind") or "") == "cluster" or str(item.get("region_id") or "").startswith("cluster:")
            ]
            cluster_labels = [str(item.get("label") or "") for item in cluster_nodes[:3]]
            return [
                {
                    "priority": "hero",
                    "title": "Memory Chamber",
                    "value": mode_label,
                    "summary": "Cluster-first archive view over the same cognition substrate.",
                    "meta": f"{len(cluster_nodes)} visible clusters | {counts.get('nodes', len(nodes))} stored nodes",
                    "accent": "#dcb96a",
                },
                {
                    "title": "Visible Clusters",
                    "value": str(len(cluster_nodes)),
                    "summary": ", ".join(cluster_labels) or "No clusters visible yet.",
                    "accent": "#5fc7bb",
                },
                {
                    "title": "Selected Focus",
                    "value": str(selected.get("label") or "None") if selected else "None",
                    "summary": "Current archive focus for neighborhood and document drill-ins.",
                    "accent": "#9dc5ff",
                },
                {
                    "title": "Graph Scale",
                    "value": f"{len(nodes)} / {counts.get('nodes', len(nodes))}",
                    "summary": "Visible nodes against the current stored cognition graph size.",
                    "accent": "#7ba6c9",
                },
            ]

        overall = str(startup.get("overall_state") or "warming")
        return [
            {
                "priority": "hero",
                "title": "Mode",
                "value": mode_label,
                "summary": "Focused graph slice over the same shared backend.",
                "meta": f"State: {overall_state.title()}",
                "accent": "#dcb96a",
            },
            {
                "title": "Startup",
                "value": overall.replace("_", " ").title(),
                "summary": "Workspace startup truth remains shared across the shell.",
                "accent": accent_for_state(overall),
            },
            {
                "title": "Selected",
                "value": str(selected.get("label") or "None") if selected else "None",
                "summary": "Current graph focus and inspection target.",
                "accent": "#9dc5ff",
            },
            {
                "title": "Visible Graph",
                "value": f"{len(nodes)} nodes",
                "summary": f"{visible_edges} edges visible in this mode.",
                "accent": "#5fc7bb",
            },
        ]

    def _toggle_developer_details(self, checked: bool) -> None:
        self.developer_output.setVisible(bool(checked))

    def _layout(self, nodes: List[Dict[str, Any]], *, regions: Sequence[Dict[str, Any]], mode: str) -> Dict[str, QPointF]:
        if mode == "live_brain":
            return self._layout_live_brain(nodes)
        if mode == "cluster_map":
            return self._layout_cluster_map(nodes)
        return self._layout_zone_arcs(nodes)

    def _layout_live_brain(self, nodes: List[Dict[str, Any]]) -> Dict[str, QPointF]:
        width = 1120.0
        height = 760.0
        center_x = width / 2.0
        center_y = height / 2.0
        slot_positions = {
            "user": QPointF(center_x, 118.0),
            "blackfong": QPointF(center_x, center_y - 18.0),
            "daise": QPointF(center_x, center_y + 156.0),
            "system": QPointF(812.0, 372.0),
            "tiny": QPointF(780.0, 214.0),
            "memory": QPointF(282.0, 344.0),
            "queue": QPointF(812.0, 582.0),
            "faults": QPointF(886.0, 490.0),
            "integrations": QPointF(868.0, 164.0),
            "active_cluster": QPointF(284.0, 192.0),
            "current_task": QPointF(450.0, 598.0),
            "current_session": QPointF(408.0, 146.0),
            "recent_memory": QPointF(236.0, 492.0),
            "current_fault": QPointF(930.0, 640.0),
            "sensors": QPointF(938.0, 304.0),
        }
        region_centers = {
            "operator_core": QPointF(center_x, center_y),
            "memory_region": QPointF(314.0, 356.0),
            "system_ring": QPointF(810.0, 362.0),
            "fault_region": QPointF(892.0, 544.0),
            "sensor_region": QPointF(892.0, 258.0),
        }
        positions: Dict[str, QPointF] = {}
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for node in nodes:
            slot = str(node.get("home_slot") or "")
            node_id = str(node.get("id") or "")
            if slot and slot in slot_positions:
                positions[node_id] = slot_positions[slot]
                continue
            grouped.setdefault(str(node.get("region_id") or "memory_region"), []).append(node)

        for region_id, items in grouped.items():
            center = region_centers.get(region_id, QPointF(center_x, center_y))
            primary_radius = 162.0 if region_id == "memory_region" else 128.0 if region_id == "system_ring" else 104.0
            secondary_radius = primary_radius + 58.0
            ordered = sorted(items, key=lambda item: (-float(item.get("label_priority") or 0.0), str(item.get("label") or "")))
            for index, node in enumerate(ordered):
                ring_index = index // 8
                slot_index = index % 8
                slots = min(8, max(len(ordered) - (ring_index * 8), 1))
                angle = (math.pi * 2.0 * slot_index) / max(slots, 1)
                radius = primary_radius if ring_index == 0 else secondary_radius + ((ring_index - 1) * 38.0)
                positions[str(node.get("id"))] = QPointF(
                    center.x() + math.cos(angle) * radius,
                    center.y() + math.sin(angle) * (radius * 0.75),
                )
        return positions

    def _layout_cluster_map(self, nodes: List[Dict[str, Any]]) -> Dict[str, QPointF]:
        width = 1120.0
        height = 760.0
        center_x = width / 2.0
        center_y = height / 2.0
        anchors = [node for node in nodes if node.get("anchor")]
        others = [node for node in nodes if not node.get("anchor")]
        positions: Dict[str, QPointF] = {}
        cluster_centers: Dict[str, QPointF] = {}
        for index, node in enumerate(anchors):
            angle = (math.pi * 2.0 * index) / max(len(anchors), 1)
            point = QPointF(center_x + math.cos(angle) * 250.0, center_y + math.sin(angle) * 220.0)
            node_id = str(node.get("id"))
            positions[node_id] = point
            cluster_centers[str(node.get("region_id") or node_id)] = point
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for node in others:
            grouped.setdefault(str(node.get("region_id") or "memory_region"), []).append(node)
        for region_id, items in grouped.items():
            center = cluster_centers.get(region_id, QPointF(center_x, center_y))
            ordered = sorted(items, key=lambda item: (-float(item.get("label_priority") or 0.0), str(item.get("label") or "")))
            for index, node in enumerate(ordered):
                ring_index = index // 6
                slot_index = index % 6
                slots = min(6, max(len(ordered) - (ring_index * 6), 1))
                angle = (math.pi * 2.0 * slot_index) / max(slots, 1)
                radius = 94.0 + (ring_index * 42.0)
                positions[str(node.get("id"))] = QPointF(
                    center.x() + math.cos(angle) * radius,
                    center.y() + math.sin(angle) * (radius * 0.72),
                )
        return positions

    def _layout_zone_arcs(self, nodes: List[Dict[str, Any]]) -> Dict[str, QPointF]:
        width = 1120.0
        height = 760.0
        center_x = width / 2.0
        center_y = height / 2.0
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for node in nodes:
            buckets.setdefault(str(node.get("zone") or "memory"), []).append(node)

        def place_arc(zone: str, radius: float, start: float, end: float) -> Dict[str, QPointF]:
            items = buckets.get(zone, [])
            placed: Dict[str, QPointF] = {}
            for index, node in enumerate(items):
                angle = (start + end) / 2.0 if len(items) == 1 else start + ((end - start) * index) / max(len(items) - 1, 1)
                placed[str(node.get("id"))] = QPointF(
                    center_x + math.cos(angle) * radius,
                    center_y + math.sin(angle) * radius * 0.82,
                )
            return placed

        positions: Dict[str, QPointF] = {}
        positions.update(place_arc("memory", 220.0, math.pi * 0.7, math.pi * 1.3))
        positions.update(place_arc("agent", 250.0, -math.pi * 0.35, math.pi * 0.3))
        positions.update(place_arc("forge", 290.0, math.pi * 0.1, math.pi * 0.55))
        positions.update(place_arc("sensor", 250.0, math.pi * 0.55, math.pi * 1.0))
        positions.update(place_arc("tasks", 190.0, math.pi * 1.25, math.pi * 1.8))
        positions.update(place_arc("queue", 170.0, math.pi * 1.35, math.pi * 1.65))
        for index, node in enumerate(nodes):
            node_id = str(node.get("id") or "")
            if node_id in positions:
                continue
            angle = (index / max(len(nodes), 1)) * math.pi * 2.0
            positions[node_id] = QPointF(
                center_x + math.cos(angle) * 300.0,
                center_y + math.sin(angle) * 240.0,
            )
        return positions
