"""
Knowledge Intelligence benchmark - the frozen regression baseline.

This is the Phase 3 control point. It freezes the deterministic outputs of the
knowledge layer over the four real corpora (spawn, security_plus, python, obeos)
and asserts they do not drift. It must be green before any ``/api/knowledge/*``
route is built, so the API exposes a known-good, stable contract.

Design (per the agreed benchmark rules):
- Deterministic artifacts (inventory / coverage / gaps / timeline / summary /
  provenance summary + sample traces) are frozen EXACTLY and diffed. A field
  rename or value change is a real failure that requires an intentional fixture
  update.
- Everything runs in ``ComputeMode.DETERMINISTIC`` so it is reproducible and never
  depends on a live model.
- Librarian PROSE is never frozen. The librarian benchmark only asserts invariants:
  retrieved chunk ids, provenance shape, collection isolation, no cross-collection
  leakage, and non-empty answers.
- Paths are normalized to basenames and volatile fields (timestamps) are dropped so
  the benchmark survives a machine move and the out-of-repo Python corpus. The exact
  machine-specific outputs live in ``knowledge_benchmark.md`` for human reference.

Refreshing the baseline (intentional update only):
    BENCHMARK_REFRESH=1 pytest tests/test_knowledge_benchmark.py
This regenerates every fixture from the current live data, then skips assertions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from blackfong_ecosystem.config import paths
from blackfong_ecosystem.ingestion.source_registry import SourceRegistry, slugify_collection
from blackfong_ecosystem.knowledge_intel import collections as collections_mod
from blackfong_ecosystem.knowledge_intel import coverage as coverage_mod
from blackfong_ecosystem.knowledge_intel import gaps as gaps_mod
from blackfong_ecosystem.knowledge_intel import sources as src
from blackfong_ecosystem.knowledge_intel import timeline as timeline_mod
from blackfong_ecosystem.knowledge_intel.collection_registry import CollectionRegistry
from blackfong_ecosystem.knowledge_intel.provenance import KnowledgeProvenanceService
from blackfong_ecosystem.knowledge_intel.summaries import build_summary, KIND_COLLECTION

BENCHMARK_DIR = Path(__file__).parent / "benchmark" / "knowledge"
REFRESH = os.getenv("BENCHMARK_REFRESH") == "1"

COLLECTIONS = ["spawn", "security_plus", "python", "obeos"]

# Frozen librarian probes (questions chosen to retrieve sensibly per domain).
LIBRARIAN_QUESTIONS: Dict[str, str] = {
    "spawn": "what is spawn about?",
    "security_plus": "what domains are on the security plus exam?",
    "python": "how do variables work in python?",
    "obeos": "what is the obeos tooling matrix?",
}

# Keys whose values are filesystem paths -> reduced to a basename for portability.
_PATH_KEYS = {"source_file", "path", "relative_path"}
# Keys that change every run and carry no regression signal.
_VOLATILE_KEYS = {"generated_at"}


def _basename(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1] if value else value


def _normalize(obj: Any) -> Any:
    """Drop volatile keys and reduce path-valued fields to basenames, recursively."""
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for key, value in obj.items():
            if key in _VOLATILE_KEYS:
                continue
            if key in _PATH_KEYS and isinstance(value, str):
                out[key] = _basename(value)
            else:
                out[key] = _normalize(value)
        return out
    if isinstance(obj, list):
        return [_normalize(item) for item in obj]
    return obj


# ---- live inputs (real ingested corpora) -------------------------------


def _registry() -> SourceRegistry:
    return SourceRegistry(paths.source_registry_path)


def _collection_registry() -> CollectionRegistry:
    return CollectionRegistry(paths.collection_registry_path)


def _has_collection(collection: str) -> bool:
    reg = _registry()
    return bool(src.collection_records(reg, collection))


def _fixture_path(collection: str, name: str) -> Path:
    return BENCHMARK_DIR / slugify_collection(collection) / f"{name}.json"


def _check_or_refresh(collection: str, name: str, live: Any) -> None:
    """Either write the fixture (refresh mode) or assert live == frozen."""
    normalized = _normalize(live)
    path = _fixture_path(collection, name)
    if REFRESH:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        pytest.skip(f"refreshed fixture: {path.relative_to(BENCHMARK_DIR.parent.parent)}")
    if not path.exists():
        pytest.skip(f"missing fixture (run BENCHMARK_REFRESH=1): {path.name}")
    frozen = json.loads(path.read_text(encoding="utf-8"))
    assert normalized == frozen, (
        f"benchmark drift for {collection}/{name}. If intentional, regenerate with "
        f"BENCHMARK_REFRESH=1 and review the diff."
    )


def _skip_if_absent(collection: str) -> None:
    if not _has_collection(collection):
        pytest.skip(f"collection '{collection}' not present in this environment")


# ---- deterministic artifact benchmarks ---------------------------------


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_benchmark_inventory(collection: str) -> None:
    _skip_if_absent(collection)
    live = collections_mod.build_inventory(
        _registry(), collection, chunks_dir=paths.knowledge_chunks_path, collection_registry=_collection_registry()
    )
    _check_or_refresh(collection, "inventory", live)


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_benchmark_coverage(collection: str) -> None:
    _skip_if_absent(collection)
    live = coverage_mod.build_coverage(
        _registry(), collection, chunks_dir=paths.knowledge_chunks_path, collection_registry=_collection_registry()
    )
    _check_or_refresh(collection, "coverage", live)


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_benchmark_gaps(collection: str) -> None:
    _skip_if_absent(collection)
    live = gaps_mod.build_gaps(
        _registry(), collection, chunks_dir=paths.knowledge_chunks_path, collection_registry=_collection_registry()
    )
    _check_or_refresh(collection, "gaps", live)


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_benchmark_timeline(collection: str) -> None:
    _skip_if_absent(collection)
    live = timeline_mod.build_timeline(_registry(), collection, chunks_dir=paths.knowledge_chunks_path)
    _check_or_refresh(collection, "timeline", live)


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_benchmark_summary_extractive(collection: str) -> None:
    _skip_if_absent(collection)
    live = build_summary(
        _registry(), collection, kind=KIND_COLLECTION,
        chunks_dir=paths.knowledge_chunks_path, collection_registry=_collection_registry(),
        mode="deterministic",
    )
    # Extractive summary must be deterministic (never the LLM path).
    assert live.get("compute_used") == "extractive"
    _check_or_refresh(collection, "summary", live)


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_benchmark_provenance_summary(collection: str) -> None:
    _skip_if_absent(collection)
    service = KnowledgeProvenanceService(
        registry=_registry(), chunks_dir=paths.knowledge_chunks_path,
        vector_store=None, collection_registry=_collection_registry(),
    )
    prov_map = service.build_map(collection)
    summary = {
        "collection": prov_map.get("collection"),
        "domain": prov_map.get("domain"),
        "chunk_count": prov_map.get("chunk_count"),
        "sources": prov_map.get("sources", []),
    }
    _check_or_refresh(collection, "provenance_summary", summary)


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_benchmark_provenance_samples(collection: str) -> None:
    _skip_if_absent(collection)
    service = KnowledgeProvenanceService(
        registry=_registry(), chunks_dir=paths.knowledge_chunks_path,
        vector_store=None, collection_registry=_collection_registry(),
    )
    prov_map = service.build_map(collection)
    chunk_ids = sorted(prov_map.get("chunks", {}).keys())
    if not chunk_ids:
        _check_or_refresh(collection, "provenance_samples", {"samples": []})
        return
    # Deterministic sample: first, middle, last.
    sample_ids = sorted({chunk_ids[0], chunk_ids[len(chunk_ids) // 2], chunk_ids[-1]})
    traces: List[Dict[str, Any]] = []
    for cid in sample_ids:
        trace = service.for_chunk(cid)
        # text_preview can carry volatile whitespace; keep stable structural fields.
        traces.append({
            "chunk_id": trace.get("chunk_id"),
            "found": trace.get("found"),
            "collection": trace.get("collection"),
            "domain": trace.get("domain"),
            "source_file": trace.get("source_file"),
            "source_type": trace.get("source_type"),
            "section": trace.get("section"),
            "confidence": trace.get("confidence"),
        })
    _check_or_refresh(collection, "provenance_samples", {"samples": traces})


# ---- librarian benchmark (invariants only, never prose) ----------------


_VECTOR_STORE: Any = None
_VECTOR_STORE_FAILED = False


def _shared_vector_store():
    """Return a single, process-wide ChromaVectorStore.

    Chroma's PersistentClient does not support multiple instances on the same
    path within one process; creating one per test can corrupt the on-disk HNSW
    index. The benchmark therefore shares exactly one client across all
    librarian cases.
    """
    global _VECTOR_STORE, _VECTOR_STORE_FAILED
    if _VECTOR_STORE_FAILED:
        pytest.skip("vector store unavailable")
    if _VECTOR_STORE is None:
        from blackfong_ecosystem.storage import ChromaVectorStore

        try:
            _VECTOR_STORE = ChromaVectorStore(paths.vector_db_path)
        except Exception as exc:  # pragma: no cover - environment without Chroma
            _VECTOR_STORE_FAILED = True
            pytest.skip(f"vector store unavailable: {exc}")
    return _VECTOR_STORE


def _librarian_agent():
    from blackfong_ecosystem.knowledge_intel.librarian import LibrarianAgent

    return LibrarianAgent(
        registry=_registry(), vector_store=_shared_vector_store(),
        chunks_dir=paths.knowledge_chunks_path, collection_registry=_collection_registry(),
    )


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_benchmark_librarian(collection: str) -> None:
    _skip_if_absent(collection)
    slug = slugify_collection(collection)
    question = LIBRARIAN_QUESTIONS[collection]
    agent = _librarian_agent()
    record = agent.answer(question, collection=collection, top_k=3, mode="deterministic")

    # Invariant: deterministic structured-facts path (no model in the benchmark).
    assert record["used_llm"] is False
    # Invariant: non-empty answer grounded in retrieved passages.
    assert record["answer"].strip()
    # Invariant: retrieval returned provenance.
    assert record["retrieved_count"] >= 1
    assert record["provenance"]
    # Invariant: collection isolation + no cross-collection leakage.
    for prov in record["provenance"]:
        assert prov["collection"] == slug, f"cross-collection leakage: {prov['collection']} != {slug}"
        assert prov["chunk_id"]
        assert prov["source_file"]

    # Frozen invariant: retrieved chunk ids + provenance basenames (NOT the prose).
    frozen_view = {
        "question": question,
        "collection": slug,
        "retrieved_chunk_ids": [p["chunk_id"] for p in record["provenance"]],
        "provenance_sources": [
            {"collection": p["collection"], "source": _basename(p["source_file"])}
            for p in record["provenance"]
        ],
    }
    _check_or_refresh(collection, "librarian", frozen_view)
