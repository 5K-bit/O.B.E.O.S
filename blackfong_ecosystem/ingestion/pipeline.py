"""
Knowledge ingestion pipeline orchestration.

Flow per series:
  scan -> register ALL sources -> group by issue_range ->
  select + validate canonical (djvu_text > epub > pdf_text) with fallback ->
  clean -> write cleaned text -> chunk (JSONL) -> embed into ``daise_knowledge``
  -> update registry. Visual ``.jp2`` sources are registered as
  ``pending_processing`` and never embedded here.

Lifecycle events are emitted at each stage:
  ``knowledge_source_added`` -> ``knowledge_cleaned`` -> ``knowledge_chunked``
  -> ``knowledge_embedded`` (and ``knowledge_ingestion_failed`` on any error).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..events import Event
from ..storage import EventStore, VectorStore
from . import chunk_builder
from .document_loaders import LoaderError, load_source
from .source_registry import (
    ROLE_CANONICAL,
    ROLE_FALLBACK,
    ROLE_REFERENCE,
    SOURCE_TYPE_DJVU,
    SOURCE_TYPE_EPUB,
    SOURCE_TYPE_MARKDOWN,
    SOURCE_TYPE_PDF,
    SOURCE_TYPE_VISUAL,
    STATUS_EMBEDDED,
    STATUS_FAILED,
    STATUS_REFERENCE_ONLY,
    TEXT_SOURCE_PRIORITY,
    SourceRecord,
    SourceRegistry,
)
from .text_cleaner import clean_markdown, clean_text, validate_text
from .visual_pipeline import register_visual_source

# Map file extension -> source type.
EXTENSION_SOURCE_TYPE = {
    ".txt": SOURCE_TYPE_DJVU,
    ".epub": SOURCE_TYPE_EPUB,
    ".pdf": SOURCE_TYPE_PDF,
    ".jp2": SOURCE_TYPE_VISUAL,
    ".md": SOURCE_TYPE_MARKDOWN,
    ".markdown": SOURCE_TYPE_MARKDOWN,
}

# Vendored / generated directories never ingested: dependency code, caches, VCS.
# Keeps ``--root <repo>`` from vacuuming up site-package readmes and licenses.
_DEFAULT_IGNORE_DIRS = {
    ".venv",
    "venv",
    "env",
    "site-packages",
    "node_modules",
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".egg-info",
}

_ISSUE_RANGE_RE = re.compile(r"(\d+)\s*(?:to|-|_to_|_)\s*(\d+)", re.I)
_SINGLE_ISSUE_RE = re.compile(r"(\d+)")


def parse_issue_range(name: str) -> str:
    """Derive an issue range like ``"1-8"`` from a filename stem."""
    match = _ISSUE_RANGE_RE.search(name)
    if match:
        return f"{int(match.group(1))}-{int(match.group(2))}"
    single = _SINGLE_ISSUE_RE.search(name)
    if single:
        return str(int(single.group(1)))
    return "unknown"


@dataclass
class DiscoveredSource:
    source_type: str
    path: Path
    issue_range: str


def _dir_ignored(name: str, rel_posix: str, extra_ignores: Sequence[str]) -> bool:
    """True if a directory (by name or relative path) should not be descended into."""
    low = name.lower()
    if low in _DEFAULT_IGNORE_DIRS or low.endswith(".egg-info"):
        return True
    rel = rel_posix.lower()
    for pattern in extra_ignores:
        pat = pattern.lower()
        if low == pat or fnmatch(low, pat) or fnmatch(rel, pat):
            return True
    return False


def _file_ignored(rel_posix: str, extra_ignores: Sequence[str]) -> bool:
    """True if a specific file's relative path matches a caller-supplied exclude."""
    rel = rel_posix.lower()
    name = rel.rsplit("/", 1)[-1]
    for pattern in extra_ignores:
        pat = pattern.lower()
        if name == pat or fnmatch(name, pat) or fnmatch(rel, pat):
            return True
    return False


