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
from blackfong_ecosystem.chat_backends import BaseChatBackend, ChatBackendError, ChatBackendResult, HeuristicChatBackend, OllamaChatBackend
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


class EvidenceEchoAgent:
    """Stands in for the live model: it writes the reply, weaving in the runtime facts
    it was handed. Deterministic text reaching the operator only via this reply proves
    the facts were evidence, not the answer."""

    def __init__(self) -> None:
        self.contexts: List[Dict[str, Any]] = []

    def process(self, user_input, context=None):
        context = dict(context or {})
        self.contexts.append(context)
        truth = context.get("runtime_truth") or {}
        reply = "In my own words: " + str(truth.get("response") or "nothing to add.")
        return {
            "response": reply,
            "confidence": 0.9,
            "tools_invoked": [],
            "trace_event_id": "trace-evidence-echo",
            "task_type": "general_conversation",
            "provider": "fake_model",
            "model": "fake-model",
            "backend_error": None,
            "backend_metadata": {},
            "agent": "jarvis",
        }


class EvidenceEchoBackend(BaseChatBackend):
    """Live-model stand-in for a real JARVISAgent: it writes the reply from the
    deterministic evidence blocks in its prompt, so their text reaching the operator
    proves they were evidence the model used rather than a reply that skipped it."""

    provider_name = "fake_model"
    model = "fake-model"
    EVIDENCE_HEADERS = (
        "Memory-backed answer for this turn:",
        "Knowledge-collection answer for this turn:",
        "Verified local runtime facts for this turn",
    )

    def __init__(self, model: str = "fake-model") -> None:
        self.prompts: List[List[Dict[str, str]]] = []

    def generate(self, messages, **options: Any) -> ChatBackendResult:
        self.prompts.append([dict(message) for message in messages])
        evidence = [
            message["content"].split("\n", 1)[1].rsplit("\n", 1)[0]
            for message in messages
            if message.get("role") == "system" and message["content"].startswith(self.EVIDENCE_HEADERS)
        ]
        return ChatBackendResult(
            text="In my own words: " + (" ".join(evidence) or "nothing to add."),
            provider=self.provider_name,
            model=self.model,
            metadata={"response_path": "live_model"},
        )


class FakeCodingAgent:
    def process(self, user_input, context=None):
        return {
            "response": "Coding agent kept the implementation thread in focus.",
            "confidence": 0.91,
            "tools_invoked": [],
            "trace_event_id": "trace-coding-1",
            "task_type": "coding_help",
            "provider": "heuristic",
            "model": "fake-coding",
            "backend_error": None,
            "backend_metadata": {"context_present": bool((context or {}).get("memory_context"))},
            "agent": "coding",
        }


class FakeLiloAgent:
    def process(self, user_input, context=None):
        return {
            "response": "Lilo training agent answered.",
            "confidence": 0.9,
            "tools_invoked": [],
            "trace_event_id": "trace-lilo-1",
            "task_type": "lilo",
            "provider": "heuristic",
            "model": "fake-lilo",
            "backend_error": None,
            "backend_metadata": {},
            "agent": "lilo",
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
    assert result["selected_agent"] == "daise"
    assert result["runtime_agent"] == "jarvis"
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
        agents={"jarvis": EvidenceEchoAgent()},
        context={"request_id": "grounded-date-1", "analysis_mode": "standard"},
    )
    identity_result = daise.process_request(
        user_input="what does D.A.I.S.E stand for?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={"request_id": "grounded-identity-1", "analysis_mode": "standard"},
    )
    remembered_name_result = daise.process_request(
        user_input="do you remember your name?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={"request_id": "grounded-identity-2", "analysis_mode": "standard"},
    )
    grounded_outcomes = memory.event_store.list_events_by_type("request_outcome", limit=5)
    trace = memory.get_request_trace("grounded-identity-2")
    expected_date = f"{datetime.now().astimezone().strftime('%A, %B')} {datetime.now().astimezone().day}, {datetime.now().astimezone().year}"

    assert expected_date in date_result["response"]
    assert date_result["provider"] == "fake_model"
    assert "Direct Artificial Intelligence System Environment" in identity_result["response"]
    assert "Jarvis alias" not in identity_result["response"]
    assert remembered_name_result["provider"] == "fake_model"
    assert "I am D.A.I.S.E." in remembered_name_result["response"]
    assert grounded_outcomes[0].payload["provider"] == "fake_model"
    assert grounded_outcomes[0].payload["grounded_truth_kind"] == "identity"
    assert trace["request_outcome"]["provider"] == "fake_model"
    assert trace["request_outcome"]["grounded_truth_kind"] == "identity"


def test_daise_grounded_identity_handles_broader_natural_prompts(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    queries = (
        "what are you exactly inside obeos?",
        "what should i call you in this system?",
        "do you still carry your identity correctly now?",
    )
    for index, query in enumerate(queries, start=1):
        request_id = f"grounded-identity-broad-{index}"
        result = daise.process_request(
            user_input=query,
            agents={"jarvis": EvidenceEchoAgent()},
            context={"request_id": request_id, "analysis_mode": "standard"},
        )
        trace = memory.get_request_trace(request_id)

        assert result["provider"] == "fake_model"
        assert "D.A.I.S.E." in result["response"]
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
        agents={"jarvis": EvidenceEchoAgent()},
        context={"request_id": "grounded-date-override-1", "analysis_mode": "standard"},
    )

    assert "Monday, August 10, 2026" in date_result["response"]
    assert date_result["provider"] == "fake_model"
    assert date_result["runtime_truth"]["observation"]["clock_source"] == "runtime_truth_override"


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
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-greeting-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    tiny_result = daise.process_request(
        user_input="would you happen to know why tinyprogrammer isnt working online?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-tiny-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    greeting_trace = memory.get_request_trace("grounded-greeting-1")
    tiny_trace = memory.get_request_trace("grounded-tiny-1")

    assert greeting_result["provider"] == "fake_model"
    assert "Good evening, Five." in greeting_result["response"]
    assert "TinyProgrammer startup" in greeting_result["response"] or "HUD notifications" in greeting_result["response"]
    assert tiny_result["provider"] == "fake_model"
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
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-return-greeting-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-return-greeting-1")

    assert result["provider"] == "fake_model"
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
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-checkin-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-checkin-1")

    assert result["provider"] == "fake_model"
    assert "I'm here, Five, and operating normally inside O.B.E.O.S." in result["response"]
    assert "response latency" in result["response"] or "Spawn knowledge recall" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "check_in"


def test_daise_grounded_status_handles_broader_natural_prompts(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". We are working on response latency and Telegram grounding.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    queries = (
        "are you still up right now?",
        "how's the runtime looking?",
        "is the backend alive right now?",
    )
    for index, query in enumerate(queries, start=1):
        request_id = f"grounded-status-broad-{index}"
        result = daise.process_request(
            user_input=query,
            agents={"jarvis": EvidenceEchoAgent()},
            context={
                "request_id": request_id,
                "analysis_mode": "standard",
                "session_id": session_id,
            },
        )
        trace = memory.get_request_trace(request_id)

        assert result["provider"] == "fake_model"
        assert trace["request_outcome"]["grounded_truth_kind"] == "status"


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
        chat_backend=EvidenceEchoBackend(),
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

    assert result["provider"] == "fake_model"
    assert "Where we left off, we were working on" in result["response"]
    assert "response latency" in result["response"]
    assert trace["request_outcome"]["provider"] == "fake_model"


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
        chat_backend=EvidenceEchoBackend(),
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

    assert result["provider"] == "fake_model"
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
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-memory-progress-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-memory-progress-1")

    assert result["provider"] == "fake_model"
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
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-memory-progress-identity-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-memory-progress-identity-1")

    assert result["provider"] == "fake_model"
    assert "I can also carry forward that I am D.A.I.S.E." in result["response"]
    assert "OBEOS improvements" in result["response"] or "OBEOS memory system" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "memory_progress"


def test_daise_grounded_memory_progress_handles_broader_natural_prompts(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on OBEOS memory system, Telegram grounding, and response latency.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    queries = (
        "can you tell that obeos is remembering more of who you are now and what changed?",
        "do you feel like your identity is carrying forward better now that the memory system improved?",
        "have you noticed the difference in obeos memory progress lately?",
    )
    for index, query in enumerate(queries, start=1):
        request_id = f"grounded-memory-progress-broad-{index}"
        result = daise.process_request(
            user_input=query,
            agents={"jarvis": EvidenceEchoAgent()},
            context={
                "request_id": request_id,
                "analysis_mode": "standard",
                "session_id": session_id,
            },
        )
        trace = memory.get_request_trace(request_id)

        assert result["provider"] == "fake_model"
        assert "difference" in result["response"].lower() or "shared o.b.e.o.s memory layer" in result["response"].lower()
        assert trace["request_outcome"]["grounded_truth_kind"] == "memory_progress"


def test_daise_grounded_improvement_guidance_handles_obeos_improvement_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, HUD layout improvements, and Telegram grounding.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="what can i do to improve obeos?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-improvement-guidance-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-improvement-guidance-1")

    assert result["provider"] == "fake_model"
    assert "improvements I would prioritize next" in result["response"]
    assert "response latency" in result["response"] or "HUD layout improvements" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "improvement_guidance"


def test_daise_grounded_improvement_guidance_handles_response_time_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, OBEOS memory system, and Telegram grounding.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="is there anything youd recommend to improve your response time? making your responses faster?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-improvement-guidance-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-improvement-guidance-2")

    assert result["provider"] == "fake_model"
    assert "smallest useful prompt" in result["response"] or "duplicate transcript context" in result["response"]
    assert "response latency" in result["response"] or "Telegram grounding" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "improvement_guidance"


