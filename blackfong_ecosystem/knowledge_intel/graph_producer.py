"""
Memory Chamber graph producer.

Writes knowledge graph elements (a collection node, one document node per embedded
source, ``appears_in`` edges, and document_mention rows) directly into the EXISTING
Memory Chamber tables (``memory_chamber_nodes`` / ``memory_chamber_edges`` /
``memory_chamber_document_mentions``). No new graph tables.

Every node/edge carries provenance in the existing ``provenance_json`` column
(``{source_id, chunk_id, collection, confidence}``). Upserts key on stable ids so
reruns are idempotent. A JSON overlay (``data/knowledge/intel/<c>/graph.json``) is
also written so the graph can be re-materialized after a Memory Chamber reindex
wipes-and-rebuilds its tables.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List

from ..ingestion.source_registry import SourceRegistry, slugify_collection
from . import sources as src


def _sid(*parts: Any) -> str:
    payload = "|".join(str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_graph_elements(
    registry: SourceRegistry, collection: str, *, chunks_dir: Path
) -> Dict[str, Any]:
    """Derive nodes/edges/mentions for a collection from embedded sources only."""
    slug = slugify_collection(collection)
    embedded = src.embedded_text_sources(registry, collection)

    collection_node_id = f"collection:{slug}"
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    mentions: List[Dict[str, Any]] = []

    if embedded:
        nodes.append(
            {
                "id": collection_node_id,
                "label": slug,
                "node_type": "collection",
                "zone": "memory",
                "weight": float(len(embedded)),
                "document_count": len(embedded),
                "mention_count": 0,
                "metadata": {"kind": "knowledge_collection", "collection": slug},
                "provenance": {"collection": slug, "confidence": 1.0},
            }
        )

    for record in embedded:
        refs = src.read_source_chunks(chunks_dir, record.source_id)
        first_chunk = refs[0].chunk_id if refs else ""
        source_node_id = f"knowledge_source:{record.source_id}"
        label = f"{record.series} {record.issue_range}".strip()
        provenance = {
            "source_id": record.source_id,
            "chunk_id": first_chunk,
            "collection": slug,
            "confidence": 1.0,
        }
        nodes.append(
            {
                "id": source_node_id,
                "label": label,
                "node_type": "document",
                "zone": "memory",
                "weight": float(max(1, int(record.chunk_count))),
                "document_count": 1,
                "mention_count": int(record.chunk_count),
                "metadata": {
                    "kind": "knowledge_source",
                    "collection": slug,
                    "source_id": record.source_id,
                    "source_type": record.source_type,
                    "issue_range": record.issue_range,
                    "source_file": record.path,
                    "chunk_count": int(record.chunk_count),
                },
                "provenance": provenance,
            }
        )
        edges.append(
            {
                "id": _sid("edge", source_node_id, collection_node_id, "appears_in"),
                "source_id": source_node_id,
                "target_id": collection_node_id,
                "relation": "appears_in",
                "weight": float(max(1, int(record.chunk_count))),
                "confidence": 1.0,
                "metadata": {"collection": slug, "source_id": record.source_id},
                "provenance": provenance,
            }
        )
        mentions.append(
            {
                "id": _sid("mention", source_node_id, collection_node_id),
                "document_id": source_node_id,
                "node_id": collection_node_id,
                "occurrences": int(record.chunk_count),
                "weight": float(max(1, int(record.chunk_count))),
            }
        )

    return {
        "collection": slug,
        "source_count": len(embedded),
        "collection_node_id": collection_node_id,
        "nodes": nodes,
        "edges": edges,
        "mentions": mentions,
    }


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def apply_graph(db_path: Path, elements: Dict[str, Any]) -> Dict[str, int]:
    """Idempotently upsert elements into the Memory Chamber tables.

    Keys on stable ids via ON CONFLICT so reruns do not duplicate rows and
    ``created_at`` is preserved across runs.
    """
    now = time.time()
    nodes = elements.get("nodes", [])
    edges = elements.get("edges", [])
    mentions = elements.get("mentions", [])

    with _connect(db_path) as conn:
        for node in nodes:
            conn.execute(
                """
                INSERT INTO memory_chamber_nodes (
                    id, label, normalized_label, node_type, zone, state, weight,
                    document_count, mention_count, metadata_json, provenance_json,
                    created_at, updated_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    label=excluded.label,
                    normalized_label=excluded.normalized_label,
                    node_type=excluded.node_type,
                    zone=excluded.zone,
                    weight=excluded.weight,
                    document_count=excluded.document_count,
                    mention_count=excluded.mention_count,
                    metadata_json=excluded.metadata_json,
                    provenance_json=excluded.provenance_json,
                    updated_at=excluded.updated_at,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    node["id"],
                    node["label"],
                    str(node["label"]).lower(),
                    node["node_type"],
                    node.get("zone", "memory"),
                    float(node.get("weight", 1.0)),
                    int(node.get("document_count", 0)),
                    int(node.get("mention_count", 0)),
                    json.dumps(node.get("metadata", {}), ensure_ascii=False),
                    json.dumps(node.get("provenance", {}), ensure_ascii=False),
                    now,
                    now,
                    now,
                ),
            )
        for edge in edges:
            conn.execute(
                """
                INSERT INTO memory_chamber_edges (
                    id, source_id, target_id, relation, weight, confidence,
                    freshness_s, metadata_json, provenance_json,
                    created_at, updated_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    relation=excluded.relation,
                    weight=excluded.weight,
                    confidence=excluded.confidence,
                    metadata_json=excluded.metadata_json,
                    provenance_json=excluded.provenance_json,
                    updated_at=excluded.updated_at,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    edge["id"],
                    edge["source_id"],
                    edge["target_id"],
                    edge["relation"],
                    float(edge.get("weight", 1.0)),
                    float(edge.get("confidence", 1.0)),
                    None,
                    json.dumps(edge.get("metadata", {}), ensure_ascii=False),
                    json.dumps(edge.get("provenance", {}), ensure_ascii=False),
                    now,
                    now,
                    now,
                ),
            )
        for mention in mentions:
            conn.execute(
                """
                INSERT INTO memory_chamber_document_mentions (
                    id, document_id, node_id, occurrences, positions_json, weight,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, '[]', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    occurrences=excluded.occurrences,
                    weight=excluded.weight,
                    updated_at=excluded.updated_at
                """,
                (
                    mention["id"],
                    mention["document_id"],
                    mention["node_id"],
                    int(mention.get("occurrences", 0)),
                    float(mention.get("weight", 1.0)),
                    now,
                    now,
                ),
            )
        conn.commit()

    return {"nodes": len(nodes), "edges": len(edges), "mentions": len(mentions)}


def write_graph_artifact(intel_root: Path, collection: str, elements: Dict[str, Any]) -> Path:
    slug = slugify_collection(collection)
    out_dir = Path(intel_root) / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "graph.json"
    path.write_text(json.dumps(elements, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def produce_collection_graph(
    registry: SourceRegistry,
    collection: str,
    *,
    chunks_dir: Path,
    db_path: Path,
    intel_root: Path,
) -> Dict[str, Any]:
    """Build + persist the collection graph into Memory Chamber and an overlay file."""
    elements = build_graph_elements(registry, collection, chunks_dir=chunks_dir)
    counts = apply_graph(db_path, elements)
    artifact = write_graph_artifact(intel_root, collection, elements)
    return {
        "collection": elements["collection"],
        "counts": counts,
        "artifact": str(artifact),
        "collection_node_id": elements["collection_node_id"],
    }


def apply_all_overlays(db_path: Path, intel_root: Path) -> Dict[str, int]:
    """Re-apply every persisted collection graph overlay (used after reindex).

    Reads each ``<intel_root>/*/graph.json`` and re-upserts it. Returns aggregate
    counts. Callers should guard this so a failure can never break reindex.
    """
    intel_root = Path(intel_root)
    totals = {"nodes": 0, "edges": 0, "mentions": 0, "collections": 0}
    if not intel_root.exists():
        return totals
    for graph_file in sorted(intel_root.glob("*/graph.json")):
        try:
            elements = json.loads(graph_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not elements.get("nodes"):
            continue
        counts = apply_graph(db_path, elements)
        totals["nodes"] += counts["nodes"]
        totals["edges"] += counts["edges"]
        totals["mentions"] += counts["mentions"]
        totals["collections"] += 1
    return totals
