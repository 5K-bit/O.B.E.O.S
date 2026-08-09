from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import blackfong_ecosystem.conversation_memory as conversation_memory_module
import blackfong_ecosystem.ecosystem_knowledge as ecosystem_knowledge_module
import blackfong_ecosystem.model_routing as model_routing_module
import pytest

from blackfong_ecosystem.chat_backends import ChatBackendError, ChatBackendResult, OllamaChatBackend
from blackfong_ecosystem.config import Paths
from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.daise import DAISE
from blackfong_ecosystem.ingestion.source_registry import SourceRegistry
from blackfong_ecosystem.knowledge_intel.collection_registry import CollectionRegistry
from blackfong_ecosystem.memory_chamber import MemoryChamberService
from blackfong_ecosystem.model_routing import OllamaRoutingChatBackend
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.service_registry import reset_ui_services_for_tests
from blackfong_ecosystem.storage import EventStore


class FakeVectorStore:
    def __init__(self) -> None:
        self.docs: Dict[str, Dict[str, Any]] = {}

    def add(
        self,
        ids: Iterable[str],
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        meta_list = list(metadatas or [])
        ids_list = list(ids)
        texts_list = list(texts)
        while len(meta_list) < len(ids_list):
            meta_list.append({})
        for item_id, text, metadata in zip(ids_list, texts_list, meta_list):
            self.docs[str(item_id)] = {"text": str(text), "metadata": dict(metadata or {})}

    def upsert(
        self,
        ids: Iterable[str],
        texts: Iterable[str],
        metadatas: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        self.add(ids, texts, metadatas)

    def search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        query_tokens = {token for token in query.lower().split() if token}
        ranked: List[Dict[str, Any]] = []
        for item_id, payload in self.docs.items():
            haystack = " ".join(
                [
                    payload["text"],
                    str(payload["metadata"].get("title") or ""),
                    str(payload["metadata"].get("source_path") or payload["metadata"].get("path") or ""),
                    str(payload["metadata"].get("collection") or ""),
                ]
            ).lower()
            overlap = sum(1 for token in query_tokens if token in haystack)
            if overlap <= 0:
                continue
            relevance = round(min(0.99, 0.45 + (overlap * 0.12)), 6)
            ranked.append(
                {
                    "id": item_id,
                    "text": payload["text"],
                    "metadata": dict(payload["metadata"]),
                    "distance": round(max(0.01, 1.0 - relevance), 6),
                    "relevance": relevance,
                }
            )
        ranked.sort(key=lambda item: item["relevance"], reverse=True)
        return ranked[:k]


class FakeJarvisAgent:
    def process(self, user_input, context=None):
        return {
            "response": "Jarvis used the unified retrieval stack.",
            "confidence": 0.88,
            "tools_invoked": [],
            "trace_event_id": "trace-retrieval-1",
            "task_type": "general_conversation",
            "provider": "heuristic",
            "model": "fake-jarvis",
            "backend_error": None,
            "backend_metadata": {"context_present": bool((context or {}).get("memory_context"))},
            "agent": "jarvis",
        }


def _seed_vector_store(vector_store: FakeVectorStore) -> None:
    vector_store.upsert(
        ids=["arch-1", "general-1"],
        texts=[
            "OBEOS architecture routes memory through DAISE, Thoughtglass, and the Memory Chamber graph.",
            "BlackBus decision notes and startup readiness live in bounded semantic memory retrieval.",
        ],
        metadatas=[
            {
                "collection": "obeos",
                "source_id": "arch-src",
                "source_path": "docs/architecture-memory.md",
                "path": "docs/architecture-memory.md",
                "doc_type": "markdown",
                "title": "Architecture Memory",
            },
            {
                "collection": "obeos",
                "source_id": "general-src",
                "source_path": "docs/decisions.md",
                "path": "docs/decisions.md",
                "doc_type": "markdown",
                "title": "Decision Notes",
            },
        ],
    )


def _build_memory(tmp_path: Path, vector_store: Optional[FakeVectorStore] = None) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    memory = ConversationMemoryService(event_store=event_store, vector_store=vector_store or FakeVectorStore())
    return memory


def _seed_workspace(tmp_path: Path) -> None:
    (tmp_path / "obeos_ecosystem.toml").write_text(
        """
[[projects]]
id = "docs"
path = "docs"
aliases = ["docs"]
roles = ["test_fixture"]
description = "Test fixture docs."
[projects.knowledge]
collection = "docs"
series = "Docs"
domain = "obeos_ecosystem"
category = "test"
description = "Test fixture docs."
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "architecture.md").write_text(
        "# OBEOS Architecture\n"
        "DAISE uses the Memory Chamber graph for deep retrieval and Thoughtglass for the operator surface.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "routing.md").write_text(
        "Routing contracts, queue handling, and semantic memory all belong to the same OBEOS cognition stack.\n",
        encoding="utf-8",
    )


def test_conversation_memory_retrieval_profiles_are_bounded(tmp_path: Path) -> None:
    vector_store = FakeVectorStore()
    _seed_vector_store(vector_store)
    memory = _build_memory(tmp_path, vector_store=vector_store)
    session_id = memory.create_session_id()

    fast_bundle = memory.build_context(
        session_id=session_id,
        query="how does OBEOS architecture routing work?",
        agent_name="daise",
        analysis_mode="fast",
        retrieval_profile="fast",
    )
    standard_bundle = memory.build_context(
        session_id=session_id,
        query="how does OBEOS architecture routing work?",
        agent_name="daise",
        analysis_mode="standard",
        retrieval_profile="standard",
    )
    architecture_bundle = memory.knowledge_search(
        query="architecture routing graph",
        retrieval_profile="architecture",
    )

    assert fast_bundle["knowledge_hits"] == []
    assert standard_bundle["knowledge_hits"]
    assert standard_bundle["citations"]
    assert standard_bundle["retrieval_profile"] == "standard"
    assert architecture_bundle["retrieval_profile"] == "architecture"
    assert architecture_bundle["citations"]
    assert all(
        "architecture" in str(item.get("source_path") or "").lower()
        or "architecture" in str(item.get("title") or "").lower()
        for item in architecture_bundle["citations"]
    )


def test_memory_chamber_reasoning_context_and_daise_share_profiles(tmp_path: Path) -> None:
    vector_store = FakeVectorStore()
    _seed_vector_store(vector_store)
    _seed_workspace(tmp_path)
    memory = _build_memory(tmp_path, vector_store=vector_store)
    chamber = MemoryChamberService(
        event_store=memory.event_store,
        vector_store=vector_store,
        workspace_root=tmp_path,
        start_watch=False,
    )
    chamber.request_reindex(reason="test", background=False)

    standard = chamber.reasoning_context("architecture routing memory graph", profile="standard")
    deep = chamber.reasoning_context("architecture routing memory graph", profile="deep")

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=vector_store,
        memory_service=memory,
        memory_chamber=chamber,
    )
    result = daise.process_request(
        user_input="explain the architecture routing graph",
        agents={"jarvis": FakeJarvisAgent()},
        context={"request_id": "retrieval-profile-1", "analysis_mode": "standard"},
    )
    retrieval_events = memory.event_store.list_events_by_type("memory_retrieval", limit=5)

    assert standard["retrieval_profile"] == "standard"
    assert standard["semantic_hits"]
    assert standard["neighborhoods"] == []
    assert deep["retrieval_profile"] == "deep"
    assert deep["citations"]
    assert deep["clusters"] == []
    assert deep["missing_links"] == []
    assert deep["reasoning_path"]["status"] == "disabled"
    assert deep["timing_ms"]["graph_enrichment"] >= 0.0
    assert result["selected_agent"] == "jarvis"
    assert retrieval_events[0].payload["retrieval_profile"] == "standard"
    assert retrieval_events[0].payload["knowledge_hits"] >= 1
    chamber.shutdown()


def test_daise_grounded_truth_answers_date_and_identity(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    date_result = daise.process_request(
        user_input="what's today's date?",
        agents={"jarvis": FakeJarvisAgent()},
        context={"request_id": "grounded-date-1", "analysis_mode": "standard"},
    )
    identity_result = daise.process_request(
        user_input="what does D.A.I.S.E stand for?",
        agents={"jarvis": FakeJarvisAgent()},
        context={"request_id": "grounded-identity-1", "analysis_mode": "standard"},
    )

    assert "Sunday, August 9, 2026" in date_result["response"]
    assert date_result["provider"] == "local_runtime_truth"
    assert "Direct Artificial Intelligence System Environment" in identity_result["response"]
    assert "Jarvis alias" not in identity_result["response"]


def test_standard_retrieval_prefers_collection_summary_for_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(conversation_memory_module, "paths", temp_paths)

    vector_store = FakeVectorStore()
    vector_store.upsert(
        ids=["spawn-1"],
        texts=["Spawn follows Al Simmons after a deal with Malebolgia returns him to Earth as Hellspawn."],
        metadatas=[
            {
                "collection": "spawn",
                "source_id": "spawn-src",
                "source_path": "spawn/issues_001_008.md",
                "title": "Spawn 1-8",
            }
        ],
    )
    memory = _build_memory(tmp_path, vector_store=vector_store)
    collection_registry = CollectionRegistry(temp_paths.collection_registry_path)
    collection_registry.upsert("spawn", domain="comics", description="Spawn comic universe")
    collection_registry.save()
    source_registry = SourceRegistry(temp_paths.source_registry_path)
    source_registry.register(
        source_type="markdown",
        path=tmp_path / "spawn-001.md",
        series="Spawn",
        issue_range="1-8",
        collection="spawn",
        domain="comics",
        compute_hash=False,
    )
    source_registry.save()
    summary_dir = temp_paths.knowledge_intel_path / "spawn"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "summary.json").write_text(
        '{"collection":"spawn","domain":"comics","kind":"collection_summary","text":"Spawn centers on Al Simmons, a murdered operative who returns as Hellspawn after bargaining with Malebolgia."}',
        encoding="utf-8",
    )

    bundle = memory.build_context(
        session_id=memory.create_session_id(),
        query="tell me about spawn",
        agent_name="daise",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    assert bundle["retrieval_profile"] == "standard"
    assert bundle["knowledge_hits"]
    assert bundle["citations"][0]["kind"] == "collection_summary"
    assert bundle["citations"][0]["collection"] == "spawn"
    assert "Al Simmons" in bundle["knowledge_context"]


def test_knowledge_routes_use_existing_memory_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_ui_services_for_tests()
    temp_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(conversation_memory_module, "paths", temp_paths)
    monkeypatch.setattr(ecosystem_knowledge_module, "paths", temp_paths)

    vector_store = FakeVectorStore()
    _seed_vector_store(vector_store)
    _seed_workspace(tmp_path)
    memory = _build_memory(tmp_path, vector_store=vector_store)
    chamber = MemoryChamberService(
        event_store=memory.event_store,
        vector_store=vector_store,
        workspace_root=tmp_path,
        start_watch=False,
    )
    chamber.request_reindex(reason="test", background=False)

    dashboard = WebDashboard(host="127.0.0.1", port=8096, memory_service=memory, orchestrator_instance=None)
    dashboard.memory_chamber = chamber
    dashboard.services.memory_chamber = chamber
    dashboard.operator_summary.memory_chamber = chamber
    dashboard.cognition_service.memory_chamber = chamber
    client = dashboard.app.test_client()

    status = client.get("/api/knowledge/status?ensure_ecosystem=false").get_json()
    sync = client.post("/api/knowledge/sync", json={"background": False, "max_files_per_root": 10}).get_json()
    search = client.post("/api/knowledge/search", json={"query": "architecture routing graph", "mode": "standard"}).get_json()
    context = client.post("/api/knowledge/context", json={"query": "architecture routing graph", "mode": "deep"}).get_json()
    bench = client.post("/api/knowledge/bench", json={"query": "architecture routing graph", "profiles": ["fast", "standard", "deep"]}).get_json()
    ingested = client.post(
        "/api/knowledge/ingest/text",
        json={
            "text": "EarthGlass architecture bridge lives inside the OBEOS ecosystem memory stack.",
            "title": "EarthGlass Note",
            "collection": "earthglass",
            "tags": ["earthglass", "architecture"],
        },
    ).get_json()

    assert status["status"] == "ok"
    assert status["sync_endpoint"] == "/api/knowledge/sync"
    assert status["sync"]["status"] in {"idle", "ok", "failed"}
    assert "ecosystem_sync" not in status
    assert sync["status"] == "ok"
    assert search["retrieval_profile"] == "standard"
    assert search["citations"]
    assert context["retrieval_profile"] == "deep"
    assert context["cognition_graph"] is not None
    assert len(bench["profiles"]) == 3
    assert ingested["status"] == "ok"
    chamber.shutdown()


def test_ecosystem_collection_sync_registers_governed_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(ecosystem_knowledge_module, "paths", temp_paths)

    (tmp_path / "obeos_ecosystem.toml").write_text(
        """
[[projects]]
id = "blackforge"
path = "Blackforge"
aliases = ["BlackForge"]
roles = ["project_scaffolder"]
description = "Blackforge test fixture."
[projects.knowledge]
collection = "blackforge"
series = "BlackForge"
domain = "obeos_ecosystem"
category = "tooling"
description = "Blackforge fixture collection."

[[projects]]
id = "earthglass"
path = "EarthGlass"
aliases = ["EarthGlass"]
roles = ["geospatial_dashboard"]
description = "EarthGlass test fixture."
[projects.knowledge]
collection = "earthglass"
series = "EarthGlass"
domain = "obeos_ecosystem"
category = "geospatial"
description = "EarthGlass fixture collection."
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "Blackforge").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Blackforge" / "README.md").write_text(
        "# Blackforge\nBlackforge shares architecture and tooling context with OBEOS.\n",
        encoding="utf-8",
    )
    (tmp_path / "EarthGlass").mkdir(parents=True, exist_ok=True)
    (tmp_path / "EarthGlass" / "notes.md").write_text(
        "# EarthGlass\nEarthGlass reflects the future architecture retrieval profile.\n",
        encoding="utf-8",
    )

    vector_store = FakeVectorStore()
    memory = _build_memory(tmp_path, vector_store=vector_store)
    result = ecosystem_knowledge_module.sync_governed_ecosystem_collections(
        memory_service=memory,
        workspace_root=tmp_path,
        max_files_per_root=10,
    )

    registry = SourceRegistry(temp_paths.source_registry_path)
    collection_registry = CollectionRegistry(temp_paths.collection_registry_path)

    assert result["status"] == "ok"
    assert any(item["collection"] == "blackforge" and item["available"] for item in result["collections"])
    assert any(record.resolved_collection() == "blackforge" for record in registry.all())
    assert collection_registry.domain_for("earthglass") == "obeos_ecosystem"
    assert vector_store.search("EarthGlass architecture", k=3)


def test_knowledge_runtime_hydrates_from_bundle_before_ecosystem_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(conversation_memory_module, "paths", temp_paths)
    monkeypatch.setattr(ecosystem_knowledge_module, "paths", temp_paths)

    bundle_root = temp_paths.knowledge_bundle_root
    (bundle_root / "chunks").mkdir(parents=True, exist_ok=True)
    (bundle_root / "intel" / "spawn").mkdir(parents=True, exist_ok=True)
    (bundle_root / "collections.json").write_text(
        json.dumps(
            {
                "version": 1,
                "collections": [
                    {
                        "collection": "spawn",
                        "domain": "comics",
                        "description": "Spawn comic universe",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_root / "source_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "source_id": "spawn-seed-1",
                        "source_type": "markdown",
                        "path": "portable://spawn/issues_001_008.md",
                        "series": "Spawn",
                        "issue_range": "1-8",
                        "role": "canonical",
                        "embedded": True,
                        "processing_status": "embedded",
                        "copyright_status": "private_reference_only",
                        "collection": "spawn",
                        "domain": "comics",
                        "checksum": "seed-checksum",
                        "chunk_count": 1,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_root / "chunks" / "spawn-seed-1.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "knowledge:spawn-seed-1:0",
                "source": "Spawn 1-8",
                "text": "Spawn follows Al Simmons after a bargain with Malebolgia returns him to Earth as Hellspawn.",
                "metadata": {
                    "source_id": "spawn-seed-1",
                    "collection": "spawn",
                    "domain": "comics",
                    "source_path": "portable://spawn/issues_001_008.md",
                    "title": "Spawn 1-8",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_root / "intel" / "spawn" / "summary.json").write_text(
        '{"collection":"spawn","kind":"collection_summary","text":"Spawn centers on Al Simmons."}',
        encoding="utf-8",
    )

    vector_store = FakeVectorStore()
    memory = _build_memory(tmp_path, vector_store=vector_store)

    result = memory.hydrate_knowledge_runtime(workspace_root=tmp_path, max_files_per_root=10)
    bundle = memory.build_context(
        session_id=memory.create_session_id(),
        query="tell me about spawn",
        agent_name="daise",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    assert result["status"] == "ok"
    assert result["bundle_import"]["status"] == "ok"
    assert result["bundle_import"]["imported_sources"] == 1
    assert memory.knowledge_status()["bundle_available"] is True
    assert bundle["knowledge_hits"]
    assert "Al Simmons" in bundle["knowledge_context"]


def test_model_routing_falls_back_after_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Dict[str, Any]] = []

    def fake_generate(self, messages, **options):
        calls.append({"model": options.get("model"), "timeout": options.get("timeout")})
        if options.get("model") == "slow-model":
            raise ChatBackendError(
                "Ollama request timed out after 90s for model slow-model",
                metadata={"error_type": "read_timeout"},
            )
        return ChatBackendResult(
            text="fallback succeeded",
            provider="ollama",
            model=str(options.get("model")),
            metadata={},
        )

    monkeypatch.setattr(OllamaChatBackend, "generate", fake_generate)

    backend = OllamaRoutingChatBackend(
        model="slow-model",
        fallback_models=("fast-model",),
        timeout=90.0,
        keep_alive="30m",
        num_ctx=4096,
        num_predict=256,
    )
    result = backend.generate([{"role": "user", "content": "tell me about spawn"}], mode="standard", timeout=90.0)

    assert [item["model"] for item in calls] == ["slow-model", "fast-model"]
    assert all(item["timeout"] == model_routing_module.STANDARD_CHAIN_TIMEOUT_CAP_S for item in calls)
    assert result.text == "fallback succeeded"
    assert result.metadata["selected_model"] == "fast-model"
