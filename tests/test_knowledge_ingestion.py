"""Tests for the knowledge ingestion pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pytest

from blackfong_ecosystem.ingestion import pipeline as pipeline_mod
from blackfong_ecosystem.ingestion.chunk_builder import build_chunks, chunk_prefix
from blackfong_ecosystem.ingestion.pipeline import IngestionPipeline, parse_issue_range
from blackfong_ecosystem.ingestion.source_registry import (
    ROLE_CANONICAL,
    ROLE_REFERENCE,
    SOURCE_TYPE_DJVU,
    SOURCE_TYPE_EPUB,
    SOURCE_TYPE_PDF,
    SOURCE_TYPE_VISUAL,
    STATUS_EMBEDDED,
    STATUS_PENDING_PROCESSING,
    STATUS_REFERENCE_ONLY,
    SourceRecord,
    SourceRegistry,
)
from blackfong_ecosystem.ingestion.text_cleaner import clean_text, validate_text
from blackfong_ecosystem.ingestion.visual_pipeline import register_visual_source
from blackfong_ecosystem.storage import EventStore, VectorStore


GOOD_TEXT = (
    "Al Simmons was a soldier betrayed and murdered by his own. "
    "He bargained with Malebolgia and returned to Earth as a Hellspawn. "
    "Bound to a living symbiote, he wandered the alleys searching for meaning. "
    "Spawn protects the forgotten while resisting the pull of Hell's army. "
    "The Violator taunts him at every turn, a clown hiding a monstrous form. "
) * 4


class FakeVectorStore(VectorStore):
    """In-memory vector store capturing adds for assertions."""

    def __init__(self) -> None:
        self.ids: List[str] = []
        self.texts: List[str] = []
        self.metadatas: List[Dict[str, Any]] = []

    def add(
        self,
        ids: Iterable[str],
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        ids = list(ids)
        texts = list(texts)
        metas = list(metadatas) if metadatas is not None else [{}] * len(ids)
        self.ids.extend(ids)
        self.texts.extend(texts)
        self.metadatas.extend(metas)

    def search(
        self,
        query: str,
        k: int = 5,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        del query, k, metadata_filter
        return []


@pytest.fixture
def event_store(tmp_path: Path) -> EventStore:
    store = EventStore(db_path=tmp_path / "events.db")
    store.init_db()
    return store


@pytest.fixture
def registry(tmp_path: Path) -> SourceRegistry:
    return SourceRegistry(tmp_path / "knowledge" / "source_registry.json")


def _emitted_types(event_store: EventStore) -> set[str]:
    types = set()
    for t in (
        "knowledge_source_added",
        "knowledge_cleaned",
        "knowledge_chunked",
        "knowledge_embedded",
        "knowledge_ingestion_failed",
    ):
        if event_store.list_events_by_type(t, limit=50):
            types.add(t)
    return types


# --- registry -----------------------------------------------------------


def test_registry_roundtrip_and_checksum_idempotency(tmp_path: Path) -> None:
    reg_path = tmp_path / "registry.json"
    registry = SourceRegistry(reg_path)
    src = tmp_path / "spawn_1to8.txt"
    src.write_text("hello spawn", encoding="utf-8")

    record, changed = registry.register(
        source_type=SOURCE_TYPE_DJVU,
        path=src,
        series="Spawn",
        issue_range="1-8",
    )
    assert changed is True
    assert record.checksum
    registry.save()

    # Reload from disk.
    reloaded = SourceRegistry(reg_path)
    assert reloaded.get(record.source_id) is not None
    assert reloaded.get(record.source_id).issue_range == "1-8"

    # Unchanged file -> not changed.
    _, changed_again = reloaded.register(
        source_type=SOURCE_TYPE_DJVU,
        path=src,
        series="Spawn",
        issue_range="1-8",
    )
    assert changed_again is False

    # Modified file -> changed.
    src.write_text("hello spawn modified", encoding="utf-8")
    _, changed_after_edit = reloaded.register(
        source_type=SOURCE_TYPE_DJVU,
        path=src,
        series="Spawn",
        issue_range="1-8",
    )
    assert changed_after_edit is True


# --- cleaner ------------------------------------------------------------


def test_cleaner_strips_boilerplate_and_preserves_boundaries() -> None:
    raw = (
        "Scanned by Archive.org\n"
        "Issue 5\n"
        "Al Simmons walks the alley exam-\nple of a fallen hero.\n"
        "12\n"
        "All rights reserved.\n"
    )
    result = clean_text(raw)
    assert "Archive.org" not in result.text
    assert "all rights reserved" not in result.text.lower()
    assert "[ISSUE: 5]" in result.text
    assert "[PAGE 12]" in result.text
    # Hyphenation across the line break is repaired.
    assert "example" in result.text
    assert result.dropped_lines >= 2


def test_validate_text_rejects_short_and_boilerplate() -> None:
    assert validate_text("tiny").valid is False
    assert validate_text("").valid is False
    markers = "\n".join(["[PAGE 1]", "[ISSUE: 1]", "[PAGE]"] * 30)
    assert validate_text(markers).valid is False
    assert validate_text(GOOD_TEXT).valid is True


# --- chunk builder ------------------------------------------------------


def test_chunk_jsonl_schema(tmp_path: Path) -> None:
    record = SourceRecord(
        source_id="djvu_test123",
        source_type=SOURCE_TYPE_DJVU,
        path=str(tmp_path / "x.txt"),
        series="Spawn",
        issue_range="1-8",
    )
    result = build_chunks(record, GOOD_TEXT, chunks_dir=tmp_path / "chunks")
    assert len(result) >= 1
    assert result.jsonl_path and result.jsonl_path.exists()

    lines = result.jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    first = json.loads(lines[0])
    assert first["chunk_id"].startswith(chunk_prefix("Spawn", "1-8"))
    assert first["source"] == "Spawn 1-8"
    assert "text" in first
    meta = first["metadata"]
    for key in ("series", "issue_range", "type", "source_id", "source_type", "copyright_status"):
        assert key in meta
    assert meta["copyright_status"] == "private_reference_only"


def test_parse_issue_range() -> None:
    assert parse_issue_range("spawn_1to8") == "1-8"
    assert parse_issue_range("spawn-90to99") == "90-99"
    assert parse_issue_range("issue_7") == "7"
    assert parse_issue_range("nonumber") == "unknown"


# --- pipeline: canonical selection + fallback + events ------------------


def test_canonical_fallback_djvu_bad_to_epub(
    tmp_path: Path,
    registry: SourceRegistry,
    event_store: EventStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "spawn"
    root.mkdir()
    # Same issue range across three source types.
    (root / "spawn_1to8.txt").write_text("boiler", encoding="utf-8")
    (root / "spawn_1to8.epub").write_bytes(b"epub-bytes")
    (root / "spawn_1to8.pdf").write_bytes(b"pdf-bytes")

    def fake_load(source_type: str, path: Path) -> str:
        if source_type == SOURCE_TYPE_DJVU:
            return "too short to pass validation"
        if source_type == SOURCE_TYPE_EPUB:
            return GOOD_TEXT
        if source_type == SOURCE_TYPE_PDF:
            return GOOD_TEXT
        return ""

    monkeypatch.setattr(pipeline_mod, "load_source", fake_load)

    vector_store = FakeVectorStore()
    pipe = IngestionPipeline(
        registry=registry,
        vector_store=vector_store,
        event_store=event_store,
        cleaned_dir=tmp_path / "cleaned",
        chunks_dir=tmp_path / "chunks",
    )
    report = pipe.ingest_series(root, "Spawn")

    # EPUB became canonical/embedded; pdf is reference_only; djvu failed.
    by_type = {r.source_type: r for r in registry.all()}
    assert by_type[SOURCE_TYPE_EPUB].role == ROLE_CANONICAL
    assert by_type[SOURCE_TYPE_EPUB].embedded is True
    assert by_type[SOURCE_TYPE_EPUB].processing_status == STATUS_EMBEDDED
    assert by_type[SOURCE_TYPE_PDF].processing_status == STATUS_REFERENCE_ONLY
    assert by_type[SOURCE_TYPE_PDF].embedded is False
    assert by_type[SOURCE_TYPE_DJVU].embedded is False

    assert report.embedded == 1
    assert vector_store.ids  # something was embedded

    # All five lifecycle event types emitted across the run.
    assert _emitted_types(event_store) == {
        "knowledge_source_added",
        "knowledge_cleaned",
        "knowledge_chunked",
        "knowledge_embedded",
        "knowledge_ingestion_failed",
    }


def test_djvu_preferred_when_valid(
    tmp_path: Path,
    registry: SourceRegistry,
    event_store: EventStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "spawn"
    root.mkdir()
    (root / "spawn_1to8.txt").write_text("placeholder", encoding="utf-8")
    (root / "spawn_1to8.epub").write_bytes(b"epub")

    monkeypatch.setattr(pipeline_mod, "load_source", lambda st, p: GOOD_TEXT)

    vector_store = FakeVectorStore()
    pipe = IngestionPipeline(
        registry=registry,
        vector_store=vector_store,
        event_store=event_store,
        cleaned_dir=tmp_path / "cleaned",
        chunks_dir=tmp_path / "chunks",
    )
    pipe.ingest_series(root, "Spawn")

    by_type = {r.source_type: r for r in registry.all()}
    assert by_type[SOURCE_TYPE_DJVU].role == ROLE_CANONICAL
    assert by_type[SOURCE_TYPE_DJVU].embedded is True
    assert by_type[SOURCE_TYPE_EPUB].processing_status == STATUS_REFERENCE_ONLY


# --- visual jp2 ---------------------------------------------------------


def test_jp2_registers_pending_and_not_embedded(
    tmp_path: Path,
    registry: SourceRegistry,
    event_store: EventStore,
) -> None:
    jp2 = tmp_path / "spawn_001_page1.jp2"
    jp2.write_bytes(b"\x00\x00\x00\x0cjP  ")

    record = register_visual_source(
        jp2,
        series="Spawn",
        issue_range="1-8",
        registry=registry,
        event_store=event_store,
    )
    assert record.source_type == SOURCE_TYPE_VISUAL
    assert record.processing_status == STATUS_PENDING_PROCESSING
    assert record.embedded is False
    assert event_store.list_events_by_type("knowledge_source_added", limit=10)


def test_pipeline_registers_jp2_as_pending(
    tmp_path: Path,
    registry: SourceRegistry,
    event_store: EventStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "spawn"
    root.mkdir()
    (root / "spawn_1to8.jp2").write_bytes(b"jp2")
    monkeypatch.setattr(pipeline_mod, "load_source", lambda st, p: GOOD_TEXT)

    vector_store = FakeVectorStore()
    pipe = IngestionPipeline(
        registry=registry,
        vector_store=vector_store,
        event_store=event_store,
        cleaned_dir=tmp_path / "cleaned",
        chunks_dir=tmp_path / "chunks",
    )
    report = pipe.ingest_series(root, "Spawn")
    assert report.pending_visual == 1
    assert not vector_store.ids  # jp2 is never embedded
