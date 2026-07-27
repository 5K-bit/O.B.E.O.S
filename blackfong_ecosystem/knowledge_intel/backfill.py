"""
Collection metadata backfill.

Propagates a collection's taxonomy (``domain``) - resolved from the collection
metadata store (authority), falling back to ``record.domain`` - into:

- the SourceRegistry records (durable ``record.domain``), and
- the Chroma chunk metadata (``collection`` + ``domain``) for metadata-filtered search.

Full Chroma metadata is reconstructed from the current Chroma metadata merged with
collection/domain, so nothing is lost. Rerunnable and idempotent: chunks already
carrying the right collection/domain are skipped (unless ``force``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ingestion.source_registry import SourceRegistry, slugify_collection


def backfill_collection_metadata(
    *,
    registry: SourceRegistry,
    vector_store: Any,
    chunks_dir: Path,
    collection_registry: Any = None,
    only_collection: Optional[str] = None,
    batch_size: int = 256,
    force: bool = False,
) -> Dict[str, Any]:
    """Sync collection/domain onto registry records and Chroma chunk metadata.

    Returns counts: ``{"ok", "updated", "skipped", "missing_source",
    "sources_processed", "records_synced"}``.
    """
    chroma = getattr(vector_store, "_collection", None)
    if chroma is None:
        return {
            "ok": False,
            "reason": "vector store has no Chroma collection",
            "updated": 0,
            "skipped": 0,
            "records_synced": 0,
        }

    only_slug = slugify_collection(only_collection) if only_collection else None

    updated = 0
    skipped = 0
    missing_source = 0
    sources_processed = 0
    records_synced = 0

    pending_ids: List[str] = []
    pending_meta: List[Dict[str, Any]] = []

    def flush() -> None:
        nonlocal pending_ids, pending_meta
        if not pending_ids:
            return
        chroma.update(ids=pending_ids, metadatas=pending_meta)
        pending_ids = []
        pending_meta = []

    for record in registry.all():
        if not record.embedded:
            continue
        resolved_collection = record.resolved_collection()
        if only_slug is not None and resolved_collection != only_slug:
            continue
        sources_processed += 1

        # Domain authority: taxonomy store first, then the record's own value.
        domain = ""
        if collection_registry is not None:
            domain = collection_registry.domain_for(resolved_collection)
        if not domain:
            domain = record.domain or ""

        # Sync the durable registry record so registry-based services reflect it.
        if domain and record.domain != domain:
            registry.update(record.source_id, domain=domain)
            records_synced += 1
        if not record.collection and resolved_collection:
            registry.update(record.source_id, collection=resolved_collection)

        jsonl = Path(chunks_dir) / f"{record.source_id}.jsonl"
        if not jsonl.exists():
            missing_source += 1
            continue

        chunk_ids: List[str] = []
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = str(obj.get("chunk_id", ""))
            if cid:
                chunk_ids.append(cid)

        if not chunk_ids:
            continue

        try:
            current = chroma.get(ids=chunk_ids, include=["metadatas"])
        except Exception:
            continue
        got_ids = current.get("ids") or []
        got_meta = current.get("metadatas") or []

        for cid, meta in zip(got_ids, got_meta):
            meta = dict(meta or {})
            if (
                not force
                and meta.get("collection") == resolved_collection
                and meta.get("domain", "") == domain
            ):
                skipped += 1
                continue
            meta["collection"] = resolved_collection
            meta["domain"] = domain
            pending_ids.append(cid)
            pending_meta.append(meta)
            updated += 1
            if len(pending_ids) >= batch_size:
                flush()

    flush()
    if records_synced:
        registry.save()
    return {
        "ok": True,
        "updated": updated,
        "skipped": skipped,
        "missing_source": missing_source,
        "sources_processed": sources_processed,
        "records_synced": records_synced,
    }