def test_daise_grounded_improvement_guidance_handles_broader_natural_prompts(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, OBEOS memory system, HUD layout improvements, and Telegram grounding.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    queries = (
        "what should we tighten next to make obeos better overall?",
        "where would you prioritize fixes if we want the system to feel faster and cleaner?",
        "what do you think we should improve next in obeos?",
    )
    for index, query in enumerate(queries, start=1):
        request_id = f"grounded-improvement-guidance-broad-{index}"
        result = daise.process_request(
            user_input=query,
            agents={"jarvis": EvidenceEchoAgent()},
            context={
                "request_id": request_id,
                "analysis_mode": "standard",
                "session_id": session_id,
            },
        )
        trace = memory.get_request_trace(request_id)

        assert result["provider"] == "fake_model"
        assert "prioritize next" in result["response"].lower() or "improvements i would prioritize next" in result["response"].lower()
        assert trace["request_outcome"]["grounded_truth_kind"] == "improvement_guidance"


def test_daise_grounded_project_reflection_handles_next_step_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, Telegram grounding, and TinyProgrammer startup.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="what do you think we should do next?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-project-reflection-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-project-reflection-1")

    assert result["provider"] == "fake_model"
    assert "the next strongest move is to keep tightening" in result["response"]
    assert "response latency" in result["response"] or "Telegram grounding" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "project_reflection"


def test_daise_grounded_project_reflection_handles_state_assessment_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on OBEOS memory system, response latency, and HUD layout improvements.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="do you think the system is getting better?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-project-reflection-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-project-reflection-2")

    assert result["provider"] == "fake_model"
    assert "the system is getting better in concrete ways" in result["response"]
    assert "grounded truth" in result["response"] or "shared memory" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "project_reflection"


@pytest.mark.parametrize(
    "query",
    [
        "where should we take obeos next?",
        "how is the obeos project really doing now?",
        "how would you describe what obeos is becoming?",
    ],
)
def test_daise_grounded_project_reflection_handles_broader_natural_prompts(
    tmp_path: Path,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on OBEOS memory system, response latency, and HUD layout improvements.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input=query,
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": f"grounded-project-broad-{abs(hash(query))}",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace(f"grounded-project-broad-{abs(hash(query))}")

    assert result["provider"] == "fake_model"
    assert "response latency" in result["response"] or "HUD layout" in result["response"] or "shared memory" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "project_reflection"


def test_daise_grounded_risk_reflection_handles_telegram_grounding_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on Telegram grounding, response latency, and OBEOS memory system.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="how are we doing with telegram grounding right now?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-risk-reflection-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-risk-reflection-1")

    assert result["provider"] == "fake_model"
    assert "Telegram grounding is moving in the right direction" in result["response"]
    assert "retrieval consistency" in result["response"] or "session continuity" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "risk_reflection"


def test_daise_grounded_risk_reflection_handles_tinyprogrammer_startup_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on TinyProgrammer startup, response latency, and HUD layout improvements.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="how are we doing with tinyprogrammer startup?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-risk-reflection-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-risk-reflection-2")

    assert result["provider"] == "fake_model"
    assert "TinyProgrammer startup still looks like one of the more fragile tracks" in result["response"]
    assert "availability" in result["response"] or "observable truth" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "risk_reflection"


def test_daise_preserves_selected_agent_for_vague_followup_turns(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    first = daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "continuity-routing-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    second = daise.process_request(
        user_input="what part of that matters most?",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "continuity-routing-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    routing_events = [
        event
        for event in memory.event_store.list_events_by_type("routing_decision", limit=10)
        if (event.metadata or {}).get("request_id") == "continuity-routing-2"
    ]

    assert first["selected_agent"] == "coding"
    assert second["selected_agent"] == "coding"
    assert second["response"] == "Coding agent kept the implementation thread in focus."
    assert routing_events
    assert routing_events[0].payload["continuity_routing_hint"]["preferred_agent"] == "coding"
    assert routing_events[0].payload["continuity_routing_hint"]["frame"] == "followup"


def test_daise_grounded_risk_reflection_handles_biggest_risks_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, TinyProgrammer startup, and Telegram grounding.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="what are the biggest risks right now?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-risk-reflection-3",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-risk-reflection-3")

    assert result["provider"] == "fake_model"
    assert "biggest risks I see are" in result["response"]
    assert "response latency" in result["response"] or "TinyProgrammer" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "risk_reflection"


@pytest.mark.parametrize(
    "query",
    [
        "how is telegram grounding really holding up now?",
        "where does tinyprogrammer startup still look fragile?",
        "what still seems riskiest in obeos right now?",
    ],
)
def test_daise_grounded_risk_reflection_handles_broader_natural_prompts(
    tmp_path: Path,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on Telegram grounding, TinyProgrammer startup, and response latency.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input=query,
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": f"grounded-risk-broad-{abs(hash(query))}",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace(f"grounded-risk-broad-{abs(hash(query))}")

    assert result["provider"] == "fake_model"
    assert "Telegram grounding" in result["response"] or "TinyProgrammer startup" in result["response"] or "response latency" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "risk_reflection"


def test_daise_grounded_creator_mission_reflection_handles_creator_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='You are D.A.I.S.E. I am your creator. Call me "Five". I am working on response latency, Telegram grounding, and shared memory.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="what do you think about me as your creator?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-creator-mission-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-creator-mission-1")

    assert result["provider"] == "fake_model"
    assert "creator of O.B.E.O.S" in result["response"] or "creator" in result["response"]
    assert "response latency" in result["response"] or "Telegram grounding" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "creator_mission_reflection"


def test_daise_grounded_creator_mission_reflection_handles_difference_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, Memory Chamber, and operator surface cohesion.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="what makes obeos different from a normal chatbot?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-creator-mission-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-creator-mission-2")

    assert result["provider"] == "fake_model"
    assert "shared event spine" in result["response"] or "bounded memory" in result["response"]
    assert "multiple operator surfaces" in result["response"] or "local system truth" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "creator_mission_reflection"


