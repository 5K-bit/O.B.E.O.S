from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import blackfong_ecosystem.agents.jarvis_agent as jarvis_agent_module
import blackfong_ecosystem.conversation_memory as conversation_memory_module
import blackfong_ecosystem.ecosystem_knowledge as ecosystem_knowledge_module
import blackfong_ecosystem.model_routing as model_routing_module
import pytest

from blackfong_ecosystem.agents.jarvis_agent import JARVISAgent
from blackfong_ecosystem.chat_backends import ChatBackendError, ChatBackendResult, HeuristicChatBackend, OllamaChatBackend
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
from blackfong_ecosystem.system_awareness import SystemAwarenessService


class FakeVectorStore:
    def __init__(self) -> None:
        self.docs: Dict[str, Dict[str, Any]] = {}
        self.search_calls: List[Dict[str, Any]] = []

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

    def search(
        self,
        query: str,
        k: int = 5,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        self.search_calls.append(
            {
                "query": query,
                "k": k,
                "metadata_filter": dict(metadata_filter or {}),
            }
        )
        query_tokens = {token for token in query.lower().split() if token}
        ranked: List[Dict[str, Any]] = []
        for item_id, payload in self.docs.items():
            if metadata_filter:
                metadata = payload["metadata"]
                if any(metadata.get(key) != value for key, value in metadata_filter.items()):
                    continue
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
        context={"request_id": "retrieval-profile-1", "analysis_mode": "standard", "transport": "telegram"},
    )
    retrieval_events = memory.event_store.list_events_by_type("memory_retrieval", limit=5)
    outcome_events = memory.event_store.list_events_by_type("request_outcome", limit=5)
    trace = memory.get_request_trace("retrieval-profile-1")

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
    assert retrieval_events[0].payload["schema_version"] == "1.0"
    assert retrieval_events[0].payload["retrieval_profile"] == "standard"
    assert retrieval_events[0].payload["transport"] == "telegram"
    assert retrieval_events[0].payload["knowledge_hits"] >= 1
    assert outcome_events[0].payload["retrieval_profile"] == "standard"
    assert outcome_events[0].payload["provider"] == "heuristic"
    assert outcome_events[0].payload["transport"] == "telegram"
    assert trace["memory_retrieval"]["retrieval_profile"] == "standard"
    assert trace["memory_retrieval_observation"]["transport"] == "telegram"
    assert trace["request_outcome"]["provider"] == "heuristic"
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
    remembered_name_result = daise.process_request(
        user_input="do you remember your name?",
        agents={"jarvis": FakeJarvisAgent()},
        context={"request_id": "grounded-identity-2", "analysis_mode": "standard"},
    )
    grounded_outcomes = memory.event_store.list_events_by_type("request_outcome", limit=5)
    trace = memory.get_request_trace("grounded-identity-2")
    expected_date = f"{datetime.now().astimezone().strftime('%A, %B')} {datetime.now().astimezone().day}, {datetime.now().astimezone().year}"

    assert expected_date in date_result["response"]
    assert date_result["provider"] == "local_runtime_truth"
    assert "Direct Artificial Intelligence System Environment" in identity_result["response"]
    assert "Jarvis alias" not in identity_result["response"]
    assert remembered_name_result["provider"] == "local_runtime_truth"
    assert "I am D.A.I.S.E." in remembered_name_result["response"]
    assert grounded_outcomes[0].payload["provider"] == "local_runtime_truth"
    assert grounded_outcomes[0].payload["reason"] == "grounded_truth"
    assert trace["request_outcome"]["provider"] == "local_runtime_truth"
    assert trace["request_outcome"]["grounded_truth_kind"] == "identity"


def test_daise_grounded_truth_respects_runtime_clock_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLACKFONG_RUNTIME_TRUTH_NOW", "2026-08-10T09:15:00-04:00")
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    date_result = daise.process_request(
        user_input="what's today's date?",
        agents={"jarvis": FakeJarvisAgent()},
        context={"request_id": "grounded-date-override-1", "analysis_mode": "standard"},
    )

    assert "Monday, August 10, 2026" in date_result["response"]
    assert date_result["provider"] == "local_runtime_truth"
    assert date_result["backend_metadata"]["grounded_truth"]["clock_source"] == "runtime_truth_override"


