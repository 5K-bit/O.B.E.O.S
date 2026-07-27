"""
Visual source registration (JP2 / scanned page images).

Today this only *registers* ``.jp2`` files as ``visual_jp2`` with a
``pending_processing`` status and emits ``knowledge_source_added``. No pixels are
processed yet. The documented TODO hooks below define the intended future path so
the schema and events are stable now and the heavy work can be filled in later.

Future processing hooks (NOT implemented):
  1. OCR text extraction            -> pytesseract / docTR over each page image.
  2. Panel detection                -> segment pages into panels (CV / layout model).
  3. CLIP visual embeddings         -> embed panels/pages into a visual collection.
  4. Character recognition          -> identify recurring characters across panels.
  5. Visual scene memory            -> link panels to story beats / issue ranges.
Each hook would flip the record's processing_status and emit the matching
``knowledge_*`` lifecycle events (e.g. ``knowledge_embedded`` once CLIP vectors
are written).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..events import Event
from ..storage import EventStore
from .source_registry import (
    ROLE_REFERENCE,
    SOURCE_TYPE_VISUAL,
    STATUS_PENDING_PROCESSING,
    SourceRecord,
    SourceRegistry,
)


def _emit(event_store: Optional[EventStore], event_type: str, payload: dict) -> None:
    if event_store is None:
        return
    ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    event = Event.new(
        source="daise",
        type=event_type,
        payload=payload,
        timestamp_ms=ts_ms,
        tags=["knowledge", "visual"],
    )
    event_store.add_event(event)


def register_visual_source(
    path: Path,
    *,
    series: str,
    issue_range: str,
    registry: SourceRegistry,
    event_store: Optional[EventStore] = None,
    collection: str = "",
    domain: str = "",
) -> SourceRecord:
    """Register a single ``.jp2`` image as ``visual_jp2`` / ``pending_processing``."""
    record, changed = registry.register(
        source_type=SOURCE_TYPE_VISUAL,
        path=Path(path),
        series=series,
        issue_range=issue_range,
        role=ROLE_REFERENCE,
        processing_status=STATUS_PENDING_PROCESSING,
        collection=collection,
        domain=domain,
    )
    record.embedded = False
    record.processing_status = STATUS_PENDING_PROCESSING
    if changed:
        _emit(
            event_store,
            "knowledge_source_added",
            {
                "source_id": record.source_id,
                "source_type": record.source_type,
                "series": series,
                "issue_range": issue_range,
                "path": record.path,
                "processing_status": record.processing_status,
            },
        )
    return record


def register_visual_directory(
    directory: Path,
    *,
    series: str,
    issue_range: str,
    registry: SourceRegistry,
    event_store: Optional[EventStore] = None,
) -> List[SourceRecord]:
    """Register every ``.jp2`` under ``directory`` (non-recursive)."""
    directory = Path(directory)
    records: List[SourceRecord] = []
    for path in sorted(directory.glob("*.jp2")):
        if path.is_file():
            records.append(
                register_visual_source(
                    path,
                    series=series,
                    issue_range=issue_range,
                    registry=registry,
                    event_store=event_store,
                )
            )
    return records
