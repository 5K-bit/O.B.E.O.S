from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    import networkx as nx
except Exception:  # pragma: no cover - optional at runtime
    nx = None

from .config import paths
from .cognition.graph_model import CognitionEdge, CognitionNode
from .external_integrations import ExternalIntegrationRegistry
from .retrieval_profiles import normalize_retrieval_profile, retrieval_profile_settings
from .storage import ChromaVectorStore, EventStore, NullVectorStore, VectorStore

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    ".venv",
    ".venv-codex",
    ".venv311-codex",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    "tmp_chroma_smoke",
    "tmp_chroma_smoke_2",
    "tmp_chroma_smoke_3",
    "blackfong_ecosystem.egg-info",
    "obeos-remaster",
    "verification",
    "vm",
    "transfer",
}
DEFAULT_ALLOWED_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".sh",
    ".ps1",
    ".bat",
    ".toml",
    ".ini",
    ".cfg",
    ".log",
}
MAX_FILE_BYTES = 512_000
MAX_FILES_PER_ROOT = 400
MAX_EVENT_DOCUMENTS = 600
MAX_SESSION_DOCUMENTS = 120
MAX_DOCUMENT_TERMS = 14
MAX_SEMANTIC_HITS = 6
WATCH_INTERVAL_S = 45.0
DISPLAY_MODE_ALIASES = {
    "thoughtglass_home": "live_brain",
    "memory_chamber": "cluster_map",
    "forensics": "documents",
    "agent_view": "agents",
    "fault_region": "faults",
    "full_debug": "full_debug_graph",
}
MODE_NODE_BUDGETS = {
    "live_brain": 36,
    "cluster_map": 72,
    "neighborhood": 54,
    "documents": 48,
    "agents": 42,
    "faults": 40,
    "full_debug_graph": 120,
    "tiny_region": 40,
    "sensor_region": 32,
}
MODE_EDGE_BUDGETS = {
    "live_brain": 64,
    "cluster_map": 132,
    "neighborhood": 118,
    "documents": 102,
    "agents": 90,
    "faults": 78,
    "full_debug_graph": 300,
    "tiny_region": 84,
    "sensor_region": 64,
}
EDGE_FILTER_ALIASES = {
    "top": "top",
    "show_more": "show_more",
    "show_all": "show_all",
    "sequence": "sequence",
    "concept": "concept",
    "agent": "agent",
    "document": "document",
    "fault": "fault",
    "event": "event",
}
ZOOM_BUCKETS = ("overview", "mid", "close")

STOPWORDS = {
    "about",
    "after",
    "agent",
    "all",
    "also",
    "and",
    "any",
    "are",
    "back",
    "been",
    "before",
    "being",
    "blackfong",
    "both",
    "build",
    "button",
    "call",
    "came",
    "can",
    "code",
    "could",
    "current",
    "data",
    "daise",
    "did",
    "does",
    "done",
    "each",
    "else",
    "for",
    "from",
    "get",
    "had",
    "has",
    "have",
    "her",
    "here",
    "him",
    "his",
    "how",
    "into",
    "its",
    "just",
    "like",
    "main",
    "make",
    "many",
    "mode",
    "more",
    "most",
    "much",
    "need",
    "none",
    "not",
    "now",
    "obeos",
    "off",
    "one",
    "only",
    "other",
    "out",
    "over",
    "part",
    "path",
    "phase",
    "plan",
    "project",
    "quick",
    "really",
    "same",
    "service",
    "shell",
    "should",
    "show",
    "some",
    "still",
    "such",
    "system",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "time",
    "tiny",
    "tinyprogrammer",
    "true",
    "use",
    "user",
    "using",
    "very",
    "want",
    "well",
    "were",
    "what",
    "when",
    "which",
    "while",
    "with",
    "work",
    "would",
}

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_./:\\-]{2,}")
CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")
PATH_RE = re.compile(r"(?:(?:\.\.?[/\\])|(?:[A-Za-z0-9_.-]+[/\\]))[A-Za-z0-9_./\\-]+\.(?:py|js|ts|tsx|jsx|md|json|yaml|yml|txt|log)")
IMPORT_RE = re.compile(
    r"""
    ^\s*(?:from\s+(?P<from>[.\w/\\-]+)\s+import\s+(?P<what>[\w*, ]+)
        |import\s+(?P<import>[\w., ]+)
        |const\s+.*?=\s*require\(['"](?P<require>[^'"]+)['"]\)
        |import\s+.*?from\s+['"](?P<jsfrom>[^'"]+)['"])
    """,
    re.MULTILINE | re.VERBOSE,
)
SESSION_EVENT_TYPES = {
    "agent_query",
    "agent_response",
    "routing_decision",
    "memory_retrieval",
    "agent_report",
    "agent_evaluation",
    "agent_selection",
    "reasoning_trace",
    "mobray_interrogation",
}
TINY_EVENT_TYPES = {
    "tinyprogrammer_program_created",
    "tinyprogrammer_program_archived",
    "tinyprogrammer_reflection",
    "tinyprogrammer_brief_applied",
    "tinyprogrammer_feedback",
    "tinyprogrammer_process_state",
    "tinyprogrammer_status",
}


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def _json_loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256("::".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:18]
    return f"{prefix}:{digest}"


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _identifier_parts(token: str) -> List[str]:
    cleaned = token.replace("\\", "/")
    parts: List[str] = []
    for chunk in re.split(r"[./:_\\-]+", cleaned):
        if not chunk:
            continue
        parts.extend(CAMEL_BOUNDARY_RE.sub(" ", chunk).split())
    return [part.lower() for part in parts if len(part) >= 3]


def _token_candidates(text: str) -> List[str]:
    tokens: List[str] = []
    for match in TOKEN_RE.findall(text):
        token = match.strip().strip(".,:;()[]{}<>\"'")
        if len(token) < 3:
            continue
        lowered = token.lower()
        if lowered in STOPWORDS:
            continue
        tokens.append(lowered)
        for piece in _identifier_parts(token):
            if piece not in STOPWORDS:
                tokens.append(piece)
    return tokens


