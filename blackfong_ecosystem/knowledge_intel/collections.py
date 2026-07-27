"""
Collections + inventory.

Domain-agnostic: a "collection" is just a slug grouping registry records (derived
from the source's ``collection``/``series``). Inventory is computed from the
SourceRegistry plus on-disk chunk files - real counts, no fabrication.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..ingestion.source_registry import SourceRegistry, slugify_collection
from . import sources as src


def list_collections(
    registry: SourceRegistry,
    *,
    chunks_dir: Path,
    collection_registry: Any = None,
) -> List[Dict[str, Any]]:
    """Summarize every collection present in the registry."""
    out: List[Dict[str, Any]] = []
    for slug in src.list_collection_slugs(registry):
        records = src.collection_records(registry, slug)
        embedded = [r for r in records if r.embedded]
        out.append(
            {
                "collection": slug,
                "domain": src.resolve_domain(slug, records, collection_registry),
                "sources": len(records),
                "embedded": len(embedded),
                "chunk_count": sum(int(r.chunk_count) for r in embedded),
                "domains": sorted({r.domain for r in records if r.domain}),
            }
        )
    return out


def build_inventory(
    registry: SourceRegistry,
    collection: str,
    *,
    chunks_dir: Path,
    collection_registry: Any = None,
) -> Dict[str, Any]:
    """Build a full inventory for one collection.

    Returns a record with counts by status/role/source_type, issue ranges, and
    per-source detail. ``found`` is False (with a reason) when the collection has
    no registered sources, so callers never emit fake inventory.
    """
    slug = slugify_collection(collection)
    records = src.collection_records(registry, collection)

    if not records:
        return {
            "found": False,
            "collection": slug,
            "reason": f"no registered sources for collection '{slug}'",
            "sources": 0,
        }

    by_status: Dict[str, int] = {}
    by_role: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    issue_ranges: List[str] = []
    domains: set[str] = set()
    embedded_count = 0
    total_chunks = 0
    source_rows: List[Dict[str, Any]] = []

    for record in sorted(records, key=lambda r: src.issue_range_sort_key(r.issue_range)):
        by_status[record.processing_status] = by_status.get(record.processing_status, 0) + 1
        by_role[record.role] = by_role.get(record.role, 0) + 1
        by_type[record.source_type] = by_type.get(record.source_type, 0) + 1
        if record.issue_range not in issue_ranges:
            issue_ranges.append(record.issue_range)
        if record.domain:
            domains.add(record.domain)
        if record.embedded:
            embedded_count += 1
            total_chunks += int(record.chunk_count)
        source_rows.append(
            {
                "source_id": record.source_id,
                "source_type": record.source_type,
                "issue_range": record.issue_range,
                "role": record.role,
                "processing_status": record.processing_status,
                "embedded": record.embedded,
                "chunk_count": int(record.chunk_count),
                "source_file": record.path,
            }
        )

    return {
        "found": True,
        "collection": slug,
        "domain": src.resolve_domain(collection, records, collection_registry),
        "domains": sorted(domains),
        "sources": len(records),
        "embedded": embedded_count,
        "reference_only": by_status.get("reference_only", 0),
        "pending_processing": by_status.get("pending_processing", 0),
        "failed": by_status.get("failed", 0),
        "total_chunks": total_chunks,
        "issue_ranges": sorted(issue_ranges, key=src.issue_range_sort_key),
        "by_status": by_status,
        "by_role": by_role,
        "by_source_type": by_type,
        "source_detail": source_rows,
    }
