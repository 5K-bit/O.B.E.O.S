"""
Timeline extraction - source order (no LLM).

For collections without real dates (comics, books, docs), narrative/source order is
the timeline. Each embedded source becomes one ordered event. If real dates ever
exist in chunk metadata they are surfaced via ``approximate_time``; otherwise it is
null and ``ordering`` is "source_order". Fully deterministic; no fabrication.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..ingestion.source_registry import SourceRegistry, slugify_collection
from . import sources as src


def build_timeline(registry: SourceRegistry, collection: str, *, chunks_dir: Path) -> Dict[str, Any]:
    """Build a source-order timeline for one collection."""
    slug = slugify_collection(collection)
    records = src.collection_records(registry, collection)
    if not records:
        return {
            "found": False,
            "collection": slug,
            "reason": f"no registered sources for collection '{slug}'",
            "events": [],
        }

    embedded = src.embedded_text_sources(registry, collection)
    if not embedded:
        return {
            "found": False,
            "collection": slug,
            "reason": "no embedded sources to build a timeline from",
            "events": [],
        }

    events: List[Dict[str, Any]] = []
    for seq, record in enumerate(embedded, start=1):
        refs = src.read_source_chunks(chunks_dir, record.source_id)
        first_chunk_id = refs[0].chunk_id if refs else ""
        events.append(
            {
                "timeline_event_id": f"{slug}_tl_{seq:04d}",
                "collection": slug,
                "title": f"{record.series} {record.issue_range}".strip(),
                "description": (
                    f"{record.source_type} source covering {record.issue_range} "
                    f"with {int(record.chunk_count)} chunks"
                ),
                "event_type": "source",
                "entities": [],  # entity extraction is a later phase (no LLM here)
                "approximate_time": None,
                "ordering": "source_order",
                "sequence_index": seq,
                "source_id": record.source_id,
                "chunk_id": first_chunk_id,
                "confidence": 1.0,
            }
        )

    return {
        "found": True,
        "collection": slug,
        "ordering": "source_order",
        "event_count": len(events),
        "events": events,
    }