def discover_sources(
    root: Path, *, extra_ignores: Sequence[str] = ()
) -> List[DiscoveredSource]:
    """Recursively find ingestable source files under ``root``.

    Prunes vendored/generated directories (``.venv``, ``site-packages``,
    ``node_modules``, ``__pycache__``, ``.git``, ...) during the walk - they are
    never descended into - plus any caller-supplied ``extra_ignores`` (matched
    fnmatch-style against directory names and relative paths). Compiled ``.pyc``
    files are always skipped. Unreadable entries (e.g. broken symlinks) are skipped.
    """
    root = Path(root)
    found: List[DiscoveredSource] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        # Prune ignored subdirectories in-place so os.walk never descends into them.
        kept = []
        for d in dirnames:
            child_rel = (current / d).relative_to(root).as_posix()
            if not _dir_ignored(d, child_rel, extra_ignores):
                kept.append(d)
        dirnames[:] = kept

        for filename in filenames:
            suffix = Path(filename).suffix.lower()
            if suffix == ".pyc":
                continue
            source_type = EXTENSION_SOURCE_TYPE.get(suffix)
            if source_type is None:
                continue
            path = current / filename
            rel_posix = path.relative_to(root).as_posix()
            if _file_ignored(rel_posix, extra_ignores):
                continue
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            # Markdown documents are not comic issues: they always carry
            # issue_range="unknown" so the sequence-availability signal stays
            # honest, regardless of any digits in the filename.
            if source_type == SOURCE_TYPE_MARKDOWN:
                issue_range = "unknown"
            else:
                issue_range = parse_issue_range(path.stem)
            found.append(DiscoveredSource(source_type, path, issue_range))
    found.sort(key=lambda d: str(d.path))
    return found


@dataclass
class SourceOutcome:
    source_id: str
    source_type: str
    issue_range: str
    role: str
    status: str
    chunk_count: int = 0
    reasons: List[str] = field(default_factory=list)


@dataclass
class IngestionReport:
    series: str
    registered: int = 0
    embedded: int = 0
    reference_only: int = 0
    pending_visual: int = 0
    failed: int = 0
    outcomes: List[SourceOutcome] = field(default_factory=list)


