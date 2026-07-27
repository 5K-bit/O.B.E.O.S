"""
Shared, offline data access for the knowledge intelligence layer.

Everything here reads from the SourceRegistry and the chunk JSONL files that the
ingestion pipeline already wrote (``data/knowledge/chunks/{source_id}.jsonl``).
This keeps inventory/coverage/timeline/provenance fully deterministic and
offline - no embedding model load required - while still referencing only real,
on-disk records.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ingestion.source_registry import (
    SourceRecord,
    SourceRegistry,
    slugify_collection,
)


def issue_range_sort_key(issue_range: str) -> tuple:
    """Sort issue ranges by their leading integer, then lexically."""
    nums = re.findall(r"\d+", issue_range or "")
    lead = int(nums[0]) if nums else 1_000_000
    return (lead, issue_range or "")


@dataclass
class ChunkRef:
    chunk_id: str
    source_id: str
    text: str
    metadata: Dict[str, Any]
    source_label: str = ""


def read_source_chunks(chunks_dir: Path, source_id: str) -> List[ChunkRef]:
    """Read all chunks for a source from its JSONL file (empty list if missing)."""
    path = Path(chunks_dir) / f"{source_id}.jsonl"
    if not path.exists():
        return []
    refs: List[ChunkRef] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        meta = obj.get("metadata", {}) or {}
        refs.append(
            ChunkRef(
                chunk_id=str(obj.get("chunk_id", "")),
                source_id=str(meta.get("source_id", source_id)),
                text=str(obj.get("text", "")),
                metadata=meta,
                source_label=str(obj.get("source", "")),
            )
        )
    return refs


def find_chunk(chunks_dir: Path, chunk_id: str) -> Optional[ChunkRef]:
    """Locate a chunk by id by scanning the JSONL files (offline provenance)."""
    chunks_dir = Path(chunks_dir)
    if not chunks_dir.exists():
        return None
    for jsonl in sorted(chunks_dir.glob("*.jsonl")):
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or chunk_id not in line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(obj.get("chunk_id", "")) != chunk_id:
                continue
            meta = obj.get("metadata", {}) or {}
            return ChunkRef(
                chunk_id=chunk_id,
                source_id=str(meta.get("source_id", jsonl.stem)),
                text=str(obj.get("text", "")),
                metadata=meta,
                source_label=str(obj.get("source", "")),
            )
    return None


def collection_records(registry: SourceRegistry, collection: str) -> List[SourceRecord]:
    """All registry records belonging to a collection (matched by slug)."""
    target = slugify_collection(collection)
    return [r for r in registry.all() if r.resolved_collection() == target]


def embedded_text_sources(registry: SourceRegistry, collection: str) -> List[SourceRecord]:
    """Embedded (canonical) text sources for a collection, in issue-range order."""
    records = [r for r in collection_records(registry, collection) if r.embedded]
    return sorted(records, key=lambda r: issue_range_sort_key(r.issue_range))


def list_collection_slugs(registry: SourceRegistry) -> List[str]:
    """Distinct collection slugs present in the registry."""
    seen: List[str] = []
    for r in registry.all():
        slug = r.resolved_collection()
        if slug and slug not in seen:
            seen.append(slug)
    return sorted(seen)


def resolve_domain(
    collection: str,
    records: List[SourceRecord],
    collection_registry: Any = None,
) -> str:
    """Resolve a collection's domain.

    Authority order: the collection taxonomy store (if provided and set), then the
    first non-empty ``record.domain`` among the records. Returns ``""`` when unknown
    so callers never fabricate a domain.
    """
    if collection_registry is not None:
        declared = collection_registry.domain_for(collection)
        if declared:
            return declared
    for record in records:
        if record.domain:
            return record.domain
    return ""