def test_daise_grounded_truth_handles_greeting_and_tinyprogrammer_status(tmp_path: Path) -> None:
    class FakeTruthTinyProgrammer:
        def get_status(self, require_stream: bool = True) -> Dict[str, Any]:
            return {
                "online": False,
                "mode": "offline_snapshot",
                "state": "offline",
                "stream_enabled": False,
                "stream_required": True,
                "stream_human_reason": "TinyProgrammer timed out while waiting for the local model backend.",
                "last_error_summary": "TinyProgrammer timed out while waiting for the local model backend.",
            }

    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". We are working on TinyProgrammer startup and HUD notifications.',
    )
    awareness = SystemAwarenessService(
        memory_service=memory,
        tinyprogrammer=FakeTruthTinyProgrammer(),
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
        awareness_service=awareness,
    )

    greeting_result = daise.process_request(
        user_input="good evening",
        agents={"jarvis": FakeJarvisAgent()},
        context={
            "request_id": "grounded-greeting-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    tiny_result = daise.process_request(
        user_input="would you happen to know why tinyprogrammer isnt working online?",
        agents={"jarvis": FakeJarvisAgent()},
        context={
            "request_id": "grounded-tiny-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    greeting_trace = memory.get_request_trace("grounded-greeting-1")
    tiny_trace = memory.get_request_trace("grounded-tiny-1")

    assert greeting_result["provider"] == "local_runtime_truth"
    assert "Good evening, Five." in greeting_result["response"]
    assert "TinyProgrammer startup" in greeting_result["response"] or "HUD notifications" in greeting_result["response"]
    assert tiny_result["provider"] == "local_runtime_truth"
    assert "TinyProgrammer is not online right now." in tiny_result["response"]
    assert "timed out while waiting for the local model backend" in tiny_result["response"]
    assert greeting_trace["request_outcome"]["grounded_truth_kind"] == "greeting"
    assert tiny_trace["request_outcome"]["grounded_truth_kind"] == "tinyprogrammer_status"


def test_daise_grounded_truth_handles_return_greeting(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". We are working on HUD layout and response latency.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="hello, we are back",
        agents={"jarvis": FakeJarvisAgent()},
        context={
            "request_id": "grounded-return-greeting-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-return-greeting-1")

    assert result["provider"] == "local_runtime_truth"
    assert "Welcome back, Five." in result["response"]
    assert "HUD layout" in result["response"] or "response latency" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "greeting"


def test_daise_grounded_truth_handles_checkin_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". We are working on response latency and Spawn knowledge recall.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="how are you doing tonight?",
        agents={"jarvis": FakeJarvisAgent()},
        context={
            "request_id": "grounded-checkin-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-checkin-1")

    assert result["provider"] == "local_runtime_truth"
    assert "I'm here, Five, and operating normally inside O.B.E.O.S." in result["response"]
    assert "response latency" in result["response"] or "Spawn knowledge recall" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "check_in"


def test_daise_grounded_memory_handles_current_work_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, Spawn knowledge recall, and Telegram grounding.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    specialist = JARVISAgent(
        chat_backend=HeuristicChatBackend(model="grounded-memory-test"),
        fallback_backend=HeuristicChatBackend(model="grounded-memory-fallback"),
    )

    result = daise.process_request(
        user_input="what are we working on?",
        agents={"jarvis": specialist},
        context={
            "request_id": "grounded-work-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-work-1")

    assert result["provider"] == "conversation_memory"
    assert "Where we left off, we were working on" in result["response"]
    assert "response latency" in result["response"]
    assert trace["request_outcome"]["provider"] == "conversation_memory"


def test_daise_tinyprogrammer_truth_does_not_hijack_plain_work_update(tmp_path: Path) -> None:
    class FakeTruthTinyProgrammer:
        def get_status(self, require_stream: bool = True) -> Dict[str, Any]:
            return {
                "online": False,
                "mode": "offline_snapshot",
                "state": "offline",
                "last_error_summary": "TinyProgrammer failed during managed launch.",
            }

    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". We are working on TinyProgrammer startup and response latency.',
    )
    awareness = SystemAwarenessService(
        memory_service=memory,
        tinyprogrammer=FakeTruthTinyProgrammer(),
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
        awareness_service=awareness,
    )
    specialist = JARVISAgent(
        chat_backend=HeuristicChatBackend(model="plain-update-test"),
        fallback_backend=HeuristicChatBackend(model="plain-update-fallback"),
    )

    result = daise.process_request(
        user_input="good evening, i am still working on updates to your system as to tinyprogrammer also",
        agents={"jarvis": specialist},
        context={
            "request_id": "grounded-update-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    assert result["provider"] == "local_runtime_truth"
    assert "Good evening, Five." in result["response"]
    assert "TinyProgrammer updates" in result["response"] or "TinyProgrammer startup" in result["response"]
    assert "TinyProgrammer is offline right now." not in result["response"]


def test_daise_grounded_memory_progress_acknowledges_improvement(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency and OBEOS memory system.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input=(
            "ive made a good amount of changes and they should reflect. "
            "i noticed when i ask you what your name is you remembered it. "
            "i wanted to congratulate you on that milestone."
        ),
        agents={"jarvis": FakeJarvisAgent()},
        context={
            "request_id": "grounded-memory-progress-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-memory-progress-1")

    assert result["provider"] == "local_runtime_truth"
    assert "Yes, I can see the difference." in result["response"]
    assert 'call you "Five"' in result["response"]
    assert "shared O.B.E.O.S memory layer" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "memory_progress"


def test_daise_grounded_memory_progress_handles_identity_plus_change_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on OBEOS improvements and OBEOS memory system.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input=(
            "do you remember your name? i told it to you before. "
            "also, are you able to tell me what changed? i have improved the ecosystem alot"
        ),
        agents={"jarvis": FakeJarvisAgent()},
        context={
            "request_id": "grounded-memory-progress-identity-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-memory-progress-identity-1")

    assert result["provider"] == "local_runtime_truth"
    assert "I can also carry forward that I am D.A.I.S.E." in result["response"]
    assert "OBEOS improvements" in result["response"] or "OBEOS memory system" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "memory_progress"


def test_daise_grounded_truth_handles_chat_rendering_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". We are working on chat rendering and HUD layout.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="i think your text is being cut off when you reply. what do you think the cause of that is?",
        agents={"jarvis": FakeJarvisAgent()},
        context={
            "request_id": "grounded-chat-rendering-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-chat-rendering-1")

    assert result["provider"] == "local_runtime_truth"
    assert "active chat surface" in result["response"]
    assert "renderer" in result["response"] or "layout problem" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "chat_rendering"


def test_daise_grounded_memory_system_explains_note_to_self(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on OBEOS memory system.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input=(
            "how did you add a note to yourself? how does that work? "
            "can you explain this?"
        ),
        agents={"jarvis": FakeJarvisAgent()},
        context={
            "request_id": "grounded-memory-system-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-memory-system-1")

    assert result["provider"] == "local_runtime_truth"
    assert "shared conversation memory layer backed by events.db" in result["response"]
    assert "operator facts, active work items, pinned memory, and session summaries" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "memory_system"


def test_conversation_memory_general_recall_skips_question_shaped_topics(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency and Spawn knowledge recall.',
    )
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="what's my name?",
    )
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="tell me about Spawn",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what do you remember?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )
    grounded = bundle["grounded_memory_answer"]

    assert grounded
    assert "what's my name?" not in grounded["answer"].lower()
    assert "tell me about spawn" not in grounded["answer"].lower()


def test_conversation_memory_recall_skips_reentry_greetings_and_generic_fallbacks(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on chat rendering and response latency.',
    )
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="hello, we are back",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="D.A.I.S.E is online locally. I can keep session history, recall pinned memory, and route work through the rest of the ecosystem.",
        confidence=0.6,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what do you remember?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )
    grounded = bundle["grounded_memory_answer"]

    assert grounded is not None
    assert "hello, we are back" not in grounded["answer"].lower()
    assert "d.a.i.s.e is online locally" not in grounded["answer"].lower()
    assert "last assistant reply before this" not in grounded["answer"].lower()
    assert "chat rendering" in grounded["answer"] or "response latency" in grounded["answer"]


def test_conversation_memory_builds_grounded_recall_answer_from_history(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="You are D.A.I.S.E. Direct Artificial Intelligence System Environment.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="Understood. I am D.A.I.S.E inside O.B.E.O.S.",
        confidence=0.9,
    )
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='I am your creator, and you can call me "Five". I am working on TinyProgrammer integration and response latency.',
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I will keep that context in mind while we work on TinyProgrammer and latency.",
        confidence=0.9,
    )
    memory.maybe_create_session_summary(session_id, correlation_id=correlation_id, force=True)

    bundle = memory.build_context(
        session_id=session_id,
        query="what do you remember about me?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_memory_answer"]
    assert grounded is not None
    assert grounded["kind"] == "operator"
    assert "creator of O.B.E.O.S" in grounded["answer"]
    assert 'call you "Five"' in grounded["answer"]
    assert "TinyProgrammer integration" in grounded["answer"] or "response latency" in grounded["answer"]
    assert bundle["operator_profile"]["preferred_name"] == "Five"
    assert bundle["operator_profile"]["operator_role"] == "creator of O.B.E.O.S"
    assert "Preferred operator name: Five" in bundle["prompt_context"]


def test_conversation_memory_prefers_current_session_work_over_older_global_work(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_workflow_event(
        source="daise",
        event_type="knowledge_ingested",
        payload={
            "memory_kind": "active_work_item",
            "text": "The operator is working on response latency, HUD layout improvements, and OBEOS file visibility.",
        },
        session_id=session_id,
        correlation_id=correlation_id,
        request_id="older-work-1",
    )
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, HUD layout improvements, and Telegram grounding.',
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what do you remember?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )
    grounded = bundle["grounded_memory_answer"]

    assert grounded is not None
    assert "Telegram grounding" in grounded["answer"]


def test_conversation_memory_filters_cross_session_active_work_from_grounded_recall(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    older_session = memory.create_session_id()
    older_correlation = memory.create_correlation_id()
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_workflow_event(
        source="daise",
        event_type="knowledge_ingested",
        payload={
            "memory_kind": "active_work_item",
            "text": "The operator is working on Spawn knowledge recall and comic ingestion.",
        },
        session_id=older_session,
        correlation_id=older_correlation,
        request_id="older-session-work",
    )
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on Telegram grounding, response latency, and semantic knowledge recall.',
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what do you remember?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_memory_answer"]
    profile = bundle["operator_profile"]

    assert grounded is not None
    assert "Telegram grounding" in grounded["answer"]
    assert "Spawn knowledge recall" not in grounded["answer"]
    assert "Spawn knowledge recall" not in profile["active_work"]
    assert "Telegram grounding" in profile["active_work"]


def test_build_context_surfaces_recent_backend_health_by_transport(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_workflow_event(
        source="daise",
        event_type="latency_observation",
        payload={
            "transport": "telegram",
            "provider": "heuristic",
            "model": "fallback-heuristic",
            "execution_time_ms": 21000.0,
            "backend_error": "Ollama request timed out after 18s for model llama3.2:3b",
            "fallback_reason": "Ollama request timed out after 18s for model llama3.2:3b",
            "fallback_used": True,
        },
        session_id=session_id,
        correlation_id=correlation_id,
        request_id="backend-health-1",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="tell me about Spawn",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
        transport="telegram",
    )

    backend_health = bundle["backend_health"]
    assert backend_health["state"] == "degraded"
    assert backend_health["transport"] == "telegram"
    assert backend_health["recent_timeout_count"] == 1
    assert "timed out" in backend_health["last_backend_error"].lower()


def test_grounded_memory_recall_skips_semantic_vector_search(tmp_path: Path) -> None:
    vector_store = FakeVectorStore()
    _seed_vector_store(vector_store)
    memory = _build_memory(tmp_path, vector_store=vector_store)
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, HUD layout improvements, and Telegram grounding.',
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what do you remember?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    assert bundle["grounded_memory_answer"] is not None
    assert bundle["knowledge_hits"] == []
    assert bundle["timing_ms"]["vector_query"] == 0.0
    assert all(call["query"] != "what do you remember?" for call in vector_store.search_calls)


def test_conversation_memory_recalls_operator_name_from_grounded_profile(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='You are D.A.I.S.E. and I am your creator. Call me "Five".',
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="Understood. I will call you Five.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what's my name?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_memory_answer"]
    assert grounded is not None
    assert grounded["kind"] == "operator"
    assert 'call you "Five"' in grounded["answer"]


def test_daise_uses_grounded_memory_answer_before_ollama(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on TinyProgrammer startup, chat rendering, and response latency.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can help continue the TinyProgrammer, chat rendering, and latency work.",
        confidence=0.9,
    )
    memory.maybe_create_session_summary(session_id, correlation_id=correlation_id, force=True)

    def _should_not_reach_ollama(*args, **kwargs):
        raise AssertionError("grounded memory answers should not invoke the routed Ollama backend")

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", _should_not_reach_ollama)

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    agent = JARVISAgent(chat_backend=HeuristicChatBackend(), fallback_backend=HeuristicChatBackend())

    result = daise.process_request(
        user_input="where did we leave off?",
        agents={"jarvis": agent},
        context={"request_id": "grounded-memory-1", "analysis_mode": "standard", "session_id": session_id},
    )

    assert result["provider"] == "conversation_memory"
    assert result["model"] == "deterministic-memory-grounded"
    assert "Where we left off" in result["response"]
    assert "TinyProgrammer startup" in result["response"] or "chat rendering" in result["response"]


def test_grounded_memory_skips_low_value_assistant_snapshots(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="I am your creator and I am working on TinyProgrammer startup and HUD notifications.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="This appears to be a test message. Would you like to proceed with a legitimate question?",
        confidence=0.9,
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can help continue the TinyProgrammer startup and HUD work.",
        confidence=0.9,
    )
    memory.maybe_create_session_summary(session_id, correlation_id=correlation_id, force=True)

    bundle = memory.build_context(
        session_id=session_id,
        query="where did we leave off?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_memory_answer"]
    assert grounded is not None
    assert "test message" not in grounded["answer"].lower()
    assert "TinyProgrammer startup" in grounded["answer"] or "HUD notifications" in grounded["answer"]


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
    assert "Representative passages:" not in bundle["knowledge_context"]
    assert len(bundle["citations"]) == 1
    assert bundle["grounded_knowledge_answer"]["collection"] == "spawn"
    assert "Spawn centers on Al Simmons" in bundle["grounded_knowledge_answer"]["answer"]
    assert "registered source" not in bundle["grounded_knowledge_answer"]["answer"]


def test_standard_retrieval_rescues_hinted_collection_without_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(conversation_memory_module, "paths", temp_paths)

    vector_store = FakeVectorStore()
    vector_store.upsert(
        ids=["memory-doc-1", "spawn-1"],
        texts=[
            "Spawn startup test coverage for the dashboard and memory chamber.",
            "Spawn follows Al Simmons after bargaining with Malebolgia and returning to Earth as Hellspawn.",
        ],
        metadatas=[
            {
                "path": "test_all_phases.py",
                "source_id": "doc:test-all-phases",
                "title": "All phases test",
            },
            {
                "collection": "spawn",
                "source_id": "spawn-src",
                "source_path": "spawn/issues_001_008.md",
                "title": "Spawn 1-8",
            },
        ],
    )
    memory = _build_memory(tmp_path, vector_store=vector_store)
    collection_registry = CollectionRegistry(temp_paths.collection_registry_path)
    collection_registry.upsert("spawn", domain="comics", description="Spawn comic universe and related series")
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
    intel_dir = temp_paths.knowledge_intel_path / "spawn"
    intel_dir.mkdir(parents=True, exist_ok=True)
    (intel_dir / "inventory.json").write_text(
        json.dumps(
            {
                "collection": "spawn",
                "domain": "comics",
                "sources": 1,
                "embedded": 1,
                "total_chunks": 1,
                "issue_ranges": ["1-8"],
            }
        ),
        encoding="utf-8",
    )
    (intel_dir / "timeline.json").write_text(
        json.dumps({"collection": "spawn", "event_count": 1}),
        encoding="utf-8",
    )

    bundle = memory.build_context(
        session_id=memory.create_session_id(),
        query="tell me about spawn",
        agent_name="daise",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    assert bundle["citations"][0]["collection"] == "spawn"
    assert bundle["citations"][0]["kind"] == "collection_summary"
    assert "Spawn comic universe and related series" in bundle["knowledge_context"]
    assert "Al Simmons" in bundle["knowledge_context"]
    assert "Representative passages:" not in bundle["knowledge_context"]
    assert len(bundle["citations"]) == 1


def test_jarvis_uses_grounded_collection_answer_before_ollama(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(conversation_memory_module, "paths", temp_paths)

    vector_store = FakeVectorStore()
    vector_store.upsert(
        ids=["spawn-1", "spawn-2"],
        texts=[
            "Spawn centers on Al Simmons, who returns to Earth as Hellspawn after bargaining with Malebolgia.",
            "Wanda, Terry, and the grim street-level atmosphere define the early Spawn issues.",
        ],
        metadatas=[
            {
                "collection": "spawn",
                "source_id": "spawn-src-1",
                "source_path": "spawn/issues_001_008.md",
                "title": "Spawn 1-8",
            },
            {
                "collection": "spawn",
                "source_id": "spawn-src-2",
                "source_path": "spawn/issues_009_017.md",
                "title": "Spawn 9-17",
            },
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
        '{"collection":"spawn","domain":"comics","kind":"collection_summary","text":"Spawn follows Al Simmons after a bargain with Malebolgia returns him to Earth as Hellspawn."}',
        encoding="utf-8",
    )

    bundle = memory.build_context(
        session_id=memory.create_session_id(),
        query="tell me about spawn",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    assert bundle["grounded_knowledge_answer"] is not None

    def _should_not_reach_ollama(*args, **kwargs):
        raise AssertionError("collection-grounded answers should not invoke the routed Ollama backend")

    agent = JARVISAgent(chat_backend=HeuristicChatBackend(), fallback_backend=HeuristicChatBackend())
    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", _should_not_reach_ollama)

    result = agent.process(
        "tell me about spawn",
        context={"analysis_mode": "standard", "memory_context": bundle},
    )

    assert result["provider"] == "knowledge_librarian"
    assert result["model"] == "deterministic-collection-grounded"
    assert "Spawn follows Al Simmons" in result["response"] or "Spawn centers on Al Simmons" in result["response"]
    assert "Al Simmons" in result["response"]


def test_grounded_collection_answer_surfaces_coverage_only_when_requested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(conversation_memory_module, "paths", temp_paths)

    vector_store = FakeVectorStore()
    vector_store.upsert(
        ids=["spawn-1"],
        texts=["Spawn follows Al Simmons after a bargain with Malebolgia returns him to Earth as Hellspawn."],
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
        json.dumps(
            {
                "collection": "spawn",
                "domain": "comics",
                "kind": "collection_summary",
                "text": "Spawn centers on Al Simmons, a murdered operative who returns as Hellspawn after bargaining with Malebolgia.",
                "sources": 3,
                "embedded": 2,
                "total_chunks": 22,
                "issue_ranges": ["1-8", "9-17"],
            }
        ),
        encoding="utf-8",
    )

    overview = memory.grounded_collection_answer(
        query="tell me about spawn",
        retrieval_profile="standard",
        analysis_mode="standard",
    )
    coverage = memory.grounded_collection_answer(
        query="how much do you know about spawn and what issues are covered?",
        retrieval_profile="standard",
        analysis_mode="standard",
    )

    assert overview is not None
    assert coverage is not None
    assert "registered source" not in overview["answer"]
    assert "issue ranges" not in overview["answer"]
    assert "registered source" in coverage["answer"]
    assert "issue ranges" in coverage["answer"]


def test_grounded_collection_answer_gives_obeos_identity_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(conversation_memory_module, "paths", temp_paths)

    vector_store = FakeVectorStore()
    vector_store.upsert(
        ids=["obeos-1", "obeos-2"],
        texts=[
            (
                "O.B.E.O.S. is the Official Blackfong Environment Operating System: "
                "a sovereign, offline-first intelligence environment built around one governing spine, "
                "one shared memory system, and multiple operator surfaces."
            ),
            (
                "Blackfong is the governing spine, D.A.I.S.E. is the visible assistant, "
                "Thoughtglass is the live cognition surface, and Memory Chamber is the deeper workbench."
            ),
        ],
        metadatas=[
            {
                "collection": "obeos",
                "source_id": "obeos-readme",
                "source_path": "README.md",
                "title": "O.B.E.O.S. Overview",
            },
            {
                "collection": "obeos",
                "source_id": "obeos-guide",
                "source_path": "OBEOS user guide.md",
                "title": "O.B.E.O.S. User Guide",
            },
        ],
    )
    memory = _build_memory(tmp_path, vector_store=vector_store)
    collection_registry = CollectionRegistry(temp_paths.collection_registry_path)
    collection_registry.upsert("obeos", domain="architecture", description="OBEOS authored project + system documentation")
    collection_registry.save()
    source_registry = SourceRegistry(temp_paths.source_registry_path)
    source_registry.register(
        source_type="markdown",
        path=tmp_path / "README.md",
        series="OBEOS",
        issue_range="overview",
        collection="obeos",
        domain="architecture",
        compute_hash=False,
    )
    source_registry.save()
    intel_dir = temp_paths.knowledge_intel_path / "obeos"
    intel_dir.mkdir(parents=True, exist_ok=True)
    (intel_dir / "inventory.json").write_text(
        json.dumps(
            {
                "collection": "obeos",
                "domain": "architecture",
                "sources": 2,
                "embedded": 2,
                "total_chunks": 2,
            }
        ),
        encoding="utf-8",
    )
    (intel_dir / "coverage.json").write_text(
        json.dumps(
            {
                "collection": "obeos",
                "domain": "architecture",
                "embedded_sources": 2,
                "total_chunks": 2,
            }
        ),
        encoding="utf-8",
    )

    answer = memory.grounded_collection_answer(
        query="what is obeos?",
        retrieval_profile="standard",
        analysis_mode="standard",
    )

    assert answer is not None
    assert "Official Blackfong Environment Operating System" in answer["answer"]
    assert "offline-first intelligence environment" in answer["answer"]
    assert "one governing spine" in answer["answer"]
    assert "covered in local O.B.E.O.S. knowledge around" not in answer["answer"]


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
    context = client.post(
        "/api/knowledge/context",
        json={"query": "architecture routing graph", "mode": "deep", "request_id": "knowledge-context-1"},
    ).get_json()
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
    assert context["request_id"] == "knowledge-context-1"
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


def test_model_routing_caps_single_model_timeout_by_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Dict[str, Any]] = []

    def fake_generate(self, messages, **options):
        calls.append({"model": options.get("model"), "timeout": options.get("timeout"), "mode": options.get("mode")})
        return ChatBackendResult(
            text="ok",
            provider="ollama",
            model=str(options.get("model")),
            metadata={},
        )

    monkeypatch.setattr(OllamaChatBackend, "generate", fake_generate)

    backend = OllamaRoutingChatBackend(
        model="llama3.2:3b",
        fallback_models=(),
        timeout=90.0,
        keep_alive="30m",
        num_ctx=4096,
        num_predict=256,
    )
    backend.generate([{"role": "user", "content": "hello, we are back"}], mode="fast", timeout=60.0)
    backend.generate([{"role": "user", "content": "tell me about spawn"}], mode="standard", timeout=90.0)

    assert calls[0]["timeout"] == model_routing_module.FAST_CHAIN_TIMEOUT_CAP_S
    assert calls[1]["timeout"] == model_routing_module.STANDARD_CHAIN_TIMEOUT_CAP_S


def test_conversation_review_summarizes_transport_and_fallbacks(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path)

    telegram_session = "telegram-session"
    telegram_correlation = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=telegram_session,
        correlation_id=telegram_correlation,
        text="hello, we are back",
        analysis_mode="standard",
        request_id="telegram-1",
    )
    memory.persist_agent_response(
        session_id=telegram_session,
        correlation_id=telegram_correlation,
        agent_name="daise",
        response_text="D.A.I.S.E is online locally.",
        confidence=0.8,
        provider="heuristic",
        model="fallback-heuristic",
        execution_time_ms=62000,
        extras={
            "transport": "telegram",
            "retrieval_profile": "standard",
            "backend_error": "Ollama request timed out after 60s for model llama3.2:3b",
        },
        request_id="telegram-1",
    )
    memory.persist_workflow_event(
        source="daise",
        event_type="request_outcome",
        payload={
            "provider": "heuristic",
            "model": "fallback-heuristic",
            "transport": "telegram",
            "retrieval_profile": "standard",
            "fallback_used": True,
            "backend_error": "Ollama request timed out after 60s for model llama3.2:3b",
            "execution_time_ms": 62000,
        },
        session_id=telegram_session,
        correlation_id=telegram_correlation,
        request_id="telegram-1",
    )

    hud_session = "hud-session"
    hud_correlation = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=hud_session,
        correlation_id=hud_correlation,
        text="what's today's date?",
        analysis_mode="standard",
        request_id="hud-1",
    )
    memory.persist_agent_response(
        session_id=hud_session,
        correlation_id=hud_correlation,
        agent_name="daise",
        response_text="Today is Monday, August 10, 2026.",
        confidence=1.0,
        provider="local_runtime_truth",
        model="grounded-truth-v1",
        execution_time_ms=42,
        extras={
            "transport": "hud",
            "retrieval_profile": "standard",
        },
        request_id="hud-1",
    )
    memory.persist_workflow_event(
        source="daise",
        event_type="request_outcome",
        payload={
            "provider": "local_runtime_truth",
            "model": "grounded-truth-v1",
            "transport": "hud",
            "retrieval_profile": "standard",
            "reason": "grounded_truth",
            "grounded_truth_kind": "date",
            "fallback_used": False,
            "execution_time_ms": 42,
        },
        session_id=hud_session,
        correlation_id=hud_correlation,
        request_id="hud-1",
    )

    review = memory.review_conversations(transport="telegram", limit=5, message_limit=2)

    assert review["matched_sessions"] == 1
    session = review["sessions"][0]
    assert session["session_id"] == telegram_session
    assert session["primary_transport"] == "telegram"
    assert session["transports"]["telegram"] == 1
    assert session["providers"]["heuristic"] == 1
    assert session["fallback_count"] == 1
    assert session["timeout_count"] == 1
    assert session["recent_queries"][-1]["text"] == "hello, we are back"
    assert "timed out" in session["fallback_requests"][-1]["backend_error"].lower()


def test_heuristic_fallback_uses_grounded_context_when_available() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate(
        [
            {
                "role": "system",
                "content": (
                    "Conversation memory and ecosystem context:\n"
                    "[Source 1] title=Spawn | kind=collection_summary | collection=spawn\n"
                    "Spawn is an OBEOS knowledge collection focused on Spawn comic universe and related series. "
                    "Representative passages: Spawn follows Al Simmons after bargaining with Malebolgia and returning to Earth as Hellspawn.\n"
                ),
            },
            {"role": "user", "content": "tell me about spawn"},
        ]
    )

    assert "From local O.B.E.O.S. knowledge" in result.text
    assert "Al Simmons" in result.text
    assert "fallback path" not in result.text


def test_heuristic_fallback_uses_word_boundaries_for_greetings() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate([{"role": "user", "content": "this milestone matters"}])

    assert "D.A.I.S.E is online locally" not in result.text
    assert "live model lane recovers" in result.text


def test_heuristic_fallback_handles_memory_commitment_prompt() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate(
        [
            {
                "role": "system",
                "content": (
                    "Conversation memory and ecosystem context:\n"
                    "[Source 1] title=Speech | kind=document | collection=local\n"
                    "Now we live as a nation only when it is striving.\n"
                ),
            },
            {"role": "user", "content": "will you remember to do that in the future?"},
        ]
    )

    assert "carry it forward" in result.text
    assert "nation only when it is striving" not in result.text