def _path_category(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in {".py", ".js", ".ts", ".tsx", ".jsx"}:
        return "source_code", suffix.lstrip(".") or "code"
    if suffix in {".md", ".txt"}:
        return "notes", suffix.lstrip(".")
    if suffix in {".json", ".yaml", ".yml"}:
        return "structured", suffix.lstrip(".")
    if suffix == ".log":
        return "log", "log"
    return "artifact", suffix.lstrip(".") or "artifact"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


class MemoryChamberService:
    """Unified cognition graph, analysis workbench, and live background ingest service."""

    def __init__(
        self,
        *,
        event_store: Optional[EventStore] = None,
        vector_store: Optional[VectorStore] = None,
        workspace_root: Optional[Path] = None,
        integration_registry: Optional[ExternalIntegrationRegistry] = None,
        startup_coordinator: Any = None,
        extra_roots: Optional[Iterable[Path]] = None,
        start_watch: bool = True,
        watch_interval_s: float = WATCH_INTERVAL_S,
    ) -> None:
        self.event_store = event_store or EventStore()
        self.event_store.init_db()
        self.vector_store = vector_store or NullVectorStore()
        self.workspace_root = Path(workspace_root or paths.root)
        self._include_external_defaults = self.workspace_root.resolve() == paths.root.resolve()
        self.integration_registry = integration_registry or ExternalIntegrationRegistry(workspace_root=self.workspace_root)
        self.startup_coordinator = startup_coordinator
        self.extra_roots = [Path(root) for root in (extra_roots or [])]
        self.graph_read_scope = "graph_read_scope"
        self.graph_write_scope = "graph_write_scope"
        self.agent_write_scope = "agent_write_scope"
        self._status_lock = threading.RLock()
        self._graph_cache_lock = threading.RLock()
        self._graph_cache: Any = None
        self._graph_cache_signature: Optional[tuple[int, int, float]] = None
        self._graph_cache_epoch = 0
        self._graph_prewarm_thread: Optional[threading.Thread] = None
        self._graph_prewarm_epoch: Optional[int] = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory-chamber")
        self._index_future: Optional[Future[Any]] = None
        self._watch_stop = threading.Event()
        self._watch_thread: Optional[threading.Thread] = None
        self._watch_interval_s = max(20.0, float(watch_interval_s))
        self._last_signature: Optional[tuple[int, int, int]] = None
        self._fts_enabled = self._detect_fts5()
        self._status: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "state": "booting",
            "started_at": time.time(),
            "updated_at": time.time(),
            "last_indexed_at": None,
            "last_error": None,
            "last_error_code": None,
            "queue_depth": 0,
            "watch_mode": "polling" if start_watch else "manual",
            "fts_enabled": self._fts_enabled,
            "counts": {"documents": 0, "nodes": 0, "edges": 0, "clusters": 0},
            "graph_cache_state": "cold",
            "graph_cache_warmed_at": None,
        }
        self._restore_runtime_state_from_storage(start_watch=start_watch)
        if start_watch:
            self._start_watch()
        self._schedule_graph_prewarm(reason="startup")

    def shutdown(self) -> None:
        self._watch_stop.set()
        if self._watch_thread is not None and self._watch_thread.is_alive():
            self._watch_thread.join(timeout=1.0)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def ensure_live_watch(self, *, start_index: bool = False) -> None:
        if self._watch_thread is None or not self._watch_thread.is_alive():
            self._watch_stop.clear()
            self._status["watch_mode"] = "polling"
            self._start_watch()
        if start_index and not self.summary().get("counts", {}).get("nodes"):
            self.request_reindex(reason="warmup", background=True)
        else:
            self._schedule_graph_prewarm(reason="ensure_live_watch")

    def status(self) -> Dict[str, Any]:
        with self._status_lock:
            payload = dict(self._status)
        payload["watch_roots"] = [str(root) for root in self.watch_roots()]
        payload["permissions"] = self.permissions()
        payload["top_concepts"] = self.top_concepts(limit=6)
        payload["recent_deltas"] = self.recent_deltas(limit=6)
        payload["clusters"] = self.clusters(limit=4)
        return payload

    def permissions(self) -> Dict[str, Any]:
        return {
            "graph_read_scope": {
                "description": "Read graph documents, nodes, edges, snapshots, and analysis outputs.",
                "allows": ["summary", "graph", "neighborhood", "clusters", "orphans", "missing_links", "deltas"],
            },
            "graph_write_scope": {
                "description": "Rebuild canonical graph facts, clusters, snapshots, and embeddings.",
                "allows": ["reindex", "persist_snapshot", "promote_relationship_hint"],
            },
            "agent_write_scope": {
                "description": "Append artifacts, traces, hints, and sessions without mutating canonical graph facts directly.",
                "allows": ["append_agent_artifact", "propose_relationship_hint", "append_session_trace"],
            },
        }

    def watch_roots(self) -> List[Path]:
        roots: List[Path] = []
        candidates = [self.workspace_root, *self.extra_roots]
        if self._include_external_defaults:
            tiny_root = self.workspace_root.parent / "TinyProgrammer-main"
            if tiny_root.exists():
                candidates.append(tiny_root)
            try:
                for integration_id in self.integration_registry.discoverable_ids():
                    try:
                        details = self.integration_registry.describe(integration_id)
                    except KeyError:
                        continue
                    project_path = Path(str(details.get("project_path") or ""))
                    if details.get("available") and project_path.exists():
                        candidates.append(project_path)
            except Exception:
                logger.exception("memory_chamber_watch_root_discovery_failed")
        for root in candidates:
            resolved = Path(root)
            if resolved.exists() and resolved not in roots:
                roots.append(resolved)
        return roots

    def request_reindex(
        self,
        *,
        reason: str = "manual",
        roots: Optional[Sequence[Path]] = None,
        background: bool = True,
    ) -> Dict[str, Any]:
        if not background:
            return self._reindex(reason=reason, roots=list(roots) if roots else None)
        with self._status_lock:
            if self._index_future is not None and not self._index_future.done():
                self._status["queue_depth"] = 1
                self._status["state"] = "warming"
                self._status["updated_at"] = time.time()
                return {"status": "queued", "reason": reason, "state": self._status["state"]}
            self._status["state"] = "warming"
            self._status["updated_at"] = time.time()
            self._status["queue_depth"] = 1
            self._index_future = self._executor.submit(self._reindex, reason=reason, roots=list(roots) if roots else None)
            self._index_future.add_done_callback(self._complete_future)
        return {"status": "accepted", "reason": reason, "state": "warming"}

    def summary(self) -> Dict[str, Any]:
        counts = self._query_one(
            """
            SELECT
                (SELECT COUNT(*) FROM memory_chamber_documents) AS documents,
                (SELECT COUNT(*) FROM memory_chamber_nodes) AS nodes,
                (SELECT COUNT(*) FROM memory_chamber_edges) AS edges,
                (SELECT COUNT(*) FROM memory_chamber_clusters) AS clusters
            """
        )
        with self._status_lock:
            status = dict(self._status)
        status["counts"] = {
            "documents": int((counts or {}).get("documents") or 0),
            "nodes": int((counts or {}).get("nodes") or 0),
            "edges": int((counts or {}).get("edges") or 0),
            "clusters": int((counts or {}).get("clusters") or 0),
        }
        status["watch_roots"] = [str(root) for root in self.watch_roots()]
        status["top_concepts"] = self.top_concepts(limit=8)
        status["active_clusters"] = self.clusters(limit=6)
        status["recent_deltas"] = self.recent_deltas(limit=6)
        status["permissions"] = self.permissions()
        return status

    def graph(
        self,
        *,
        mode: str = "memory_chamber",
        layer: str = "live_brain",
        selected_node: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 36,
        edge_filter: str = "top",
        zoom_bucket: Optional[str] = None,
        max_nodes: Optional[int] = None,
        max_edges: Optional[int] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        summary_override: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        canonical_mode = self._normalize_display_mode(mode)
        projection = self.build_projection(
            mode=canonical_mode,
            layer=layer,
            selected_node=selected_node,
            search=search,
            limit=limit,
            edge_filter=edge_filter,
            zoom_bucket=zoom_bucket,
            max_nodes=max_nodes,
            max_edges=max_edges,
            session_id=session_id,
            request_id=request_id,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": canonical_mode,
            "layer": layer,
            "summary": dict(summary_override or self.summary()),
            "graph": projection,
        }

    def build_projection(
        self,
        *,
        mode: str = "thoughtglass_home",
        layer: str = "live_brain",
        selected_node: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 30,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        edge_filter: str = "top",
        zoom_bucket: Optional[str] = None,
        max_nodes: Optional[int] = None,
        max_edges: Optional[int] = None,
    ) -> Dict[str, Any]:
        canonical_mode = self._normalize_display_mode(mode)
        resolved_filter = self._normalize_edge_filter(edge_filter)
        resolved_zoom = zoom_bucket if zoom_bucket in ZOOM_BUCKETS else ("mid" if canonical_mode != "full_debug_graph" else "close")
        node_budget = max_nodes or MODE_NODE_BUDGETS.get(canonical_mode, max(12, limit))
        edge_budget = max_edges or MODE_EDGE_BUDGETS.get(canonical_mode, max(40, limit * 3))

        if canonical_mode == "live_brain":
            projection = self._build_live_brain_projection(
                layer=layer,
                selected_node=selected_node,
                search=search,
                node_budget=node_budget,
                edge_budget=edge_budget,
                edge_filter=resolved_filter,
                zoom_bucket=resolved_zoom,
                session_id=session_id,
                request_id=request_id,
            )
        elif canonical_mode == "cluster_map":
            projection = self._build_cluster_map_projection(
                layer=layer,
                selected_node=selected_node,
                search=search,
                node_budget=node_budget,
                edge_budget=edge_budget,
                edge_filter=resolved_filter,
                zoom_bucket=resolved_zoom,
            )
        elif canonical_mode == "documents":
            projection = self._build_documents_projection(
                layer=layer,
                selected_node=selected_node,
                search=search,
                node_budget=node_budget,
                edge_budget=edge_budget,
                edge_filter=resolved_filter,
                zoom_bucket=resolved_zoom,
            )
        elif canonical_mode == "agents":
            projection = self._build_agents_projection(
                layer=layer,
                selected_node=selected_node,
                search=search,
                node_budget=node_budget,
                edge_budget=edge_budget,
                edge_filter=resolved_filter,
                zoom_bucket=resolved_zoom,
            )
        elif canonical_mode == "faults":
            projection = self._build_faults_projection(
                layer=layer,
                selected_node=selected_node,
                search=search,
                node_budget=node_budget,
                edge_budget=edge_budget,
                edge_filter=resolved_filter,
                zoom_bucket=resolved_zoom,
            )
        elif canonical_mode == "tiny_region":
            projection = self._build_tiny_projection(
                layer=layer,
                selected_node=selected_node,
                search=search,
                node_budget=node_budget,
                edge_budget=edge_budget,
                edge_filter=resolved_filter,
                zoom_bucket=resolved_zoom,
            )
        elif canonical_mode == "sensor_region":
            projection = self._build_sensor_projection(
                layer=layer,
                selected_node=selected_node,
                search=search,
                node_budget=node_budget,
                edge_budget=edge_budget,
                edge_filter=resolved_filter,
                zoom_bucket=resolved_zoom,
            )
        elif canonical_mode == "neighborhood":
            projection = self._build_neighborhood_projection(
                layer=layer,
                selected_node=selected_node,
                search=search,
                node_budget=node_budget,
                edge_budget=edge_budget,
                edge_filter=resolved_filter,
                zoom_bucket=resolved_zoom,
            )
        else:
            projection = self._build_full_debug_projection(
                layer=layer,
                selected_node=selected_node,
                search=search,
                node_budget=node_budget,
                edge_budget=edge_budget,
                edge_filter=resolved_filter,
                zoom_bucket=resolved_zoom,
            )

        projection.update(
            {
                "mode": canonical_mode,
                "layer": layer,
                "session_id": session_id,
                "request_id": request_id,
                "zoom_bucket": resolved_zoom,
                "edge_filter_state": {
                    "selected": resolved_filter,
                    "available": list(EDGE_FILTER_ALIASES.values()),
                },
                "caps": {"max_nodes": node_budget, "max_edges": edge_budget},
            }
        )
        if "inspector_card" not in projection and "inspector" in projection:
            projection["inspector_card"] = projection["inspector"]
        if "visible_nodes" not in projection:
            projection["visible_nodes"] = list(projection.get("nodes") or [])
        if "visible_edges" not in projection:
            projection["visible_edges"] = list(projection.get("edges") or [])
        if "anchor_nodes" not in projection:
            projection["anchor_nodes"] = []
        if "regions" not in projection:
            projection["regions"] = []
        return projection

    def _normalize_display_mode(self, mode: str) -> str:
        normalized = str(mode or "live_brain").strip().lower().replace(" ", "_")
        return DISPLAY_MODE_ALIASES.get(normalized, normalized)

    def _normalize_edge_filter(self, edge_filter: str) -> str:
        normalized = str(edge_filter or "top").strip().lower().replace(" ", "_")
        return EDGE_FILTER_ALIASES.get(normalized, "top")

    def _empty_projection(self, *, title: str, summary: str) -> Dict[str, Any]:
        inspector = {"title": title, "summary": summary, "details": [], "developer_details": {}}
        return {
            "nodes": [],
            "edges": [],
            "visible_nodes": [],
            "visible_edges": [],
            "selected_node": None,
            "inspector": inspector,
            "inspector_card": inspector,
            "anchor_nodes": [],
            "regions": [],
        }

    def _build_live_brain_projection(
        self,
        *,
        layer: str,
        selected_node: Optional[str],
        search: Optional[str],
        node_budget: int,
        edge_budget: int,
        edge_filter: str,
        zoom_bucket: str,
        session_id: Optional[str],
        request_id: Optional[str],
    ) -> Dict[str, Any]:
        startup = self.startup_coordinator.snapshot() if self.startup_coordinator is not None else {}
        top_concepts = self.top_concepts(limit=8)
        cluster_rows = self._cluster_rows(limit=3)
        fault_rows = self._rows_for_faults(limit=6)
        recent_docs = self._rows_for_recent_documents(limit=6)
        recent_sessions = self.event_store.list_recent_sessions(limit=2)
        integration_listing = self.integration_registry.list_integrations()
        focus_rows = self._rows_for_live_brain_focus(selected_node=selected_node, search=search, limit=8)

        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        actual_ids: List[str] = []
        anchor_nodes: List[str] = []

        for spec in self._live_brain_anchor_specs(
            startup=startup,
            cluster_rows=cluster_rows,
            fault_rows=fault_rows,
            recent_docs=recent_docs,
            recent_sessions=recent_sessions,
            integration_listing=integration_listing,
            session_id=session_id,
            request_id=request_id,
        ):
            node = self._synthetic_anchor_node(**spec)
            nodes.append(node)
            anchor_nodes.append(str(node["id"]))

        for label, zone, tier, slot in (
            ("blackfong", "agent", 1, "blackfong"),
            ("daise", "agent", 1, "daise"),
            ("user", "operator", 1, "user"),
            ("system", "agent", 2, "system"),
            ("tiny", "agent", 2, "tiny"),
            ("mobray", "agent", 2, "mobray"),
            ("redamon", "agent", 2, "redamon"),
        ):
            row = self._best_matching_row(label, preferred_types=("agent", "concept", "session"))
            if row is None:
                continue
            actual_ids.append(str(row["id"]))
            nodes.append(
                self._annotate_display_node(
                    self._node_dict_from_row(row, layer=layer),
                    mode="live_brain",
                    anchor=True,
                    tier=tier,
                    region_id="operator_core" if tier == 1 else "system_ring",
                    home_slot=slot,
                    always_show_label=True,
                    label_priority=1.0,
                    detail_level="overview" if tier == 1 else "mid",
                )
            )

        for cluster in cluster_rows[:2]:
            cluster_row = self._query_node_row(str(cluster["id"]))
            if cluster_row is None:
                continue
            actual_ids.append(str(cluster_row["id"]))
            nodes.append(
                self._annotate_display_node(
                    self._node_dict_from_row(cluster_row, layer=layer),
                    mode="live_brain",
                    anchor=True,
                    tier=2,
                    region_id="memory_region",
                    home_slot="active_cluster",
                    always_show_label=True,
                    label_priority=0.92,
                    detail_level="overview",
                    label=self._friendly_cluster_label(cluster_row),
                    why="The most active or central memory cluster in the current cognition graph.",
                )
            )

        for concept in top_concepts:
            row = self._query_node_row(str(concept["id"]))
            if row is None:
                continue
            actual_ids.append(str(row["id"]))
            nodes.append(
                self._annotate_display_node(
                    self._node_dict_from_row(row, layer=layer),
                    mode="live_brain",
                    tier=3,
                    region_id="memory_region",
                    label_priority=0.84,
                    detail_level="mid",
                    why="One of the highest-weight concepts in the current cognition graph.",
                )
            )

        for row in focus_rows:
            actual_ids.append(str(row["id"]))
            nodes.append(
                self._annotate_display_node(
                    self._node_dict_from_row(row, layer=layer),
                    mode="live_brain",
                    tier=3,
                    region_id=self._region_for_zone(str(row.get("zone") or "memory")),
                    detail_level="mid",
                    label_priority=0.88,
                    why="Directly related to the currently selected or searched graph neighborhood.",
                )
            )

        actual_ids = list(dict.fromkeys(actual_ids))
        edges.extend(self._edge_dicts_for_node_ids(actual_ids, layer=layer, limit=max(edge_budget * 2, 120)))
        edges.extend(self._live_brain_anchor_edges(nodes))

        return self._finalize_projection(
            mode="live_brain",
            layer=layer,
            nodes=nodes,
            edges=edges,
            selected_node=selected_node,
            search=search,
            edge_filter=edge_filter,
            max_nodes=node_budget,
            max_edges=edge_budget,
            zoom_bucket=zoom_bucket,
            anchor_nodes=anchor_nodes,
            region_strategy="live_brain",
        )

    def _build_cluster_map_projection(
        self,
        *,
        layer: str,
        selected_node: Optional[str],
        search: Optional[str],
        node_budget: int,
        edge_budget: int,
        edge_filter: str,
        zoom_bucket: str,
    ) -> Dict[str, Any]:
        if search:
            return self._build_neighborhood_projection(
                layer=layer,
                selected_node=selected_node,
                search=search,
                node_budget=node_budget,
                edge_budget=edge_budget,
                edge_filter=edge_filter,
                zoom_bucket=zoom_bucket,
            )
        cluster_rows = self._cluster_rows(limit=max(6, min(10, node_budget // 4)))
        if not cluster_rows:
            return self._empty_projection(title="Memory Chamber", summary="Graph warming in the background.")
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        actual_ids: List[str] = []
        for index, cluster in enumerate(cluster_rows):
            cluster_row = self._query_node_row(str(cluster["id"]))
            if cluster_row is None:
                continue
            actual_ids.append(str(cluster_row["id"]))
            nodes.append(
                self._annotate_display_node(
                    self._node_dict_from_row(cluster_row, layer=layer),
                    mode="cluster_map",
                    anchor=True,
                    tier=2,
                    region_id=f"cluster:{cluster_row['id']}",
                    always_show_label=True,
                    label_priority=0.96 - (index * 0.04),
                    detail_level="overview",
                    label=self._friendly_cluster_label(cluster_row),
                    why="Cluster anchor for deeper Memory Chamber exploration.",
                )
            )
            for member_row in self._cluster_member_rows(cluster_row, limit=4):
                actual_ids.append(str(member_row["id"]))
                nodes.append(
                    self._annotate_display_node(
                        self._node_dict_from_row(member_row, layer=layer),
                        mode="cluster_map",
                        tier=3,
                        region_id=f"cluster:{cluster_row['id']}",
                        detail_level="mid",
                        label_priority=0.74,
                    )
                )
                edges.append(
                    self._synthetic_edge(
                        source=str(cluster_row["id"]),
                        target=str(member_row["id"]),
                        relation="clusters",
                        category="concept",
                        urgency=0.9,
                        state="active",
                    )
                )
        actual_ids = list(dict.fromkeys(actual_ids))
        edges.extend(self._edge_dicts_for_node_ids(actual_ids, layer=layer, limit=max(edge_budget * 2, 140)))
        return self._finalize_projection(
            mode="cluster_map",
            layer=layer,
            nodes=nodes,
            edges=edges,
            selected_node=selected_node,
            search=search,
            edge_filter=edge_filter,
            max_nodes=node_budget,
            max_edges=edge_budget,
            zoom_bucket=zoom_bucket,
            region_strategy="cluster_map",
        )

    def _build_neighborhood_projection(
        self,
        *,
        layer: str,
        selected_node: Optional[str],
        search: Optional[str],
        node_budget: int,
        edge_budget: int,
        edge_filter: str,
        zoom_bucket: str,
    ) -> Dict[str, Any]:
        focus_rows: List[Dict[str, Any]]
        chosen = selected_node
        if search:
            matches = self._match_nodes(search, limit=max(6, node_budget // 3))
            chosen = chosen or (matches[0]["id"] if matches else None)
            focus_rows = self._rows_for_neighborhood(chosen or search, depth=1, limit=node_budget) if (chosen or search) else []
        elif selected_node:
            focus_rows = self._rows_for_neighborhood(selected_node, depth=1, limit=node_budget)
        else:
            default_center = self._default_neighborhood_center()
            chosen = default_center
            focus_rows = self._rows_for_neighborhood(default_center, depth=1, limit=node_budget) if default_center else self._rows_for_default_view(limit=node_budget)
        if not focus_rows:
            return self._empty_projection(title="Neighborhood", summary="No neighborhood is available yet.")
        nodes = [
            self._annotate_display_node(
                self._node_dict_from_row(row, layer=layer),
                mode="neighborhood",
                region_id=self._region_for_zone(str(row.get("zone") or "memory")),
                detail_level="mid",
            )
            for row in focus_rows
        ]
        actual_ids = [str(row["id"]) for row in focus_rows]
        edges = self._edge_dicts_for_node_ids(actual_ids, layer=layer, limit=max(edge_budget * 2, 120))
        return self._finalize_projection(
            mode="neighborhood",
            layer=layer,
            nodes=nodes,
            edges=edges,
            selected_node=chosen,
            search=search,
            edge_filter=edge_filter,
            max_nodes=node_budget,
            max_edges=edge_budget,
            zoom_bucket=zoom_bucket,
            region_strategy="zones",
        )

    def _build_documents_projection(
        self,
        *,
        layer: str,
        selected_node: Optional[str],
        search: Optional[str],
        node_budget: int,
        edge_budget: int,
        edge_filter: str,
        zoom_bucket: str,
    ) -> Dict[str, Any]:
        focus_rows = self._rows_for_recent_documents(limit=node_budget)
        if search:
            focus_rows = self._rows_for_neighborhood(search, depth=1, limit=node_budget)
        nodes = [
            self._annotate_display_node(
                self._node_dict_from_row(row, layer=layer),
                mode="documents",
                region_id="documents_region",
                detail_level="mid" if row.get("node_type") in {"document", "session"} else "close",
            )
            for row in focus_rows
        ]
        actual_ids = [str(row["id"]) for row in focus_rows]
        edges = self._edge_dicts_for_node_ids(actual_ids, layer=layer, limit=max(edge_budget * 2, 100))
        return self._finalize_projection(
            mode="documents",
            layer=layer,
            nodes=nodes,
            edges=edges,
            selected_node=selected_node,
            search=search,
            edge_filter=edge_filter,
            max_nodes=node_budget,
            max_edges=edge_budget,
            zoom_bucket=zoom_bucket,
            region_strategy="documents",
        )

    def _build_agents_projection(
        self,
        *,
        layer: str,
        selected_node: Optional[str],
        search: Optional[str],
        node_budget: int,
        edge_budget: int,
        edge_filter: str,
        zoom_bucket: str,
    ) -> Dict[str, Any]:
        focus_rows = self._rows_by_type_prefix("agent", limit=node_budget)
        for label in ("blackfong", "daise", "tiny", "mobray", "redamon"):
            row = self._best_matching_row(label, preferred_types=("agent", "concept"))
            if row is not None and row not in focus_rows:
                focus_rows.append(row)
        nodes = [
            self._annotate_display_node(
                self._node_dict_from_row(row, layer=layer),
                mode="agents",
                anchor=str(row.get("label", "")).lower() in {"blackfong", "daise", "tinyprogrammer", "tiny", "mobray", "redamon"},
                tier=2 if str(row.get("label", "")).lower() in {"blackfong", "daise", "tinyprogrammer", "tiny", "mobray", "redamon"} else 3,
                region_id="agents_region",
                detail_level="mid",
            )
            for row in focus_rows[:node_budget]
        ]
        actual_ids = [str(row["id"]) for row in focus_rows[:node_budget]]
        edges = self._edge_dicts_for_node_ids(actual_ids, layer=layer, limit=max(edge_budget * 2, 100))
        return self._finalize_projection(
            mode="agents",
            layer=layer,
            nodes=nodes,
            edges=edges,
            selected_node=selected_node,
            search=search,
            edge_filter=edge_filter,
            max_nodes=node_budget,
            max_edges=edge_budget,
            zoom_bucket=zoom_bucket,
            region_strategy="agents",
        )

    def _build_faults_projection(
        self,
        *,
        layer: str,
        selected_node: Optional[str],
        search: Optional[str],
        node_budget: int,
        edge_budget: int,
        edge_filter: str,
        zoom_bucket: str,
    ) -> Dict[str, Any]:
        focus_rows = self._rows_for_faults(limit=max(8, node_budget))
        if search:
            focus_rows = self._rows_for_neighborhood(search, depth=1, limit=node_budget)
        nodes = [
            self._annotate_display_node(
                self._node_dict_from_row(row, layer=layer),
                mode="faults",
                tier=2 if str(row.get("state") or "ready") in {"degraded", "fault", "warning"} else 3,
                region_id="fault_region",
                detail_level="mid",
                why="Fault and degraded-state visibility for the live operator graph.",
            )
            for row in focus_rows[:node_budget]
        ]
        actual_ids = [str(row["id"]) for row in focus_rows[:node_budget]]
        edges = self._edge_dicts_for_node_ids(actual_ids, layer=layer, limit=max(edge_budget * 2, 90))
        return self._finalize_projection(
            mode="faults",
            layer=layer,
            nodes=nodes,
            edges=edges,
            selected_node=selected_node,
            search=search,
            edge_filter=edge_filter,
            max_nodes=node_budget,
            max_edges=edge_budget,
            zoom_bucket=zoom_bucket,
            region_strategy="faults",
        )

    def _build_full_debug_projection(
        self,
        *,
        layer: str,
        selected_node: Optional[str],
        search: Optional[str],
        node_budget: int,
        edge_budget: int,
        edge_filter: str,
        zoom_bucket: str,
    ) -> Dict[str, Any]:
        if search:
            focus_rows = self._match_nodes(search, limit=max(12, node_budget // 2))
            if selected_node:
                focus_rows.extend(self._rows_for_neighborhood(selected_node, depth=1, limit=max(12, node_budget // 2)))
        elif selected_node:
            focus_rows = self._rows_for_neighborhood(selected_node, depth=1, limit=node_budget)
        else:
            focus_rows = self._rows_for_default_view(limit=node_budget)
        if not focus_rows:
            return self._empty_projection(title="Full Debug Graph", summary="No graph nodes are available yet.")
        nodes = [
            self._annotate_display_node(
                self._node_dict_from_row(row, layer=layer),
                mode="full_debug_graph",
                region_id=self._region_for_zone(str(row.get("zone") or "memory")),
                detail_level="close",
                label_priority=0.55,
            )
            for row in focus_rows[:node_budget]
        ]
        actual_ids = [str(row["id"]) for row in focus_rows[:node_budget]]
        edges = self._edge_dicts_for_node_ids(actual_ids, layer=layer, limit=max(edge_budget * 2, 240))
        return self._finalize_projection(
            mode="full_debug_graph",
            layer=layer,
            nodes=nodes,
            edges=edges,
            selected_node=selected_node,
            search=search,
            edge_filter=edge_filter,
            max_nodes=node_budget,
            max_edges=edge_budget,
            zoom_bucket=zoom_bucket,
            region_strategy="zones",
        )

    def _build_tiny_projection(
        self,
        *,
        layer: str,
        selected_node: Optional[str],
        search: Optional[str],
        node_budget: int,
        edge_budget: int,
        edge_filter: str,
        zoom_bucket: str,
    ) -> Dict[str, Any]:
        focus_rows = self._rows_by_type_prefix("tiny_", limit=max(8, node_budget))
        nodes = [
            self._annotate_display_node(
                self._node_dict_from_row(row, layer=layer),
                mode="tiny_region",
                tier=2 if str(row.get("node_type") or "").startswith("tiny_") else 3,
                region_id="tiny_region",
                detail_level="mid",
            )
            for row in focus_rows[:node_budget]
        ]
        actual_ids = [str(row["id"]) for row in focus_rows[:node_budget]]
        edges = self._edge_dicts_for_node_ids(actual_ids, layer=layer, limit=max(edge_budget * 2, 90))
        return self._finalize_projection(
            mode="tiny_region",
            layer=layer,
            nodes=nodes,
            edges=edges,
            selected_node=selected_node,
            search=search,
            edge_filter=edge_filter,
            max_nodes=node_budget,
            max_edges=edge_budget,
            zoom_bucket=zoom_bucket,
            region_strategy="agents",
        )

    def _build_sensor_projection(
        self,
        *,
        layer: str,
        selected_node: Optional[str],
        search: Optional[str],
        node_budget: int,
        edge_budget: int,
        edge_filter: str,
        zoom_bucket: str,
    ) -> Dict[str, Any]:
        nodes = [
            self._synthetic_anchor_node(
                node_id="anchor:sensors",
                label="Sensors",
                zone="sensor",
                tier=2,
                state="manual_only",
                summary="Sensors remain operator-enabled only and are visible here as policy-gated inputs.",
                region_id="sensor_region",
                home_slot="sensors",
                drill={"mode": "faults"},
                why="Shows the policy-gated status of microphone, camera, and future sensor lanes.",
            )
        ]
        return self._finalize_projection(
            mode="sensor_region",
            layer=layer,
            nodes=nodes,
            edges=[],
            selected_node="anchor:sensors",
            search=search,
            edge_filter=edge_filter,
            max_nodes=node_budget,
            max_edges=edge_budget,
            zoom_bucket=zoom_bucket,
            region_strategy="sensors",
        )

    def _finalize_projection(
        self,
        *,
        mode: str,
        layer: str,
        nodes: Sequence[Dict[str, Any]],
        edges: Sequence[Dict[str, Any]],
        selected_node: Optional[str],
        search: Optional[str],
        edge_filter: str,
        max_nodes: int,
        max_edges: int,
        zoom_bucket: str,
        anchor_nodes: Optional[Sequence[str]] = None,
        region_strategy: str = "zones",
    ) -> Dict[str, Any]:
        deduped_nodes: Dict[str, Dict[str, Any]] = {}
        for node in nodes:
            node_id = str(node.get("id") or "")
            if not node_id:
                continue
            deduped_nodes[node_id] = node
        chosen = selected_node
        if search and not chosen:
            matches = self._match_nodes(search, limit=1)
            chosen = matches[0]["id"] if matches else None
        if chosen not in deduped_nodes:
            chosen = next((node_id for node_id, node in deduped_nodes.items() if node.get("anchor")), None) or next(iter(deduped_nodes), None)
        filtered_nodes = self._cap_nodes(list(deduped_nodes.values()), selected_node=chosen, max_nodes=max_nodes)
        visible_node_ids = {str(node["id"]) for node in filtered_nodes}
        filtered_edges = self._filter_edges(
            [dict(edge) for edge in edges],
            visible_node_ids=visible_node_ids,
            selected_node=chosen,
            edge_filter=edge_filter,
            max_edges=max_edges,
        )
        neighbor_ids: set[str] = set()
        for edge in filtered_edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if chosen and chosen in {source, target}:
                neighbor_ids.update({source, target})
        for node in filtered_nodes:
            node["selected"] = str(node.get("id")) == chosen
            node["neighbor"] = str(node.get("id")) in neighbor_ids
        regions = self._build_regions(filtered_nodes, mode=mode, strategy=region_strategy, selected_node=chosen)
        inspector_card = self._build_inspector_card(filtered_nodes, filtered_edges, chosen, regions=regions)
        payload = {
            "nodes": filtered_nodes,
            "edges": filtered_edges,
            "visible_nodes": filtered_nodes,
            "visible_edges": filtered_edges,
            "selected_node": chosen,
            "inspector": inspector_card,
            "inspector_card": inspector_card,
            "anchor_nodes": list(anchor_nodes or [node["id"] for node in filtered_nodes if node.get("anchor")]),
            "regions": regions,
            "mode": mode,
            "layer": layer,
            "zoom_bucket": zoom_bucket,
        }
        return payload

    def top_concepts(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self._query_all(
            """
            SELECT id, label, weight, document_count, mention_count, metadata_json
            FROM memory_chamber_nodes
            WHERE node_type = 'concept'
            ORDER BY weight DESC, mention_count DESC, label ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "id": row["id"],
                "label": row["label"],
                "weight": float(row["weight"] or 0.0),
                "document_count": int(row["document_count"] or 0),
                "mention_count": int(row["mention_count"] or 0),
                "metadata": _json_loads(row["metadata_json"], {}),
            }
            for row in rows
        ]

    def neighborhood(self, center: str, *, depth: int = 1, limit: int = 18) -> Dict[str, Any]:
        rows = self._rows_for_neighborhood(center, depth=depth, limit=limit)
        node_ids = [row["id"] for row in rows]
        edges = self._edge_rows_for_nodes(node_ids, limit=max(24, limit * 3))
        return {
            "schema_version": SCHEMA_VERSION,
            "center": center,
            "depth": depth,
            "nodes": [
                {
                    "id": row["id"],
                    "label": row["label"],
                    "node_type": row["node_type"],
                    "weight": float(row["weight"] or 0.0),
                    "metadata": _json_loads(row["metadata_json"], {}),
                }
                for row in rows
            ],
            "edges": [
                {
                    "id": row["id"],
                    "source": row["source_id"],
                    "target": row["target_id"],
                    "relation": row["relation"],
                    "weight": float(row["weight"] or 0.0),
                }
                for row in edges
            ],
        }

    def related_concepts(self, concept: str, *, limit: int = 8) -> List[Dict[str, Any]]:
        center = self._resolve_node_id(concept)
        if not center:
            return []
        rows = self._query_all(
            """
            SELECT
                CASE WHEN source_id = ? THEN target_id ELSE source_id END AS related_id,
                MAX(weight) AS weight
            FROM memory_chamber_edges
            WHERE relation IN ('co_occurrence', 'clusters', 'suggested_link')
              AND (source_id = ? OR target_id = ?)
            GROUP BY related_id
            ORDER BY weight DESC
            LIMIT ?
            """,
            (center, center, center, limit),
        )
        results: List[Dict[str, Any]] = []
        for row in rows:
            node = self._query_one("SELECT id, label, node_type, metadata_json FROM memory_chamber_nodes WHERE id = ?", (row["related_id"],))
            if node is None:
                continue
            results.append(
                {
                    "id": node["id"],
                    "label": node["label"],
                    "node_type": node["node_type"],
                    "weight": float(row["weight"] or 0.0),
                    "metadata": _json_loads(node["metadata_json"], {}),
                }
            )
        return results

    def clusters(self, *, limit: int = 8) -> List[Dict[str, Any]]:
        rows = self._query_all(
            """
            SELECT id, label, algorithm, score, member_count, metadata_json, updated_at
            FROM memory_chamber_clusters
            ORDER BY member_count DESC, score DESC, label ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "id": row["id"],
                "label": row["label"],
                "algorithm": row["algorithm"],
                "score": float(row["score"] or 0.0),
                "member_count": int(row["member_count"] or 0),
                "metadata": _json_loads(row["metadata_json"], {}),
                "updated_at": float(row["updated_at"] or 0.0),
            }
            for row in rows
        ]

    def orphans(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self._query_all(
            """
            SELECT n.id, n.label, n.node_type, n.weight
            FROM memory_chamber_nodes n
            LEFT JOIN memory_chamber_edges e1 ON e1.source_id = n.id
            LEFT JOIN memory_chamber_edges e2 ON e2.target_id = n.id
            WHERE e1.id IS NULL AND e2.id IS NULL
            ORDER BY n.weight DESC, n.label ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in rows]

    def missing_links(self, *, limit: int = 12) -> List[Dict[str, Any]]:
        rows = self._query_all(
            """
            SELECT id, source_node_id, target_node_id, hint_type, score, reason, status, metadata_json
            FROM memory_chamber_relationship_hints
            ORDER BY score DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "id": row["id"],
                "source_node_id": row["source_node_id"],
                "target_node_id": row["target_node_id"],
                "hint_type": row["hint_type"],
                "score": float(row["score"] or 0.0),
                "reason": row["reason"],
                "status": row["status"],
                "metadata": _json_loads(row["metadata_json"], {}),
            }
            for row in rows
        ]

    def recent_deltas(self, *, limit: int = 8) -> List[Dict[str, Any]]:
        rows = self._query_all(
            """
            SELECT id, label, source, node_count, edge_count, cluster_count, delta_summary_json, created_at
            FROM memory_chamber_graph_snapshots
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        deltas: List[Dict[str, Any]] = []
        previous: Optional[Dict[str, Any]] = None
        for row in rows:
            delta_summary = _json_loads(row["delta_summary_json"], {})
            if previous is None:
                delta_summary = delta_summary or {
                    "documents_delta": 0,
                    "nodes_delta": 0,
                    "edges_delta": 0,
                    "clusters_delta": 0,
                }
            deltas.append(
                {
                    "id": row["id"],
                    "label": row["label"],
                    "source": row["source"],
                    "node_count": int(row["node_count"] or 0),
                    "edge_count": int(row["edge_count"] or 0),
                    "cluster_count": int(row["cluster_count"] or 0),
                    "delta_summary": delta_summary,
                    "created_at": float(row["created_at"] or 0.0),
                }
            )
            previous = row
        return deltas

    def trace_reasoning_path(
        self,
        *,
        source: Optional[str] = None,
        target: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        graph = self._networkx_graph()
        source_id = self._resolve_node_id(source or "")
        target_id = self._resolve_node_id(target or "")
        if not source_id and query:
            terms = self._query_terms(query)
            if terms:
                source_id = self._resolve_node_id(terms[0])
                if len(terms) > 1:
                    target_id = self._resolve_node_id(terms[1])
        if not source_id or not target_id or graph is None or source_id not in graph or target_id not in graph:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "unresolved",
                "source": source,
                "target": target,
                "query": query,
                "path": [],
            }
        try:
            node_path = nx.shortest_path(graph, source=source_id, target=target_id, weight=None)
        except Exception:
            node_path = []
        readable = [self._query_one("SELECT id, label, node_type FROM memory_chamber_nodes WHERE id = ?", (node_id,)) for node_id in node_path]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if readable else "unresolved",
            "source": source_id,
            "target": target_id,
            "query": query,
            "path": [dict(row) for row in readable[:limit] if row is not None],
        }

    def document(self, document_id: str) -> Dict[str, Any]:
        row = self._query_one(
            """
            SELECT id, path, source_kind, doc_type, title, content, metadata_json, updated_at
            FROM memory_chamber_documents
            WHERE id = ?
            """,
            (document_id,),
        )
        if row is None:
            return {"status": "missing", "document_id": document_id}
        mentions = self._query_all(
            """
            SELECT node_id, occurrences, weight
            FROM memory_chamber_document_mentions
            WHERE document_id = ?
            ORDER BY weight DESC, occurrences DESC
            LIMIT 20
            """,
            (document_id,),
        )
        return {
            "status": "ok",
            "document": {
                "id": row["id"],
                "path": row["path"],
                "source_kind": row["source_kind"],
                "doc_type": row["doc_type"],
                "title": row["title"],
                "content": row["content"],
                "metadata": _json_loads(row["metadata_json"], {}),
                "updated_at": float(row["updated_at"] or 0.0),
            },
            "mentions": [dict(item) for item in mentions],
        }

    def reasoning_context(
        self,
        query: str,
        *,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        limit: int = 8,
        profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_profile = normalize_retrieval_profile(profile, "deep")
        retrieval_settings = retrieval_profile_settings(resolved_profile)
        started = time.perf_counter()
        terms = self._query_terms(query)
        graph_limit = int(retrieval_settings.get("graph_limit") or limit or 0)
        matched = self._match_nodes(" ".join(terms), limit=max(3, max(graph_limit, limit)))
        if retrieval_settings.get("architecture_focus"):
            architecture_matches = [row for row in matched if self._is_architecture_node(row)]
            if architecture_matches:
                matched = architecture_matches

        vector_started = time.perf_counter()
        semantic_hits = []
        if retrieval_settings.get("include_semantic"):
            semantic_hits = self._semantic_search(query, limit=max(4, min(limit, MAX_SEMANTIC_HITS)))
            if retrieval_settings.get("architecture_focus"):
                semantic_hits = [item for item in semantic_hits if self._is_architecture_semantic_hit(item)]
        vector_completed = time.perf_counter()

        graph_started = time.perf_counter()
        if retrieval_settings.get("include_graph"):
            neighborhood_limit = max(4, int(retrieval_settings.get("neighborhood_limit") or 6))
            neighborhood_centers = min(
                int(retrieval_settings.get("neighborhood_centers") or 2),
                min(len(matched), max(1, graph_limit)),
            )
            neighborhoods = [
                self.neighborhood(row["id"], depth=1, limit=neighborhood_limit)
                for row in matched[: neighborhood_centers]
            ]
            clusters = self.clusters(limit=4) if retrieval_settings.get("include_clusters") else []
            missing_links = self.missing_links(limit=4) if retrieval_settings.get("include_missing_links") else []
            recent_deltas = self.recent_deltas(limit=4) if retrieval_settings.get("include_recent_deltas") else []
            path_trace = self.trace_reasoning_path(query=query) if retrieval_settings.get("include_reasoning_path") else {
                "schema_version": SCHEMA_VERSION,
                "status": "disabled",
                "query": query,
                "path": [],
            }
        else:
            neighborhoods = []
            clusters = []
            missing_links = []
            recent_deltas = self.recent_deltas(limit=2) if resolved_profile != "fast" else []
            path_trace = {
                "schema_version": SCHEMA_VERSION,
                "status": "disabled",
                "query": query,
                "path": [],
            }
        graph_completed = time.perf_counter()

        citations = self._reasoning_citations(
            semantic_hits=semantic_hits,
            matched_rows=matched[:limit],
            limit=max(2, min(limit, int(retrieval_settings.get("citation_limit") or limit))),
        )
        total_completed = time.perf_counter()
        return {
            "schema_version": SCHEMA_VERSION,
            "query": query,
            "session_id": session_id,
            "request_id": request_id,
            "retrieval_profile": resolved_profile,
            "retrieval_contract": {
                "profile": resolved_profile,
                "offline_first": True,
                "one_vector_authority": str(paths.vector_db_path),
                "graph_enrichment": bool(retrieval_settings.get("include_graph")),
                "citation_required": bool(retrieval_settings.get("include_semantic")),
                "rule": "Use bounded semantic hits and graph neighborhoods only. Never dump the whole cognition graph.",
            },
            "query_terms": terms,
            "matched_nodes": [
                {"id": row["id"], "label": row["label"], "node_type": row["node_type"], "weight": float(row["weight"] or 0.0)}
                for row in matched[:limit]
            ],
            "neighborhoods": neighborhoods,
            "clusters": clusters,
            "missing_links": missing_links,
            "recent_deltas": recent_deltas,
            "semantic_hits": semantic_hits,
            "citations": citations,
            "reasoning_path": path_trace,
            "timing_ms": {
                "embedding": None,
                "vector_query": round((vector_completed - vector_started) * 1000, 2) if retrieval_settings.get("include_semantic") else 0.0,
                "graph_enrichment": round((graph_completed - graph_started) * 1000, 2),
                "total_retrieval": round((total_completed - started) * 1000, 2),
            },
        }

    def recent_agent_artifacts(self, agent_name: str, *, limit: int = 6) -> List[Dict[str, Any]]:
        rows = self._query_all(
            """
            SELECT artifact_type, title, created_at, artifact_ref
            FROM memory_chamber_agent_artifacts
            WHERE agent_name = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (agent_name, limit),
        )
        return [dict(row) for row in rows]

    def append_agent_artifact(
        self,
        *,
        agent_name: str,
        artifact_type: str,
        content: str,
        title: Optional[str] = None,
        artifact_ref: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        scope: str = "agent_write_scope",
    ) -> Dict[str, Any]:
        if scope != self.agent_write_scope:
            return {"status": "forbidden", "reason": "agent_write_scope_required"}
        now = time.time()
        artifact_id = _stable_id("agent-artifact", agent_name, artifact_type, artifact_ref or title or now)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_chamber_agent_artifacts (
                    id, agent_name, artifact_type, artifact_ref, title, content, metadata_json, session_id, request_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    agent_name,
                    artifact_type,
                    artifact_ref or artifact_id,
                    title or artifact_type.replace("_", " ").title(),
                    content,
                    _json_dumps(metadata or {}),
                    session_id,
                    request_id,
                    now,
                ),
            )
            conn.commit()
        return {"status": "ok", "artifact_id": artifact_id}

    def propose_relationship_hint(
        self,
        *,
        source_node_id: str,
        target_node_id: str,
        reason: str,
        score: float = 0.5,
        hint_type: str = "suggested_link",
        created_by: str = "agent",
        metadata: Optional[Dict[str, Any]] = None,
        scope: str = "agent_write_scope",
    ) -> Dict[str, Any]:
        if scope != self.agent_write_scope:
            return {"status": "forbidden", "reason": "agent_write_scope_required"}
        now = time.time()
        hint_id = _stable_id("hint", source_node_id, target_node_id, reason)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_chamber_relationship_hints (
                    id, source_node_id, target_node_id, hint_type, score, reason, status, created_by, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hint_id,
                    source_node_id,
                    target_node_id,
                    hint_type,
                    float(score),
                    reason,
                    "proposed",
                    created_by,
                    _json_dumps(metadata or {}),
                    now,
                    now,
                ),
            )
            conn.commit()
        return {"status": "ok", "hint_id": hint_id}

    def _reindex(self, *, reason: str, roots: Optional[List[Path]]) -> Dict[str, Any]:
        started_at = time.time()
        watch_roots = [Path(root) for root in (roots or self.watch_roots()) if Path(root).exists()]
        run_id = _stable_id("ingest-run", reason, started_at, uuid.uuid4().hex[:6])
        self._set_status(state="warming", updated_at=started_at, last_error=None, last_error_code=None)
        try:
            parsed = self._build_graph(watch_roots)
            self._persist_graph(parsed, run_id=run_id, root_path=str(self.workspace_root), reason=reason, started_at=started_at)
            completed_at = time.time()
            counts = parsed["counts"]
            self._set_status(
                state="ready" if counts["nodes"] else "degraded",
                updated_at=completed_at,
                last_indexed_at=completed_at,
                last_error=None,
                last_error_code=None,
                counts=counts,
                queue_depth=0,
            )
            self._schedule_graph_prewarm(reason="reindex")
            return {"status": "ok", "run_id": run_id, "counts": counts, "roots": [str(root) for root in watch_roots]}
        except Exception as exc:
            logger.exception("memory_chamber_reindex_failed")
            failed_at = time.time()
            self._record_ingest_run(
                run_id=run_id,
                source=reason,
                status="failed",
                root_path=str(self.workspace_root),
                document_count=0,
                node_count=0,
                edge_count=0,
                started_at=started_at,
                completed_at=failed_at,
                error=str(exc),
                metadata={"roots": [str(root) for root in watch_roots]},
            )
            self._set_status(
                state="degraded",
                updated_at=failed_at,
                last_error=str(exc),
                last_error_code="reindex_failed",
                queue_depth=0,
            )
            return {"status": "error", "error": str(exc), "run_id": run_id}

    def _build_graph(self, roots: Sequence[Path]) -> Dict[str, Any]:
        documents = self._collect_documents(roots)
        node_map: Dict[str, Dict[str, Any]] = {}
        edge_map: Dict[tuple[str, str, str], Dict[str, Any]] = {}
        mentions: List[Dict[str, Any]] = []
        sessions: Dict[str, Dict[str, Any]] = {}
        artifacts: List[Dict[str, Any]] = []
        concept_docs: Dict[str, set[str]] = defaultdict(set)
        doc_reference_targets: Dict[str, List[str]] = {}

        for document in documents:
            doc_node_id = f"document:{document['id']}"
            self._accumulate_node(
                node_map,
                doc_node_id,
                label=document["title"],
                node_type="document",
                zone=self._zone_for_document(document),
                state="ready",
                weight=1.0,
                metadata={
                    "path": document["path"],
                    "source_kind": document["source_kind"],
                    "doc_type": document["doc_type"],
                },
            )
            parsed = self._extract_document_graph(document)
            doc_reference_targets[document["id"]] = parsed["references"]
            top_terms = parsed["top_terms"][:MAX_DOCUMENT_TERMS]
            for term, weight in top_terms:
                concept_id = f"concept:{term}"
                concept_docs[concept_id].add(document["id"])
                self._accumulate_node(
                    node_map,
                    concept_id,
                    label=term,
                    node_type="concept",
                    zone="memory",
                    state="active" if weight >= 3 else "ready",
                    weight=weight,
                    document_count=1,
                    mention_count=int(weight),
                )
                mentions.append(
                    {
                        "id": _stable_id("mention", document["id"], concept_id),
                        "document_id": document["id"],
                        "node_id": concept_id,
                        "occurrences": int(weight),
                        "positions_json": "[]",
                        "weight": float(weight),
                    }
                )
                self._accumulate_edge(
                    edge_map,
                    doc_node_id,
                    concept_id,
                    relation="mentions",
                    weight=float(weight),
                    confidence=0.82,
                    metadata={"document_id": document["id"]},
                )
            for left_index, (left_term, left_weight) in enumerate(top_terms):
                for right_term, right_weight in top_terms[left_index + 1 : left_index + 6]:
                    left_id = f"concept:{left_term}"
                    right_id = f"concept:{right_term}"
                    if left_id == right_id:
                        continue
                    self._accumulate_edge(
                        edge_map,
                        left_id,
                        right_id,
                        relation="co_occurrence",
                        weight=float(min(left_weight, right_weight)),
                        confidence=0.74,
                        metadata={"document_id": document["id"]},
                    )

            session_id = str(document["metadata"].get("session_id") or "")
            if session_id:
                session_node_id = f"session:{session_id}"
                sessions[session_id] = {
                    "id": session_node_id,
                    "session_id": session_id,
                    "label": f"Session {session_id[:8]}",
                    "summary": document["title"],
                    "metadata": {"request_id": document["metadata"].get("request_id")},
                }
                self._accumulate_node(
                    node_map,
                    session_node_id,
                    label=f"Session {session_id[:8]}",
                    node_type="session",
                    zone="tasks",
                    state="ready",
                    weight=1.0,
                )
                self._accumulate_edge(
                    edge_map,
                    session_node_id,
                    doc_node_id,
                    relation="session_contains",
                    weight=1.0,
                    confidence=0.88,
                    metadata={"session_id": session_id},
                )

            for actor in parsed["actors"]:
                actor_id = f"agent:{actor}"
                self._accumulate_node(
                    node_map,
                    actor_id,
                    label=actor.replace("_", " ").title(),
                    node_type="agent",
                    zone="agent",
                    state="active" if actor in {"daise", "blackfong"} else "ready",
                    weight=1.2,
                )
                self._accumulate_edge(
                    edge_map,
                    actor_id,
                    doc_node_id,
                    relation="participates",
                    weight=1.0,
                    confidence=0.78,
                    metadata={"actor": actor},
                )

            if parsed["tiny_kind"]:
                tiny_node_id = f"{parsed['tiny_kind']}:{document['id']}"
                self._accumulate_node(
                    node_map,
                    tiny_node_id,
                    label=document["title"],
                    node_type=parsed["tiny_kind"],
                    zone="forge",
                    state="active",
                    weight=1.3,
                    metadata={"path": document["path"]},
                )
                self._accumulate_edge(
                    edge_map,
                    "agent:tinyprogrammer",
                    tiny_node_id,
                    relation="creates",
                    weight=1.0,
                    confidence=0.81,
                    metadata={"document_id": document["id"]},
                )
                artifacts.append(
                    {
                        "id": _stable_id("artifact", parsed["tiny_kind"], document["id"]),
                        "agent_name": "tinyprogrammer",
                        "artifact_type": parsed["tiny_kind"],
                        "artifact_ref": document["id"],
                        "title": document["title"],
                        "content": document["content"][:2000],
                        "metadata_json": _json_dumps({"path": document["path"], "source_kind": document["source_kind"]}),
                        "session_id": session_id or None,
                        "request_id": document["metadata"].get("request_id"),
                    }
                )

        self._link_document_references(documents, node_map, edge_map, doc_reference_targets)
        self._link_event_sequences(documents, edge_map)
        clusters = self._infer_clusters(node_map, edge_map)
        hints = self._infer_missing_links(concept_docs, edge_map)
        snapshot = self._snapshot_payload(node_map, edge_map, clusters, hints)
        return {
            "documents": documents,
            "nodes": list(node_map.values()),
            "edges": list(edge_map.values()),
            "mentions": mentions,
            "sessions": list(sessions.values()),
            "artifacts": artifacts,
            "clusters": clusters,
            "hints": hints,
            "snapshot": snapshot,
            "counts": {
                "documents": len(documents),
                "nodes": len(node_map),
                "edges": len(edge_map),
                "clusters": len(clusters),
            },
        }

    def _collect_documents(self, roots: Sequence[Path]) -> List[Dict[str, Any]]:
        documents: List[Dict[str, Any]] = []
        seen_paths: set[str] = set()
        for root in roots:
            for file_path in self._iter_files(root):
                absolute = str(file_path.resolve())
                if absolute in seen_paths:
                    continue
                seen_paths.add(absolute)
                source_kind, doc_type = _path_category(file_path)
                content = _read_text(file_path)
                relative_path = self._relative_display_path(file_path)
                metadata = {
                    "absolute_path": absolute,
                    "relative_path": relative_path,
                    "root": str(root),
                }
                documents.append(
                    {
                        "id": _stable_id("doc", relative_path, file_path.stat().st_mtime_ns),
                        "path": relative_path,
                        "source_kind": source_kind,
                        "doc_type": doc_type,
                        "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        "title": file_path.name,
                        "content": content,
                        "metadata": metadata,
                        "updated_at": file_path.stat().st_mtime,
                    }
                )
        documents.extend(self._event_documents())
        documents.extend(self._session_documents())
        documents.sort(key=lambda item: float(item.get("updated_at") or 0.0), reverse=True)
        return documents

    def _event_documents(self) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        for event in self.event_store.list_events(limit=MAX_EVENT_DOCUMENTS):
            metadata = dict(event.metadata or {})
            text_parts = [
                event.type,
                event.source,
                _json_dumps(event.payload),
                _json_dumps(metadata),
            ]
            content = "\n".join(text_parts)
            docs.append(
                {
                    "id": f"event:{event.id}",
                    "path": f"event://{event.source}/{event.type}/{event.id}",
                    "source_kind": "event",
                    "doc_type": event.type,
                    "hash": event.hash,
                    "title": f"{event.source}:{event.type}",
                    "content": content,
                    "metadata": {
                        "event_id": event.id,
                        "source": event.source,
                        "event_type": event.type,
                        "session_id": metadata.get("session_id"),
                        "request_id": metadata.get("request_id"),
                        "timestamp": event.timestamp,
                    },
                    "updated_at": event.timestamp / 1000.0,
                }
            )
        return docs

    def _session_documents(self) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        for session in self.event_store.list_recent_sessions(limit=MAX_SESSION_DOCUMENTS):
            session_id = str(session.get("session_id") or "")
            content = "\n".join(
                [
                    str(session.get("preview") or ""),
                    f"Agents: {', '.join(session.get('agents', []))}",
                    f"Events: {session.get('event_count', 0)}",
                    f"Messages: {session.get('message_count', 0)}",
                ]
            )
            docs.append(
                {
                    "id": f"session:{session_id}",
                    "path": f"session://{session_id}",
                    "source_kind": "session",
                    "doc_type": "session",
                    "hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "title": f"Session {session_id[:8]}",
                    "content": content,
                    "metadata": session,
                    "updated_at": float(session.get("last_activity") or time.time()),
                }
            )
        return docs

    def _extract_document_graph(self, document: Dict[str, Any]) -> Dict[str, Any]:
        content = str(document["content"])
        tokens = _token_candidates(content)
        counter = Counter(tokens)
        for reference in PATH_RE.findall(content):
            for part in _identifier_parts(reference):
                counter[part] += 2
        references: List[str] = []
        for match in IMPORT_RE.finditer(content):
            for key, value in match.groupdict().items():
                if not value:
                    continue
                references.extend([part.strip() for part in re.split(r"[, ]+", value) if part.strip()])
        references.extend(PATH_RE.findall(content))
        actors = {str(document["metadata"].get("source") or "").strip().lower()}
        actor_from_path = self._infer_actor_from_path(document["path"])
        if actor_from_path:
            actors.add(actor_from_path)
        tiny_kind = self._infer_tiny_kind(document)
        if tiny_kind:
            actors.add("tinyprogrammer")
        top_terms = [(term, weight) for term, weight in counter.most_common(MAX_DOCUMENT_TERMS * 2) if term not in STOPWORDS][:MAX_DOCUMENT_TERMS]
        return {
            "top_terms": top_terms,
            "references": references[:24],
            "actors": sorted(actor for actor in actors if actor),
            "tiny_kind": tiny_kind,
        }

    def _infer_actor_from_path(self, path_value: str) -> Optional[str]:
        lowered = path_value.lower()
        if "tinyprogrammer" in lowered or "tiny" in lowered:
            return "tinyprogrammer"
        if "mobray" in lowered:
            return "mobray"
        if "daise" in lowered:
            return "daise"
        if "blackfong" in lowered:
            return "blackfong"
        return None

    def _infer_tiny_kind(self, document: Dict[str, Any]) -> Optional[str]:
        path_value = str(document["path"]).lower()
        title = str(document["title"]).lower()
        doc_type = str(document["doc_type"]).lower()
        if "reflection" in path_value or "reflection" in title or doc_type == "tinyprogrammer_reflection":
            return "tiny_reflection"
        if "program" in path_value or "program" in title or doc_type in {"tinyprogrammer_program_created", "tinyprogrammer_program_archived"}:
            return "tiny_program"
        if "mood" in path_value or "mood" in title:
            return "tiny_mood"
        if "decision" in path_value or "decision" in title:
            return "tiny_decision"
        if "session" in path_value or document["source_kind"] == "session":
            return "tiny_session" if "tiny" in path_value else None
        if "art" in path_value or "image" in path_value:
            return "tiny_generated_art"
        return None

    def _link_document_references(
        self,
        documents: Sequence[Dict[str, Any]],
        node_map: Dict[str, Dict[str, Any]],
        edge_map: Dict[tuple[str, str, str], Dict[str, Any]],
        doc_reference_targets: Dict[str, List[str]],
    ) -> None:
        known_paths = {str(document["path"]).lower(): f"document:{document['id']}" for document in documents}
        known_titles = {str(document["title"]).lower(): f"document:{document['id']}" for document in documents}
        for document in documents:
            source_id = f"document:{document['id']}"
            for reference in doc_reference_targets.get(document["id"], []):
                normalized = reference.replace("\\", "/").strip().lower()
                target = known_paths.get(normalized) or known_titles.get(Path(normalized).name.lower())
                if not target or target == source_id:
                    continue
                self._accumulate_edge(
                    edge_map,
                    source_id,
                    target,
                    relation="references",
                    weight=1.4,
                    confidence=0.9,
                    metadata={"reference": reference},
                )

    def _link_event_sequences(
        self,
        documents: Sequence[Dict[str, Any]],
        edge_map: Dict[tuple[str, str, str], Dict[str, Any]],
    ) -> None:
        event_docs = [doc for doc in documents if doc["source_kind"] == "event"]
        event_docs.sort(key=lambda item: float(item["metadata"].get("timestamp") or 0.0))
        for left, right in zip(event_docs, event_docs[1:]):
            self._accumulate_edge(
                edge_map,
                f"document:{left['id']}",
                f"document:{right['id']}",
                relation="sequence",
                weight=0.6,
                confidence=0.72,
                metadata={"window_ms": max(0.0, float(right["metadata"].get("timestamp") or 0.0) - float(left["metadata"].get("timestamp") or 0.0))},
            )

    def _infer_clusters(
        self,
        node_map: Dict[str, Dict[str, Any]],
        edge_map: Dict[tuple[str, str, str], Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if nx is None:
            return []
        graph = nx.Graph()
        for node_id, node in node_map.items():
            if node["node_type"] == "concept":
                graph.add_node(node_id)
        for edge in edge_map.values():
            if edge["relation"] != "co_occurrence":
                continue
            if edge["source_id"] in graph and edge["target_id"] in graph:
                graph.add_edge(edge["source_id"], edge["target_id"], weight=float(edge["weight"]))
        if graph.number_of_nodes() == 0:
            return []
        if graph.number_of_edges() == 0:
            communities = [{node_id} for node_id in graph.nodes()]
        else:
            communities = list(nx.algorithms.community.greedy_modularity_communities(graph, weight="weight"))
        clusters: List[Dict[str, Any]] = []
        for index, community in enumerate(communities, start=1):
            members = list(community)
            if len(members) < 2:
                continue
            labels = [node_map[node_id]["label"] for node_id in members if node_id in node_map][:3]
            label = " / ".join(labels) or f"Cluster {index}"
            cluster_id = f"cluster:{index}"
            clusters.append(
                {
                    "id": cluster_id,
                    "label": label,
                    "algorithm": "greedy_modularity",
                    "score": float(len(members)),
                    "member_count": len(members),
                    "metadata": {"members": members},
                }
            )
            self._accumulate_node(
                node_map,
                cluster_id,
                label=label,
                node_type="cluster",
                zone="memory",
                state="ready",
                weight=float(len(members)),
                metadata={"members": members},
            )
            for member in members:
                self._accumulate_edge(
                    edge_map,
                    cluster_id,
                    member,
                    relation="clusters",
                    weight=1.1,
                    confidence=0.76,
                    metadata={"cluster_id": cluster_id},
                )
        return clusters

    def _infer_missing_links(
        self,
        concept_docs: Dict[str, set[str]],
        edge_map: Dict[tuple[str, str, str], Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        connected_pairs = {
            tuple(sorted((edge["source_id"], edge["target_id"])))
            for edge in edge_map.values()
            if edge["relation"] == "co_occurrence"
        }
        hints: List[Dict[str, Any]] = []
        concepts = sorted(concept_docs.items(), key=lambda item: len(item[1]), reverse=True)[:30]
        for index, (left_id, left_docs) in enumerate(concepts):
            for right_id, right_docs in concepts[index + 1 :]:
                pair = tuple(sorted((left_id, right_id)))
                if pair in connected_pairs:
                    continue
                union = left_docs | right_docs
                if not union:
                    continue
                score = len(left_docs & right_docs) / len(union)
                if score < 0.25:
                    continue
                hints.append(
                    {
                        "id": _stable_id("hint", left_id, right_id, round(score, 3)),
                        "source_node_id": left_id,
                        "target_node_id": right_id,
                        "hint_type": "suggested_link",
                        "score": round(score, 3),
                        "reason": "Shared documents suggest an unmaterialized relationship.",
                        "status": "proposed",
                        "created_by": "memory_chamber",
                        "metadata": {"shared_documents": sorted(left_docs & right_docs)},
                    }
                )
        hints.sort(key=lambda item: float(item["score"]), reverse=True)
        return hints[:20]

    def _snapshot_payload(
        self,
        node_map: Dict[str, Dict[str, Any]],
        edge_map: Dict[tuple[str, str, str], Dict[str, Any]],
        clusters: Sequence[Dict[str, Any]],
        hints: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        top_concepts = sorted(
            (node for node in node_map.values() if node["node_type"] == "concept"),
            key=lambda item: float(item["weight"]),
            reverse=True,
        )[:10]
        return {
            "schema_version": SCHEMA_VERSION,
            "top_concepts": [{"id": node["id"], "label": node["label"], "weight": node["weight"]} for node in top_concepts],
            "active_clusters": [{"id": cluster["id"], "label": cluster["label"], "member_count": cluster["member_count"]} for cluster in clusters[:6]],
            "missing_links": [{"source": hint["source_node_id"], "target": hint["target_node_id"], "score": hint["score"]} for hint in hints[:6]],
        }

    def _persist_graph(
        self,
        parsed: Dict[str, Any],
        *,
        run_id: str,
        root_path: str,
        reason: str,
        started_at: float,
    ) -> None:
        completed_at = time.time()
        previous_snapshot = self._query_one(
            """
            SELECT node_count, edge_count, cluster_count, snapshot_json
            FROM memory_chamber_graph_snapshots
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        counts = parsed["counts"]
        delta_summary = {
            "documents_delta": counts["documents"] - int(_json_loads((previous_snapshot or {}).get("snapshot_json"), {}).get("document_count", 0) if previous_snapshot else 0),
            "nodes_delta": counts["nodes"] - int((previous_snapshot or {}).get("node_count") or 0),
            "edges_delta": counts["edges"] - int((previous_snapshot or {}).get("edge_count") or 0),
            "clusters_delta": counts["clusters"] - int((previous_snapshot or {}).get("cluster_count") or 0),
        }
        snapshot_id = _stable_id("snapshot", completed_at, counts["nodes"], counts["edges"])
        with self._connect() as conn:
            for table in (
                "memory_chamber_documents",
                "memory_chamber_nodes",
                "memory_chamber_edges",
                "memory_chamber_document_mentions",
                "memory_chamber_agent_artifacts",
                "memory_chamber_sessions",
                "memory_chamber_clusters",
                "memory_chamber_relationship_hints",
                "memory_chamber_embeddings",
            ):
                conn.execute(f"DELETE FROM {table}")
            if self._fts_enabled:
                conn.execute("DELETE FROM memory_chamber_documents_fts")

            conn.executemany(
                """
                INSERT INTO memory_chamber_documents (
                    id, path, source_kind, doc_type, hash, title, content, metadata_json, created_at, updated_at, last_ingested_at, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["id"],
                        item["path"],
                        item["source_kind"],
                        item["doc_type"],
                        item["hash"],
                        item["title"],
                        item["content"],
                        _json_dumps(item["metadata"]),
                        float(item["updated_at"] or completed_at),
                        float(item["updated_at"] or completed_at),
                        completed_at,
                        1,
                    )
                    for item in parsed["documents"]
                ],
            )
            if self._fts_enabled:
                conn.executemany(
                    "INSERT INTO memory_chamber_documents_fts (id, path, title, content) VALUES (?, ?, ?, ?)",
                    [(item["id"], item["path"], item["title"], item["content"]) for item in parsed["documents"]],
                )

            conn.executemany(
                """
                INSERT INTO memory_chamber_nodes (
                    id, label, normalized_label, node_type, zone, state, weight, document_count, mention_count, metadata_json, provenance_json, created_at, updated_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["id"],
                        item["label"],
                        item["normalized_label"],
                        item["node_type"],
                        item["zone"],
                        item["state"],
                        float(item["weight"]),
                        int(item["document_count"]),
                        int(item["mention_count"]),
                        _json_dumps(item["metadata"]),
                        _json_dumps(item["provenance"]),
                        completed_at,
                        completed_at,
                        completed_at,
                    )
                    for item in parsed["nodes"]
                ],
            )
            conn.executemany(
                """
                INSERT INTO memory_chamber_edges (
                    id, source_id, target_id, relation, weight, confidence, freshness_s, metadata_json, provenance_json, created_at, updated_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["id"],
                        item["source_id"],
                        item["target_id"],
                        item["relation"],
                        float(item["weight"]),
                        float(item["confidence"]),
                        item.get("freshness_s"),
                        _json_dumps(item["metadata"]),
                        _json_dumps(item["provenance"]),
                        completed_at,
                        completed_at,
                        completed_at,
                    )
                    for item in parsed["edges"]
                ],
            )
            conn.executemany(
                """
                INSERT INTO memory_chamber_document_mentions (
                    id, document_id, node_id, occurrences, positions_json, weight, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["id"],
                        item["document_id"],
                        item["node_id"],
                        int(item["occurrences"]),
                        item["positions_json"],
                        float(item["weight"]),
                        completed_at,
                        completed_at,
                    )
                    for item in parsed["mentions"]
                ],
            )
            conn.executemany(
                """
                INSERT INTO memory_chamber_agent_artifacts (
                    id, agent_name, artifact_type, artifact_ref, title, content, metadata_json, session_id, request_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["id"],
                        item["agent_name"],
                        item["artifact_type"],
                        item["artifact_ref"],
                        item["title"],
                        item["content"],
                        item["metadata_json"],
                        item["session_id"],
                        item["request_id"],
                        completed_at,
                    )
                    for item in parsed["artifacts"]
                ],
            )
            conn.executemany(
                """
                INSERT INTO memory_chamber_sessions (
                    id, session_id, label, last_request_id, summary, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["id"],
                        item["session_id"],
                        item["label"],
                        item["metadata"].get("request_id"),
                        item["summary"],
                        _json_dumps(item["metadata"]),
                        completed_at,
                    )
                    for item in parsed["sessions"]
                ],
            )
            conn.executemany(
                """
                INSERT INTO memory_chamber_clusters (
                    id, label, algorithm, score, member_count, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["id"],
                        item["label"],
                        item["algorithm"],
                        float(item["score"]),
                        int(item["member_count"]),
                        _json_dumps(item["metadata"]),
                        completed_at,
                        completed_at,
                    )
                    for item in parsed["clusters"]
                ],
            )
            conn.executemany(
                """
                INSERT INTO memory_chamber_relationship_hints (
                    id, source_node_id, target_node_id, hint_type, score, reason, status, created_by, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item["id"],
                        item["source_node_id"],
                        item["target_node_id"],
                        item["hint_type"],
                        float(item["score"]),
                        item["reason"],
                        item["status"],
                        item["created_by"],
                        _json_dumps(item["metadata"]),
                        completed_at,
                        completed_at,
                    )
                    for item in parsed["hints"]
                ],
            )

            embedding_rows = []
            for index, item in enumerate(parsed["documents"]):
                vector_key = f"memory-chamber:{item['id']}"
                embedding_rows.append(
                    (
                        _stable_id("embedding", vector_key),
                        vector_key,
                        item["source_kind"],
                        item["id"],
                        0,
                        item["hash"],
                        _json_dumps({"path": item["path"], "doc_type": item["doc_type"]}),
                        completed_at,
                        completed_at,
                    )
                )
            conn.executemany(
                """
                INSERT INTO memory_chamber_embeddings (
                    id, vector_key, source_type, source_id, chunk_index, content_hash, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                embedding_rows,
            )
            conn.execute(
                """
                INSERT INTO memory_chamber_graph_snapshots (
                    id, label, source, node_count, edge_count, cluster_count, delta_summary_json, snapshot_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    f"Reindex {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(completed_at))}",
                    reason,
                    counts["nodes"],
                    counts["edges"],
                    counts["clusters"],
                    _json_dumps(delta_summary),
                    _json_dumps({**parsed["snapshot"], "document_count": counts["documents"]}),
                    completed_at,
                ),
            )
            conn.commit()

        self._invalidate_graph_cache()
        self._vector_upsert(parsed["documents"])
        # Re-materialize knowledge-intel graph overlays that this reindex wiped.
        # Fully guarded: any failure here must never break reindex or HUD load.
        self._reapply_knowledge_overlays()
        self._record_ingest_run(
            run_id=run_id,
            source=reason,
            status="passed",
            root_path=root_path,
            document_count=counts["documents"],
            node_count=counts["nodes"],
            edge_count=counts["edges"],
            started_at=started_at,
            completed_at=completed_at,
            error=None,
            metadata={"clusters": counts["clusters"]},
        )

    def _reapply_knowledge_overlays(self) -> None:
        """Re-upsert persisted knowledge-intel graph overlays after a reindex.

        Reindex wipes ``memory_chamber_*`` tables; the knowledge intelligence layer
        persists its graph to ``data/knowledge/intel/<collection>/graph.json`` so it
        can be re-materialized here. Guarded so a missing/corrupt overlay or any
        error cannot break the reindex pipeline or HUD loading.
        """
        try:
            from .config import paths as _paths
            from .knowledge_intel import graph_producer as _gp

            intel_root = getattr(_paths, "knowledge_intel_path", None)
            if intel_root is None:
                return
            _gp.apply_all_overlays(self.event_store.db_path, intel_root)
        except Exception:  # pragma: no cover - defensive, never propagate
            pass

    def _record_ingest_run(
        self,
        *,
        run_id: str,
        source: str,
        status: str,
        root_path: str,
        document_count: int,
        node_count: int,
        edge_count: int,
        started_at: float,
        completed_at: Optional[float],
        error: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_chamber_ingest_runs (
                    id, source, status, root_path, document_count, node_count, edge_count, started_at, completed_at, error, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    source,
                    status,
                    root_path,
                    document_count,
                    node_count,
                    edge_count,
                    started_at,
                    completed_at,
                    error,
                    _json_dumps(metadata or {}),
                ),
            )
            conn.commit()

    def _vector_upsert(self, documents: Sequence[Dict[str, Any]]) -> None:
        if isinstance(self.vector_store, NullVectorStore):
            return
        ids = [f"memory-chamber:{item['id']}" for item in documents]
        texts = [str(item["content"])[:12000] for item in documents]
        metadatas = [
            {
                "source_type": item["source_kind"],
                "source_id": item["id"],
                "path": item["path"],
                "doc_type": item["doc_type"],
            }
            for item in documents
        ]
        try:
            self.vector_store.upsert(ids, texts, metadatas)
        except Exception:
            logger.exception("memory_chamber_vector_upsert_failed")

    def _query_terms(self, query: str) -> List[str]:
        tokens = _token_candidates(query)
        counts = Counter(token for token in tokens if token not in STOPWORDS)
        return [term for term, _weight in counts.most_common(6)]

    def _reasoning_citations(
        self,
        *,
        semantic_hits: List[Dict[str, Any]],
        matched_rows: List[Dict[str, Any]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        citations: List[Dict[str, Any]] = []
        for hit in semantic_hits[:limit]:
            citations.append(
                {
                    "kind": "semantic_hit",
                    "source_id": hit.get("source_id"),
                    "source_type": hit.get("source_type"),
                    "vector_key": hit.get("vector_key"),
                    "relevance": hit.get("relevance"),
                    "text_preview": hit.get("text_preview"),
                    "path": str((hit.get("metadata") or {}).get("path") or ""),
                }
            )
        remaining = max(0, limit - len(citations))
        for row in matched_rows[:remaining]:
            citations.append(
                {
                    "kind": "graph_node",
                    "source_id": row.get("id"),
                    "label": row.get("label"),
                    "node_type": row.get("node_type"),
                    "weight": float(row.get("weight") or 0.0),
                }
            )
        return citations

    def _is_architecture_node(self, row: Dict[str, Any]) -> bool:
        haystack = " ".join(
            [
                str(row.get("label") or ""),
                str(row.get("node_type") or ""),
                str(row.get("zone") or ""),
                str(row.get("metadata_json") or ""),
            ]
        ).lower()
        return any(
            token in haystack
            for token in (
                "architecture",
                "graph",
                "memory",
                "system",
                "workflow",
                "routing",
                "integration",
                "obeos",
                "blackfong",
                "daise",
            )
        )

    def _is_architecture_semantic_hit(self, item: Dict[str, Any]) -> bool:
        metadata = item.get("metadata") or {}
        haystack = " ".join(
            [
                str(item.get("source_type") or ""),
                str(item.get("source_id") or ""),
                str(metadata.get("path") or ""),
                str(metadata.get("doc_type") or ""),
                str(item.get("text_preview") or ""),
            ]
        ).lower()
        return any(
            token in haystack
            for token in (
                "architecture",
                "graph",
                "memory",
                "system",
                "workflow",
                "routing",
                "contract",
                "obeos",
                "blackfong",
                "daise",
            )
        )

    def _semantic_search(self, query: str, *, limit: int) -> List[Dict[str, Any]]:
        try:
            matches = self.vector_store.search(query, k=limit)
        except Exception:
            logger.exception("memory_chamber_semantic_search_failed")
            return []
        results: List[Dict[str, Any]] = []
        for match in matches[:limit]:
            metadata = dict(match.get("metadata") or {})
            vector_key = str(match.get("id") or "")
            embedding = self._query_one(
                """
                SELECT source_type, source_id, metadata_json
                FROM memory_chamber_embeddings
                WHERE vector_key = ?
                LIMIT 1
                """,
                (vector_key,),
            )
            results.append(
                {
                    "vector_key": vector_key,
                    "relevance": float(match.get("relevance") or 0.0),
                    "distance": float(match.get("distance") or 0.0),
                    "source_type": (embedding or {}).get("source_type") or metadata.get("source_type"),
                    "source_id": (embedding or {}).get("source_id") or metadata.get("source_id"),
                    "metadata": metadata,
                    "text_preview": str(match.get("text") or "")[:280],
                }
            )
        return results

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.event_store.db_path), timeout=30.0, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _query_all(self, statement: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(statement, tuple(params))
            return [dict(row) for row in cursor.fetchall()]

    def _query_one(self, statement: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self._query_all(statement, params)
        return rows[0] if rows else None

    def _detect_fts5(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("SELECT count(*) FROM memory_chamber_documents_fts")
            return True
        except Exception:
            return False

    def _complete_future(self, future: Future[Any]) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("memory_chamber_background_future_failed")
        with self._status_lock:
            self._status["queue_depth"] = 0
            self._status["updated_at"] = time.time()
            self._index_future = None

    def _set_status(self, **updates: Any) -> None:
        with self._status_lock:
            self._status.update(updates)

    def _restore_runtime_state_from_storage(self, *, start_watch: bool) -> None:
        counts_row = self._query_one(
            """
            SELECT
                (SELECT COUNT(*) FROM memory_chamber_documents) AS documents,
                (SELECT COUNT(*) FROM memory_chamber_nodes) AS nodes,
                (SELECT COUNT(*) FROM memory_chamber_edges) AS edges,
                (SELECT COUNT(*) FROM memory_chamber_clusters) AS clusters
            """
        )
        counts = {
            "documents": int((counts_row or {}).get("documents") or 0),
            "nodes": int((counts_row or {}).get("nodes") or 0),
            "edges": int((counts_row or {}).get("edges") or 0),
            "clusters": int((counts_row or {}).get("clusters") or 0),
        }
        latest_snapshot = self._query_one(
            """
            SELECT created_at
            FROM memory_chamber_graph_snapshots
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        latest_ingest = self._query_one(
            """
            SELECT completed_at, status, error
            FROM memory_chamber_ingest_runs
            ORDER BY completed_at DESC
            LIMIT 1
            """
        )
        last_indexed_at = float(
            (latest_snapshot or {}).get("created_at")
            or (latest_ingest or {}).get("completed_at")
            or 0.0
        )
        if counts["nodes"] > 0:
            self._set_status(
                state="ready",
                updated_at=time.time(),
                last_indexed_at=last_indexed_at or self._status["started_at"],
                counts=counts,
                last_error=None,
                last_error_code=None,
                graph_cache_state="queued",
            )
            return
        if not start_watch:
            last_error = str((latest_ingest or {}).get("error") or "").strip() or None
            last_error_code = "no_graph_data" if counts["documents"] <= 0 else None
            self._set_status(
                state="degraded",
                updated_at=time.time(),
                last_indexed_at=last_indexed_at or None,
                counts=counts,
                last_error=last_error,
                last_error_code=last_error_code,
                graph_cache_state="cold",
            )

    def _schedule_graph_prewarm(self, *, reason: str) -> None:
        if nx is None:
            return
        with self._status_lock:
            counts = dict(self._status.get("counts", {}))
        if int(counts.get("nodes") or 0) <= 0:
            return
        with self._graph_cache_lock:
            if self._graph_cache is not None:
                warmed_at = time.time()
                self._set_status(graph_cache_state="ready", graph_cache_warmed_at=warmed_at, updated_at=warmed_at)
                return
            if self._graph_prewarm_thread is not None and self._graph_prewarm_thread.is_alive():
                return
            epoch = self._graph_cache_epoch
            self._graph_prewarm_epoch = epoch
        self._set_status(graph_cache_state="warming", updated_at=time.time())
        self._graph_prewarm_thread = threading.Thread(
            target=self._prewarm_graph_cache,
            kwargs={"epoch": epoch, "reason": reason},
            name="memory-chamber-prewarm",
            daemon=True,
        )
        self._graph_prewarm_thread.start()

    def _prewarm_graph_cache(self, *, epoch: int, reason: str) -> None:
        try:
            self._networkx_graph()
        except Exception:
            logger.exception("memory_chamber_graph_prewarm_failed", extra={"reason": reason})
            with self._graph_cache_lock:
                if epoch != self._graph_cache_epoch:
                    return
                self._graph_prewarm_epoch = None
            self._set_status(graph_cache_state="degraded", updated_at=time.time())
            return
        with self._graph_cache_lock:
            if epoch != self._graph_cache_epoch:
                return
            self._graph_prewarm_epoch = None
        warmed_at = time.time()
        self._set_status(graph_cache_state="ready", graph_cache_warmed_at=warmed_at, updated_at=warmed_at)

    def _start_watch(self) -> None:
        if self._watch_thread is not None and self._watch_thread.is_alive():
            return
        self._watch_thread = threading.Thread(target=self._watch_loop, name="memory-chamber-watch", daemon=True)
        self._watch_thread.start()

    def _watch_loop(self) -> None:
        while not self._watch_stop.wait(self._watch_interval_s):
            try:
                signature = self._root_signature(self.watch_roots())
            except Exception:
                logger.exception("memory_chamber_watch_signature_failed")
                continue
            if self._last_signature is None:
                self._last_signature = signature
                continue
            if signature != self._last_signature:
                self._last_signature = signature
                self.request_reindex(reason="watch", background=True)

    def _root_signature(self, roots: Sequence[Path]) -> tuple[int, int, int]:
        file_count = 0
        newest_mtime = 0
        total_size = 0
        for root in roots:
            for file_path in self._iter_files(root):
                try:
                    stat = file_path.stat()
                except OSError:
                    continue
                file_count += 1
                newest_mtime = max(newest_mtime, int(stat.st_mtime))
                total_size += min(int(stat.st_size), 65_535)
        return (file_count, newest_mtime, total_size)

    def _iter_files(self, root: Path) -> Iterable[Path]:
        yielded = 0
        for current_root, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name
                for name in dirnames
                if name not in DEFAULT_EXCLUDED_DIRS
                and not name.startswith(".chroma")
                and "cache" not in name.lower()
            ]
            current_path = Path(current_root)
            if any(part in DEFAULT_EXCLUDED_DIRS for part in current_path.parts):
                continue
            for filename in filenames:
                if yielded >= MAX_FILES_PER_ROOT:
                    return
                path = current_path / filename
                if path.suffix.lower() not in DEFAULT_ALLOWED_EXTENSIONS:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > MAX_FILE_BYTES:
                    continue
                yielded += 1
                yield path

    def _relative_display_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace_root.resolve())).replace("\\", "/")
        except ValueError:
            return str(path.resolve()).replace("\\", "/")

    def _accumulate_node(
        self,
        node_map: Dict[str, Dict[str, Any]],
        node_id: str,
        *,
        label: str,
        node_type: str,
        zone: str,
        state: str,
        weight: float,
        metadata: Optional[Dict[str, Any]] = None,
        document_count: int = 0,
        mention_count: int = 0,
    ) -> None:
        node = node_map.setdefault(
            node_id,
            {
                "id": node_id,
                "label": label,
                "normalized_label": _normalize_label(label),
                "node_type": node_type,
                "zone": zone,
                "state": state,
                "weight": 0.0,
                "document_count": 0,
                "mention_count": 0,
                "metadata": {},
                "provenance": {"source": "memory_chamber"},
            },
        )
        node["weight"] = float(node["weight"]) + float(weight)
        node["document_count"] = int(node["document_count"]) + int(document_count)
        node["mention_count"] = int(node["mention_count"]) + int(mention_count)
        if metadata:
            node["metadata"].update(metadata)
        if state in {"active", "degraded"} or node["state"] == "ready":
            node["state"] = state

    def _accumulate_edge(
        self,
        edge_map: Dict[tuple[str, str, str], Dict[str, Any]],
        source_id: str,
        target_id: str,
        *,
        relation: str,
        weight: float,
        confidence: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        ordered_source, ordered_target = (source_id, target_id) if source_id <= target_id or relation not in {"co_occurrence", "suggested_link"} else (target_id, source_id)
        key = (ordered_source, ordered_target, relation)
        edge = edge_map.setdefault(
            key,
            {
                "id": _stable_id("edge", ordered_source, ordered_target, relation),
                "source_id": ordered_source,
                "target_id": ordered_target,
                "relation": relation,
                "weight": 0.0,
                "confidence": confidence,
                "freshness_s": None,
                "metadata": {},
                "provenance": {"source": "memory_chamber"},
            },
        )
        edge["weight"] = float(edge["weight"]) + float(weight)
        edge["confidence"] = max(float(edge["confidence"]), float(confidence))
        if metadata:
            edge["metadata"].update(metadata)

    def _live_brain_anchor_specs(
        self,
        *,
        startup: Dict[str, Any],
        cluster_rows: Sequence[Dict[str, Any]],
        fault_rows: Sequence[Dict[str, Any]],
        recent_docs: Sequence[Dict[str, Any]],
        recent_sessions: Sequence[Dict[str, Any]],
        integration_listing: Dict[str, Any],
        session_id: Optional[str],
        request_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        subsystems = startup.get("subsystems", {}) if isinstance(startup, dict) else {}
        session_row = recent_sessions[0] if recent_sessions else {}
        active_cluster = cluster_rows[0] if cluster_rows else {}
        recent_doc = recent_docs[0] if recent_docs else {}
        current_fault = fault_rows[0] if fault_rows else {}
        integration_counts = integration_listing.get("counts", {}) if isinstance(integration_listing, dict) else {}
        available_integrations = int(integration_counts.get("available") or 0)
        current_session_id = session_id or str(session_row.get("session_id") or "")
        current_session_node = f"session:{current_session_id}" if current_session_id else None
        current_task_label = (
            str(session_row.get("preview") or "").strip()
            or (request_id and f"Request {request_id}")
            or "Workspace warmup"
        )
        cluster_target = str(active_cluster.get("id") or "") or None
        recent_doc_target = str(recent_doc.get("id") or "") or None
        fault_target = str(current_fault.get("id") or "") or None

        def subsystem_state(key: str, default: str = "warming") -> str:
            return str((subsystems.get(key) or {}).get("state") or default)

        def subsystem_summary(key: str, default: str) -> str:
            return str((subsystems.get(key) or {}).get("summary") or default)

        memory_state = "degraded" if any(
            subsystem_state(key) in {"degraded", "offline", "missing"}
            for key in ("session_memory", "pinned_memory", "black_box_archive")
        ) else "ready"
        system_state = "degraded" if any(
            subsystem_state(key) in {"degraded", "offline", "missing"}
            for key in ("backend_model", "blackfong_core")
        ) else "ready"
        fault_state = "degraded" if current_fault else "ready"
        queue_state = subsystem_state("operator_queue", "ready")
        integration_state = subsystem_state("integrations", "missing")
        cluster_state = "active" if active_cluster else "missing"
        session_state = "active" if current_session_node else "missing"
        recent_memory_state = "ready" if recent_doc_target else "missing"

        return [
            {
                "node_id": "anchor:user",
                "label": "User",
                "zone": "operator",
                "tier": 1,
                "state": "active" if current_session_node else "ready",
                "summary": "Current operator context and active session entry point.",
                "region_id": "operator_core",
                "home_slot": "user",
                "drill": {"mode": "neighborhood", "selected_node": current_session_node} if current_session_node else None,
                "why": "Represents the operator as the live origin of current cognition and session flow.",
            },
            {
                "node_id": "anchor:blackfong",
                "label": "Blackfong",
                "zone": "agent",
                "tier": 1,
                "state": subsystem_state("blackfong_core", "warming"),
                "summary": subsystem_summary("blackfong_core", "Blackfong holds workspace authority."),
                "region_id": "operator_core",
                "home_slot": "blackfong",
                "drill": {"mode": "agents", "selected_node": self._resolve_node_id("blackfong")},
                "why": "Session authority and the center of the Blackcomputer workspace contract.",
            },
            {
                "node_id": "anchor:daise",
                "label": "DAISE",
                "zone": "agent",
                "tier": 1,
                "state": subsystem_state("daise", "warming"),
                "summary": subsystem_summary("daise", "D.A.I.S.E. is preparing the local reasoning surface."),
                "region_id": "operator_core",
                "home_slot": "daise",
                "drill": {"mode": "agents", "selected_node": self._resolve_node_id("daise")},
                "why": "Primary reasoning consumer/producer for the unified cognition graph.",
            },
            {
                "node_id": "anchor:system",
                "label": "System",
                "zone": "system",
                "tier": 2,
                "state": system_state,
                "summary": subsystem_summary("backend_model", "Tracks runtime and backend readiness."),
                "region_id": "system_ring",
                "home_slot": "system",
                "drill": {"mode": "agents", "selected_node": self._resolve_node_id("system")},
                "why": "Summarizes runtime health, backend readiness, and workspace infrastructure.",
            },
            {
                "node_id": "anchor:tinyprogrammer",
                "label": "TinyProgrammer",
                "zone": "agent",
                "tier": 2,
                "state": subsystem_state("tinyprogrammer", "booting"),
                "summary": subsystem_summary("tinyprogrammer", "TinyProgrammer startup state."),
                "region_id": "system_ring",
                "home_slot": "tiny",
                "drill": {"mode": "agents", "selected_node": self._resolve_node_id("tiny")},
                "why": "Shows the live state of the Tiny companion and its graph participation.",
            },
            {
                "node_id": "anchor:memory",
                "label": "Memory",
                "zone": "memory",
                "tier": 2,
                "state": memory_state,
                "summary": "Session, pinned, and archive memory surfaces condensed into one operational region.",
                "region_id": "memory_region",
                "home_slot": "memory",
                "drill": {"mode": "cluster_map", "selected_node": cluster_target or self._resolve_node_id("memory")},
                "why": "High-level anchor for the durable and active memory mass behind OBEOS cognition.",
            },
            {
                "node_id": "anchor:queue",
                "label": "Queue",
                "zone": "queue",
                "tier": 2,
                "state": queue_state,
                "summary": subsystem_summary("operator_queue", "Queue state for requests and background work."),
                "region_id": "system_ring",
                "home_slot": "queue",
                "drill": {"mode": "faults", "selected_node": fault_target} if fault_target else {"mode": "documents", "selected_node": current_session_node},
                "why": "Tracks pending, active, and recently resolved operator work.",
            },
            {
                "node_id": "anchor:faults",
                "label": "Faults",
                "zone": "faults",
                "tier": 2,
                "state": fault_state,
                "summary": str(current_fault.get("label") or "No current fault cluster is dominating the graph."),
                "region_id": "fault_region",
                "home_slot": "faults",
                "drill": {"mode": "faults", "selected_node": fault_target},
                "why": "Collapses degraded, warning, and fault-adjacent graph structures into one readable anchor.",
            },
            {
                "node_id": "anchor:integrations",
                "label": "Integrations",
                "zone": "integrations",
                "tier": 2,
                "state": integration_state,
                "summary": f"{available_integrations} integration(s) currently available." if available_integrations else "No active optional integrations detected.",
                "region_id": "system_ring",
                "home_slot": "integrations",
                "drill": {"mode": "agents", "selected_node": self._resolve_node_id("redamon") or self._resolve_node_id("mobray")},
                "why": "Summarizes optional external projects without polluting the canonical graph.",
            },
            {
                "node_id": "anchor:active_cluster",
                "label": "Active Cluster",
                "zone": "memory",
                "tier": 2,
                "state": cluster_state,
                "summary": self._friendly_cluster_label(active_cluster) if active_cluster else "No active cluster available yet.",
                "region_id": "memory_region",
                "home_slot": "active_cluster",
                "drill": {"mode": "neighborhood", "selected_node": cluster_target},
                "why": "Shortcut into the strongest currently surfaced memory region.",
            },
            {
                "node_id": "anchor:current_task",
                "label": "Current Task",
                "zone": "tasks",
                "tier": 2,
                "state": "active" if request_id or current_session_node else "ready",
                "summary": current_task_label,
                "region_id": "operator_core",
                "home_slot": "current_task",
                "drill": {"mode": "neighborhood", "selected_node": current_session_node},
                "why": "Condenses the currently active or most recent operator task into a stable anchor.",
            },
            {
                "node_id": "anchor:current_session",
                "label": "Current Session",
                "zone": "tasks",
                "tier": 2,
                "state": session_state,
                "summary": current_session_id or "No active session node available.",
                "region_id": "operator_core",
                "home_slot": "current_session",
                "drill": {"mode": "neighborhood", "selected_node": current_session_node},
                "why": "Shows the live session context without exposing the full session graph by default.",
            },
            {
                "node_id": "anchor:recent_memory",
                "label": "Recent Memory",
                "zone": "memory",
                "tier": 2,
                "state": recent_memory_state,
                "summary": str(recent_doc.get("label") or "No recent memory document available."),
                "region_id": "memory_region",
                "home_slot": "recent_memory",
                "drill": {"mode": "documents", "selected_node": recent_doc_target},
                "why": "Fast path into the latest surfaced memory artifact or document cluster.",
            },
            {
                "node_id": "anchor:current_fault",
                "label": "Current Fault",
                "zone": "faults",
                "tier": 2,
                "state": fault_state,
                "summary": str(current_fault.get("label") or "No fault is currently selected."),
                "region_id": "fault_region",
                "home_slot": "current_fault",
                "drill": {"mode": "faults", "selected_node": fault_target},
                "why": "Highlights the single most relevant current fault when one exists.",
            },
        ]

    def _synthetic_anchor_node(
        self,
        *,
        node_id: str,
        label: str,
        zone: str,
        tier: int,
        state: str,
        summary: str,
        region_id: str,
        home_slot: str,
        drill: Optional[Dict[str, Any]] = None,
        why: str = "",
    ) -> Dict[str, Any]:
        return {
            "id": node_id,
            "label": label,
            "kind": "anchor",
            "zone": zone,
            "layers": ["live_brain", "memory"],
            "state": state,
            "emphasis": 0.92 if tier == 1 else 0.74,
            "metrics": {"weight": 1000.0 - (tier * 75.0), "document_count": 0, "mention_count": 0},
            "provenance": {"source": "memory_chamber_presentation", "synthetic": True},
            "meta": {"summary": summary},
            "tier": tier,
            "anchor": True,
            "synthetic": True,
            "region_id": region_id,
            "home_slot": home_slot,
            "always_show_label": True,
            "label_priority": 1.0 if tier == 1 else 0.9,
            "detail_level": "overview",
            "why": why or summary,
            "drill": drill or {},
        }

    def _best_matching_row(self, text: str, preferred_types: Sequence[str] = ()) -> Optional[Dict[str, Any]]:
        matches = self._match_nodes(text, limit=12)
        if not matches:
            return None
        if preferred_types:
            lowered = tuple(item.lower() for item in preferred_types)
            typed = [row for row in matches if str(row.get("node_type") or "").lower().startswith(lowered)]
            if typed:
                return typed[0]
        return matches[0]

    def _query_node_row(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self._query_one(
            """
            SELECT id, label, normalized_label, node_type, zone, state, weight, document_count, mention_count, metadata_json, provenance_json
            FROM memory_chamber_nodes
            WHERE id = ?
            """,
            (node_id,),
        )

    def _node_dict_from_row(self, row: Dict[str, Any], *, layer: str) -> Dict[str, Any]:
        node = self._row_to_cognition_node(row, layer=layer).to_dict()
        node["label"] = self._display_label_for_row(row)
        return node

    def _display_label_for_row(self, row: Dict[str, Any]) -> str:
        node_type = str(row.get("node_type") or "")
        label = str(row.get("label") or row.get("id") or "")
        if node_type == "cluster":
            return self._friendly_cluster_label(row)
        if node_type == "document":
            path_value = (_json_loads(row.get("metadata_json"), {}) or {}).get("path")
            if path_value:
                return Path(str(path_value)).name
        if label.startswith("session:"):
            return label.replace("session:", "Session ")
        if self._looks_low_signal_label(label):
            return label[:18] + "..." if len(label) > 18 else label
        return label

    def _friendly_cluster_label(self, cluster_row: Dict[str, Any]) -> str:
        if not cluster_row:
            return "Cluster"
        metadata = _json_loads(cluster_row.get("metadata_json"), {}) if "metadata_json" in cluster_row else dict(cluster_row.get("metadata") or {})
        members = list(metadata.get("members") or [])
        labels: List[str] = []
        for member_id in members[:18]:
            member = self._query_one("SELECT label, node_type FROM memory_chamber_nodes WHERE id = ?", (member_id,))
            if member is None:
                continue
            candidate = str(member.get("label") or "").strip()
            if not candidate or self._looks_low_signal_label(candidate):
                continue
            if str(member.get("node_type") or "") == "concept":
                labels.append(candidate)
            if len(labels) >= 3:
                break
        if labels:
            return " / ".join(labels[:3])
        label = str(cluster_row.get("label") or "Cluster")
        return label if not self._looks_low_signal_label(label) else "Cluster"

    def _cluster_rows(self, *, limit: int) -> List[Dict[str, Any]]:
        return self._query_all(
            """
            SELECT id, label, algorithm, score, member_count, metadata_json, updated_at
            FROM memory_chamber_clusters
            ORDER BY member_count DESC, score DESC, label ASC
            LIMIT ?
            """,
            (limit,),
        )

    def _cluster_member_rows(self, cluster_row: Dict[str, Any], *, limit: int) -> List[Dict[str, Any]]:
        metadata = _json_loads(cluster_row.get("metadata_json"), {}) if "metadata_json" in cluster_row else dict(cluster_row.get("metadata") or {})
        member_ids = [member_id for member_id in list(metadata.get("members") or []) if member_id]
        rows: List[Dict[str, Any]] = []
        for member_id in member_ids[: max(limit * 3, limit)]:
            row = self._query_node_row(str(member_id))
            if row is None:
                continue
            rows.append(row)
        rows.sort(key=lambda row: float(row.get("weight") or 0.0), reverse=True)
        return rows[:limit]

    def _rows_for_live_brain_focus(self, *, selected_node: Optional[str], search: Optional[str], limit: int) -> List[Dict[str, Any]]:
        if search:
            center = self._resolve_node_id(search)
            if center:
                return self._rows_for_neighborhood(center, depth=1, limit=limit)
            return self._match_nodes(search, limit=limit)
        if selected_node and not str(selected_node).startswith("anchor:"):
            return self._rows_for_neighborhood(selected_node, depth=1, limit=limit)
        return []

    def _default_neighborhood_center(self) -> Optional[str]:
        for label in ("blackfong", "daise", "memory", "tiny"):
            resolved = self._resolve_node_id(label)
            if resolved:
                return resolved
        return None

    def _edge_dicts_for_node_ids(self, node_ids: Sequence[str], *, layer: str, limit: int) -> List[Dict[str, Any]]:
        edge_rows = self._edge_rows_for_nodes(node_ids, limit=limit)
        return [
            self._annotate_display_edge(self._row_to_cognition_edge(row, layer=layer).to_dict())
            for row in edge_rows
        ]

    def _annotate_display_node(
        self,
        node: Dict[str, Any],
        *,
        mode: str,
        anchor: bool = False,
        synthetic: bool = False,
        tier: Optional[int] = None,
        region_id: Optional[str] = None,
        home_slot: Optional[str] = None,
        always_show_label: bool = False,
        label_priority: Optional[float] = None,
        detail_level: Optional[str] = None,
        why: str = "",
        drill: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
    ) -> Dict[str, Any]:
        node = dict(node)
        current_label = label or str(node.get("label") or node.get("id") or "")
        resolved_tier = tier or self._infer_node_tier(current_label, str(node.get("kind") or "concept"))
        node["label"] = current_label
        node["tier"] = resolved_tier
        node["anchor"] = bool(anchor)
        node["synthetic"] = bool(synthetic or node.get("synthetic"))
        node["region_id"] = region_id or self._region_for_zone(str(node.get("zone") or "memory"))
        node["home_slot"] = home_slot
        node["always_show_label"] = bool(always_show_label or resolved_tier == 1)
        node["label_priority"] = float(label_priority if label_priority is not None else self._label_priority_for_node(node))
        node["detail_level"] = detail_level or self._detail_level_for_node(node, mode=mode)
        node["why"] = why or str((node.get("meta") or {}).get("summary") or "")
        node["drill"] = drill or node.get("drill") or {}
        node["low_signal_label"] = self._looks_low_signal_label(current_label)
        return node

    def _annotate_display_edge(self, edge: Dict[str, Any]) -> Dict[str, Any]:
        edge = dict(edge)
        edge["edge_category"] = self._edge_category(edge)
        edge["display_weight"] = max(float(edge.get("urgency") or 0.0), float(edge.get("confidence") or 0.0))
        edge["detail_level"] = "close" if edge["edge_category"] in {"event", "fault"} else "mid"
        return edge

    def _synthetic_edge(
        self,
        *,
        source: str,
        target: str,
        relation: str,
        category: str,
        urgency: float,
        state: str = "ready",
    ) -> Dict[str, Any]:
        return {
            "id": _stable_id("presentation-edge", source, target, relation),
            "source": source,
            "target": target,
            "layers": ["live_brain", "memory"],
            "state": state,
            "confidence": 1.0,
            "urgency": urgency,
            "freshness_s": None,
            "label": relation.replace("_", " "),
            "provenance": {"source": "memory_chamber_presentation"},
            "meta": {"synthetic": True},
            "edge_category": category,
            "display_weight": urgency,
            "detail_level": "overview" if category in {"agent", "concept"} else "mid",
        }

    def _live_brain_anchor_edges(self, nodes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        node_ids = {str(node.get("id") or "") for node in nodes}
        edges: List[Dict[str, Any]] = []

        def connect(source: str, target: str, relation: str, category: str, urgency: float) -> None:
            if source in node_ids and target in node_ids:
                edges.append(self._synthetic_edge(source=source, target=target, relation=relation, category=category, urgency=urgency, state="active"))

        connect("anchor:blackfong", "anchor:daise", "authority_link", "agent", 0.95)
        connect("anchor:blackfong", "anchor:user", "operator_link", "agent", 0.9)
        connect("anchor:blackfong", "anchor:system", "runtime_link", "agent", 0.84)
        connect("anchor:blackfong", "anchor:tinyprogrammer", "companion_link", "agent", 0.82)
        connect("anchor:daise", "anchor:memory", "reasons_over", "concept", 0.88)
        connect("anchor:daise", "anchor:current_task", "current_task", "event", 0.8)
        connect("anchor:user", "anchor:current_session", "session_focus", "event", 0.78)
        connect("anchor:memory", "anchor:active_cluster", "active_cluster", "concept", 0.8)
        connect("anchor:memory", "anchor:recent_memory", "recent_memory", "document", 0.76)
        connect("anchor:system", "anchor:queue", "queue_state", "event", 0.72)
        connect("anchor:system", "anchor:faults", "fault_state", "fault", 0.72)
        connect("anchor:system", "anchor:integrations", "integration_state", "agent", 0.7)
        connect("anchor:faults", "anchor:current_fault", "current_fault", "fault", 0.76)
        return edges

    def _cap_nodes(self, nodes: Sequence[Dict[str, Any]], *, selected_node: Optional[str], max_nodes: int) -> List[Dict[str, Any]]:
        if len(nodes) <= max_nodes:
            return list(nodes)

        def score(node: Dict[str, Any]) -> float:
            tier = int(node.get("tier") or 4)
            selected_bonus = 900.0 if str(node.get("id")) == str(selected_node or "") else 0.0
            anchor_bonus = 700.0 if node.get("anchor") else 0.0
            tier_bonus = {1: 450.0, 2: 300.0, 3: 160.0, 4: 40.0}.get(tier, 20.0)
            label_bonus = float(node.get("label_priority") or 0.0) * 100.0
            weight_bonus = float((node.get("metrics") or {}).get("weight") or 0.0)
            return selected_bonus + anchor_bonus + tier_bonus + label_bonus + weight_bonus

        ranked = sorted(nodes, key=score, reverse=True)
        chosen = ranked[:max_nodes]
        chosen_ids = {str(node.get("id")) for node in chosen}
        if selected_node and selected_node not in chosen_ids:
            selected = next((node for node in ranked if str(node.get("id")) == str(selected_node)), None)
            if selected is not None and chosen:
                chosen[-1] = selected
        deduped: Dict[str, Dict[str, Any]] = {}
        for node in chosen:
            deduped[str(node.get("id"))] = node
        return list(deduped.values())

    def _filter_edges(
        self,
        edges: Sequence[Dict[str, Any]],
        *,
        visible_node_ids: set[str],
        selected_node: Optional[str],
        edge_filter: str,
        max_edges: int,
    ) -> List[Dict[str, Any]]:
        scoped = [
            dict(edge)
            for edge in edges
            if str(edge.get("source") or "") in visible_node_ids and str(edge.get("target") or "") in visible_node_ids
        ]
        if edge_filter not in {"top", "show_more", "show_all"}:
            scoped = [edge for edge in scoped if edge.get("edge_category") == edge_filter]

        per_node_limit = {"top": 4, "show_more": 8, "show_all": 999}.get(edge_filter, 6)
        if per_node_limit < 999:
            counts: Dict[str, int] = defaultdict(int)
            filtered: List[Dict[str, Any]] = []
            for edge in sorted(
                scoped,
                key=lambda item: (
                    0 if selected_node and selected_node in {item.get("source"), item.get("target")} else 1,
                    -float(item.get("display_weight") or 0.0),
                    -float(item.get("confidence") or 0.0),
                ),
            ):
                source = str(edge.get("source") or "")
                target = str(edge.get("target") or "")
                if counts[source] >= per_node_limit or counts[target] >= per_node_limit:
                    continue
                counts[source] += 1
                counts[target] += 1
                filtered.append(edge)
            scoped = filtered

        deduped: Dict[str, Dict[str, Any]] = {}
        for edge in scoped:
            deduped[str(edge.get("id"))] = edge
        ordered = sorted(
            deduped.values(),
            key=lambda item: (
                0 if selected_node and selected_node in {item.get("source"), item.get("target")} else 1,
                -float(item.get("display_weight") or 0.0),
                -float(item.get("confidence") or 0.0),
            ),
        )
        return ordered[:max_edges]

    def _edge_category(self, edge: Dict[str, Any]) -> str:
        relation = str(edge.get("label") or "").strip().lower().replace(" ", "_")
        if relation in {"sequence", "session_contains"}:
            return "sequence"
        if relation in {"references"}:
            return "document"
        if relation in {"participates", "creates"}:
            return "agent"
        if relation in {"mentions", "co_occurrence", "clusters", "suggested_link"}:
            return "concept"
        if relation in {"fault", "fault_state", "current_fault"}:
            return "fault"
        return "event"

    def _build_regions(
        self,
        nodes: Sequence[Dict[str, Any]],
        *,
        mode: str,
        strategy: str,
        selected_node: Optional[str],
    ) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for node in nodes:
            grouped[str(node.get("region_id") or self._region_for_zone(str(node.get("zone") or "memory")))].append(node)
        regions: List[Dict[str, Any]] = []
        for region_id, items in grouped.items():
            if not items:
                continue
            label = self._region_label(region_id, items, mode=mode)
            regions.append(
                {
                    "id": region_id,
                    "label": label,
                    "node_ids": [str(item.get("id")) for item in items],
                    "selected": bool(selected_node and any(str(item.get("id")) == selected_node for item in items)),
                    "kind": "cluster" if region_id.startswith("cluster:") else "region",
                    "zone": str(items[0].get("zone") or "memory"),
                    "detail_level": "overview" if strategy in {"live_brain", "cluster_map"} else "mid",
                }
            )
        return regions

    def _build_inspector_card(
        self,
        nodes: Sequence[Dict[str, Any]],
        edges: Sequence[Dict[str, Any]],
        selected_node: Optional[str],
        *,
        regions: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not selected_node:
            return {
                "title": "Thoughtglass",
                "summary": "Select a node to inspect its role, neighborhood, and recent graph changes.",
                "details": [],
                "developer_details": {},
            }
        node = next((item for item in nodes if str(item.get("id")) == str(selected_node)), None)
        if node is None:
            return {
                "title": "Thoughtglass",
                "summary": "The selected node is no longer visible in this presentation mode.",
                "details": [],
                "developer_details": {},
            }
        related_edges = [edge for edge in edges if selected_node in {edge.get("source"), edge.get("target")}]
        related_nodes = []
        for edge in related_edges:
            neighbor_id = str(edge.get("target") if str(edge.get("source")) == str(selected_node) else edge.get("source"))
            neighbor = next((item for item in nodes if str(item.get("id")) == neighbor_id), None)
            if neighbor is not None:
                related_nodes.append(neighbor)
        top_neighbors = [neighbor.get("label") for neighbor in sorted(related_nodes, key=lambda item: float((item.get("metrics") or {}).get("weight") or 0.0), reverse=True)[:5]]
        related_documents = [neighbor.get("label") for neighbor in related_nodes if str(neighbor.get("kind") or "") == "document"][:4]
        related_agents = [neighbor.get("label") for neighbor in related_nodes if str(neighbor.get("kind") or "").startswith("agent") or str(neighbor.get("label") or "").lower() in {"blackfong", "daise", "tinyprogrammer", "mobray", "redamon"}][:4]
        recent_deltas = self.recent_deltas(limit=3)
        metrics = node.get("metrics") or {}
        region = next((item for item in regions if str(node.get("id")) in set(item.get("node_ids") or [])), None)
        developer_details = {
            "meta": node.get("meta") or {},
            "provenance": node.get("provenance") or {},
            "regions": region or {},
            "related_edges": related_edges[:8],
        }
        actions = []
        if node.get("drill"):
            drill = node["drill"]
            actions.append(f"Open {str(drill.get('mode') or 'drill').replace('_', ' ').title()}")
        actions.extend(["Ask DAISE", "Trace Path", "Show Documents", "Hide Cluster"])
        return {
            "title": str(node.get("label") or selected_node),
            "summary": str(node.get("why") or f"{node.get('kind')} in {node.get('zone')}"),
            "fields": [
                {"label": "Name", "value": str(node.get("label") or selected_node)},
                {"label": "Type", "value": str(node.get("kind") or "node").replace("_", " ").title()},
                {"label": "Zone", "value": str(node.get("zone") or "memory").replace("_", " ").title()},
                {"label": "State", "value": str(node.get("state") or "ready").replace("_", " ").title()},
                {"label": "Weight", "value": f"{float(metrics.get('weight') or 0.0):.2f}"},
                {"label": "Centrality", "value": f"{float(node.get('label_priority') or 0.0):.2f}"},
                {"label": "Top Neighbors", "value": ", ".join(str(item) for item in top_neighbors) or "None"},
                {"label": "Related Documents", "value": ", ".join(str(item) for item in related_documents) or "None"},
                {"label": "Related Agents", "value": ", ".join(str(item) for item in related_agents) or "None"},
            ],
            "details": [
                f"Region: {region.get('label')}" if region else "Region: Unassigned",
                f"Recent deltas: {len(recent_deltas)} snapshot(s) available.",
                f"Suggested actions: {', '.join(actions[:4])}",
            ],
            "recent_deltas": recent_deltas,
            "actions": actions,
            "developer_details": developer_details,
        }

    def _region_for_zone(self, zone: str) -> str:
        normalized = str(zone or "memory").lower()
        mapping = {
            "operator": "operator_core",
            "agent": "system_ring",
            "memory": "memory_region",
            "queue": "system_ring",
            "task": "operator_core",
            "tasks": "operator_core",
            "faults": "fault_region",
            "forge": "memory_region",
            "sensor": "sensor_region",
            "integrations": "system_ring",
            "system": "system_ring",
        }
        return mapping.get(normalized, f"{normalized}_region")

    def _region_label(self, region_id: str, nodes: Sequence[Dict[str, Any]], *, mode: str) -> str:
        if region_id.startswith("cluster:"):
            return next((str(node.get("label")) for node in nodes if node.get("anchor")), "Cluster Region")
        labels = {
            "operator_core": "Operator Core",
            "system_ring": "System Ring",
            "memory_region": "Memory Region",
            "fault_region": "Fault Region",
            "sensor_region": "Sensor Region",
            "documents_region": "Documents",
            "agents_region": "Agents",
            "tiny_region": "Tiny Region",
        }
        return labels.get(region_id, region_id.replace("_", " ").title())

    def _infer_node_tier(self, label: str, kind: str) -> int:
        normalized = _normalize_label(label)
        if normalized in {"blackfong", "daise", "user"}:
            return 1
        if normalized in {"system", "tinyprogrammer", "tiny", "mobray", "redamon", "memory", "integrations", "faults", "queue"}:
            return 2
        if kind in {"session", "document", "concept", "event", "agent", "task", "cluster"}:
            return 3
        return 4

    def _label_priority_for_node(self, node: Dict[str, Any]) -> float:
        if node.get("anchor"):
            return 1.0
        if self._looks_low_signal_label(str(node.get("label") or "")):
            return 0.15
        return min(0.95, 0.25 + float((node.get("metrics") or {}).get("weight") or 0.0) / 16.0)

    def _detail_level_for_node(self, node: Dict[str, Any], *, mode: str) -> str:
        if node.get("anchor"):
            return "overview"
        kind = str(node.get("kind") or "")
        if mode == "full_debug_graph":
            return "close"
        if kind in {"cluster"}:
            return "overview"
        if kind in {"document", "event", "session"}:
            return "close"
        return "mid"

    def _looks_low_signal_label(self, label: str) -> bool:
        candidate = str(label or "").strip().lower()
        if not candidate:
            return True
        if re.fullmatch(r"[0-9a-f]{8,}", candidate):
            return True
        if re.fullmatch(r"[0-9a-f]{4,}(?:\s*/\s*[0-9a-f]{4,}){1,3}", candidate):
            return True
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f\-]{8,}", candidate):
            return True
        if candidate.startswith("concept:") or candidate.startswith("document:") or candidate.startswith("event:"):
            return True
        if "/" in candidate and len(candidate) > 24:
            return True
        if len(candidate) <= 2:
            return True
        return False

    def _rows_for_default_view(self, *, limit: int) -> List[Dict[str, Any]]:
        return self._query_all(
            """
            SELECT id, label, normalized_label, node_type, zone, state, weight, document_count, mention_count, metadata_json, provenance_json
            FROM memory_chamber_nodes
            ORDER BY
                CASE node_type
                    WHEN 'cluster' THEN 0
                    WHEN 'agent' THEN 1
                    WHEN 'concept' THEN 2
                    WHEN 'document' THEN 3
                    ELSE 4
                END,
                weight DESC,
                label ASC
            LIMIT ?
            """,
            (limit,),
        )

    def _rows_for_recent_documents(self, *, limit: int) -> List[Dict[str, Any]]:
        return self._query_all(
            """
            SELECT n.id, n.label, n.normalized_label, n.node_type, n.zone, n.state, n.weight, n.document_count, n.mention_count, n.metadata_json, n.provenance_json
            FROM memory_chamber_nodes n
            WHERE n.node_type IN ('document', 'session', 'event')
            ORDER BY n.weight DESC, n.label ASC
            LIMIT ?
            """,
            (limit,),
        )

    def _rows_by_type_prefix(self, prefix: str, *, limit: int) -> List[Dict[str, Any]]:
        return self._query_all(
            """
            SELECT id, label, normalized_label, node_type, zone, state, weight, document_count, mention_count, metadata_json, provenance_json
            FROM memory_chamber_nodes
            WHERE node_type LIKE ?
            ORDER BY weight DESC, label ASC
            LIMIT ?
            """,
            (f"{prefix}%", limit),
        )

    def _rows_for_faults(self, *, limit: int) -> List[Dict[str, Any]]:
        return self._query_all(
            """
            SELECT n.id, n.label, n.normalized_label, n.node_type, n.zone, n.state, n.weight, n.document_count, n.mention_count, n.metadata_json, n.provenance_json
            FROM memory_chamber_nodes n
            WHERE n.node_type IN ('event', 'document')
              AND lower(n.label) LIKE '%fault%'
            ORDER BY n.weight DESC, n.label ASC
            LIMIT ?
            """,
            (limit,),
        )

    def _rows_for_neighborhood(self, center: str, *, depth: int, limit: int) -> List[Dict[str, Any]]:
        center_id = self._resolve_node_id(center)
        if not center_id:
            return self._rows_for_default_view(limit=limit)
        graph = self._networkx_graph()
        if graph is None or center_id not in graph:
            row = self._query_one(
                """
                SELECT id, label, normalized_label, node_type, zone, state, weight, document_count, mention_count, metadata_json, provenance_json
                FROM memory_chamber_nodes WHERE id = ?
                """,
                (center_id,),
            )
            return [row] if row else []
        lengths = nx.single_source_shortest_path_length(graph, center_id, cutoff=max(1, depth))
        ordered_ids = [node_id for node_id, _distance in sorted(lengths.items(), key=lambda item: (item[1], item[0]))][:limit]
        placeholders = ",".join("?" for _ in ordered_ids)
        rows = self._query_all(
            f"""
            SELECT id, label, normalized_label, node_type, zone, state, weight, document_count, mention_count, metadata_json, provenance_json
            FROM memory_chamber_nodes
            WHERE id IN ({placeholders})
            """,
            tuple(ordered_ids),
        )
        row_map = {row["id"]: row for row in rows}
        return [row_map[node_id] for node_id in ordered_ids if node_id in row_map]

    def _match_nodes(self, text: str, *, limit: int) -> List[Dict[str, Any]]:
        terms = self._query_terms(text)
        if not terms:
            return []
        rows: List[Dict[str, Any]] = []
        for term in terms:
            matched = self._query_all(
                """
                SELECT id, label, normalized_label, node_type, zone, state, weight, document_count, mention_count, metadata_json, provenance_json
                FROM memory_chamber_nodes
                WHERE normalized_label = ? OR normalized_label LIKE ?
                ORDER BY weight DESC, label ASC
                LIMIT ?
                """,
                (_normalize_label(term), f"%{_normalize_label(term)}%", limit),
            )
            rows.extend(matched)
        deduped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            deduped.setdefault(row["id"], row)
        return list(deduped.values())[:limit]

    def _edge_rows_for_nodes(self, node_ids: Sequence[str], *, limit: int) -> List[Dict[str, Any]]:
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        params = tuple(node_ids) + tuple(node_ids) + (limit,)
        return self._query_all(
            f"""
            SELECT id, source_id, target_id, relation, weight, confidence, freshness_s, metadata_json, provenance_json
            FROM memory_chamber_edges
            WHERE source_id IN ({placeholders}) AND target_id IN ({placeholders})
            ORDER BY weight DESC, confidence DESC
            LIMIT ?
            """,
            params,
        )

    def _resolve_node_id(self, value: str) -> Optional[str]:
        if not value:
            return None
        row = self._query_one("SELECT id FROM memory_chamber_nodes WHERE id = ?", (value,))
        if row is not None:
            return str(row["id"])
        row = self._query_one(
            """
            SELECT id
            FROM memory_chamber_nodes
            WHERE normalized_label = ? OR normalized_label LIKE ?
            ORDER BY weight DESC, label ASC
            LIMIT 1
            """,
            (_normalize_label(value), f"%{_normalize_label(value)}%"),
        )
        return str(row["id"]) if row is not None else None

    def _row_to_cognition_node(self, row: Dict[str, Any], *, layer: str) -> CognitionNode:
        metadata = _json_loads(row["metadata_json"], {})
        node_type = str(row["node_type"])
        emphasis = min(1.0, max(0.08, float(row["weight"] or 0.0) / 8.0))
        layers = ["live_brain", "memory"]
        if node_type.startswith("tiny_"):
            layers.append("forge")
        if node_type in {"document", "session"}:
            layers.append("tasks")
        if node_type == "event":
            layers.append("faults")
        return CognitionNode(
            id=str(row["id"]),
            label=str(row["label"]),
            kind=node_type,
            zone=str(row["zone"] or "memory"),
            layers=list(dict.fromkeys(layers)),
            state=str(row["state"] or "ready"),
            emphasis=emphasis,
            metrics={
                "weight": float(row["weight"] or 0.0),
                "document_count": int(row["document_count"] or 0),
                "mention_count": int(row["mention_count"] or 0),
            },
            provenance=_json_loads(row["provenance_json"], {"source": "memory_chamber"}),
            meta=metadata,
        )

    def _row_to_cognition_edge(self, row: Dict[str, Any], *, layer: str) -> CognitionEdge:
        relation = str(row["relation"])
        state = "active" if float(row["weight"] or 0.0) >= 1.5 else "ready"
        layers = ["live_brain", "memory"]
        if relation == "sequence":
            layers.append("tasks")
        if relation == "creates":
            layers.append("forge")
        return CognitionEdge(
            id=str(row["id"]),
            source=str(row["source_id"]),
            target=str(row["target_id"]),
            layers=list(dict.fromkeys(layers)),
            state=state,
            confidence=float(row["confidence"] or 0.0),
            urgency=min(1.0, float(row["weight"] or 0.0) / 6.0),
            freshness_s=row["freshness_s"],
            label=relation.replace("_", " "),
            provenance=_json_loads(row["provenance_json"], {"source": "memory_chamber"}),
            meta=_json_loads(row["metadata_json"], {}),
        )

    def _build_inspector(self, selected_node: Optional[str], *, edges: Sequence[CognitionEdge]) -> Dict[str, Any]:
        if not selected_node:
            return {"title": "Memory Chamber", "summary": "Select a node to inspect its cognitive neighborhood.", "details": []}
        row = self._query_one(
            """
            SELECT id, label, node_type, zone, weight, document_count, mention_count, metadata_json
            FROM memory_chamber_nodes
            WHERE id = ?
            """,
            (selected_node,),
        )
        if row is None:
            return {"title": "Memory Chamber", "summary": "Selected node is no longer available.", "details": []}
        connected = [edge for edge in edges if edge.source == selected_node or edge.target == selected_node][:12]
        detail_lines = [
            f"{edge.label}: {edge.source} -> {edge.target} (weight {edge.urgency:.2f})"
            for edge in connected
        ]
        return {
            "title": str(row["label"]),
            "summary": f"{row['node_type']} | zone={row['zone']} | weight={float(row['weight'] or 0.0):.2f}",
            "details": detail_lines,
            "metadata": _json_loads(row["metadata_json"], {}),
        }

    def _zone_for_document(self, document: Dict[str, Any]) -> str:
        source_kind = str(document["source_kind"])
        if source_kind == "source_code":
            return "forge"
        if source_kind in {"event", "session"}:
            return "tasks"
        if source_kind == "log":
            return "queue"
        return "memory"

    def _networkx_graph(self) -> Any:
        if nx is None:
            return None
        with self._status_lock:
            counts = dict(self._status.get("counts", {}))
            last_indexed_at = float(self._status.get("last_indexed_at") or 0.0)
        signature = (
            int(counts.get("nodes") or 0),
            int(counts.get("edges") or 0),
            last_indexed_at,
        )
        with self._graph_cache_lock:
            if self._graph_cache is not None and self._graph_cache_signature == signature:
                return self._graph_cache
        graph = nx.Graph()
        rows = self._query_all("SELECT id FROM memory_chamber_nodes")
        for row in rows:
            graph.add_node(str(row["id"]))
        edge_rows = self._query_all("SELECT source_id, target_id, weight, relation FROM memory_chamber_edges")
        for row in edge_rows:
            graph.add_edge(str(row["source_id"]), str(row["target_id"]), weight=float(row["weight"] or 0.0), relation=row["relation"])
        with self._graph_cache_lock:
            self._graph_cache = graph
            self._graph_cache_signature = signature
        return graph

    def _invalidate_graph_cache(self) -> None:
        with self._graph_cache_lock:
            self._graph_cache = None
            self._graph_cache_signature = None
            self._graph_cache_epoch += 1
            self._graph_prewarm_epoch = None
        self._set_status(graph_cache_state="cold", graph_cache_warmed_at=None, updated_at=time.time())
