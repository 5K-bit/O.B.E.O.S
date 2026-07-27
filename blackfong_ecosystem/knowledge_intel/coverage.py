"""
Coverage reports.

Generic, domain-agnostic coverage for a collection:
- embedded ratio (embedded sources / total sources)
- numeric sequence continuity inferred from issue_range/chapter/section integers
  (works for "1-8" comic ranges, "ch 1-3" chapters, etc.); skipped gracefully when
  ranges are non-numeric
- presence flags for timeline/graph (passed in by the orchestrator)

No domain-specific logic and no fabricated numbers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..ingestion.source_registry import SourceRegistry, slugify_collection
from . import sources as src


def _parse_range(issue_range: str) -> Optional[Tuple[int, int]]:
    nums = re.findall(r"\d+", issue_range or "")
    if not nums:
        return None
    if len(nums) == 1:
        v = int(nums[0])
        return (v, v)
    return (int(nums[0]), int(nums[1]))


def _sequence_continuity(issue_ranges: List[str]) -> Dict[str, Any]:
    spans: List[Tuple[int, int]] = []
    for ir in issue_ranges:
        parsed = _parse_range(ir)
        if parsed is not None:
            spans.append(parsed)
    if not spans:
        return {"available": False, "reason": "no numeric ranges to analyze"}

    covered: set[int] = set()
    for lo, hi in spans:
        if hi < lo:
            lo, hi = hi, lo
        covered.update(range(lo, hi + 1))
    lo_all = min(covered)
    hi_all = max(covered)
    full = set(range(lo_all, hi_all + 1))
    missing = sorted(full - covered)
    span = hi_all - lo_all + 1
    return {
        "available": True,
        "unit": "issue_range",
        "min": lo_all,
        "max": hi_all,
        "span": span,
        "covered_count": len(covered),
        "coverage_ratio": round(len(covered) / span, 4) if span else 0.0,
        "continuous": not missing,
        "missing_numbers": missing,
    }


def build_coverage(
    registry: SourceRegistry,
    collection: str,
    *,
    chunks_dir: Path,
    timeline_present: bool = False,
    graph_present: bool = False,
    collection_registry: Any = None,
) -> Dict[str, Any]:
    """Build a coverage report for one collection."""
    slug = slugify_collection(collection)
    records = src.collection_records(registry, collection)
    if not records:
        return {
            "found": False,
            "collection": slug,
            "reason": f"no registered sources for collection '{slug}'",
        }

    embedded = [r for r in records if r.embedded]
    issue_ranges = sorted(
        {r.issue_range for r in embedded}, key=src.issue_range_sort_key
    )

    return {
        "found": True,
        "collection": slug,
        "domain": src.resolve_domain(collection, records, collection_registry),
        "total_sources": len(records),
        "embedded_sources": len(embedded),
        "embedded_ratio": round(len(embedded) / len(records), 4) if records else 0.0,
        "total_chunks": sum(int(r.chunk_count) for r in embedded),
        "sequence": _sequence_continuity(issue_ranges),
        "timeline_present": bool(timeline_present),
        "graph_present": bool(graph_present),
        "reference_only_sources": [
            r.source_id for r in records if r.processing_status == "reference_only"
        ],
    }
