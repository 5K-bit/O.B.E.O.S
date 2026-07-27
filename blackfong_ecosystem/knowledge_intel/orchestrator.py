"""
Phase 1 orchestration.

``run_phase1(collection)`` produces real, provenance-backed records for a
collection: inventory, coverage, source-order timeline, a provenance map, and
Memory Chamber graph updates. Emits the seven Phase 1 lifecycle events.

Guarantees:
- No fabrication. An unknown/empty collection returns ok=False with a reason and
  emits ``knowledge_phase1_failed``.
- Per-stage failures are caught, logged as errors, and emit ``knowledge_phase1_failed``
  for that stage without aborting the rest; raw chunks/sources are never mutated.
- Idempotent/rerunnable: artifacts are overwritten deterministically and graph
  upserts key on stable ids.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..events import Event
from ..ingestion.source_registry import SourceRegistry, slugify_collection
from ..storage import EventStore
from . import collections as collections_mod
from . import coverage as coverage_mod
from . import gaps as gaps_mod
from . import graph_producer
from . import sources as src
from . import timeline as timeline_mod
from .provenance import KnowledgeProvenanceService


@dataclass
class Phase1Result:
    ok: bool
    collection: str
    reason: str = ""
    warnings: List[str] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    artifacts: Dict[str, str] = field(default_factory=dict)
    counts: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "collection": self.collection,
            "reason": self.reason,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "artifacts": dict(self.artifacts),
            "counts": dict(self.counts),
        }


def _emit(event_store: Optional[EventStore], event_type: str, payload: Dict[str, Any]) -> None:
    if event_store is None:
        return
    ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    event = Event.new(
        source="daise",
        type=event_type,
        payload=payload,
        timestamp_ms=ts_ms,
        tags=["knowledge", "phase1"],
    )
    event_store.add_event(event)


def _write_artifact(intel_root: Path, collection: str, name: str, data: Dict[str, Any]) -> Path:
    out_dir = Path(intel_root) / slugify_collection(collection)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_phase1(
    collection: str,
    *,
    registry: SourceRegistry,
    event_store: Optional[EventStore],
    chunks_dir: Path,
    intel_root: Path,
    db_path: Optional[Path] = None,
    vector_store: Any = None,
    collection_registry: Any = None,
) -> Phase1Result:
    """Run the Phase 1 enrichment pipeline for one collection."""
    slug = slugify_collection(collection)
    result = Phase1Result(ok=True, collection=slug)
    chunks_dir = Path(chunks_dir)
    intel_root = Path(intel_root)
    if db_path is None and event_store is not None:
        db_path = event_store.db_path

    _emit(event_store, "knowledge_phase1_started", {"collection": slug})

    # Hard guard: unknown/empty collection -> no fake outputs.
    records = src.collection_records(registry, collection)
    if not records:
        reason = f"no registered sources for collection '{slug}'"
        result.ok = False
        result.reason = reason
        result.warnings.append(reason)
        _emit(event_store, "knowledge_phase1_failed", {"collection": slug, "reason": reason})
        return result

    embedded = [r for r in records if r.embedded]
    has_embedded = bool(embedded)
    if not has_embedded:
        result.warnings.append(
            "collection has registered sources but none are embedded; "
            "timeline and graph will be empty"
        )

    # --- inventory ---
    try:
        inventory = collections_mod.build_inventory(
            registry, collection, chunks_dir=chunks_dir, collection_registry=collection_registry
        )
        path = _write_artifact(intel_root, collection, "inventory.json", inventory)
        result.artifacts["inventory"] = str(path)
        result.counts["sources"] = inventory.get("sources", 0)
        result.counts["embedded"] = inventory.get("embedded", 0)
        result.counts["total_chunks"] = inventory.get("total_chunks", 0)
        result.counts["domain"] = inventory.get("domain", "")
        _emit(
            event_store,
            "knowledge_inventory_created",
            {
                "collection": slug,
                "domain": inventory.get("domain", ""),
                "sources": inventory.get("sources", 0),
                "embedded": inventory.get("embedded", 0),
                "total_chunks": inventory.get("total_chunks", 0),
                "artifact": str(path),
            },
        )
    except Exception as exc:  # noqa: BLE001 - record + continue
        _stage_failed(result, event_store, slug, "inventory", exc)

    # --- coverage ---
    try:
        coverage = coverage_mod.build_coverage(
            registry,
            collection,
            chunks_dir=chunks_dir,
            timeline_present=has_embedded,
            graph_present=has_embedded,
            collection_registry=collection_registry,
        )
        path = _write_artifact(intel_root, collection, "coverage.json", coverage)
        result.artifacts["coverage"] = str(path)
        _emit(
            event_store,
            "knowledge_coverage_created",
            {
                "collection": slug,
                "embedded_sources": coverage.get("embedded_sources", 0),
                "embedded_ratio": coverage.get("embedded_ratio", 0.0),
                "sequence_continuous": coverage.get("sequence", {}).get("continuous"),
                "artifact": str(path),
            },
        )
    except Exception as exc:  # noqa: BLE001
        _stage_failed(result, event_store, slug, "coverage", exc)

    # --- provenance map (real artifact; no dedicated event required) ---
    try:
        prov = KnowledgeProvenanceService(
            registry=registry,
            chunks_dir=chunks_dir,
            vector_store=vector_store,
            collection_registry=collection_registry,
        )
        prov_map = prov.build_map(collection)
        path = _write_artifact(intel_root, collection, "provenance.json", prov_map)
        result.artifacts["provenance"] = str(path)
        result.counts["provenance_chunks"] = prov_map.get("chunk_count", 0)
    except Exception as exc:  # noqa: BLE001
        _stage_failed(result, event_store, slug, "provenance", exc)

    # --- gaps (deterministic) ---
    try:
        gaps_record = gaps_mod.build_gaps(
            registry,
            collection,
            chunks_dir=chunks_dir,
            collection_registry=collection_registry,
            db_path=db_path,
        )
        path = _write_artifact(intel_root, collection, "gaps.json", gaps_record)
        result.artifacts["gaps"] = str(path)
        result.counts["gap_count"] = gaps_record.get("gap_count", 0)
        _emit(
            event_store,
            "knowledge_gaps_detected",
            {
                "collection": slug,
                "gap_count": gaps_record.get("gap_count", 0),
                "summary_by_type": gaps_record.get("summary_by_type", {}),
                "summary_by_severity": gaps_record.get("summary_by_severity", {}),
                "artifact": str(path),
            },
        )
    except Exception as exc:  # noqa: BLE001
        _stage_failed(result, event_store, slug, "gaps", exc)

    # --- timeline (source order) ---
    try:
        timeline = timeline_mod.build_timeline(registry, collection, chunks_dir=chunks_dir)
        path = _write_artifact(intel_root, collection, "timeline.json", timeline)
        result.artifacts["timeline"] = str(path)
        result.counts["timeline_events"] = timeline.get("event_count", 0)
        if not timeline.get("found"):
            result.warnings.append(f"timeline: {timeline.get('reason', 'empty')}")
        _emit(
            event_store,
            "knowledge_timeline_created",
            {
                "collection": slug,
                "event_count": timeline.get("event_count", 0),
                "ordering": timeline.get("ordering", "source_order"),
                "found": timeline.get("found", False),
                "artifact": str(path),
            },
        )
    except Exception as exc:  # noqa: BLE001
        _stage_failed(result, event_store, slug, "timeline", exc)

    # --- Memory Chamber graph ---
    try:
        if db_path is None:
            raise RuntimeError("no db_path/event_store available for graph update")
        graph = graph_producer.produce_collection_graph(
            registry,
            collection,
            chunks_dir=chunks_dir,
            db_path=db_path,
            intel_root=intel_root,
        )
        result.artifacts["graph"] = graph["artifact"]
        result.counts["graph"] = graph["counts"]
        _emit(
            event_store,
            "knowledge_memory_chamber_graph_updated",
            {
                "collection": slug,
                "nodes": graph["counts"]["nodes"],
                "edges": graph["counts"]["edges"],
                "mentions": graph["counts"]["mentions"],
                "collection_node_id": graph["collection_node_id"],
                "artifact": graph["artifact"],
            },
        )
    except Exception as exc:  # noqa: BLE001
        _stage_failed(result, event_store, slug, "graph", exc)

    result.ok = not result.errors
    _emit(
        event_store,
        "knowledge_phase1_completed",
        {
            "collection": slug,
            "ok": result.ok,
            "warnings": result.warnings,
            "errors": result.errors,
            "counts": result.counts,
            "artifacts": result.artifacts,
        },
    )
    return result


def _stage_failed(
    result: Phase1Result,
    event_store: Optional[EventStore],
    collection: str,
    stage: str,
    exc: Exception,
) -> None:
    error = {"stage": stage, "error": f"{type(exc).__name__}: {exc}"}
    result.errors.append(error)
    result.warnings.append(f"{stage} failed: {error['error']}")
    _emit(
        event_store,
        "knowledge_phase1_failed",
        {"collection": collection, "stage": stage, "reason": error["error"]},
    )
