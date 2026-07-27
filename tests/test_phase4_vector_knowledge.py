"""
Phase 4 unit tests: vector add/search, stable chunk ID, event emission, context structure.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import click
from click.testing import CliRunner

from blackfong_ecosystem.events import EVENT_TYPES
from blackfong_ecosystem.knowledge import (
    CHUNK_SIZE,
    _chunk_text,
    _content_hash,
    _stable_chunk_id,
    ingest_file,
    ingest_directory,
)
from blackfong_ecosystem.storage import EventStore, NullVectorStore
try:
    from blackfong_ecosystem.storage import ChromaVectorStore
except ImportError:
    # chromadb may not be installed in test environment; fall back to null store
    ChromaVectorStore = NullVectorStore
from blackfong_ecosystem.events import Event
from blackfong_ecosystem.daise.engines import SynthesisEngine
from blackfong_ecosystem.cli import cli as bf_cli


def test_knowledge_ingested_event_type_registered() -> None:
    assert "knowledge_ingested" in EVENT_TYPES


def test_stable_chunk_id_deterministic() -> None:
    a = _stable_chunk_id("/path/to/file.txt", 0)
    b = _stable_chunk_id("/path/to/file.txt", 0)
    assert a == b
    c = _stable_chunk_id("/path/to/file.txt", 1)
    assert a != c


def test_chunk_text_fixed_size() -> None:
    # ensure that large text is broken into chunks no larger than CHUNK_SIZE
    text = "a" * (CHUNK_SIZE * 2 + 10)
    chunks = _chunk_text(text)
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
    # ensure the text is split into more than one chunk for large input
    assert len(chunks) >= 2


def test_chunk_text_sentence_aware() -> None:
    text = "This is sentence one. This is sentence two! And a third?" * 5
    chunks = _chunk_text(text, chunk_size=50)
    # ensure no chunk exceeds size
    assert all(len(c) <= 50 for c in chunks)
    # ensure sentence boundaries roughly preserved
    assert any("sentence one" in c for c in chunks)


def test_ingest_file_metadata(tmp_path: Path) -> None:
    text_file = tmp_path / "doc.txt"
    text_file.write_text("Hello world. More text.", encoding="utf-8")
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    try:
        vector_store = ChromaVectorStore(tmp_path / "chroma")
    except ModuleNotFoundError:
        vector_store = NullVectorStore()
    n, eid = ingest_file(text_file, vector_store, event_store)
    assert n >= 1
    # inspect vector store metadata if available
    if not isinstance(vector_store, NullVectorStore):
        # assume at least one metadata exists in collection
        res = vector_store.search("Hello", k=1)
        if res:
            meta = res[0].get("metadata", {})
            assert "keywords" in meta
            assert "length" in meta
            assert "source_type" in meta


def test_content_hash_deterministic() -> None:
    assert _content_hash("hello") == _content_hash("hello")
    assert _content_hash("hello") != _content_hash("world")


def test_vector_store_add_search(tmp_path: Path) -> None:
    try:
        store = ChromaVectorStore(tmp_path)
    except ModuleNotFoundError:
        store = NullVectorStore()
    store.add(
        ids=["id1", "id2"],
        texts=["first chunk text", "second chunk text"],
        metadatas=[{"source_path": "/a.txt", "chunk_index": 0}, {"source_path": "/a.txt", "chunk_index": 1}],
    )
    results = store.search("chunk", k=2)
    if isinstance(store, NullVectorStore):
        # null backend returns nothing; just ensure it's list
        assert results == []
        return
    assert len(results) == 2
    for r in results:
        assert "id" in r
        assert "text" in r
        assert "metadata" in r
        assert "distance" in r
        assert isinstance(r["distance"], float)


def test_null_vector_store_search_returns_empty() -> None:
    store = NullVectorStore()
    assert store.search("anything", k=5) == []


def test_ingest_file_emits_knowledge_ingested_event(tmp_path: Path) -> None:
    text_file = tmp_path / "doc.txt"
    text_file.write_text("short", encoding="utf-8")
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    try:
        vector_store = ChromaVectorStore(tmp_path / "chroma")
    except ModuleNotFoundError:
        vector_store = NullVectorStore()
    n, eid = ingest_file(text_file, vector_store, event_store)
    assert n >= 1
    assert eid
    events = event_store.list_events_by_type("knowledge_ingested", limit=10)
    assert len(events) == 1
    assert events[0].payload["source_path"] == str(text_file.resolve())
    assert events[0].payload["chunk_count"] == n
    assert "timestamp" in events[0].payload


def test_build_context_structure(tmp_path: Path) -> None:
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    vector_store = NullVectorStore()
    synthesis = SynthesisEngine(event_store=event_store, vector_store=vector_store)
    ctx = synthesis.build_context("test task")
    assert ctx["task"] == "test task"
    assert "recent_events" in ctx
    assert "task_events" in ctx
    assert "semantic_context" in ctx
    assert isinstance(ctx["recent_events"], list)
    assert isinstance(ctx["task_events"], list)
    assert isinstance(ctx["semantic_context"], list)
    assert ctx["semantic_context"] == []


def test_search_payload_and_memory_wrappers(tmp_path: Path) -> None:
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    # add a few events manually
    e1 = Event.new(source="user", type="session_summary", payload={"text": "hello world"}, confidence=1.0)
    e2 = Event.new(source="project3000", type="motion_spike", payload={"description": "motion detected"}, confidence=0.9)
    event_store.add_event(e1)
    event_store.add_event(e2)
    # search payload
    results = event_store.search_payload("hello")
    assert any(r.id == e1.id for r in results)
    # list by source and type
    filtered = event_store.list_events_by_source_and_type("project3000", "motion_spike")
    assert len(filtered) == 1 and filtered[0].id == e2.id
    # recent events
    rec = event_store.recent_events(limit=5)
    assert len(rec) >= 2
    # get by id and around
    ev = event_store.get_event_by_id(e1.id)
    assert ev is not None
    around = event_store.events_around_event(e1.id, window_ms=10000)
    assert any(a.id == e1.id for a in around)
    # MemoryEngine wrappers
    from blackfong_ecosystem.daise.engines import MemoryEngine
    mem = MemoryEngine(event_store=event_store)
    assert mem.search_payload("motion")
    assert mem.events_for_task("hello")
    assert mem.list_events_by_source_and_type("user", "session_summary")


def test_event_trace_queries_use_indexed_columns(tmp_path: Path) -> None:
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    event = Event.new(
        source="user",
        type="agent_query",
        payload={"text": "trace me"},
        confidence=1.0,
        session_id="session-a",
        request_id="request-a",
        correlation_id="correlation-a",
    )
    event_store.add_event(event)

    assert event_store.list_events_for_session("session-a")[0].id == event.id
    assert event_store.list_events_for_request("request-a")[0].id == event.id
    assert event_store.list_events_for_correlation("correlation-a")[0].id == event.id

    with event_store.engine.connect() as connection:
        indexes = {row[1] for row in connection.exec_driver_sql("PRAGMA index_list(events)").fetchall()}

    assert "idx_events_session_id_timestamp" in indexes
    assert "idx_events_request_id_timestamp" in indexes
    assert "idx_events_correlation_id_timestamp" in indexes


def test_hypothesis_confidence_and_metadata() -> None:
    # verify confidence increases with context signals and metadata fields present
    synthesis = SynthesisEngine(event_store=EventStore(), vector_store=NullVectorStore())
    ctx_empty = {"task": "foo", "recent_events": [], "task_events": [], "semantic_context": []}
    h1 = synthesis.generate_hypothesis("foo", ctx_empty)
    assert "confidence" in h1 and isinstance(h1["confidence"], float)
    assert h1["used_events"] == []
    assert h1["used_semantic"] == []
    ctx_with = {"task": "foo", "recent_events": [{"id": "e1"}], "task_events": [{"id": "t1"}], "semantic_context": [{"id": "s1"}]}
    h2 = synthesis.generate_hypothesis("foo", ctx_with)
    assert h2["confidence"] >= h1["confidence"]
    assert h2["used_events"] == ["e1"]
    assert h2["used_semantic"] == ["s1"]

def test_cli_memory_and_instability(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    # monkeypatch EventStore.__init__ so the CLI uses our temp database
    orig_init = EventStore.__init__

    def fake_init(self, db_path=None):
        # ignore provided path, always use tmp_path events.db
        return orig_init(self, db_path=tmp_path / "events.db")

    monkeypatch.setattr(EventStore, "__init__", fake_init)

    # prepare some events in the same temp database
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    e = Event.new(source="user", type="session_summary", payload={"note": "foo"}, confidence=1.0)
    event_store.add_event(e)
    result = runner.invoke(bf_cli, ["memory-query", "--keyword", "foo"])
    assert result.exit_code == 0
    assert "session_summary" in result.output
    # test instability-stats (no mobray events yet)
    result2 = runner.invoke(bf_cli, ["instability-stats", "--last", "1", "--format", "json"])
    assert result2.exit_code == 0
    # no events yet, output should be empty JSON array; sparkline only shown when scores exist
    assert result2.output.strip().startswith("[")


def test_ingest_directory_respects_extensions(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("aa", encoding="utf-8")
    (tmp_path / "b.md").write_text("bb", encoding="utf-8")
    (tmp_path / "c.xyz").write_text("cc", encoding="utf-8")
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    try:
        vector_store = ChromaVectorStore(tmp_path / "chroma")
    except ModuleNotFoundError:
        vector_store = NullVectorStore()
    total, eids = ingest_directory(tmp_path, vector_store, event_store, extensions=(".txt", ".md"))
    assert total >= 2
    assert len(eids) == 2
    assert len(event_store.list_events_by_type("knowledge_ingested", limit=10)) == 2