def test_daise_grounded_creator_mission_reflection_handles_main_focus_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, TinyProgrammer startup, and Telegram grounding.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="what are we really trying to accomplish here?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-creator-mission-3",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-creator-mission-3")

    assert result["provider"] == "fake_model"
    assert "coherent living system" in result["response"] or "fragile chatbot shell" in result["response"]
    assert "response latency" in result["response"] or "TinyProgrammer startup" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "creator_mission_reflection"


@pytest.mark.parametrize(
    "query",
    [
        "how do you see me as the person building obeos?",
        "what is this obeos system really for now?",
        "why are we building obeos in the first place?",
        "what really separates obeos from a normal assistant?",
    ],
)
def test_daise_grounded_creator_mission_reflection_handles_broader_natural_prompts(
    tmp_path: Path,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am building OBEOS around response latency, TinyProgrammer startup, and shared memory grounding.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input=query,
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": f"grounded-creator-mission-broad-{abs(hash(query))}",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace(f"grounded-creator-mission-broad-{abs(hash(query))}")

    assert result["provider"] == "fake_model"
    assert "response latency" in result["response"] or "TinyProgrammer startup" in result["response"] or "shared memory" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "creator_mission_reflection"


def test_daise_grounded_values_reflection_handles_progress_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, Telegram grounding, and shared memory.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="are you proud of the progress we have made?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-values-reflection-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-values-reflection-1")

    assert result["provider"] == "fake_model"
    assert "progress is real and meaningful" in result["response"]
    assert "truth, memory, and bounded retrieval" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "values_reflection"


def test_daise_grounded_values_reflection_handles_success_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, TinyProgrammer startup, and operator surface cohesion.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="what would success look like for obeos?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-values-reflection-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-values-reflection-2")

    assert result["provider"] == "fake_model"
    assert "success would look like O.B.E.O.S. feeling coherent and trustworthy" in result["response"]
    assert "same memory" in result["response"] or "same grounded answers" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "values_reflection"


def test_daise_grounded_values_reflection_handles_direction_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, OBEOS improvements, and TinyProgrammer startup.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="how do you feel about the direction we are going?",
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-values-reflection-3",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-values-reflection-3")

    assert result["provider"] == "fake_model"
    assert "direction looks healthy" in result["response"]
    assert "grounded behavior over fragile improvisation" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "values_reflection"


@pytest.mark.parametrize(
    "query",
    [
        "what feels most alive about obeos right now?",
        "what matters most in obeos right now to you?",
        "do you think the obeos direction still feels healthy?",
        "do you feel like you understand me more clearly now inside obeos?",
    ],
)
def test_daise_grounded_values_reflection_handles_broader_natural_prompts(
    tmp_path: Path,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, OBEOS improvements, and TinyProgrammer startup.',
    )
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input=query,
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": f"grounded-values-broad-{abs(hash(query))}",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace(f"grounded-values-broad-{abs(hash(query))}")

    assert result["provider"] == "fake_model"
    assert "response latency" in result["response"] or "TinyProgrammer startup" in result["response"] or "grounded" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "values_reflection"


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
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-chat-rendering-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-chat-rendering-1")

    assert result["provider"] == "fake_model"
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
        agents={"jarvis": EvidenceEchoAgent()},
        context={
            "request_id": "grounded-memory-system-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    trace = memory.get_request_trace("grounded-memory-system-1")

    assert result["provider"] == "fake_model"
    assert "shared conversation memory layer backed by events.db" in result["response"]
    assert "operator facts, active work items, pinned memory, and session summaries" in result["response"]
    assert trace["request_outcome"]["grounded_truth_kind"] == "memory_system"


def test_daise_grounded_memory_system_handles_broader_natural_prompts(tmp_path: Path) -> None:
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

    queries = (
        "how are you actually remembering things between turns now?",
        "can you explain how that memory layer works a little more clearly?",
        "will you still carry that forward later if i tell you now?",
    )
    for index, query in enumerate(queries, start=1):
        request_id = f"grounded-memory-system-broad-{index}"
        result = daise.process_request(
            user_input=query,
            agents={"jarvis": EvidenceEchoAgent()},
            context={
                "request_id": request_id,
                "analysis_mode": "standard",
                "session_id": session_id,
            },
        )
        trace = memory.get_request_trace(request_id)

        assert result["provider"] == "fake_model"
        assert "shared conversation memory layer backed by events.db" in result["response"]
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
    assert backend_health["allow_recovery_probe"] is True
    assert backend_health["degraded_reason"] == "recent_transient_timeout"


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


def test_conversation_memory_recalls_operator_name_from_natural_language_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='You are D.A.I.S.E. and I am your creator. Call me "Five".',
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what name did i give you again?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_memory_answer"]
    assert grounded is not None
    assert grounded["kind"] == "operator"
    assert 'call you "Five"' in grounded["answer"]


def test_conversation_memory_recalls_recent_work_from_natural_language_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="can you re-anchor me in our recent work?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_memory_answer"]
    assert grounded is not None
    assert grounded["kind"] == "session"
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]


def test_conversation_memory_recalls_where_we_left_off_from_natural_language_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="where did we leave that off?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_memory_answer"]
    assert grounded is not None
    assert grounded["kind"] == "session"
    assert "Where we left off" in grounded["answer"]
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]


@pytest.mark.parametrize(
    "query",
    [
        "what do you still know about where we were?",
        "what are you still carrying forward from all this?",
        "what was the thing we were tightening again?",
        "what were we circling around before?",
        "what do you still have in mind from our recent work?",
        "what were you just anchoring to a second ago?",
        "what thread are you still holding onto here?",
        "what were we trying to stabilize again?",
        "what part of the recent work are you still remembering most clearly?",
        "what still seems live from our side of this?",
        "what are you still keeping straight from our recent work?",
        "what are you still tracking from all of this?",
        "what are you still anchored around here?",
        "what do you still have in focus from before?",
        "what is still in your memory about our work?",
        "what are you still carrying as the main thread?",
        "what are you still keyed to from our recent session?",
        "what do you still have retained from our last stretch?",
    ],
)
def test_conversation_memory_recalls_recent_work_from_looser_natural_language_prompts(tmp_path: Path, query: str) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can keep response latency, Telegram grounding, and HUD improvements in focus.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query=query,
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_memory_answer"]
    assert grounded is not None
    assert grounded["kind"] == "session"
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]


