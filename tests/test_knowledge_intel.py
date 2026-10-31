"""Tests for the knowledge intelligence layer (Phase 1)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Tuple

import pytest

from blackfong_ecosystem.ingestion.chunk_builder import build_chunks, chunk_prefix
from blackfong_ecosystem.ingestion.source_registry import SourceRegistry
from blackfong_ecosystem.knowledge_intel import (
    build_coverage,
    build_inventory,
    build_timeline,
    run_phase1,
)
from blackfong_ecosystem.knowledge_intel import graph_producer
from blackfong_ecosystem.knowledge_intel.provenance import KnowledgeProvenanceService
from blackfong_ecosystem.storage import EventStore


def _make_collection(
    tmp_path: Path,
    *,
    series: str = "Spawn",
    ranges: List[Tuple[str, int]] = (("1-8", 3), ("9-17", 2)),
    embedded: bool = True,
    domain: str = "comics",
    registry_path: Path | None = None,
    chunks_dir: Path | None = None,
) -> Tuple[SourceRegistry, Path]:
    """Create a registry + chunk JSONL mirroring a real ingested collection."""
    registry = SourceRegistry(registry_path or (tmp_path / "registry.json"))
    chunks_dir = chunks_dir or (tmp_path / "chunks")
    chunks_dir.mkdir(parents=True, exist_ok=True)
    src_dir = tmp_path / "src"
    src_dir.mkdir(exist_ok=True)

    for issue_range, n in ranges:
        src_file = src_dir / f"{series}_{issue_range}.txt"
        src_file.write_text(f"content for {issue_range}", encoding="utf-8")
        record, _ = registry.register(
            source_type="djvu_text",
            path=src_file,
            series=series,
            issue_range=issue_range,
            domain=domain,
        )
        if embedded:
            registry.update(
                record.source_id,
                embedded=True,
                processing_status="embedded",
                role="canonical",
                chunk_count=n,
            )
        else:
            registry.update(
                record.source_id,
                embedded=False,
                processing_status="reference_only",
                role="reference",
                chunk_count=0,
            )
        prefix = chunk_prefix(series, issue_range)
        lines = []
        for i in range(1, n + 1):
            cid = f"{prefix}_{i:04d}"
            meta = {
                "series": series,
                "issue_range": issue_range,
                "collection": "spawn",
                "domain": domain,
                "type": "lore",
                "source_id": record.source_id,
                "source_type": "djvu_text",
                "chunk_index": i,
            }
            lines.append(
                json.dumps(
                    {
                        "chunk_id": cid,
                        "source": f"{series} {issue_range}",
                        "text": f"text body {i} for {issue_range}",
                        "metadata": meta,
                    }
                )
            )
        (chunks_dir / f"{record.source_id}.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    registry.save()
    return registry, chunks_dir


def _event_types(event_store: EventStore, types: List[str]) -> set:
    present = set()
    for t in types:
        if event_store.list_events_by_type(t, limit=50):
            present.add(t)
    return present


def _count_rows(db_path: Path, table: str, where: str = "") -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


# --- ingestion intact ---------------------------------------------------


def test_chunk_metadata_includes_collection_domain(tmp_path: Path) -> None:
    registry = SourceRegistry(tmp_path / "registry.json")
    src_file = tmp_path / "Spawn_1-8.txt"
    src_file.write_text("x", encoding="utf-8")
    record, _ = registry.register(
        source_type="djvu_text",
        path=src_file,
        series="Spawn",
        issue_range="1-8",
        domain="comics",
    )
    result = build_chunks(record, "Sentence one. Sentence two. Sentence three.", chunks_dir=tmp_path / "chunks")
    assert len(result) >= 1
    meta = result.metadatas[0]
    # New fields present, old fields intact (ingestion not broken).
    assert meta["collection"] == "spawn"
    assert meta["domain"] == "comics"
    assert meta["source_id"] == record.source_id
    assert meta["series"] == "Spawn"
    assert meta["copyright_status"] == "private_reference_only"


def test_registry_roundtrip_collection_domain(tmp_path: Path) -> None:
    registry, _ = _make_collection(tmp_path)
    reloaded = SourceRegistry(tmp_path / "registry.json")
    rec = reloaded.all()[0]
    assert rec.resolved_collection() == "spawn"
    assert rec.domain == "comics"


# --- inventory ----------------------------------------------------------


def test_inventory_generation(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    inv = build_inventory(registry, "spawn", chunks_dir=chunks_dir)
    assert inv["found"] is True
    assert inv["collection"] == "spawn"
    assert inv["sources"] == 2
    assert inv["embedded"] == 2
    assert inv["total_chunks"] == 5
    assert inv["issue_ranges"] == ["1-8", "9-17"]


def test_inventory_missing_collection(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    inv = build_inventory(registry, "does_not_exist", chunks_dir=chunks_dir)
    assert inv["found"] is False
    assert "reason" in inv


# --- coverage -----------------------------------------------------------


def test_coverage_generation_continuous(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    cov = build_coverage(registry, "spawn", chunks_dir=chunks_dir)
    assert cov["found"] is True
    assert cov["embedded_sources"] == 2
    assert cov["embedded_ratio"] == 1.0
    seq = cov["sequence"]
    assert seq["available"] is True
    assert seq["min"] == 1 and seq["max"] == 17
    assert seq["continuous"] is True
    assert seq["missing_numbers"] == []


def test_coverage_detects_gap(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path, ranges=[("1-8", 2), ("18-26", 2)])
    cov = build_coverage(registry, "spawn", chunks_dir=chunks_dir)
    seq = cov["sequence"]
    assert seq["continuous"] is False
    assert 9 in seq["missing_numbers"] and 17 in seq["missing_numbers"]


# --- provenance ---------------------------------------------------------


def test_provenance_metadata(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    service = KnowledgeProvenanceService(registry=registry, chunks_dir=chunks_dir, vector_store=None)
    prov = service.for_chunk("spawn_001_008_0001")
    assert prov["found"] is True
    assert prov["collection"] == "spawn"
    assert prov["source_id"]
    assert prov["source_file"].endswith(".txt")
    assert prov["confidence"] == 1.0


def test_provenance_unknown_chunk_returns_reason(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    service = KnowledgeProvenanceService(registry=registry, chunks_dir=chunks_dir, vector_store=None)
    prov = service.for_chunk("does_not_exist_9999")
    assert prov["found"] is False
    assert "reason" in prov


# --- timeline -----------------------------------------------------------


def test_timeline_from_source_order(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    tl = build_timeline(registry, "spawn", chunks_dir=chunks_dir)
    assert tl["found"] is True
    assert tl["ordering"] == "source_order"
    assert tl["event_count"] == 2
    seqs = [e["sequence_index"] for e in tl["events"]]
    assert seqs == [1, 2]
    # First event is the earliest issue range and references a real chunk id.
    first = tl["events"][0]
    assert first["title"] == "Spawn 1-8"
    assert first["chunk_id"] == "spawn_001_008_0001"
    assert first["approximate_time"] is None


def test_timeline_no_embedded_sources(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path, embedded=False)
    tl = build_timeline(registry, "spawn", chunks_dir=chunks_dir)
    assert tl["found"] is False
    assert tl["events"] == []
    assert "reason" in tl


# --- memory chamber graph -----------------------------------------------


def test_memory_chamber_node_edge_creation(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    db_path = event_store.db_path

    out = graph_producer.produce_collection_graph(
        registry, "spawn", chunks_dir=chunks_dir, db_path=db_path, intel_root=tmp_path / "intel"
    )
    assert out["counts"] == {"nodes": 3, "edges": 2, "mentions": 2}

    assert _count_rows(db_path, "memory_chamber_nodes", "id='collection:spawn'") == 1
    assert _count_rows(db_path, "memory_chamber_nodes", "node_type='document'") == 2
    assert _count_rows(db_path, "memory_chamber_edges", "relation='appears_in'") == 2
    assert _count_rows(db_path, "memory_chamber_document_mentions") == 2

    # Provenance is preserved on the nodes.
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT provenance_json FROM memory_chamber_nodes WHERE node_type='document' LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    prov = json.loads(row[0])
    assert prov["collection"] == "spawn"
    assert prov["source_id"]
    assert prov["confidence"] == 1.0


def test_graph_rerun_idempotent(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    db_path = event_store.db_path
    intel = tmp_path / "intel"

    graph_producer.produce_collection_graph(registry, "spawn", chunks_dir=chunks_dir, db_path=db_path, intel_root=intel)
    graph_producer.produce_collection_graph(registry, "spawn", chunks_dir=chunks_dir, db_path=db_path, intel_root=intel)

    # No duplicate rows after a second run.
    assert _count_rows(db_path, "memory_chamber_nodes") == 3
    assert _count_rows(db_path, "memory_chamber_edges") == 2
    assert _count_rows(db_path, "memory_chamber_document_mentions") == 2


def test_graph_survives_simulated_reindex(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    db_path = event_store.db_path
    intel = tmp_path / "intel"

    graph_producer.produce_collection_graph(registry, "spawn", chunks_dir=chunks_dir, db_path=db_path, intel_root=intel)
    assert _count_rows(db_path, "memory_chamber_nodes") == 3

    # Simulate the reindex wipe.
    conn = sqlite3.connect(str(db_path))
    try:
        for table in ("memory_chamber_nodes", "memory_chamber_edges", "memory_chamber_document_mentions"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    assert _count_rows(db_path, "memory_chamber_nodes") == 0

    # The overlay re-applies the knowledge graph.
    totals = graph_producer.apply_all_overlays(db_path, intel)
    assert totals["nodes"] == 3
    assert _count_rows(db_path, "memory_chamber_nodes") == 3


def test_memory_chamber_hud_still_loads(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    graph_producer.produce_collection_graph(
        registry, "spawn", chunks_dir=chunks_dir, db_path=event_store.db_path, intel_root=tmp_path / "intel"
    )

    from blackfong_ecosystem.memory_chamber import MemoryChamberService

    service = MemoryChamberService(
        event_store=event_store, workspace_root=tmp_path, start_watch=False
    )
    try:
        graph = service.graph()
        assert isinstance(graph, dict)
        # graph() returns a projection envelope with a nested "graph" payload.
        inner = graph.get("graph", graph)
        assert "nodes" in inner and "edges" in inner
        summary = service.summary()
        assert isinstance(summary, dict)
    finally:
        service.shutdown()


# --- orchestrator (extract-all) -----------------------------------------


def test_run_phase1_produces_all_outputs_and_events(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    intel = tmp_path / "intel"

    result = run_phase1(
        "spawn",
        registry=registry,
        event_store=event_store,
        chunks_dir=chunks_dir,
        intel_root=intel,
        db_path=event_store.db_path,
    )
    assert result.ok is True
    # Real artifacts on disk.
    for name in ("inventory", "coverage", "timeline", "provenance", "graph"):
        assert name in result.artifacts
        assert Path(result.artifacts[name]).exists()
    assert (intel / "spawn" / "inventory.json").exists()
    assert (intel / "spawn" / "timeline.json").exists()

    # All seven Phase 1 events emitted.
    assert _event_types(
        event_store,
        [
            "knowledge_phase1_started",
            "knowledge_inventory_created",
            "knowledge_coverage_created",
            "knowledge_timeline_created",
            "knowledge_memory_chamber_graph_updated",
            "knowledge_phase1_completed",
        ],
    ) == {
        "knowledge_phase1_started",
        "knowledge_inventory_created",
        "knowledge_coverage_created",
        "knowledge_timeline_created",
        "knowledge_memory_chamber_graph_updated",
        "knowledge_phase1_completed",
    }

    # Graph really landed in Memory Chamber.
    assert _count_rows(event_store.db_path, "memory_chamber_nodes", "id='collection:spawn'") == 1


def test_run_phase1_missing_collection(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    intel = tmp_path / "intel"

    result = run_phase1(
        "nonexistent",
        registry=registry,
        event_store=event_store,
        chunks_dir=chunks_dir,
        intel_root=intel,
        db_path=event_store.db_path,
    )
    assert result.ok is False
    assert result.reason
    assert result.artifacts == {}
    assert event_store.list_events_by_type("knowledge_phase1_failed", limit=10)
    # No fake artifacts written for an unknown collection.
    assert not (intel / "nonexistent").exists()


def test_run_phase1_rerun_idempotent(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path)
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    intel = tmp_path / "intel"

    first = run_phase1("spawn", registry=registry, event_store=event_store, chunks_dir=chunks_dir, intel_root=intel, db_path=event_store.db_path)
    second = run_phase1("spawn", registry=registry, event_store=event_store, chunks_dir=chunks_dir, intel_root=intel, db_path=event_store.db_path)

    assert first.counts["graph"] == second.counts["graph"]
    # Rerun does not duplicate graph rows.
    assert _count_rows(event_store.db_path, "memory_chamber_nodes") == 3
    assert _count_rows(event_store.db_path, "memory_chamber_edges") == 2


# --- collection taxonomy layer ------------------------------------------


class _FakeChroma:
    """Minimal in-memory stand-in for a Chroma collection (no model load)."""

    def __init__(self) -> None:
        self.store: dict = {}

    def get(self, ids=None, include=None):
        ids = ids or []
        present = [i for i in ids if i in self.store]
        return {"ids": present, "metadatas": [dict(self.store[i]) for i in present]}

    def update(self, ids=None, metadatas=None):
        for cid, meta in zip(ids or [], metadatas or []):
            self.store[cid] = dict(meta)


class _FakeVectorStore:
    def __init__(self) -> None:
        self._collection = _FakeChroma()


def _seed_chroma_from_chunks(registry: SourceRegistry, chunks_dir: Path, *, with_domain: str = "") -> _FakeVectorStore:
    """Populate a fake Chroma with the chunks on disk (optionally pre-tagged)."""
    vs = _FakeVectorStore()
    for record in registry.all():
        if not record.embedded:
            continue
        jsonl = chunks_dir / f"{record.source_id}.jsonl"
        if not jsonl.exists():
            continue
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            meta = dict(obj.get("metadata", {}))
            meta.pop("collection", None)
            if with_domain:
                meta["domain"] = with_domain
            else:
                meta.pop("domain", None)
            vs._collection.store[obj["chunk_id"]] = meta
    return vs


def test_collection_registry_roundtrip(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel.collection_registry import CollectionRegistry

    path = tmp_path / "collections.json"
    reg = CollectionRegistry(path)
    reg.upsert("Spawn", domain="comics", description="Spawn comic universe")
    reg.upsert("security_plus", domain="cybersecurity")
    reg.save()

    reloaded = CollectionRegistry(path)
    assert reloaded.domain_for("spawn") == "comics"
    assert reloaded.get("spawn").description == "Spawn comic universe"
    assert reloaded.domain_for("security_plus") == "cybersecurity"
    # Unknown collection -> empty (no fabrication).
    assert reloaded.domain_for("unknown") == ""
    # Slugged key.
    assert {m.collection for m in reloaded.all()} == {"spawn", "security_plus"}


def test_collection_registry_upsert_preserves_unset_fields(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel.collection_registry import CollectionRegistry

    reg = CollectionRegistry(tmp_path / "collections.json")
    reg.upsert("spawn", domain="comics", description="first")
    reg.upsert("spawn", domain="comics2")  # description not provided -> unchanged
    meta = reg.get("spawn")
    assert meta.domain == "comics2"
    assert meta.description == "first"


def test_inventory_resolves_domain_from_taxonomy(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel.collection_registry import CollectionRegistry

    # Records created WITHOUT domain (predate taxonomy).
    registry, chunks_dir = _make_collection(tmp_path, domain="")
    creg = CollectionRegistry(tmp_path / "collections.json")
    creg.upsert("spawn", domain="comics")

    inv = build_inventory(registry, "spawn", chunks_dir=chunks_dir, collection_registry=creg)
    assert inv["domain"] == "comics"


def test_backfill_resolves_domain_from_taxonomy(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel.backfill import backfill_collection_metadata
    from blackfong_ecosystem.knowledge_intel.collection_registry import CollectionRegistry

    registry, chunks_dir = _make_collection(tmp_path, domain="")
    creg = CollectionRegistry(tmp_path / "collections.json")
    creg.upsert("spawn", domain="comics")
    vs = _seed_chroma_from_chunks(registry, chunks_dir, with_domain="")

    summary = backfill_collection_metadata(
        registry=registry,
        vector_store=vs,
        chunks_dir=chunks_dir,
        collection_registry=creg,
    )
    assert summary["ok"] is True
    assert summary["updated"] == 5  # 3 + 2 chunks
    assert summary["records_synced"] == 2  # both embedded sources gained domain

    # Chroma chunks now tagged.
    for meta in vs._collection.store.values():
        assert meta["collection"] == "spawn"
        assert meta["domain"] == "comics"

    # Registry records durably updated.
    assert all(r.domain == "comics" for r in registry.all() if r.embedded)

    # Rerun is idempotent (everything already correct -> all skipped).
    again = backfill_collection_metadata(
        registry=registry, vector_store=vs, chunks_dir=chunks_dir, collection_registry=creg
    )
    assert again["updated"] == 0
    assert again["skipped"] == 5


def test_backfill_falls_back_to_record_domain(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel.backfill import backfill_collection_metadata

    # No taxonomy store; domain comes from the record itself.
    registry, chunks_dir = _make_collection(tmp_path, domain="comics")
    vs = _seed_chroma_from_chunks(registry, chunks_dir, with_domain="")

    summary = backfill_collection_metadata(
        registry=registry, vector_store=vs, chunks_dir=chunks_dir, collection_registry=None
    )
    assert summary["ok"] is True
    for meta in vs._collection.store.values():
        assert meta["domain"] == "comics"


def test_backfill_scopes_to_collection(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel.backfill import backfill_collection_metadata
    from blackfong_ecosystem.knowledge_intel.collection_registry import CollectionRegistry

    registry, chunks_dir = _make_collection(tmp_path, domain="")
    creg = CollectionRegistry(tmp_path / "collections.json")
    creg.upsert("spawn", domain="comics")
    vs = _seed_chroma_from_chunks(registry, chunks_dir, with_domain="")

    # Restrict to a different collection -> nothing processed.
    summary = backfill_collection_metadata(
        registry=registry,
        vector_store=vs,
        chunks_dir=chunks_dir,
        collection_registry=creg,
        only_collection="security_plus",
    )
    assert summary["sources_processed"] == 0
    assert summary["updated"] == 0


def test_provenance_map_includes_domain(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel.collection_registry import CollectionRegistry

    registry, chunks_dir = _make_collection(tmp_path, domain="")
    creg = CollectionRegistry(tmp_path / "collections.json")
    creg.upsert("spawn", domain="comics")
    service = KnowledgeProvenanceService(
        registry=registry, chunks_dir=chunks_dir, vector_store=None, collection_registry=creg
    )
    prov_map = service.build_map("spawn")
    assert prov_map["domain"] == "comics"
    chunk = service.for_chunk("spawn_001_008_0001")
    assert chunk["domain"] == "comics"


def test_set_domain_cli_writes_taxonomy_and_syncs_records(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from blackfong_ecosystem import cli as cli_mod
    from blackfong_ecosystem.config import Paths
    from blackfong_ecosystem.knowledge_intel.collection_registry import CollectionRegistry

    fake_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(cli_mod, "paths", fake_paths)

    # Seed a registry (domain empty) at the CLI's source registry path.
    fake_paths.knowledge_root.mkdir(parents=True, exist_ok=True)
    _make_collection(
        tmp_path,
        domain="",
        registry_path=fake_paths.source_registry_path,
        chunks_dir=fake_paths.knowledge_chunks_path,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_mod.cli,
        ["knowledge", "set-domain", "--collection", "spawn", "--domain", "comics", "--description", "Spawn comics"],
    )
    assert result.exit_code == 0, result.output

    # Taxonomy persisted.
    creg = CollectionRegistry(fake_paths.collection_registry_path)
    assert creg.domain_for("spawn") == "comics"
    # Registry records synced.
    reg = SourceRegistry(fake_paths.source_registry_path)
    assert all(r.domain == "comics" for r in reg.all() if r.embedded)


def test_resolve_ingest_domain_inherits_from_taxonomy(tmp_path: Path, monkeypatch) -> None:
    from blackfong_ecosystem import cli as cli_mod
    from blackfong_ecosystem.config import Paths
    from blackfong_ecosystem.knowledge_intel.collection_registry import CollectionRegistry

    fake_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(cli_mod, "paths", fake_paths)
    fake_paths.knowledge_root.mkdir(parents=True, exist_ok=True)
    creg = CollectionRegistry(fake_paths.collection_registry_path)
    creg.upsert("security_plus", domain="cybersecurity")
    creg.save()

    # Omitted --domain inherits from the taxonomy store.
    assert cli_mod._resolve_ingest_domain("security_plus", None) == "cybersecurity"
    # Explicit --domain wins.
    assert cli_mod._resolve_ingest_domain("security_plus", "override") == "override"
    # Unknown collection, no override -> empty (no fabrication).
    assert cli_mod._resolve_ingest_domain("unknown", None) == ""


def test_list_domains_cli_flags_untagged(tmp_path: Path, monkeypatch) -> None:
    from click.testing import CliRunner

    from blackfong_ecosystem import cli as cli_mod
    from blackfong_ecosystem.config import Paths

    fake_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(cli_mod, "paths", fake_paths)
    fake_paths.knowledge_root.mkdir(parents=True, exist_ok=True)
    _make_collection(
        tmp_path,
        domain="",
        registry_path=fake_paths.source_registry_path,
        chunks_dir=fake_paths.knowledge_chunks_path,
    )

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["knowledge", "list-domains"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    spawn = next(r for r in rows if r["collection"] == "spawn")
    assert spawn["in_registry"] is True
    assert spawn["tagged"] is False  # not yet declared


# --- Phase 2A: compute layer --------------------------------------------


def _mixed_collection(tmp_path: Path) -> Tuple[SourceRegistry, Path]:
    """One embedded source + three reference-only sources (thin coverage)."""
    registry = SourceRegistry(tmp_path / "registry.json")
    chunks_dir = tmp_path / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    src_dir = tmp_path / "src"
    src_dir.mkdir(exist_ok=True)

    embedded_file = src_dir / "Spawn_1-8.txt"
    embedded_file.write_text("content", encoding="utf-8")
    rec, _ = registry.register(
        source_type="djvu_text", path=embedded_file, series="Spawn", issue_range="1-8", domain="comics"
    )
    registry.update(rec.source_id, embedded=True, processing_status="embedded", role="canonical", chunk_count=5)
    prefix = chunk_prefix("Spawn", "1-8")
    lines = []
    for i in range(1, 6):
        lines.append(json.dumps({
            "chunk_id": f"{prefix}_{i:04d}",
            "source": "Spawn 1-8",
            "text": f"text body {i}",
            "metadata": {"source_id": rec.source_id, "collection": "spawn", "domain": "comics", "chunk_index": i},
        }))
    (chunks_dir / f"{rec.source_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for n, rng in enumerate(("9-17", "18-26", "27-35"), start=1):
        ref_file = src_dir / f"Spawn_{rng}.txt"
        ref_file.write_text("ref", encoding="utf-8")
        ref, _ = registry.register(
            source_type="epub", path=ref_file, series="Spawn", issue_range=rng, domain="comics"
        )
        registry.update(ref.source_id, embedded=False, processing_status="reference_only", role="reference", chunk_count=0)
    registry.save()
    return registry, chunks_dir


def test_compute_mode_resolution(monkeypatch) -> None:
    from blackfong_ecosystem.knowledge_intel import compute as c

    monkeypatch.delenv("BLACKFONG_KNOWLEDGE_COMPUTE_MODE", raising=False)
    assert c.resolve_compute_mode() is c.ComputeMode.HYBRID
    assert c.resolve_compute_mode("deterministic") is c.ComputeMode.DETERMINISTIC
    assert c.resolve_compute_mode("local_llm") is c.ComputeMode.LOCAL_LLM
    monkeypatch.setenv("BLACKFONG_KNOWLEDGE_COMPUTE_MODE", "deterministic")
    assert c.resolve_compute_mode() is c.ComputeMode.DETERMINISTIC
    # Explicit arg overrides env.
    assert c.resolve_compute_mode("hybrid") is c.ComputeMode.HYBRID


def test_synthesize_deterministic_mode_uses_fallback() -> None:
    from blackfong_ecosystem.knowledge_intel import compute as c

    result = c.synthesize(
        system="s", grounding={"x": "y"}, question="q",
        mode="deterministic", fallback=lambda: "DET",
    )
    assert result.used_llm is False
    assert result.text == "DET"
    assert result.fallback_reason == "mode_deterministic"


def test_synthesize_empty_grounding_uses_fallback() -> None:
    from blackfong_ecosystem.knowledge_intel import compute as c

    result = c.synthesize(system="s", grounding="", question="q", mode="hybrid", fallback=lambda: "FB")
    assert result.used_llm is False
    assert result.fallback_reason == "empty_grounding"
    assert result.text == "FB"


def test_synthesize_llm_unavailable_uses_fallback(monkeypatch) -> None:
    from blackfong_ecosystem.knowledge_intel import compute as c

    monkeypatch.setattr(c, "local_llm_available", lambda **_: False)
    result = c.synthesize(system="s", grounding={"a": "b"}, question="q", mode="hybrid", fallback=lambda: "FB")
    assert result.used_llm is False
    assert result.fallback_reason == "llm_unavailable"
    assert result.text == "FB"


def test_synthesize_uses_llm_when_available(monkeypatch) -> None:
    from blackfong_ecosystem.knowledge_intel import compute as c
    from blackfong_ecosystem import model_routing

    class _FakeResult:
        text = "LLM SAYS HELLO"
        provider = "ollama"
        model = "test-model"
        metadata = {"selected_model": "test-model", "wall_time_ms": 1.0}

    class _FakeBackend:
        def generate(self, messages, **options):
            return _FakeResult()

    monkeypatch.setattr(c, "local_llm_available", lambda **_: True)
    monkeypatch.setattr(model_routing, "build_routed_chat_backend", lambda *a, **k: _FakeBackend())

    result = c.synthesize(system="s", grounding={"a": "b"}, question="q", mode="hybrid", fallback=lambda: "FB")
    assert result.used_llm is True
    assert result.text == "LLM SAYS HELLO"
    assert result.fallback_reason == ""
    assert result.model == "test-model"


def test_synthesize_llm_error_degrades_to_fallback(monkeypatch) -> None:
    from blackfong_ecosystem.knowledge_intel import compute as c
    from blackfong_ecosystem import model_routing

    class _BoomBackend:
        def generate(self, messages, **options):
            raise RuntimeError("ollama exploded")

    monkeypatch.setattr(c, "local_llm_available", lambda **_: True)
    monkeypatch.setattr(model_routing, "build_routed_chat_backend", lambda *a, **k: _BoomBackend())

    result = c.synthesize(system="s", grounding={"a": "b"}, question="q", mode="hybrid", fallback=lambda: "SAFE")
    assert result.used_llm is False
    assert result.text == "SAFE"
    assert result.fallback_reason.startswith("llm_error:")


# --- Phase 2A: gap detection --------------------------------------------


def _gap_types(gaps) -> set:
    return {g["gap_type"] for g in gaps}


def test_detect_gaps_missing_sequence(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel import gaps as gaps_mod

    registry, chunks_dir = _make_collection(tmp_path, ranges=[("1-8", 3), ("18-26", 3)])
    gaps = gaps_mod.detect_gaps(registry, "spawn", chunks_dir=chunks_dir)
    assert "missing_sequence" in _gap_types(gaps)
    ms = next(g for g in gaps if g["gap_type"] == "missing_sequence")
    assert 9 in ms["evidence"]["missing_numbers"]


def test_detect_gaps_low_chunk_count(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel import gaps as gaps_mod

    registry, chunks_dir = _make_collection(tmp_path, ranges=[("1-8", 1), ("9-17", 1)])
    gaps = gaps_mod.detect_gaps(registry, "spawn", chunks_dir=chunks_dir)
    assert "low_chunk_count" in _gap_types(gaps)
    lc = next(g for g in gaps if g["gap_type"] == "low_chunk_count")
    assert len(lc["affected_sources"]) == 2


def test_detect_gaps_unembedded_reference_only(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel import gaps as gaps_mod

    registry, chunks_dir = _make_collection(tmp_path, embedded=False)
    gaps = gaps_mod.detect_gaps(registry, "spawn", chunks_dir=chunks_dir)
    types = _gap_types(gaps)
    assert "unembedded_reference_only" in types
    assert "reference_only_not_embedded" in types
    assert "no_timeline" in types
    assert "no_graph_nodes" in types


def test_detect_gaps_thin_source_coverage(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel import gaps as gaps_mod

    registry, chunks_dir = _mixed_collection(tmp_path)
    gaps = gaps_mod.detect_gaps(registry, "spawn", chunks_dir=chunks_dir)
    assert "thin_source_coverage" in _gap_types(gaps)
    tsc = next(g for g in gaps if g["gap_type"] == "thin_source_coverage")
    assert tsc["evidence"]["embedded"] == 1
    assert tsc["evidence"]["total_sources"] == 4


def test_detect_gaps_clean_collection_has_none(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel import gaps as gaps_mod

    # Continuous, well-chunked, fully embedded -> no gaps.
    registry, chunks_dir = _make_collection(tmp_path, ranges=[("1-8", 5), ("9-17", 5)])
    gaps = gaps_mod.detect_gaps(registry, "spawn", chunks_dir=chunks_dir)
    assert gaps == []


def test_build_gaps_missing_collection(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel import gaps as gaps_mod

    registry, chunks_dir = _make_collection(tmp_path)
    record = gaps_mod.build_gaps(registry, "nope", chunks_dir=chunks_dir)
    assert record["found"] is False
    assert "reason" in record


def test_explain_gaps_deterministic_fallback(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel import gaps as gaps_mod

    registry, chunks_dir = _make_collection(tmp_path, ranges=[("1-8", 3), ("18-26", 3)])
    gaps = gaps_mod.detect_gaps(registry, "spawn", chunks_dir=chunks_dir)
    out = gaps_mod.explain_gaps(gaps, collection="spawn", domain="comics", mode="deterministic")
    assert out["used_llm"] is False
    assert "missing_sequence" in out["text"]


# --- Phase 2A: summaries ------------------------------------------------


def test_build_summary_extractive_collection(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel.summaries import build_summary, KIND_COLLECTION

    registry, chunks_dir = _make_collection(tmp_path)
    summary = build_summary(registry, "spawn", kind=KIND_COLLECTION, chunks_dir=chunks_dir, mode="deterministic")
    assert summary["found"] is True
    assert summary["compute_used"] == "extractive"
    assert summary["confidence"] == 1.0
    assert summary["text"] == summary["extractive_text"]
    assert summary["supporting_chunk_ids"]
    assert summary["source_ids"]
    assert summary["fallback_reason"] == "mode_deterministic"


def test_build_summary_source_kind(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel.summaries import build_summary, KIND_SOURCE

    registry, chunks_dir = _make_collection(tmp_path)
    source_id = registry.all()[0].source_id
    summary = build_summary(
        registry, "spawn", kind=KIND_SOURCE, source_id=source_id, chunks_dir=chunks_dir, mode="deterministic"
    )
    assert summary["found"] is True
    assert summary["source_ids"] == [source_id]
    assert summary["kind"] == KIND_SOURCE


def test_build_summary_missing_collection(tmp_path: Path) -> None:
    from blackfong_ecosystem.knowledge_intel.summaries import build_summary

    registry, chunks_dir = _make_collection(tmp_path)
    summary = build_summary(registry, "nope", chunks_dir=chunks_dir, mode="deterministic")
    assert summary["found"] is False
    assert "reason" in summary


# --- Phase 2A: orchestrator folds gaps ----------------------------------


def test_run_phase1_emits_gaps(tmp_path: Path) -> None:
    registry, chunks_dir = _make_collection(tmp_path, ranges=[("1-8", 3), ("18-26", 3)])
    event_store = EventStore(db_path=tmp_path / "events.db")
    event_store.init_db()
    intel = tmp_path / "intel"

    result = run_phase1(
        "spawn", registry=registry, event_store=event_store,
        chunks_dir=chunks_dir, intel_root=intel, db_path=event_store.db_path,
    )
    assert result.ok is True
    assert "gaps" in result.artifacts
    assert (intel / "spawn" / "gaps.json").exists()
    assert event_store.list_events_by_type("knowledge_gaps_detected", limit=10)
    record = json.loads((intel / "spawn" / "gaps.json").read_text(encoding="utf-8"))
    assert record["found"] is True
    assert "missing_sequence" in {g["gap_type"] for g in record["gaps"]}


# --- Markdown ingestion -------------------------------------------------


def test_discover_sources_finds_markdown_and_skips_vendored(tmp_path: Path) -> None:
    from blackfong_ecosystem.ingestion.pipeline import discover_sources

    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("# Guide\n\nReal content here.", encoding="utf-8")
    (root / "readme.markdown").write_text("# Readme\n\nMore content.", encoding="utf-8")
    # Vendored noise that must be ignored.
    (root / ".venv" / "lib" / "site-packages" / "pkg").mkdir(parents=True)
    (root / ".venv" / "lib" / "site-packages" / "pkg" / "README.md").write_text("dep", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "x.md").write_text("dep", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "mod.pyc").write_text("bytecode", encoding="utf-8")

    found = discover_sources(root)
    names = sorted(p.path.name for p in found)
    assert names == ["guide.md", "readme.markdown"]
    assert all(p.source_type == "markdown" for p in found)


def test_discover_sources_honors_extra_ignores(tmp_path: Path) -> None:
    from blackfong_ecosystem.ingestion.pipeline import discover_sources

    root = tmp_path / "repo"
    root.mkdir()
    (root / "keep.md").write_text("# Keep\n\ncontent", encoding="utf-8")
    (root / "obeos-remaster-check").mkdir()
    (root / "obeos-remaster-check" / "dup.md").write_text("# Dup\n\ncontent", encoding="utf-8")

    found = discover_sources(root, extra_ignores=["obeos-remaster-check"])
    names = sorted(p.path.name for p in found)
    assert names == ["keep.md"]


def test_markdown_chunking_is_heading_aware(tmp_path: Path) -> None:
    from blackfong_ecosystem.ingestion.chunk_builder import build_chunks
    from blackfong_ecosystem.ingestion.source_registry import SourceRegistry

    registry = SourceRegistry(tmp_path / "registry.json")
    md = tmp_path / "lesson.md"
    md.write_text(
        "# Day 1\n\nIntro to variables.\n\n## Strings\n\nStrings are text.\n\n## Lists\n\nLists hold items.\n",
        encoding="utf-8",
    )
    record, _ = registry.register(
        source_type="markdown", path=md, series="python", issue_range="unknown", domain="programming"
    )
    result = build_chunks(record, md.read_text(encoding="utf-8"), chunks_dir=tmp_path / "chunks")
    assert len(result) >= 3
    # Chunk ids are unique and prefixed by the (unique) source id.
    assert len(set(result.chunk_ids)) == len(result.chunk_ids)
    assert all(cid.startswith(record.source_id) for cid in result.chunk_ids)
    # Section titles come from the markdown headings.
    titles = {m.get("section_title") for m in result.metadatas}
    assert {"Day 1", "Strings", "Lists"}.issubset(titles)
    for meta in result.metadatas:
        assert meta["source_type"] == "markdown"
        assert meta["file_name"] == "lesson.md"
        assert meta["issue_range"] == "unknown"


def test_markdown_files_each_embed_independently(tmp_path: Path) -> None:
    """Many markdown files sharing issue_range='unknown' all embed (not deduped)."""
    from blackfong_ecosystem.ingestion.pipeline import IngestionPipeline
    from blackfong_ecosystem.ingestion.source_registry import SourceRegistry

    root = tmp_path / "docs"
    root.mkdir()
    for i in range(4):
        (root / f"lesson_{i}.md").write_text(
            f"# Lesson {i}\n\nThis is lesson {i} with enough content to pass validation. " * 10,
            encoding="utf-8",
        )

    class _CaptureStore:
        def __init__(self) -> None:
            self.ids: list = []

        def upsert(self, ids, texts, metadatas):
            self.ids.extend(ids)

    registry = SourceRegistry(tmp_path / "registry.json")
    vs = _CaptureStore()
    pipeline = IngestionPipeline(
        registry=registry, vector_store=vs, event_store=None,
        cleaned_dir=tmp_path / "cleaned", chunks_dir=tmp_path / "chunks",
    )
    report = pipeline.ingest_series(root, "python", collection="python", domain="programming")
    # All four markdown files embedded as their own canonical document.
    assert report.embedded == 4
    embedded_records = [r for r in registry.all() if r.embedded]
    assert len(embedded_records) == 4
    assert all(r.issue_range == "unknown" for r in embedded_records)
    # Globally unique chunk ids (no collisions across files).
    assert len(set(vs.ids)) == len(vs.ids)


# --- Phase 2B: librarian ------------------------------------------------


class _FakeSearchStore:
    """Vector store stub returning canned chunk hits for the librarian."""

    def __init__(self, hits) -> None:
        self._hits = hits
        self.last_metadata_filter = None

    def search(self, query, k=5, metadata_filter=None):
        self.last_metadata_filter = metadata_filter
        return list(self._hits)[:k]


def test_librarian_structured_fallback_with_provenance(tmp_path: Path, monkeypatch) -> None:
    from blackfong_ecosystem.knowledge_intel.librarian import LibrarianAgent
    from blackfong_ecosystem.knowledge_intel import compute as c

    registry, chunks_dir = _make_collection(tmp_path)
    # Force the deterministic (no-LLM) path.
    monkeypatch.setattr(c, "local_llm_available", lambda **_: False)

    hits = [
        {
            "id": "spawn_001_008_0001",
            "text": "text body 1 for 1-8",
            "metadata": {"source_id": registry.all()[0].source_id, "collection": "spawn"},
            "relevance": 0.91,
        }
    ]
    agent = LibrarianAgent(
        registry=registry, vector_store=_FakeSearchStore(hits), chunks_dir=chunks_dir
    )
    record = agent.answer("what happens in spawn?", collection="spawn", mode="hybrid")
    assert record["used_llm"] is False
    assert record["compute_used"] == "structured_facts"
    assert record["retrieved_count"] == 1
    assert record["provenance"]
    prov = record["provenance"][0]
    assert prov["collection"] == "spawn"
    assert prov["source_file"].endswith(".txt")
    assert prov["chunk_id"] == "spawn_001_008_0001"
    # The answer is grounded in the retrieved passage (no fabrication).
    assert "1-8" in record["answer"] or "spawn" in record["answer"].lower()


def test_librarian_no_results_is_honest(tmp_path: Path, monkeypatch) -> None:
    from blackfong_ecosystem.knowledge_intel.librarian import LibrarianAgent
    from blackfong_ecosystem.knowledge_intel import compute as c

    registry, chunks_dir = _make_collection(tmp_path)
    monkeypatch.setattr(c, "local_llm_available", lambda **_: False)

    agent = LibrarianAgent(
        registry=registry, vector_store=_FakeSearchStore([]), chunks_dir=chunks_dir
    )
    record = agent.answer("unrelated question", collection="spawn", mode="hybrid")
    assert record["retrieved_count"] == 0
    assert record["provenance"] == []
    assert "nothing relevant was retrieved" in record["answer"].lower()


def test_librarian_filters_by_collection(tmp_path: Path, monkeypatch) -> None:
    from blackfong_ecosystem.knowledge_intel.librarian import LibrarianAgent
    from blackfong_ecosystem.knowledge_intel import compute as c

    registry, chunks_dir = _make_collection(tmp_path)
    monkeypatch.setattr(c, "local_llm_available", lambda **_: False)

    hits = [
        {"id": "a", "text": "spawn passage", "metadata": {"source_id": registry.all()[0].source_id, "collection": "spawn"}, "relevance": 0.9},
        {"id": "b", "text": "other passage", "metadata": {"source_id": "x", "collection": "other"}, "relevance": 0.8},
    ]
    agent = LibrarianAgent(
        registry=registry, vector_store=_FakeSearchStore(hits), chunks_dir=chunks_dir
    )
    record = agent.answer("q", collection="spawn", mode="hybrid")
    # Only the spawn passage survives the collection filter.
    assert record["retrieved_count"] == 1
    assert all(p["collection"] == "spawn" for p in record["provenance"])
    assert agent.vector_store.last_metadata_filter == {"collection": "spawn"}
