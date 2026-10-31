from __future__ import annotations

import sqlite3
from pathlib import Path

from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.daise import DAISE
from blackfong_ecosystem.memory_chamber import MemoryChamberService
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.service_registry import reset_ui_services_for_tests
from blackfong_ecosystem.storage import EventStore, NullVectorStore


def _build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


def _seed_workspace(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "notes" / "memory-chamber.md").write_text(
        "# Memory Chamber\n"
        "DAISE links graph clusters, missing links, thoughtglass, and operator memory.\n"
        "Tiny reflection and Mobray interrogation should appear in the same cognition graph.\n",
        encoding="utf-8",
    )
    (tmp_path / "notes" / "operators.txt").write_text(
        "Blackfong operator summary tracks queue health, files, sessions, and cognition overlays.\n",
        encoding="utf-8",
    )
    (tmp_path / "logs" / "tiny.log").write_text(
        "tinyprogrammer reflection created program artifact mood decision\n",
        encoding="utf-8",
    )
    (tmp_path / "trace.py").write_text(
        "from core.orchestrator import start\n"
        "from brain.memory import chamber\n"
        "class MemoryGraph: pass\n"
        "def trace_reasoning_path():\n"
        "    return chamber\n",
        encoding="utf-8",
    )


class FakeJarvisAgent:
    def process(self, user_input, context=None):
        return {
            "response": "Jarvis saw the graph context.",
            "confidence": 0.89,
            "tools_invoked": [],
            "trace_event_id": "trace-graph-1",
            "task_type": "general_conversation",
            "provider": "heuristic",
            "model": "graph-aware",
            "backend_error": None,
            "backend_metadata": {"prompt_metrics": {"prompt_mode": "quick_compact"}},
            "agent": "jarvis",
            "graph_context_present": bool((context or {}).get("memory_context", {}).get("cognition_graph")),
        }


def test_memory_chamber_reindex_builds_unified_graph(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    memory = _build_memory(tmp_path)
    memory.persist_workflow_event(
        source="daise",
        event_type="reasoning_trace",
        payload={"summary": "DAISE connected Tiny and Memory Chamber."},
        session_id="session-1",
        request_id="req-1",
    )
    memory.persist_workflow_event(
        source="tinyprogrammer",
        event_type="tinyprogrammer_reflection",
        payload={"reflection": "Tiny reflected on a new graph artifact."},
        session_id="session-1",
        request_id="req-1",
    )
    chamber = MemoryChamberService(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        workspace_root=tmp_path,
        start_watch=False,
    )

    result = chamber.request_reindex(reason="test", background=False)
    summary = chamber.summary()

    assert result["status"] == "ok"
    assert summary["counts"]["documents"] >= 4
    assert summary["counts"]["nodes"] > 0
    assert summary["counts"]["edges"] > 0
    assert chamber.top_concepts(limit=6)
    assert chamber.neighborhood("memory", depth=1, limit=10)["nodes"]
    assert chamber.clusters(limit=4)
    assert chamber.recent_deltas(limit=2)
    cluster_map = chamber.graph(mode="memory_chamber")["graph"]
    live_brain = chamber.graph(mode="live_brain")["graph"]

    assert cluster_map["nodes"]
    assert cluster_map["mode"] == "cluster_map"
    assert live_brain["mode"] == "live_brain"
    assert len(live_brain["visible_nodes"]) <= 75
    visible_ids = {str(node["id"]) for node in live_brain["visible_nodes"]}
    assert {"anchor:blackfong", "anchor:daise", "anchor:user", "anchor:memory"}.issubset(visible_ids)
    with sqlite3.connect(tmp_path / "events.db") as conn:
        stored_anchor = conn.execute(
            "SELECT id FROM memory_chamber_nodes WHERE id LIKE 'anchor:%' LIMIT 1"
        ).fetchone()
    assert stored_anchor is None
    chamber.shutdown()


def test_memory_chamber_agent_write_scope_is_bounded(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    chamber = MemoryChamberService(
        event_store=EventStore(tmp_path / "events.db"),
        vector_store=NullVectorStore(),
        workspace_root=tmp_path,
        start_watch=False,
    )

    denied = chamber.append_agent_artifact(
        agent_name="tinyprogrammer",
        artifact_type="tiny_program",
        content="print('hi')",
        scope="graph_write_scope",
    )
    allowed = chamber.append_agent_artifact(
        agent_name="tinyprogrammer",
        artifact_type="tiny_program",
        content="print('hi')",
        scope="agent_write_scope",
    )

    assert denied["status"] == "forbidden"
    assert allowed["status"] == "ok"
    chamber.shutdown()


def test_memory_chamber_restores_ready_state_from_persisted_graph(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    memory = _build_memory(tmp_path)
    chamber = MemoryChamberService(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        workspace_root=tmp_path,
        start_watch=False,
    )
    chamber.request_reindex(reason="test", background=False)
    chamber.shutdown()

    restored = MemoryChamberService(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        workspace_root=tmp_path,
        start_watch=False,
    )
    summary = restored.summary()

    assert summary["state"] == "ready"
    assert summary["counts"]["nodes"] > 0
    assert summary["graph_cache_state"] in {"warming", "ready"}
    restored.shutdown()


def test_memory_chamber_routes_and_daise_graph_context(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    reset_ui_services_for_tests()
    memory = _build_memory(tmp_path)
    chamber = MemoryChamberService(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        workspace_root=tmp_path,
        start_watch=False,
    )
    chamber.request_reindex(reason="test", background=False)

    dashboard = WebDashboard(host="127.0.0.1", port=8097, memory_service=memory, orchestrator_instance=None)
    dashboard.memory_chamber = chamber
    dashboard.services.memory_chamber = chamber
    dashboard.operator_summary.memory_chamber = chamber
    dashboard.cognition_service.memory_chamber = chamber
    client = dashboard.app.test_client()

    summary = client.get("/api/memory-chamber/summary").get_json()
    graph = client.get("/api/memory-chamber/graph?mode=memory_chamber").get_json()
    snapshot = client.get("/api/cognition/snapshot").get_json()

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        memory_service=memory,
        memory_chamber=chamber,
    )
    result = daise.process_request(
        user_input="how are Tiny and Memory Chamber related?",
        agents={"jarvis": FakeJarvisAgent()},
        context={"request_id": "graph-request-1", "analysis_mode": "deep"},
    )
    retrieval_events = memory.event_store.list_events_by_type("memory_retrieval", limit=5)

    assert summary["counts"]["nodes"] > 0
    assert graph["graph"]["nodes"]
    assert snapshot["overlays"]["memory_chamber"]["counts"]["nodes"] > 0
    assert result["selected_agent"] == "daise"
    assert retrieval_events
    assert retrieval_events[0].payload["retrieval_profile"] == "deep"
    assert retrieval_events[0].payload["graph_neighborhoods"]["jarvis"]["matched_nodes"]
    chamber.shutdown()
