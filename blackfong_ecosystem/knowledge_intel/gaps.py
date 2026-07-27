"""
Gap detection - deterministic, domain-agnostic.

Given a collection, ``detect_gaps`` reports what is missing or thin using only
on-disk facts (registry + chunk files + the same inventory/coverage/timeline/graph
derivations the rest of Phase 1 uses). No model is involved in detection: each gap
is a real, explainable rule firing on real data.

An optional ``explain_gaps`` turns the deterministic gap list into a short
natural-language briefing via the hybrid compute layer, always falling back to a
structured rule-name summary when no local model is available. Detection itself
never depends on a model.

Gap rules:
- ``missing_sequence``: numeric sequence has holes (from coverage ``missing_numbers``).
- ``low_chunk_count``: an embedded source has suspiciously few chunks.
- ``reference_only_not_embedded``: a known source is reference-only (never embedded).
- ``unembedded_reference_only``: the whole collection has sources but none embedded.
- ``thin_source_coverage``: embedded ratio across the collection is low.
- ``no_timeline``: no timeline could be built (no embedded sources).
- ``no_graph_nodes``: no graph nodes were produced for the collection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ingestion.source_registry import SourceRegistry, slugify_collection
from . import collections as collections_mod
from . import compute as compute_mod
from . import coverage as coverage_mod
from . import graph_producer
from . import sources as src
from . import timeline as timeline_mod

# Tunable thresholds (kept conservative; domain-agnostic).
LOW_CHUNK_COUNT_THRESHOLD = 3
THIN_COVERAGE_RATIO = 0.5

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _gap(
    gap_type: str,
    severity: str,
    *,
    reason: str,
    affected_sources: Optional[List[str]] = None,
    affected_chunks: Optional[List[str]] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "gap_type": gap_type,
        "severity": severity,
        "affected_sources": list(affected_sources or []),
        "affected_chunks": list(affected_chunks or []),
        "evidence": dict(evidence or {}),
        "reason": reason,
    }


def detect_gaps(
    registry: SourceRegistry,
    collection: str,
    *,
    chunks_dir: Path,
    collection_registry: Any = None,
    db_path: Optional[Path] = None,  # noqa: ARG001 - accepted for API symmetry; detection is on-disk
) -> List[Dict[str, Any]]:
    """Return the deterministic list of gaps for a collection (possibly empty)."""
    chunks_dir = Path(chunks_dir)
    records = src.collection_records(registry, collection)
    if not records:
        return []

    inventory = collections_mod.build_inventory(
        registry, collection, chunks_dir=chunks_dir, collection_registry=collection_registry
    )
    coverage = coverage_mod.build_coverage(
        registry, collection, chunks_dir=chunks_dir, collection_registry=collection_registry
    )
    timeline = timeline_mod.build_timeline(registry, collection, chunks_dir=chunks_dir)
    graph_elements = graph_producer.build_graph_elements(registry, collection, chunks_dir=chunks_dir)

    gaps: List[Dict[str, Any]] = []
    embedded_rows = [row for row in inventory.get("source_detail", []) if row.get("embedded")]
    embedded_count = inventory.get("embedded", 0)
    total_sources = inventory.get("sources", 0)

    # --- missing_sequence ---
    sequence = coverage.get("sequence", {}) if isinstance(coverage, dict) else {}
    missing_numbers = sequence.get("missing_numbers") or []
    if sequence.get("available") and missing_numbers:
        gaps.append(
            _gap(
                "missing_sequence",
                "medium",
                reason=(
                    f"numeric sequence has {len(missing_numbers)} missing entr"
                    f"{'y' if len(missing_numbers) == 1 else 'ies'} "
                    f"between {sequence.get('min')} and {sequence.get('max')}"
                ),
                evidence={
                    "unit": sequence.get("unit"),
                    "missing_numbers": missing_numbers,
                    "min": sequence.get("min"),
                    "max": sequence.get("max"),
                },
            )
        )

    # --- low_chunk_count (per embedded source) ---
    thin_sources = [
        row for row in embedded_rows if int(row.get("chunk_count", 0)) < LOW_CHUNK_COUNT_THRESHOLD
    ]
    if thin_sources:
        gaps.append(
            _gap(
                "low_chunk_count",
                "low",
                reason=(
                    f"{len(thin_sources)} embedded source(s) have fewer than "
                    f"{LOW_CHUNK_COUNT_THRESHOLD} chunks, which may indicate "
                    "truncated or under-extracted content"
                ),
                affected_sources=[str(row.get("source_id")) for row in thin_sources],
                evidence={
                    "threshold": LOW_CHUNK_COUNT_THRESHOLD,
                    "sources": [
                        {"source_id": row.get("source_id"), "chunk_count": int(row.get("chunk_count", 0))}
                        for row in thin_sources
                    ],
                },
            )
        )

    # --- reference_only_not_embedded (per source) ---
    reference_only = [
        row
        for row in inventory.get("source_detail", [])
        if row.get("processing_status") == "reference_only" and not row.get("embedded")
    ]
    if reference_only:
        gaps.append(
            _gap(
                "reference_only_not_embedded",
                "info",
                reason=(
                    f"{len(reference_only)} source(s) are registered as reference-only "
                    "and have not been embedded; their content is not searchable"
                ),
                affected_sources=[str(row.get("source_id")) for row in reference_only],
                evidence={"source_types": sorted({str(row.get("source_type")) for row in reference_only})},
            )
        )

    # --- unembedded_reference_only (collection-level) ---
    if total_sources and not embedded_count:
        gaps.append(
            _gap(
                "unembedded_reference_only",
                "high",
                reason=(
                    f"collection has {total_sources} registered source(s) but none are "
                    "embedded; inventory exists but the collection is not searchable and "
                    "has no timeline or graph"
                ),
                affected_sources=[str(row.get("source_id")) for row in inventory.get("source_detail", [])],
                evidence={"total_sources": total_sources, "embedded": 0},
            )
        )

    # --- thin_source_coverage (collection-level ratio) ---
    embedded_ratio = float(coverage.get("embedded_ratio", 0.0)) if isinstance(coverage, dict) else 0.0
    if embedded_count and 0.0 < embedded_ratio < THIN_COVERAGE_RATIO:
        gaps.append(
            _gap(
                "thin_source_coverage",
                "medium",
                reason=(
                    f"only {embedded_count} of {total_sources} sources are embedded "
                    f"(ratio {embedded_ratio:.2f}); large portions of the collection are "
                    "not yet searchable"
                ),
                evidence={
                    "embedded": embedded_count,
                    "total_sources": total_sources,
                    "embedded_ratio": embedded_ratio,
                    "threshold": THIN_COVERAGE_RATIO,
                },
            )
        )

    # --- no_timeline ---
    if not timeline.get("found"):
        gaps.append(
            _gap(
                "no_timeline",
                "low",
                reason=f"no timeline could be built: {timeline.get('reason', 'unknown')}",
                evidence={"timeline_reason": timeline.get("reason", "")},
            )
        )

    # --- no_graph_nodes ---
    if not graph_elements.get("nodes"):
        gaps.append(
            _gap(
                "no_graph_nodes",
                "medium",
                reason="no Memory Chamber graph nodes were produced (no embedded sources to graph)",
                evidence={"source_count": graph_elements.get("source_count", 0)},
            )
        )

    gaps.sort(key=lambda g: (_SEVERITY_ORDER.get(g["severity"], 9), g["gap_type"]))
    return gaps


def build_gaps(
    registry: SourceRegistry,
    collection: str,
    *,
    chunks_dir: Path,
    collection_registry: Any = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a full, persistable gaps record for a collection.

    Returns ``found=False`` with a reason when the collection has no registered
    sources, so callers never write a fabricated gaps artifact.
    """
    slug = slugify_collection(collection)
    records = src.collection_records(registry, collection)
    if not records:
        return {
            "found": False,
            "collection": slug,
            "reason": f"no registered sources for collection '{slug}'",
            "gaps": [],
        }

    gaps = detect_gaps(
        registry,
        collection,
        chunks_dir=chunks_dir,
        collection_registry=collection_registry,
        db_path=db_path,
    )
    summary_by_type: Dict[str, int] = {}
    summary_by_severity: Dict[str, int] = {}
    for gap in gaps:
        summary_by_type[gap["gap_type"]] = summary_by_type.get(gap["gap_type"], 0) + 1
        summary_by_severity[gap["severity"]] = summary_by_severity.get(gap["severity"], 0) + 1

    return {
        "found": True,
        "collection": slug,
        "domain": src.resolve_domain(collection, records, collection_registry),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "gap_count": len(gaps),
        "summary_by_type": summary_by_type,
        "summary_by_severity": summary_by_severity,
        "gaps": gaps,
    }