@pytest.mark.parametrize(
    "query",
    [
        "what do you still know about me from all this?",
        "what are you still remembering about me here?",
        "what do you still carry about who i am in this system?",
        "what am i to you in obeos again?",
        "what do you still have on me from our side of this?",
        "what do you still remember about my role here?",
        "what still seems true about me to you?",
        "what do you still know about who i am to this project?",
        "what are you still holding onto about me?",
    ],
)
def test_conversation_memory_recalls_operator_identity_from_looser_natural_language_prompts(
    tmp_path: Path,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='You are D.A.I.S.E. and I am your creator. Call me "Five". I am working on response latency and Telegram grounding.',
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="Understood. I will keep your role and current work in mind.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query=query,
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_memory_answer"]
    assert grounded is not None
    assert grounded["kind"] == "operator"
    assert 'call you "Five"' in grounded["answer"]
    assert "creator of O.B.E.O.S" in grounded["answer"]


@pytest.mark.parametrize(
    "query",
    [
        "what do you still know about us here?",
        "what do you still have on us from all this?",
        "what still seems true about our side of this?",
        "what do you still remember about our place in this system?",
        "what are you still holding onto about us here?",
    ],
)
def test_conversation_memory_recalls_shared_work_identity_from_mixed_us_prompts(
    tmp_path: Path,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='You are D.A.I.S.E. and I am your creator. Call me "Five". We have been working on response latency, Telegram grounding, and HUD improvements.',
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="Understood. I will keep your role and the current work in mind.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query=query,
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_memory_answer"]
    assert grounded is not None
    assert grounded["kind"] == "session"
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]


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
        raise AssertionError("the explicit test backend, not the routed one, writes the reply")

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", _should_not_reach_ollama)

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    agent = JARVISAgent(chat_backend=EvidenceEchoBackend(), fallback_backend=HeuristicChatBackend())

    result = daise.process_request(
        user_input="where did we leave off?",
        agents={"jarvis": agent},
        context={"request_id": "grounded-memory-1", "analysis_mode": "standard", "session_id": session_id},
    )

    assert result["provider"] == "fake_model"
    assert result["model"] == "fake-model"
    assert "Where we left off" in result["response"]
    assert "TinyProgrammer startup" in result["response"] or "chat rendering" in result["response"]


@pytest.mark.parametrize(
    "query",
    [
        "what do you still know about where we were?",
        "what are you still carrying forward from all this?",
        "what was the thing we were tightening again?",
        "what were we circling around before?",
        "what do you still have in mind from our recent work?",
        "what were you just anchoring to a second ago?",
        "what thread are you still holding onto here?",
        "what were we trying to stabilize again?",
        "what part of the recent work are you still remembering most clearly?",
        "what still seems live from our side of this?",
        "what are you still keeping straight from our recent work?",
        "what are you still tracking from all of this?",
        "what are you still anchored around here?",
        "what do you still have in focus from before?",
        "what is still in your memory about our work?",
        "what are you still carrying as the main thread?",
        "what are you still keyed to from our recent session?",
        "what do you still have retained from our last stretch?",
        "what are you still centered on?",
        "what are you still orbiting around?",
        "what are you still holding together here?",
        "what is still live in this for you?",
    ],
)
def test_daise_uses_grounded_memory_for_looser_natural_language_recall_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can keep response latency, Telegram grounding, and HUD improvements in focus.",
        confidence=0.9,
    )

    def _should_not_reach_ollama(*args, **kwargs):
        raise AssertionError("the explicit test backend, not the routed one, writes the reply")

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", _should_not_reach_ollama)

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    agent = JARVISAgent(chat_backend=EvidenceEchoBackend(), fallback_backend=HeuristicChatBackend())

    result = daise.process_request(
        user_input=query,
        agents={"jarvis": agent},
        context={"request_id": f"grounded-memory-natural-{abs(hash(query))}", "analysis_mode": "standard", "session_id": session_id},
    )

    assert result["provider"] == "fake_model"
    assert result["model"] == "fake-model"
    assert "response latency" in result["response"] or "Telegram grounding" in result["response"]


@pytest.mark.parametrize(
    "query",
    [
        "what do you still know about me from all this?",
        "what are you still remembering about me here?",
        "what do you still carry about who i am in this system?",
        "what am i to you in obeos again?",
        "what do you still have on me from our side of this?",
        "what do you still remember about my role here?",
        "what still seems true about me to you?",
        "what do you still know about who i am to this project?",
        "what are you still holding onto about me?",
    ],
)
def test_daise_uses_grounded_memory_for_looser_operator_identity_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='You are D.A.I.S.E. and I am your creator. Call me "Five". I am working on response latency and Telegram grounding.',
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="Understood. I will keep your role and current work in mind.",
        confidence=0.9,
    )

    def _should_not_reach_ollama(*args, **kwargs):
        raise AssertionError("the explicit test backend, not the routed one, writes the reply")

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", _should_not_reach_ollama)

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    agent = JARVISAgent(chat_backend=EvidenceEchoBackend(), fallback_backend=HeuristicChatBackend())

    result = daise.process_request(
        user_input=query,
        agents={"jarvis": agent},
        context={"request_id": f"grounded-operator-natural-{abs(hash(query))}", "analysis_mode": "standard", "session_id": session_id},
    )

    assert result["provider"] == "fake_model"
    assert result["model"] == "fake-model"
    assert 'call you "Five"' in result["response"]
    assert "creator of O.B.E.O.S" in result["response"]


@pytest.mark.parametrize(
    "query",
    [
        "what do you still know about us here?",
        "what do you still have on us from all this?",
        "what still seems true about our side of this?",
        "what do you still remember about our place in this system?",
        "what are you still holding onto about us here?",
    ],
)
def test_daise_uses_grounded_memory_for_mixed_us_recall_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='You are D.A.I.S.E. and I am your creator. Call me "Five". We have been working on response latency, Telegram grounding, and HUD improvements.',
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="Understood. I will keep your role and the current work in mind.",
        confidence=0.9,
    )

    def _should_not_reach_ollama(*args, **kwargs):
        raise AssertionError("the explicit test backend, not the routed one, writes the reply")

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", _should_not_reach_ollama)

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    agent = JARVISAgent(chat_backend=EvidenceEchoBackend(), fallback_backend=HeuristicChatBackend())

    result = daise.process_request(
        user_input=query,
        agents={"jarvis": agent},
        context={"request_id": f"grounded-us-natural-{abs(hash(query))}", "analysis_mode": "standard", "session_id": session_id},
    )

    assert result["provider"] == "fake_model"
    assert result["model"] == "fake-model"
    assert "response latency" in result["response"] or "Telegram grounding" in result["response"]