class IngestionPipeline:
    """Coordinates registration, canonical selection, cleaning, chunking, embedding."""

    def __init__(
        self,
        *,
        registry: SourceRegistry,
        vector_store: VectorStore,
        event_store: Optional[EventStore],
        cleaned_dir: Path,
        chunks_dir: Path,
    ) -> None:
        self.registry = registry
        self.vector_store = vector_store
        self.event_store = event_store
        self.cleaned_dir = Path(cleaned_dir)
        self.chunks_dir = Path(chunks_dir)
        self.cleaned_dir.mkdir(parents=True, exist_ok=True)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)

    # ---- events --------------------------------------------------------

    def _emit(self, event_type: str, payload: dict) -> None:
        if self.event_store is None:
            return
        ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        event = Event.new(
            source="daise",
            type=event_type,
            payload=payload,
            timestamp_ms=ts_ms,
            tags=["knowledge", "spawn"],
        )
        self.event_store.add_event(event)

    # ---- public API ----------------------------------------------------

    def ingest_series(
        self,
        root: Path,
        series: str,
        *,
        content_type: str = "lore",
        collection: str = "",
        domain: str = "",
        force: bool = False,
        extra_ignores: Sequence[str] = (),
    ) -> IngestionReport:
        report = IngestionReport(series=series)

        discovered = discover_sources(root, extra_ignores=extra_ignores)

        # 1) Register ALL sources first.
        # Group by a key that is the issue_range for comic-style sources, but the
        # unique source_id for Markdown so each Markdown file is embedded as its
        # own document (issue_range stays "unknown").
        text_by_range: Dict[str, List[SourceRecord]] = {}
        for item in discovered:
            if item.source_type == SOURCE_TYPE_VISUAL:
                record = register_visual_source(
                    item.path,
                    series=series,
                    issue_range=item.issue_range,
                    registry=self.registry,
                    event_store=self.event_store,
                    collection=collection,
                    domain=domain,
                )
                report.registered += 1
                report.pending_visual += 1
                report.outcomes.append(
                    SourceOutcome(
                        source_id=record.source_id,
                        source_type=record.source_type,
                        issue_range=record.issue_range,
                        role=record.role,
                        status=record.processing_status,
                    )
                )
                continue

            record, changed = self.registry.register(
                source_type=item.source_type,
                path=item.path,
                series=series,
                issue_range=item.issue_range,
                role=ROLE_REFERENCE,
                collection=collection,
                domain=domain,
            )
            report.registered += 1
            if changed:
                self._emit(
                    "knowledge_source_added",
                    {
                        "source_id": record.source_id,
                        "source_type": record.source_type,
                        "series": series,
                        "issue_range": record.issue_range,
                        "path": record.path,
                    },
                )
            group_key = (
                record.source_id
                if record.source_type == SOURCE_TYPE_MARKDOWN
                else record.issue_range
            )
            text_by_range.setdefault(group_key, []).append(record)

        # 2) Per group: pick + validate canonical with fallback.
        for _group_key, records in sorted(text_by_range.items()):
            self._process_issue_range(
                series,
                records[0].issue_range,
                records,
                report,
                content_type=content_type,
                force=force,
            )

        self.registry.save()
        return report

    def reembed_source(self, source_id: str, *, content_type: str = "lore") -> SourceOutcome:
        """Force re-clean/chunk/embed of a single already-registered source."""
        record = self.registry.get(source_id)
        if record is None:
            raise KeyError(f"Unknown source_id: {source_id}")
        if record.source_type == SOURCE_TYPE_VISUAL:
            raise ValueError("Visual sources cannot be embedded yet (pending_processing).")
        ok, chunk_count, reasons = self._try_embed_candidate(
            record, ROLE_CANONICAL, content_type=content_type
        )
        self.registry.save()
        return SourceOutcome(
            source_id=record.source_id,
            source_type=record.source_type,
            issue_range=record.issue_range,
            role=ROLE_CANONICAL if ok else record.role,
            status=STATUS_EMBEDDED if ok else STATUS_FAILED,
            chunk_count=chunk_count,
            reasons=reasons,
        )

    # ---- internals -----------------------------------------------------

    def _ordered_candidates(self, records: List[SourceRecord]) -> List[SourceRecord]:
        priority = {t: i for i, t in enumerate(TEXT_SOURCE_PRIORITY)}
        return sorted(
            records,
            key=lambda r: (priority.get(r.source_type, len(TEXT_SOURCE_PRIORITY)), r.path),
        )

    def _process_issue_range(
        self,
        series: str,
        issue_range: str,
        records: List[SourceRecord],
        report: IngestionReport,
        *,
        content_type: str,
        force: bool,
    ) -> None:
        candidates = self._ordered_candidates(records)

        # Idempotency: if an unchanged canonical is already embedded, skip.
        if not force:
            already = next(
                (
                    r
                    for r in candidates
                    if r.role == ROLE_CANONICAL
                    and r.embedded
                    and r.processing_status == STATUS_EMBEDDED
                ),
                None,
            )
            if already is not None:
                for r in candidates:
                    report.outcomes.append(
                        SourceOutcome(
                            source_id=r.source_id,
                            source_type=r.source_type,
                            issue_range=issue_range,
                            role=r.role,
                            status=r.processing_status,
                            chunk_count=r.chunk_count,
                        )
                    )
                    if r.embedded:
                        report.embedded += 1
                    else:
                        report.reference_only += 1
                return

        canonical: Optional[SourceRecord] = None
        attempt_index = 0

        for candidate in candidates:
            role = ROLE_CANONICAL if attempt_index == 0 else ROLE_FALLBACK
            ok, chunk_count, reasons = self._try_embed_candidate(
                candidate, role, content_type=content_type
            )
            attempt_index += 1
            if ok:
                canonical = candidate
                report.embedded += 1
                report.outcomes.append(
                    SourceOutcome(
                        source_id=candidate.source_id,
                        source_type=candidate.source_type,
                        issue_range=issue_range,
                        role=ROLE_CANONICAL,
                        status=STATUS_EMBEDDED,
                        chunk_count=chunk_count,
                    )
                )
                break
            else:
                report.failed += 1
                report.outcomes.append(
                    SourceOutcome(
                        source_id=candidate.source_id,
                        source_type=candidate.source_type,
                        issue_range=issue_range,
                        role=role,
                        status=STATUS_FAILED,
                        reasons=reasons,
                    )
                )

        # Remaining (non-canonical) sources -> reference_only.
        for candidate in candidates:
            if canonical is not None and candidate.source_id == canonical.source_id:
                continue
            if candidate.processing_status == STATUS_FAILED:
                continue
            self.registry.update(
                candidate.source_id,
                role=ROLE_REFERENCE,
                embedded=False,
                processing_status=STATUS_REFERENCE_ONLY,
            )
            report.reference_only += 1
            report.outcomes.append(
                SourceOutcome(
                    source_id=candidate.source_id,
                    source_type=candidate.source_type,
                    issue_range=issue_range,
                    role=ROLE_REFERENCE,
                    status=STATUS_REFERENCE_ONLY,
                )
            )

    def _try_embed_candidate(
        self,
        record: SourceRecord,
        role: str,
        *,
        content_type: str,
    ) -> Tuple[bool, int, List[str]]:
        """Load -> clean -> validate -> chunk -> embed a single candidate.

        Returns ``(success, chunk_count, reasons)``. On failure emits
        ``knowledge_ingestion_failed`` and marks the record failed.
        """
        path = Path(record.path)
        try:
            raw = load_source(record.source_type, path)
        except LoaderError as exc:
            return self._fail(record, ["loader_error", str(exc)])
        except Exception as exc:  # defensive: never let one bad file kill the run
            return self._fail(record, ["unexpected_loader_error", str(exc)])

        if record.source_type == SOURCE_TYPE_MARKDOWN:
            clean_result = clean_markdown(raw)
        else:
            clean_result = clean_text(raw)
        validation = validate_text(clean_result.text)
        self.registry.update(record.source_id, validation=validation.to_dict())

        if not validation.valid:
            return self._fail(record, validation.reasons)

        # Persist cleaned text.
        cleaned_path = self.cleaned_dir / f"{record.source_id}.txt"
        cleaned_path.write_text(clean_result.text, encoding="utf-8")
        self.registry.update(record.source_id, role=role)
        self._emit(
            "knowledge_cleaned",
            {
                "source_id": record.source_id,
                "issue_range": record.issue_range,
                "cleaned_path": str(cleaned_path),
                "char_count": clean_result.cleaned_chars,
                "dropped_lines": clean_result.dropped_lines,
            },
        )

        # Chunk.
        try:
            build = chunk_builder.build_chunks(
                record,
                clean_result.text,
                chunks_dir=self.chunks_dir,
                content_type=content_type,
            )
        except Exception as exc:
            return self._fail(record, ["chunk_error", str(exc)])

        if len(build) == 0:
            return self._fail(record, ["no_chunks"])

        self._emit(
            "knowledge_chunked",
            {
                "source_id": record.source_id,
                "issue_range": record.issue_range,
                "chunk_count": len(build),
                "jsonl_path": str(build.jsonl_path) if build.jsonl_path else None,
            },
        )

        # Embed.
        try:
            self.vector_store.upsert(
                ids=build.chunk_ids,
                texts=build.texts,
                metadatas=build.metadatas,
            )
        except Exception as exc:
            return self._fail(record, ["embed_error", str(exc)])

        self.registry.update(
            record.source_id,
            role=ROLE_CANONICAL,
            embedded=True,
            processing_status=STATUS_EMBEDDED,
            chunk_count=len(build),
        )
        self._emit(
            "knowledge_embedded",
            {
                "source_id": record.source_id,
                "issue_range": record.issue_range,
                "chunk_count": len(build),
                "collection": getattr(self.vector_store, "COLLECTION_NAME", "daise_knowledge"),
            },
        )
        return True, len(build), []

    def _fail(self, record: SourceRecord, reasons: List[str]) -> Tuple[bool, int, List[str]]:
        self.registry.update(
            record.source_id,
            embedded=False,
            processing_status=STATUS_FAILED,
        )
        self._emit(
            "knowledge_ingestion_failed",
            {
                "source_id": record.source_id,
                "source_type": record.source_type,
                "issue_range": record.issue_range,
                "reasons": reasons,
            },
        )
        return False, 0, reasons


def ingest_series(
    root: Path,
    series: str,
    *,
    registry: SourceRegistry,
    vector_store: VectorStore,
    event_store: Optional[EventStore],
    cleaned_dir: Path,
    chunks_dir: Path,
    content_type: str = "lore",
    collection: str = "",
    domain: str = "",
    force: bool = False,
    extra_ignores: Sequence[str] = (),
) -> IngestionReport:
    """Convenience wrapper constructing an :class:`IngestionPipeline` and running it."""
    pipeline = IngestionPipeline(
        registry=registry,
        vector_store=vector_store,
        event_store=event_store,
        cleaned_dir=cleaned_dir,
        chunks_dir=chunks_dir,
    )
    return pipeline.ingest_series(
        root,
        series,
        content_type=content_type,
        collection=collection,
        domain=domain,
        force=force,
        extra_ignores=extra_ignores,
    )
