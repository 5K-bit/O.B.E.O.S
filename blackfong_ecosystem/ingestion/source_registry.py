"""
Source registry for the knowledge ingestion pipeline.

Tracks every source file ever seen (regardless of whether it was embedded) in a
single JSON file. Records carry an sha256 checksum so re-running ingestion is
idempotent: unchanged files are skipped, changed files are re-processed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# Source type vocabulary.
SOURCE_TYPE_DJVU = "djvu_text"
SOURCE_TYPE_EPUB = "epub"
SOURCE_TYPE_PDF = "pdf_text"
SOURCE_TYPE_VISUAL = "visual_jp2"
SOURCE_TYPE_MARKDOWN = "markdown"

SOURCE_TYPES = {
    SOURCE_TYPE_DJVU,
    SOURCE_TYPE_EPUB,
    SOURCE_TYPE_PDF,
    SOURCE_TYPE_VISUAL,
    SOURCE_TYPE_MARKDOWN,
}

# Canonical-selection priority (lower index == higher priority).
TEXT_SOURCE_PRIORITY = [SOURCE_TYPE_DJVU, SOURCE_TYPE_EPUB, SOURCE_TYPE_PDF]

# Roles a source can take inside an issue-range group.
ROLE_CANONICAL = "canonical"
ROLE_FALLBACK = "fallback"
ROLE_REFERENCE = "reference"

# Processing status values.
STATUS_REGISTERED = "registered"
STATUS_EMBEDDED = "embedded"
STATUS_REFERENCE_ONLY = "reference_only"
STATUS_PENDING_PROCESSING = "pending_processing"
STATUS_FAILED = "failed"

COPYRIGHT_PRIVATE = "private_reference_only"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def compute_checksum(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Streaming sha256 of a file's bytes."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def make_source_id(source_type: str, path: Path) -> str:
    """Deterministic, human-readable-ish id stable across runs for the same path/type."""
    payload = f"{source_type}:{path.resolve().as_posix()}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{source_type}_{digest}"


def slugify_collection(value: str) -> str:
    """Normalize a series/collection name into a stable lowercase slug."""
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


@dataclass
class SourceRecord:
    """One registered source file."""

    source_id: str
    source_type: str
    path: str
    series: str
    issue_range: str
    role: str = ROLE_REFERENCE
    embedded: bool = False
    processing_status: str = STATUS_REGISTERED
    copyright_status: str = COPYRIGHT_PRIVATE
    collection: str = ""
    domain: str = ""
    ingested_at: str = field(default_factory=_now_iso)
    checksum: str = ""
    chunk_count: int = 0
    validation: Dict[str, Any] = field(default_factory=dict)

    def resolved_collection(self) -> str:
        """Collection slug for this source, derived from series when unset."""
        return self.collection or slugify_collection(self.series)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SourceRecord":
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class SourceRegistry:
    """JSON-backed registry of all known knowledge sources."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._records: Dict[str, SourceRecord] = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        self._records = {}
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable registry: start fresh but do not destroy on disk.
            self._records = {}
            return
        items = raw.get("sources", []) if isinstance(raw, dict) else raw
        for item in items:
            try:
                record = SourceRecord.from_dict(item)
            except TypeError:
                continue
            self._records[record.source_id] = record

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": _now_iso(),
            "sources": [r.to_dict() for r in self._records.values()],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        # Atomic write so a crash mid-write cannot corrupt the registry.
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # ---- queries -------------------------------------------------------

    def get(self, source_id: str) -> Optional[SourceRecord]:
        return self._records.get(source_id)

    def all(self) -> List[SourceRecord]:
        return list(self._records.values())

    def by_issue_range(self, series: str, issue_range: str) -> List[SourceRecord]:
        return [
            r
            for r in self._records.values()
            if r.series == series and r.issue_range == issue_range
        ]

    def issue_ranges(self, series: str) -> List[str]:
        seen: List[str] = []
        for r in self._records.values():
            if r.series == series and r.issue_range not in seen:
                seen.append(r.issue_range)
        return sorted(seen)

    # ---- mutation ------------------------------------------------------

    def register(
        self,
        *,
        source_type: str,
        path: Path,
        series: str,
        issue_range: str,
        role: str = ROLE_REFERENCE,
        processing_status: str = STATUS_REGISTERED,
        collection: str = "",
        domain: str = "",
        compute_hash: bool = True,
    ) -> tuple[SourceRecord, bool]:
        """Register (or refresh) a source.

        Returns ``(record, changed)`` where ``changed`` is True when the source
        is new or its checksum differs from the stored record (idempotency key).
        """
        path = Path(path)
        source_id = make_source_id(source_type, path)
        checksum = compute_checksum(path) if compute_hash and path.exists() else ""
        resolved_collection = collection or slugify_collection(series)

        existing = self._records.get(source_id)
        if existing is not None and existing.checksum == checksum and checksum:
            # Unchanged file: keep existing record but backfill collection/domain
            # so older registries gain the new fields without re-ingesting.
            if not existing.collection:
                existing.collection = resolved_collection
            if domain and not existing.domain:
                existing.domain = domain
            return existing, False

        record = SourceRecord(
            source_id=source_id,
            source_type=source_type,
            path=str(path.resolve()),
            series=series,
            issue_range=issue_range,
            role=role,
            processing_status=processing_status,
            collection=resolved_collection,
            domain=domain,
        )
        record.checksum = checksum
        if existing is not None:
            # File changed: preserve role unless caller overrides, reset derived state.
            record.role = role if role != ROLE_REFERENCE else existing.role
            if not domain and existing.domain:
                record.domain = existing.domain
        self._records[source_id] = record
        return record, True

    def upsert(self, record: SourceRecord) -> None:
        self._records[record.source_id] = record

    def update(self, source_id: str, **changes: Any) -> Optional[SourceRecord]:
        record = self._records.get(source_id)
        if record is None:
            return None
        for key, value in changes.items():
            if hasattr(record, key):
                setattr(record, key, value)
        return record

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for r in self._records.values():
            counts[r.processing_status] = counts.get(r.processing_status, 0) + 1
        counts["total"] = len(self._records)
        return counts