def test_build_context_includes_conversational_stance_for_standard_chat(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, Telegram grounding, and HUD cohesion.',
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="can we keep talking about obeos for a minute",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    assert "Conversational stance:" in bundle["prompt_context"]
    assert "Current shared focus:" in bundle["prompt_context"]
    assert "response latency" in bundle["prompt_context"] or "Telegram grounding" in bundle["prompt_context"]


def test_build_context_includes_conversation_continuity_for_standard_chat(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". We have been working on response latency, Telegram grounding, and HUD cohesion.',
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can keep the latency, Telegram grounding, and HUD cohesion work coherent.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="can we keep talking about obeos for a minute",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    assert "Conversation continuity:" in bundle["prompt_context"]
    assert "Ongoing thread:" in bundle["prompt_context"]
    assert "Last stable assistant context:" in bundle["prompt_context"]


def test_build_context_includes_specialist_continuity_for_standard_chat(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "specialist-continuity-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what part of that matters most?",
        agent_name="coding",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    assert "Specialist continuity:" in bundle["prompt_context"]
    assert "Last selected specialist: coding." in bundle["prompt_context"]
    assert "Last task focus: coding_help." in bundle["prompt_context"]


def test_heuristic_fallback_uses_conversation_continuity_for_generic_continue_prompt() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate(
        [
            {
                "role": "system",
                "content": (
                    "Conversation continuity:\n"
                    "- Ongoing thread: response latency, Telegram grounding, and HUD cohesion.\n"
                    "- Last stable assistant context: I can keep the latency, Telegram grounding, and HUD cohesion work coherent.\n"
                ),
            },
            {"role": "user", "content": "can we keep talking about the system for a minute?"},
        ]
    )

    assert "response latency" in result.text
    assert "Telegram grounding" in result.text or "HUD cohesion" in result.text


def test_heuristic_fallback_uses_conversation_continuity_for_followup_prompt() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate(
        [
            {
                "role": "system",
                "content": (
                    "Conversation continuity:\n"
                    "- Ongoing thread: response latency, Telegram grounding, and HUD cohesion.\n"
                    "- Last stable assistant context: I can keep the latency, Telegram grounding, and HUD cohesion work coherent.\n"
                ),
            },
            {"role": "user", "content": "tell me more about that"},
        ]
    )

    assert "I can keep the latency" in result.text or "Telegram grounding" in result.text
    assert "response latency" in result.text or "HUD cohesion" in result.text


def test_heuristic_fallback_uses_conversation_continuity_for_short_followup_prompt() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate(
        [
            {
                "role": "system",
                "content": (
                    "Conversation continuity:\n"
                    "- Ongoing thread: response latency, Telegram grounding, and HUD cohesion.\n"
                    "- Last stable assistant context: I can keep the latency, Telegram grounding, and HUD cohesion work coherent.\n"
                ),
            },
            {"role": "user", "content": "how so?"},
        ]
    )

    assert result.text.lower().startswith("because")
    assert "response latency" in result.text or "HUD cohesion" in result.text


def test_heuristic_fallback_uses_conversation_continuity_for_alt_short_followup_prompt() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate(
        [
            {
                "role": "system",
                "content": (
                    "Conversation continuity:\n"
                    "- Ongoing thread: response latency, Telegram grounding, and HUD cohesion.\n"
                    "- Last stable assistant context: I can keep the latency, Telegram grounding, and HUD cohesion work coherent.\n"
                ),
            },
            {"role": "user", "content": "and then?"},
        ]
    )

    assert result.text.startswith("Another part of it is")
    assert "response latency" in result.text or "HUD cohesion" in result.text


def test_heuristic_fallback_uses_conversation_continuity_for_implicit_followup_prompt() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate(
        [
            {
                "role": "system",
                "content": (
                    "Conversation continuity:\n"
                    "- Ongoing thread: response latency, Telegram grounding, and HUD cohesion.\n"
                    "- Last stable assistant context: I can keep the latency, Telegram grounding, and HUD cohesion work coherent.\n"
                ),
            },
            {"role": "user", "content": "that feels important"},
        ]
    )

    assert "I can keep the latency" in result.text or "Telegram grounding" in result.text
    assert "response latency" in result.text or "HUD cohesion" in result.text


def test_heuristic_fallback_uses_conversation_continuity_for_referential_question_prompt() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate(
        [
            {
                "role": "system",
                "content": (
                    "Conversation continuity:\n"
                    "- Ongoing thread: response latency, Telegram grounding, and HUD cohesion.\n"
                    "- Last stable assistant context: I can keep the latency, Telegram grounding, and HUD cohesion work coherent.\n"
                ),
            },
            {"role": "user", "content": "is that still the problem?"},
        ]
    )

    assert "I can keep the latency" in result.text or "Telegram grounding" in result.text
    assert "response latency" in result.text or "HUD cohesion" in result.text


def test_heuristic_fallback_uses_conversation_continuity_for_structural_referential_prompt() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate(
        [
            {
                "role": "system",
                "content": (
                    "Conversation continuity:\n"
                    "- Ongoing thread: response latency, Telegram grounding, and HUD cohesion.\n"
                    "- Last stable assistant context: I can keep the latency, Telegram grounding, and HUD cohesion work coherent.\n"
                    "\nSpecialist continuity:\n"
                    "- Last selected specialist: coding.\n"
                    "- Last task focus: coding_help.\n"
                ),
            },
            {"role": "user", "content": "is that still the main risk?"},
        ]
    )

    assert "I can keep the latency" in result.text or "Telegram grounding" in result.text
    assert "response latency" in result.text or "HUD cohesion" in result.text
    assert "coding lane" in result.text


def test_heuristic_fallback_uses_conversation_continuity_for_main_thing_prompt() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate(
        [
            {
                "role": "system",
                "content": (
                    "Conversation continuity:\n"
                    "- Ongoing thread: response latency, Telegram grounding, and HUD cohesion.\n"
                    "- Last stable assistant context: I can keep the latency, Telegram grounding, and HUD cohesion work coherent.\n"
                    "\nSpecialist continuity:\n"
                    "- Last selected specialist: coding.\n"
                    "- Last task focus: coding_help.\n"
                ),
            },
            {"role": "user", "content": "is that still the main thing?"},
        ]
    )

    assert "I can keep the latency" in result.text or "Telegram grounding" in result.text
    assert "coding lane" in result.text


def test_daise_uses_grounded_memory_for_recent_updates_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on Telegram grounding, Spawn recall, and live-model recovery tuning.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can continue the Telegram grounding, Spawn recall, and live-model recovery work.",
        confidence=0.9,
    )
    memory.maybe_create_session_summary(session_id, correlation_id=correlation_id, force=True)

    def _should_not_reach_ollama(*args, **kwargs):
        raise AssertionError("the explicit test backend, not the routed one, writes the reply")

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", _should_not_reach_ollama)

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    agent = JARVISAgent(chat_backend=EvidenceEchoBackend(), fallback_backend=HeuristicChatBackend())

    result = daise.process_request(
        user_input="Are you aware of recent updates?",
        agents={"jarvis": agent},
        context={"request_id": "grounded-memory-2", "analysis_mode": "standard", "session_id": session_id},
    )

    assert result["provider"] == "fake_model"
    assert result["model"] == "fake-model"
    assert "recent O.B.E.O.S. updates" in result["response"]
    assert "Telegram grounding" in result["response"] or "live-model recovery tuning" in result["response"]


def test_conversation_memory_builds_broad_grounded_conversation_answer(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on response latency, Telegram grounding, and TinyProgrammer startup.',
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can help keep the latency, Telegram grounding, and TinyProgrammer work coherent.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="talk to me honestly about where you think OBEOS is heading",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "direction"
    assert "coherent living system" in grounded["answer"]
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]


def test_conversation_memory_detects_semantic_direction_prompt_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="where do you feel this project is going lately?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "direction"
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


def test_conversation_memory_detects_semantic_risk_prompt_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="does this still feel fragile to you or is it getting more solid?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "risk"
    assert "real risk" in grounded["answer"] or "weakest areas" in grounded["answer"]


def test_conversation_memory_detects_followup_prompt_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what do you mean by that?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "more specifically" in grounded["answer"].lower()
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


def test_conversation_memory_detects_short_followup_prompt_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="how so?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert grounded["answer"].lower().startswith("because")


def test_conversation_memory_detects_alt_short_followup_prompt_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="and then?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert grounded["answer"].lower().startswith("another part of it is")
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


def test_conversation_memory_detects_implicit_followup_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="that feels important",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "more specifically" in grounded["answer"].lower()
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


def test_conversation_memory_detects_referential_question_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="is that still the problem?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "more specifically" in grounded["answer"].lower()
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


def test_conversation_memory_detects_structural_referential_question_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what part of that matters most?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "more specifically" in grounded["answer"].lower()
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


def test_conversation_memory_followup_grounding_carries_specialist_lane(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "grounded-specialist-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what part of that matters most?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "coding lane" in grounded["answer"]
    assert any(item.get("kind") == "agent_selection" for item in grounded["provenance"])


def test_conversation_memory_followup_prefers_recent_thread_topics_over_older_active_work(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". I am working on OBEOS improvements and TinyProgrammer updates.',
    )
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="same there?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]


def test_recent_user_topics_keep_newest_thread_first(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        text="We have been working on OBEOS improvements and TinyProgrammer updates.",
    )
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )

    messages = memory.get_session_messages(session_id, limit=20)
    topics = memory._recent_user_topics(messages, current_query="same there?", limit=4)

    assert topics[:3] == ["response latency", "Telegram grounding", "HUD improvements"]


def test_recent_assistant_snapshot_prefers_newest_thread_overlap(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on OBEOS improvements and TinyProgrammer updates.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I can keep the OBEOS improvements and TinyProgrammer work coherent.",
        confidence=0.9,
    )
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I can keep the response latency, Telegram grounding, and HUD improvements coherent.",
        confidence=0.9,
    )

    messages = memory.get_session_messages(session_id, limit=20)
    snapshot = memory._recent_assistant_snapshot(messages, current_query="same there?")

    assert "response latency" in snapshot or "Telegram grounding" in snapshot


def test_conversation_memory_detects_main_thing_followup_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "main-thing-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="is that still the main thing?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "coding lane" in grounded["answer"]


def test_daise_preserves_coding_lane_for_python_one_followup(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    first = daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "python-one-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    second = daise.process_request(
        user_input="okay but for the python one?",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "python-one-followup-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    assert first["selected_agent"] == "coding"
    assert second["selected_agent"] == "coding"
    assert second["response"] == "Coding agent kept the implementation thread in focus."


def test_daise_keeps_recent_specialist_lane_across_successive_referential_followups(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    first = daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "recover-specialist-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    miss = daise.process_request(
        user_input="does that still hold here?",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "recover-specialist-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    recovery = daise.process_request(
        user_input="okay but for the python one?",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "recover-specialist-3",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    assert first["selected_agent"] == "coding"
    assert miss["selected_agent"] == "coding"
    assert recovery["selected_agent"] == "coding"
    assert recovery["response"] == "Coding agent kept the implementation thread in focus."


def test_daise_preserves_coding_lane_for_hold_here_followup(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    first = daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "hold-here-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    second = daise.process_request(
        user_input="does that still hold here?",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "hold-here-followup-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    assert first["selected_agent"] == "coding"
    assert second["selected_agent"] == "coding"
    assert second["response"] == "Coding agent kept the implementation thread in focus."


def test_daise_preserves_coding_lane_for_hud_followup(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    first = daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "hud-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    second = daise.process_request(
        user_input="on the hud too?",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "hud-followup-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    assert first["selected_agent"] == "coding"
    assert second["selected_agent"] == "coding"
    assert second["response"] == "Coding agent kept the implementation thread in focus."


def test_daise_preserves_coding_lane_for_over_there_followup(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    first = daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "over-there-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    second = daise.process_request(
        user_input="over there too?",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "over-there-followup-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    assert first["selected_agent"] == "coding"
    assert second["selected_agent"] == "coding"
    assert second["response"] == "Coding agent kept the implementation thread in focus."


def test_daise_preserves_coding_lane_for_locally_too_followup(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    first = daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "locally-too-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    second = daise.process_request(
        user_input="locally too?",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "locally-too-followup-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    assert first["selected_agent"] == "coding"
    assert second["selected_agent"] == "coding"
    assert second["response"] == "Coding agent kept the implementation thread in focus."


def test_daise_preserves_coding_lane_for_browser_too_followup(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    first = daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "browser-too-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    second = daise.process_request(
        user_input="in browser too?",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "browser-too-followup-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    assert first["selected_agent"] == "coding"
    assert second["selected_agent"] == "coding"
    assert second["response"] == "Coding agent kept the implementation thread in focus."


def test_daise_preserves_coding_lane_for_browser_short_scope_followup(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    first = daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "browser-short-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    second = daise.process_request(
        user_input="browser too?",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "browser-short-followup-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    assert first["selected_agent"] == "coding"
    assert second["selected_agent"] == "coding"
    assert second["response"] == "Coding agent kept the implementation thread in focus."


def test_daise_preserves_coding_lane_for_same_there_followup(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    first = daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "same-there-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    second = daise.process_request(
        user_input="same there?",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "same-there-followup-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    assert first["selected_agent"] == "coding"
    assert second["selected_agent"] == "coding"
    assert second["response"] == "Coding agent kept the implementation thread in focus."


def test_daise_does_not_preserve_stale_lilo_lane_for_vague_followup(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    memory.persist_workflow_event(
        source="daise",
        event_type="agent_selection",
        payload={
            "selected_agent": "lilo",
            "output_agent": "daise",
            "task_type": "lilo",
            "routing_confidence": 0.93,
        },
        session_id=session_id,
        request_id="stale-lilo-followup-seed",
        tags=["workflow", "agent_selection"],
    )
    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
        request_id="stale-lilo-followup-seed",
        routed_agent="daise",
        analysis_mode="standard",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I can keep the response latency, Telegram grounding, and HUD improvements thread coherent.",
        confidence=0.9,
        request_id="stale-lilo-followup-seed",
    )
    second = daise.process_request(
        user_input="same there?",
        agents={"jarvis": FakeJarvisAgent(), "lilo": FakeLiloAgent()},
        context={
            "request_id": "stale-lilo-followup-2",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    assert second["selected_agent"] == "daise"
    assert second["runtime_agent"] == "jarvis"
    assert second["response"].strip()
    assert "Lilo training agent answered." not in second["response"]


def test_conversation_memory_detects_carry_over_followup_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "carry-over-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="does that carry over?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "coding lane" in grounded["answer"]


def test_conversation_memory_detects_true_there_followup_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "true-there-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="would that still be true there?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "coding lane" in grounded["answer"]


def test_conversation_memory_detects_path_followup_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "path-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what about in that path?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "coding lane" in grounded["answer"]


def test_conversation_memory_detects_browser_followup_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "browser-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="same in browser?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "coding lane" in grounded["answer"]


def test_conversation_memory_detects_remote_followup_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "remote-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="same remotely?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "coding lane" in grounded["answer"]


def test_conversation_memory_detects_short_remote_scope_followup_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "remote-short-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="same remotely?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "coding lane" in grounded["answer"]


def test_conversation_memory_detects_there_too_followup_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "there-too-followup-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=memory.create_correlation_id(),
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="there too?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "coding lane" in grounded["answer"]


def test_jarvis_includes_grounded_conversation_guidance_in_live_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, shared memory grounding, and HUD cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can keep the latency, memory grounding, and HUD cohesion work in focus.",
        confidence=0.9,
    )

    captured: dict[str, object] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["messages"] = messages
            captured["options"] = options
            return ChatBackendResult(
                text="Live lane conversational reply",
                provider="ollama",
                model="llama3.2:3b",
                metadata={"response_path": "live_model"},
            )

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", lambda *args, **kwargs: RecordingBackend())

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    agent = JARVISAgent(chat_backend=RecordingBackend(), fallback_backend=HeuristicChatBackend())

    result = daise.process_request(
        user_input="from everything you remember, what feels weakest in the system right now?",
        agents={"jarvis": agent},
        context={"request_id": "grounded-conversation-1", "analysis_mode": "standard", "session_id": session_id},
    )

    assert result["provider"] == "ollama"
    assert result["model"] == "llama3.2:3b"
    prompt_messages = list(captured.get("messages") or [])
    combined = "\n".join(str(item.get("content") or "") for item in prompt_messages)
    assert "Broad conversational grounding for this turn" in combined
    assert "weakest areas" in combined
    assert "response latency" in combined or "shared memory grounding" in combined


def test_jarvis_includes_followup_continuity_guidance_in_live_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, shared memory grounding, and HUD cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    captured: dict[str, object] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["messages"] = messages
            captured["options"] = options
            return ChatBackendResult(
                text="Live lane follow-up reply",
                provider="ollama",
                model="llama3.2:3b",
                metadata={"response_path": "live_model"},
            )

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", lambda *args, **kwargs: RecordingBackend())

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    agent = JARVISAgent(chat_backend=RecordingBackend(), fallback_backend=HeuristicChatBackend())

    result = daise.process_request(
        user_input="what do you mean by that?",
        agents={"jarvis": agent},
        context={"request_id": "grounded-conversation-followup-1", "analysis_mode": "standard", "session_id": session_id},
    )

    assert result["provider"] == "ollama"
    assert result["model"] == "llama3.2:3b"
    prompt_messages = list(captured.get("messages") or [])
    combined = "\n".join(str(item.get("content") or "") for item in prompt_messages)
    assert "Broad conversational grounding for this turn" in combined
    assert "Follow-up continuity for this turn" in combined
    assert "Last stable assistant context" in combined
    assert "avoid restarting from a generic high-level summary" in combined
    assert "start by resolving what 'same' or 'there' refers to" in combined


def test_jarvis_includes_specialist_continuity_guidance_in_live_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    session_id = memory.create_session_id()

    daise.process_request(
        user_input="please debug this python function and tell me why the bug is happening",
        agents={"jarvis": FakeJarvisAgent(), "coding": FakeCodingAgent()},
        context={
            "request_id": "specialist-guidance-1",
            "analysis_mode": "standard",
            "session_id": session_id,
        },
    )

    captured: dict[str, object] = {}

    class RecordingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            captured["messages"] = messages
            captured["options"] = options
            return ChatBackendResult(
                text="Live lane specialist follow-up reply",
                provider="ollama",
                model="llama3.2:3b",
                metadata={"response_path": "live_model"},
            )

    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", lambda *args, **kwargs: RecordingBackend())

    agent = JARVISAgent(chat_backend=RecordingBackend(), fallback_backend=HeuristicChatBackend())
    bundle = memory.build_context(
        session_id=session_id,
        query="what part of that matters most?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )
    messages, prompt_metrics = agent._build_chat_messages(
        "what part of that matters most?",
        {"memory_context": bundle, "analysis_mode": "standard", "session_id": session_id},
    )
    captured["messages"] = messages
    captured["options"] = {"prompt_metrics": prompt_metrics}
    prompt_messages = list(captured.get("messages") or [])
    combined = "\n".join(str(item.get("content") or "") for item in prompt_messages)
    assert "Active specialist lane for this turn" in combined
    assert "Last selected specialist: coding." in combined
    assert "Last task focus: coding_help." in combined


def test_jarvis_infers_terse_scope_followup_guidance_from_prompt_context() -> None:
    agent = JARVISAgent(chat_backend=HeuristicChatBackend(), fallback_backend=HeuristicChatBackend())
    messages, prompt_metrics = agent._build_chat_messages(
        "browser too?",
        {
            "analysis_mode": "standard",
            "memory_context": {
                "prompt_context": (
                    "Conversation continuity:\n"
                    "- Ongoing thread: response latency, Telegram grounding, and HUD cohesion.\n"
                    "- Last stable assistant context: I can keep the latency, Telegram grounding, and HUD cohesion work coherent.\n\n"
                    "Specialist continuity:\n"
                    "- Last selected specialist: coding.\n"
                    "- Last task focus: coding_help.\n"
                ),
                "grounded_conversation_answer": {},
                "session_messages": [],
                "prompt_metrics": {},
                "memory_profile": {"prompt_mode": "standard"},
            },
        },
    )

    combined = "\n".join(str(item.get("content") or "") for item in messages)
    assert "Follow-up continuity for this turn" in combined
    assert "Active specialist lane for this turn" in combined
    assert prompt_metrics["followup_continuity_guidance"] is True
    assert prompt_metrics["specialist_continuity_guidance"] is True


def test_conversation_memory_structural_followup_flags_cover_short_natural_turns(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())

    cases = {
        "same?": "ultra_short_followup",
        "there?": "ultra_short_followup",
        "browser too?": "short_scope_followup",
        "telegram too?": "short_scope_followup",
        "cloud too?": "environment_followup",
        "does that carry over?": "referential_followup",
        "and telegram?": "short_scope_followup",
        "and on hud?": "short_scope_followup",
        "what about telegram then?": "environment_followup",
        "same in the bot?": "short_scope_followup",
    }

    for query, expected_flag in cases.items():
        flags = memory._conversational_followup_flags(query)
        assert flags[expected_flag] is True, query


@pytest.mark.parametrize(
    "query",
    [
        "can you unpack that a little?",
        "what are you really meaning there?",
        "walk me through that more",
        "how are you thinking about that exactly?",
        "would you expand that point a little?",
    ],
)
def test_conversation_memory_detects_structural_explanatory_followups_without_exact_phrase_match(
    tmp_path: Path,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I mean the system still needs the memory, latency, and surface behavior to feel like one coherent thing.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query=query,
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


def test_conversation_memory_detects_cross_surface_followup_without_exact_phrase_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="The current thread is response latency, Telegram grounding, and HUD improvements across the surfaces.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="does that still line up in legion?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]


@pytest.mark.parametrize(
    "query",
    [
        "and telegram?",
        "and on hud?",
        "what about telegram then?",
        "same in the bot?",
    ],
)
def test_conversation_memory_detects_bridge_surface_followups_without_exact_phrase_match(
    tmp_path: Path,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="The current thread is response latency, Telegram grounding, and HUD improvements across the surfaces.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query=query,
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]


@pytest.mark.parametrize(
    "query",
    [
        "what are you still centered on?",
        "what are you still orbiting around?",
        "what are you still holding together here?",
        "what is still live in this for you?",
    ],
)
def test_conversation_memory_recalls_recent_work_from_additional_natural_session_phrasings(
    tmp_path: Path,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can keep response latency, Telegram grounding, and HUD improvements in focus.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query=query,
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_memory_answer"]
    assert grounded is not None
    assert grounded["kind"] == "session"
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]


def test_conversation_memory_detects_cross_surface_tracking_prompt_without_exact_phrase_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="The current thread is response latency, Telegram grounding, and HUD improvements across the surfaces.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what still tracks across telegram and hud?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]


def test_conversation_memory_detects_carry_over_there_followup_without_exact_phrase_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, Telegram grounding, and HUD improvements.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="The current thread is response latency, Telegram grounding, and HUD improvements across the surfaces.",
        confidence=0.9,
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what still carries over there?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "followup"
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]


def test_conversation_memory_detects_semantic_landing_direction_prompt(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="where is all this starting to land for us?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "direction"
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


def test_conversation_memory_detects_semantic_values_prompt_from_realness_language(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what seems most real about this now?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "values"
    assert "matters most" in grounded["answer"] or "response latency" in grounded["answer"]


def test_conversation_memory_detects_semantic_take_shape_prompt_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what are you actually seeing take shape here?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "state"
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


@pytest.mark.parametrize(
    ("query", "expected_kind"),
    [
        ("what is this becoming for us now?", "direction"),
        ("where do you think this system is really heading now?", "direction"),
        ("what feels most solid in this now?", "state"),
        ("what seems most grounded in the project right now?", "state"),
        ("what is this really for at this point?", "mission"),
        ("why are we building this system in the first place?", "mission"),
        ("what feels most alive about this now?", "values"),
        ("what matters most in this right now to you?", "values"),
    ],
)
def test_conversation_memory_detects_broader_structural_reflection_prompts_without_exact_phrase_match(
    tmp_path: Path,
    query: str,
    expected_kind: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query=query,
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == expected_kind
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


@pytest.mark.parametrize(
    ("query", "expected_kind"),
    [
        ("what keeps throwing this off?", "risk"),
        ("where does this still wobble?", "risk"),
        ("what still seems messy to you?", "risk"),
        ("how is this all sitting with you now?", "state"),
        ("where is this still fighting itself?", "risk"),
        ("what is still getting in our own way?", "risk"),
        ("what is starting to stabilize now?", "state"),
        ("what still looks unresolved to you?", "risk"),
        ("what seems like the biggest drag on coherence?", "risk"),
    ],
)
def test_conversation_memory_detects_more_natural_reflective_prompts_without_exact_family_match(
    tmp_path: Path,
    query: str,
    expected_kind: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query=query,
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == expected_kind
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


def test_conversation_memory_detects_pressure_point_prompt_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )

    bundle = memory.build_context(
        session_id=session_id,
        query="what still seems like the main pressure point?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    grounded = bundle["grounded_conversation_answer"]
    assert grounded is not None
    assert grounded["kind"] == "risk"
    assert "response latency" in grounded["answer"] or "memory grounding" in grounded["answer"]


def test_heuristic_fallback_uses_structural_followup_flags_for_ultra_short_turns() -> None:
    backend = HeuristicChatBackend()
    context = (
        "Conversation continuity:\n"
        "- Ongoing thread: response latency, Telegram grounding, and HUD cohesion.\n"
        "- Last stable assistant context: I can keep the latency, Telegram grounding, and HUD cohesion work coherent.\n\n"
        "Specialist continuity:\n"
        "- Last selected specialist: coding.\n"
        "- Last task focus: coding_help.\n"
    )

    response = backend._grounded_context_fallback("same?", context)

    assert "latency, Telegram grounding, and HUD cohesion" in response
    assert "coding lane" in response


def test_broad_grounded_conversation_still_drives_fallback_when_backend_misses(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, shared memory grounding, and HUD cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can keep the latency, memory grounding, and HUD cohesion work in focus.",
        confidence=0.9,
    )

    class LocalFailingBackend(BaseChatBackend):
        def generate(self, messages, **options):
            raise ChatBackendError("Ollama request timed out after 18s for model llama3.2:3b")

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )
    agent = JARVISAgent(chat_backend=LocalFailingBackend(), fallback_backend=HeuristicChatBackend())

    result = daise.process_request(
        user_input="from everything you remember, what feels weakest in the system right now?",
        agents={"jarvis": agent},
        context={"request_id": "grounded-conversation-fallback-1", "analysis_mode": "standard", "session_id": session_id},
    )

    # A failed live turn reports the backend that actually failed; the fallback
    # identity lives in backend metadata instead of the provider name.
    assert result["provider"] == "local"
    assert result["model"] == "bounded-context-grounded"
    assert result["backend_metadata"]["fallback_provider"] == "heuristic"
    assert result["backend_metadata"]["response_path"] == "bounded_context_fallback"
    assert "weakest areas" in result["response"]
    assert "response latency" in result["response"] or "shared memory grounding" in result["response"]


def test_daise_uses_grounded_conversation_for_take_shape_prompt_without_exact_family_match(tmp_path: Path) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can keep response latency, memory grounding, and Telegram cohesion in focus.",
        confidence=0.9,
    )

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input="what are you actually seeing take shape here?",
        agents={"jarvis": FakeJarvisAgent()},
        context={"request_id": "grounded-conversation-take-shape-1", "analysis_mode": "standard", "session_id": session_id},
    )

    assert result["provider"] == "conversation_memory"
    assert result["model"] == "deterministic-conversation-grounded"
    assert "response latency" in result["response"] or "memory grounding" in result["response"]


@pytest.mark.parametrize(
    "query",
    [
        "can you unpack that a little?",
        "what are you really meaning there?",
        "walk me through that more",
        "how are you thinking about that exactly?",
        "would you expand that point a little?",
        "tell me more about that",
        "why do you think that?",
        "what makes you say that?",
        "why though?",
        "expand on that",
        "continue that",
        "what still carries over there?",
        "what still tracks across telegram and hud?",
        "what keeps throwing this off?",
        "where does this still wobble?",
        "what still seems messy to you?",
        "how is this all sitting with you now?",
        "where is this still fighting itself?",
        "what is still getting in our own way?",
        "what is starting to stabilize now?",
        "what still looks unresolved to you?",
        "what seems like the biggest drag on coherence?",
        "what still seems like the main pressure point?",
        "what is this becoming for us now?",
        "where do you think this system is really heading now?",
        "what feels most solid in this now?",
        "what seems most grounded in the project right now?",
        "what is this really for at this point?",
        "why are we building this system in the first place?",
        "what feels most alive about this now?",
        "what matters most in this right now to you?",
    ],
)
def test_daise_uses_grounded_conversation_for_more_natural_reflective_prompts_without_exact_family_match(
    tmp_path: Path,
    query: str,
) -> None:
    memory = _build_memory(tmp_path, vector_store=FakeVectorStore())
    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text="We have been working on response latency, memory grounding, and Telegram cohesion.",
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can keep response latency, memory grounding, and Telegram cohesion in focus.",
        confidence=0.9,
    )

    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=memory.vector_store,
        memory_service=memory,
    )

    result = daise.process_request(
        user_input=query,
        agents={"jarvis": FakeJarvisAgent()},
        context={"request_id": f"grounded-conversation-natural-{abs(hash(query))}", "analysis_mode": "standard", "session_id": session_id},
    )

    assert result["provider"] == "conversation_memory"
    assert result["model"] == "deterministic-conversation-grounded"
    assert "response latency" in result["response"] or "memory grounding" in result["response"]


def test_broad_grounded_conversation_resists_recent_knowledge_lookup_topic_contamination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        '{"collection":"spawn","domain":"comics","kind":"collection_summary","text":"Spawn centers on Al Simmons, a murdered operative who returns as Hellspawn after bargaining with Malebolgia."}',
        encoding="utf-8",
    )

    session_id = memory.create_session_id()
    correlation_id = memory.create_correlation_id()

    memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text='Call me "Five". We have been working on response latency, Telegram grounding, and HUD improvements.',
    )
    memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name="daise",
        response_text="I can keep response latency, Telegram grounding, and HUD improvements in focus.",
        confidence=0.9,
    )

    memory.build_context(
        session_id=session_id,
        query="what do we know about spawn so far?",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )
    bundle = memory.build_context(
        session_id=session_id,
        query="talk to me honestly about where you think OBEOS is heading",
        agent_name="jarvis",
        analysis_mode="standard",
        retrieval_profile="standard",
    )
    grounded = bundle["grounded_conversation_answer"]

    assert grounded is not None
    assert grounded["kind"] == "direction"
    assert "response latency" in grounded["answer"] or "Telegram grounding" in grounded["answer"]
    assert "Spawn knowledge recall" not in grounded["answer"]


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


def test_standard_retrieval_prefers_collection_summary_for_spawn_from_natural_knowledge_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        query="what do we know about spawn so far?",
        agent_name="daise",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    assert bundle["grounded_knowledge_answer"] is not None
    assert bundle["grounded_knowledge_answer"]["collection"] == "spawn"
    assert "Spawn centers on Al Simmons" in bundle["grounded_knowledge_answer"]["answer"]


def test_standard_retrieval_prefers_collection_summary_for_spawn_from_still_know_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        query="do you still know anything about spawn?",
        agent_name="daise",
        analysis_mode="standard",
        retrieval_profile="standard",
    )

    assert bundle["grounded_knowledge_answer"] is not None
    assert bundle["grounded_knowledge_answer"]["collection"] == "spawn"
    assert "Spawn centers on Al Simmons" in bundle["grounded_knowledge_answer"]["answer"]


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
        raise AssertionError("the explicit test backend, not the routed one, writes the reply")

    agent = JARVISAgent(chat_backend=EvidenceEchoBackend(), fallback_backend=HeuristicChatBackend())
    monkeypatch.setattr(jarvis_agent_module, "build_routed_chat_backend", _should_not_reach_ollama)

    result = agent.process(
        "tell me about spawn",
        context={"analysis_mode": "standard", "memory_context": bundle},
    )

    assert result["provider"] == "fake_model"
    assert result["model"] == "fake-model"
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


def test_grounded_collection_answer_handles_obeos_resource_overview_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(conversation_memory_module, "paths", temp_paths)

    vector_store = FakeVectorStore()
    vector_store.upsert(
        ids=["obeos-1", "obeos-2"],
        texts=[
            "O.B.E.O.S includes Thoughtglass HUD, Memory Chamber, D.A.I.S.E., the native workspace shell, and governed ecosystem tools.",
            "The OBEOS architecture centers on one shared memory stack and multiple operator surfaces.",
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

    answer = memory.grounded_collection_answer(
        query="what resources within OBEOS do we have?",
        retrieval_profile="standard",
        analysis_mode="standard",
    )

    assert answer is not None
    assert "Thoughtglass" in answer["answer"] or "Memory Chamber" in answer["answer"]
    assert "browser HUD" in answer["answer"] or "D.A.I.S.E." in answer["answer"]


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
    expected_timeout = model_routing_module.OllamaRoutingChatBackend._capped_timeout(
        {"mode": "standard", "timeout": 90.0},
        chain_length=2,
    )
    assert all(item["timeout"] == expected_timeout for item in calls)
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


def test_heuristic_fallback_drops_noisy_ocr_excerpt_for_social_checkin() -> None:
    backend = HeuristicChatBackend()
    result = backend.generate(
        [
            {
                "role": "system",
                "content": (
                    "Conversation memory and ecosystem context:\n"
                    "[Source 1] title=Spawn | kind=knowledge_collection | collection=spawn\n"
                    "UFg AT AM ZJbOQ r v^eze MOf (jOfNO TO V WOP. Er^'i L: J! i! [PAGE 1]\n"
                ),
            },
            {"role": "user", "content": "are you still there?"},
        ]
    )

    assert "I am here inside O.B.E.O.S." in result.text
    assert "UFg AT AM" not in result.text