def _deterministic_explanation(gaps: List[Dict[str, Any]], *, collection: str) -> str:
    if not gaps:
        return f"No gaps detected for collection '{collection}'."
    lines = [f"{len(gaps)} gap(s) detected for collection '{collection}':"]
    for gap in gaps:
        affected = gap.get("affected_sources") or []
        suffix = f" (sources: {', '.join(affected[:5])}{'...' if len(affected) > 5 else ''})" if affected else ""
        lines.append(f"- [{gap['severity']}] {gap['gap_type']}: {gap['reason']}{suffix}")
    return "\n".join(lines)


def explain_gaps(
    gaps: List[Dict[str, Any]],
    *,
    collection: str,
    domain: str = "",
    mode: Any = None,
) -> Dict[str, Any]:
    """Produce a short natural-language gap briefing, grounded in the gap list.

    Always returns deterministic rule-name text when no local model is available
    (``used_llm=False``). The model may only rephrase the supplied gaps.
    """
    fallback_text = _deterministic_explanation(gaps, collection=collection)
    grounding = {
        "collection": collection,
        "domain": domain or "(undeclared)",
        "gaps": [
            {"gap_type": g["gap_type"], "severity": g["severity"], "reason": g["reason"]}
            for g in gaps
        ],
    }
    result = compute_mod.synthesize(
        system=(
            "You explain knowledge-base coverage gaps to an operator. Summarize the "
            "detected gaps and their practical impact in a few sentences. Reference "
            "only the gaps provided."
        ),
        grounding=grounding,
        question=f"Summarize the coverage gaps for the '{collection}' collection and what to ingest or fix next.",
        mode=mode,
        fallback=lambda: fallback_text,
        agent="daise",
        response_mode="standard",
    )
    return result.to_dict()


__all__ = ["detect_gaps", "build_gaps", "explain_gaps", "LOW_CHUNK_COUNT_THRESHOLD", "THIN_COVERAGE_RATIO"]
