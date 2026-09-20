from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import chat_settings, paths
from .events import Event
from .memory_lifecycle_management import memory_lifecycle_manager
from .observability import LogLevel, log_action
from .retrieval_profiles import normalize_retrieval_profile, retrieval_profile_settings
from .storage import ChromaVectorStore, EventStore, NullVectorStore, VectorStore


CHAT_EVENT_TYPES = {"agent_query", "agent_response"}
MEMORY_EVENT_TYPES = {"session_summary", "knowledge_ingested"}
from .memory_persistence import MemoryPersistence, WORKFLOW_EVENT_TYPES
from .memory_retrieval import SemanticRetrieval


MEMORY_PROFILE_DEFAULTS: Dict[str, Any] = {
    "agent": "default",
    "transcript_window": chat_settings.transcript_window,
    "summary_limit": 3,
    "global_memory_limit": chat_settings.global_memory_limit,
    "recent_event_limit": 6,
    "memory_keywords": (),
    "preferred_event_sources": (),
    "preferred_event_types": ("session_summary", "knowledge_ingested", "reasoning_trace"),
}

AGENT_MEMORY_PROFILES: Dict[str, Dict[str, Any]] = {
    "daise": {
        "memory_keywords": ("preference", "remember", "history", "session", "context", "command", "hud"),
        "preferred_event_sources": ("daise", "jarvis", "voice"),
        "preferred_event_types": ("session_summary", "knowledge_ingested", "reasoning_trace", "service_registration"),
    },
    "jarvis": {
        "memory_keywords": ("preference", "remember", "history", "session", "context", "command", "hud"),
        "preferred_event_sources": ("jarvis", "daise", "voice"),
        "preferred_event_types": ("session_summary", "knowledge_ingested", "reasoning_trace", "service_registration"),
    },
    "coding": {
        "transcript_window": max(10, min(chat_settings.transcript_window, 14)),
        "summary_limit": 4,
        "global_memory_limit": max(chat_settings.global_memory_limit, 8),
        "recent_event_limit": 8,
        "memory_keywords": ("code", "python", "debug", "bug", "review", "test", "function", "module", "architecture"),
        "preferred_event_sources": ("coding", "jarvis", "daise"),
        "preferred_event_types": ("reasoning_trace", "knowledge_ingested", "session_summary", "mobray_interrogation"),
    },
    "lilo": {
        "transcript_window": max(8, min(chat_settings.transcript_window, 12)),
        "memory_keywords": ("study", "exam", "quiz", "training", "certification", "security", "progress", "topic"),
        "preferred_event_sources": ("lilo", "daise"),
        "preferred_event_types": ("knowledge_ingested", "session_summary", "reasoning_trace"),
    },
    "stitch": {
        "transcript_window": max(8, min(chat_settings.transcript_window, 12)),
        "memory_keywords": ("python", "exercise", "practice", "tutorial", "concept", "hint", "learning"),
        "preferred_event_sources": ("stitch", "coding", "daise"),
        "preferred_event_types": ("knowledge_ingested", "session_summary", "reasoning_trace"),
    },
    "voice": {
        "transcript_window": max(6, min(chat_settings.transcript_window, 10)),
        "global_memory_limit": max(4, min(chat_settings.global_memory_limit, 6)),
        "recent_event_limit": 10,
        "memory_keywords": ("voice", "audio", "wake", "microphone", "speech", "command"),
        "preferred_event_sources": ("voice", "jarvis", "daise"),
        "preferred_event_types": ("reasoning_trace", "service_registration", "session_summary"),
    },
}


class ConversationMemoryService(MemoryPersistence, SemanticRetrieval):
    """Shared session and global memory service for the chat surfaces."""

    def __init__(
        self,
        event_store: Optional[EventStore] = None,
        vector_store: Optional[VectorStore] = None,
    ) -> None:
        self.event_store = event_store or EventStore()
        self.event_store.init_db()
        self.vector_store = vector_store or self._build_vector_store()
        self._source_registry: Any = None
        self._collection_registry: Any = None
        self._provenance_service: Any = None
        self._derived_memory_bootstrapped = False
        self._derived_memory_signatures: Optional[set[tuple[str, str]]] = None

    @staticmethod
    def create_session_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def create_correlation_id() -> str:
        return str(uuid.uuid4())

    def maybe_create_session_summary(
        self,
        session_id: str,
        *,
        correlation_id: Optional[str] = None,
        force: bool = False,
    ) -> Optional[Event]:
        messages = self.get_session_messages(session_id, limit=chat_settings.transcript_window * 4)
        response_count = len([message for message in messages if message["event_type"] == "agent_response"])
        if not response_count:
            return None

        existing_summaries = self.event_store.list_events_for_session(
            session_id,
            types=["session_summary"],
            ascending=False,
            limit=10,
        )
        latest_summary_count = 0
        if existing_summaries:
            latest_summary_count = int(existing_summaries[0].payload.get("message_count", 0))

        if not force:
            if response_count < chat_settings.summary_cadence:
                return None
            if response_count == latest_summary_count:
                return None
            if response_count % chat_settings.summary_cadence != 0:
                return None

        summary_text = self._summarize_messages(messages)
        if not summary_text:
            return None

        event = Event.new(
            source="daise",
            type="session_summary",
            payload={
                "session_id": session_id,
                "summary": summary_text,
                "message_count": response_count,
                "generated_at": time.time(),
            },
            confidence=0.8,
            session_id=session_id,
            correlation_id=correlation_id,
            tags=["conversation", "session_summary"],
        )
        self.event_store.add_event(event)
        self._store_vector_memory(
            vector_id=f"session-summary:{event.id}",
            text=summary_text,
            metadata={
                "memory_type": "session_summary",
                "session_id": session_id,
                "event_id": event.id,
                "message_count": response_count,
            },
        )
        self._store_lifecycle_item(event, "trace")
        return event

    def pin_session_memory(
        self,
        session_id: str,
        *,
        text: Optional[str] = None,
        title: Optional[str] = None,
        event_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Event:
        chosen_text = text
        source_event_id = event_id
        if source_event_id and not chosen_text:
            source_event = self.event_store.get_event_by_id(source_event_id)
            if source_event:
                chosen_text = str(source_event.payload.get("text") or source_event.payload.get("response") or "")

        if not chosen_text:
            messages = self.get_session_messages(session_id, limit=20)
            for message in reversed(messages):
                if message["event_type"] == "agent_response":
                    chosen_text = message["text"]
                    source_event_id = message["event_id"]
                    break

        if not chosen_text:
            raise ValueError("No session memory text available to pin")

        memory_title = title or f"Pinned memory from {session_id[:8]}"
        event = Event.new(
            source="daise",
            type="knowledge_ingested",
            payload={
                "memory_kind": "pinned_memory",
                "title": memory_title,
                "text": chosen_text,
                "session_id": session_id,
                "source_event_id": source_event_id,
                "pinned": True,
                "tags": tags or [],
            },
            confidence=1.0,
            session_id=session_id,
            tags=["conversation", "pinned_memory", *(tags or [])],
        )
        self.event_store.add_event(event)
        self._store_vector_memory(
            vector_id=f"pinned-memory:{event.id}",
            text=chosen_text,
            metadata={
                "memory_type": "pinned_memory",
                "session_id": session_id,
                "event_id": event.id,
                "title": memory_title,
            },
        )
        self._store_lifecycle_item(event, "indexed")
        return event

    def _capture_derived_user_memories(
        self,
        *,
        session_id: str,
        source_event_id: str,
        text: str,
    ) -> None:
        cleaned = self._clean_grounded_memory_text(text)
        lowered = cleaned.lower()
        if not cleaned:
            return
        # Greetings, test probes, transport chatter, and fallback diagnostics
        # are conversation history, not durable operator knowledge.
        if self._looks_like_noise_topic(cleaned) and not any(
            marker in lowered
            for marker in ("working on", "improve", "build", "implement", "configure", "set up")
        ):
            return

        derived: List[Dict[str, Any]] = []
        if (
            "direct artificial intelligence system environment" in lowered
            or "you are d.a.i.s.e" in lowered
            or "you are daise" in lowered
        ):
            derived.append(
                {
                    "memory_kind": "operator_fact",
                    "title": "DAISE identity doctrine",
                    "text": "The operator named the assistant D.A.I.S.E. and defined it as Direct Artificial Intelligence System Environment.",
                    "tags": ["operator_fact", "identity", "daise"],
                }
            )
        if (
            "i am your creator" in lowered
            or "creator of this blackfong ecosystem" in lowered
            or "i made the whole system" in lowered
            or "creator of obeos" in lowered
        ):
            derived.append(
                {
                    "memory_kind": "operator_fact",
                    "title": "Operator role",
                    "text": "The operator described themself as the creator of O.B.E.O.S.",
                    "tags": ["operator_fact", "creator"],
                }
            )
        name_match = re.search(r'call+\s+me ["“]?([a-z0-9 _-]{2,24})["”]?', cleaned, re.IGNORECASE)
        if name_match:
            preferred_name = " ".join(name_match.group(1).split()).strip()
            derived.append(
                {
                    "memory_kind": "operator_fact",
                    "title": "Operator preferred name",
                    "text": f'The operator asked to be called "{preferred_name}".',
                    "tags": ["operator_fact", "preferred_name"],
                }
            )
        for topic in self._extract_work_topics(cleaned):
            derived.append(
                {
                    "memory_kind": "active_work_item",
                    "title": f"Active work: {topic}",
                    "text": f"The operator has been working on {topic}.",
                    "tags": ["active_work_item", "work_topic"],
                }
            )

        for item in derived:
            self._persist_derived_memory(
                session_id=session_id,
                source_event_id=source_event_id,
                memory_kind=str(item["memory_kind"]),
                title=str(item["title"]),
                text=str(item["text"]),
                tags=list(item.get("tags") or []),
            )

    def _ensure_derived_memory_bootstrap(self) -> None:
        if self._derived_memory_bootstrapped:
            return
        try:
            for event in reversed(self.event_store.list_events_by_type("agent_query", limit=320)):
                text = str(event.payload.get("text") or "")
                if not text.strip():
                    continue
                self._capture_derived_user_memories(
                    session_id=str((event.metadata or {}).get("session_id") or ""),
                    source_event_id=event.id,
                    text=text,
                )
        finally:
            self._derived_memory_bootstrapped = True

    def _persist_derived_memory(
        self,
        *,
        session_id: str,
        source_event_id: str,
        memory_kind: str,
        title: str,
        text: str,
        tags: List[str],
    ) -> None:
        normalized_text = self._clean_grounded_memory_text(text)
        if not normalized_text or self._derived_memory_exists(memory_kind=memory_kind, text=normalized_text):
            return
        event = Event.new(
            source="daise",
            type="knowledge_ingested",
            payload={
                "memory_kind": memory_kind,
                "title": title,
                "text": normalized_text,
                "session_id": session_id,
                "source_event_id": source_event_id,
                "derived": True,
                "tags": tags,
            },
            confidence=0.92,
            session_id=session_id,
            parent_event=source_event_id,
            tags=["conversation", memory_kind, *tags],
        )
        self.event_store.add_event(event)
        self._remember_derived_memory_signature(memory_kind=memory_kind, text=normalized_text)
        vector_hash = hashlib.sha1(f"{memory_kind}:{normalized_text.lower()}".encode("utf-8")).hexdigest()[:16]
        self._store_vector_memory(
            vector_id=f"{memory_kind}:{vector_hash}",
            text=normalized_text,
            metadata={
                "memory_type": memory_kind,
                "session_id": session_id,
                "event_id": event.id,
                "title": title,
                "derived": True,
            },
        )
        self._store_lifecycle_item(event, "indexed")

    def _derived_memory_exists(self, *, memory_kind: str, text: str) -> bool:
        normalized = self._clean_grounded_memory_text(text).lower()
        if not normalized:
            return False
        signatures = self._derived_memory_signature_index()
        return (memory_kind, normalized) in signatures

    def _derived_memory_signature_index(self) -> set[tuple[str, str]]:
        if self._derived_memory_signatures is not None:
            return self._derived_memory_signatures
        signatures: set[tuple[str, str]] = set()
        for event in self.event_store.list_events_by_type("knowledge_ingested", limit=2000):
            payload = event.payload or {}
            memory_kind = str(payload.get("memory_kind") or "").strip()
            if not memory_kind:
                continue
            normalized = self._clean_grounded_memory_text(str(payload.get("text") or "")).lower()
            if not normalized:
                continue
            signatures.add((memory_kind, normalized))
        self._derived_memory_signatures = signatures
        return signatures

    def _remember_derived_memory_signature(self, *, memory_kind: str, text: str) -> None:
        normalized = self._clean_grounded_memory_text(text).lower()
        if not normalized:
            return
        signatures = self._derived_memory_signature_index()
        signatures.add((memory_kind, normalized))

    def build_context(
        self,
        *,
        session_id: str,
        query: str,
        agent_name: Optional[str] = None,
        transcript_window: Optional[int] = None,
        global_memory_limit: Optional[int] = None,
        analysis_mode: str = "fast",
        prompt_budget_tokens: Optional[int] = None,
        skip_vector_search: bool = False,
        retrieval_profile: Optional[str] = None,
        request_id: Optional[str] = None,
        transport: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._ensure_derived_memory_bootstrap()
        resolved_retrieval_profile = normalize_retrieval_profile(retrieval_profile, analysis_mode)
        recall_kind = self._detect_grounded_memory_query(query)
        retrieval_settings = retrieval_profile_settings(resolved_retrieval_profile)
        profile = self._resolve_memory_profile(
            agent_name=agent_name,
            transcript_window=transcript_window,
            global_memory_limit=global_memory_limit,
        )
        prompt_mode = self._resolve_prompt_mode(profile["agent"], analysis_mode)
        transcript_limit = int(profile["transcript_window"])
        memory_limit = int(profile["global_memory_limit"])
        summary_limit = int(profile["summary_limit"])
        recent_event_limit = int(profile["recent_event_limit"])

        if prompt_mode == "quick_compact":
            transcript_limit = min(transcript_limit, 4)
            memory_limit = min(memory_limit, chat_settings.fast_global_memory_limit)
            summary_limit = min(summary_limit, 1)
            recent_event_limit = min(recent_event_limit, 2)
            prompt_budget_tokens = prompt_budget_tokens or chat_settings.fast_prompt_budget_tokens
        elif resolved_retrieval_profile in {"standard", "architecture"}:
            transcript_limit = min(transcript_limit, 10)
            memory_limit = min(memory_limit, chat_settings.standard_global_memory_limit)
            summary_limit = min(summary_limit, 3)
            recent_event_limit = min(recent_event_limit, 4)
            prompt_budget_tokens = prompt_budget_tokens or chat_settings.standard_prompt_budget_tokens
        else:
            prompt_budget_tokens = prompt_budget_tokens or chat_settings.deep_prompt_budget_tokens

        session_messages = self.get_session_messages(session_id, limit=transcript_limit * 2)
        session_messages = [message for message in session_messages
                            if message.get("prompt_eligible", True)]
        session_summaries = self._get_session_summaries(session_id, limit=summary_limit)
        memory_query = "" if (skip_vector_search or recall_kind is not None) else query
        global_memories = self.list_global_memories(
            query=memory_query,
            limit=memory_limit,
            agent_name=agent_name,
            session_id=session_id,
        )
        recent_events = self._get_recent_events_for_agent(
            query=query,
            agent_name=agent_name,
            session_id=session_id,
            limit=recent_event_limit,
        )
        semantic_bundle = self._retrieve_semantic_context(
            query=query,
            retrieval_profile=resolved_retrieval_profile,
            skip_vector_search=(skip_vector_search or recall_kind is not None),
        )
        grounded_knowledge_answer = None
        if recall_kind is None:
            grounded_knowledge_answer = self.grounded_collection_answer(
                query=query,
                retrieval_profile=resolved_retrieval_profile,
                analysis_mode=analysis_mode,
            )
        operator_profile = self.operator_profile_snapshot(
            session_id=session_id,
            global_memories=global_memories,
            session_messages=session_messages,
        )
        grounded_memory_answer = self.grounded_memory_answer(
            session_id=session_id,
            query=query,
            agent_name=profile["agent"],
            retrieval_profile=resolved_retrieval_profile,
            analysis_mode=analysis_mode,
            session_messages=session_messages,
            session_summaries=session_summaries,
            global_memories=global_memories,
            recent_events=recent_events,
            operator_profile=operator_profile,
        )
        grounded_conversation_answer = None
        if grounded_memory_answer is None and grounded_knowledge_answer is None:
            grounded_conversation_answer = self.grounded_conversational_answer(
                session_id=session_id,
                query=query,
                agent_name=profile["agent"],
                retrieval_profile=resolved_retrieval_profile,
                analysis_mode=analysis_mode,
                session_messages=session_messages,
                session_summaries=session_summaries,
                global_memories=global_memories,
                recent_events=recent_events,
                operator_profile=operator_profile,
                knowledge_hits=semantic_bundle["hits"],
                citations=semantic_bundle["citations"],
            )
        semantic_bundle["timing_ms"]["grounded_answer"] = round(
            float(((grounded_knowledge_answer or {}).get("timing_ms") or {}).get("librarian") or 0.0),
            2,
        )
        semantic_bundle["timing_ms"]["grounded_memory"] = round(
            float(((grounded_memory_answer or {}).get("timing_ms") or {}).get("memory_grounding") or 0.0),
            2,
        )
        semantic_bundle["timing_ms"]["grounded_conversation"] = round(
            float(((grounded_conversation_answer or {}).get("timing_ms") or {}).get("conversation_grounding") or 0.0),
            2,
        )
        focus_tokens = self._salient_query_tokens(query)
        if semantic_bundle["hits"] and focus_tokens:
            hinted_collections = self._detect_collection_hints(query)
            if hinted_collections:
                session_summaries = self._filter_session_summaries_for_query(
                    session_summaries,
                    focus_tokens=focus_tokens,
                    limit=min(summary_limit, 1),
                )
                global_memories = self._filter_global_memories_for_query(
                    global_memories,
                    focus_tokens=focus_tokens,
                    limit=min(memory_limit, 2),
                )
                recent_events = self._filter_recent_events_for_query(
                    recent_events,
                    focus_tokens=focus_tokens,
                    limit=min(recent_event_limit, 1),
                )
                global_memories = [
                    item
                    for item in global_memories
                    if str((item.get("metadata") or {}).get("session_id") or "") == session_id
                ]
                recent_events = [
                    item
                    for item in recent_events
                    if str(item.get("session_id") or "") == session_id
                ]

        prompt_context = self._format_prompt_context(
            session_id=session_id,
            agent_name=profile["agent"],
            query=query,
            session_messages=session_messages[-transcript_limit:],
            session_summaries=session_summaries,
            global_memories=global_memories,
            recent_events=recent_events,
            knowledge_context=semantic_bundle["context"],
            operator_profile=operator_profile,
            grounded_conversation_answer=grounded_conversation_answer,
            transcript_window=transcript_limit,
            prompt_budget_tokens=prompt_budget_tokens,
            prompt_mode=prompt_mode,
            retrieval_profile=resolved_retrieval_profile,
        )
        prompt_metrics = {
            "prompt_mode": prompt_mode,
            "prompt_budget_tokens": prompt_budget_tokens,
            "prompt_context_chars": len(prompt_context),
            "prompt_context_tokens_estimate": max(1, len(prompt_context) // 4) if prompt_context else 0,
            "session_messages": len(session_messages[-transcript_limit:]),
            "session_summaries": len(session_summaries),
            "global_memories": len(global_memories),
            "recent_events": len(recent_events),
            "semantic_hits": len(semantic_bundle["hits"]),
            "grounded_answer": bool(grounded_knowledge_answer),
            "grounded_memory": bool(grounded_memory_answer),
            "grounded_conversation": bool(grounded_conversation_answer),
            "operator_profile": bool(operator_profile.get("fact_statements") or operator_profile.get("active_work")),
            "truncated": (max(1, len(prompt_context) // 4) if prompt_context else 0) >= int(prompt_budget_tokens or 0),
        }
        retrieval_contract = {
            **self._build_retrieval_contract(
                resolved_retrieval_profile,
                retrieval_settings=retrieval_settings,
            ),
            "usage": "prompt_context",
        }
        backend_health = self.recent_backend_health(transport=transport)

        return {
            "agent_name": profile["agent"],
            "memory_profile": {
                "transcript_window": transcript_limit,
                "summary_limit": summary_limit,
                "global_memory_limit": memory_limit,
                "recent_event_limit": recent_event_limit,
                "analysis_mode": analysis_mode,
                "prompt_mode": prompt_mode,
                "prompt_budget_tokens": prompt_budget_tokens,
            },
            "session_messages": session_messages[-transcript_limit:],
            "session_summaries": session_summaries,
            "global_memories": global_memories,
            "recent_events": recent_events,
            "knowledge_hits": semantic_bundle["hits"],
            "knowledge_context": semantic_bundle["context"],
            "citations": semantic_bundle["citations"],
            "grounded_knowledge_answer": grounded_knowledge_answer,
            "grounded_memory_answer": grounded_memory_answer,
            "grounded_conversation_answer": grounded_conversation_answer,
            "operator_profile": operator_profile,
            "backend_health": backend_health,
            "retrieval_profile": resolved_retrieval_profile,
            "retrieval_contract": retrieval_contract,
            "timing_ms": semantic_bundle["timing_ms"],
            "prompt_context": prompt_context,
            "prompt_mode": prompt_mode,
            "prompt_metrics": prompt_metrics,
        }

    def grounded_collection_answer(
        self,
        *,
        query: str,
        retrieval_profile: Optional[str] = None,
        analysis_mode: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        resolved_profile = normalize_retrieval_profile(retrieval_profile, analysis_mode)
        settings = retrieval_profile_settings(resolved_profile)
        if not settings.get("include_semantic") or not str(query or "").strip():
            return None
        hinted_collections = self._detect_collection_hints(query)
        if not hinted_collections or not self._is_collection_overview_query(query, hinted_collections):
            return None
        source_registry, collection_registry = self._knowledge_registries()
        if source_registry is None:
            return None
        try:
            from .knowledge_intel.librarian import LibrarianAgent

            librarian = LibrarianAgent(
                registry=source_registry,
                vector_store=self.vector_store,
                chunks_dir=paths.knowledge_chunks_path,
                collection_registry=collection_registry,
                event_store=self.event_store,
            )
            started = time.perf_counter()
            answer_record = librarian.answer(
                query,
                collection=hinted_collections[0],
                top_k=4 if resolved_profile in {"deep", "architecture"} else 3,
                mode="deterministic",
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        except Exception:
            return None
        answer_text = str(answer_record.get("answer") or "").strip()
        if not answer_text:
            return None
        summary_payload, _summary_path = self._collection_summary_payload(hinted_collections[0])
        answer_text = self._compose_grounded_collection_answer(
            query=query,
            collection=hinted_collections[0],
            summary_payload=summary_payload,
            fallback_text=answer_text,
        )
        return {
            "collection": hinted_collections[0],
            "answer": answer_text,
            "provenance": list(answer_record.get("provenance") or []),
            "retrieved_count": int(answer_record.get("retrieved_count") or 0),
            "compute_used": str(answer_record.get("compute_used") or "structured_facts"),
            "fallback_reason": answer_record.get("fallback_reason"),
            "retrieval_profile": resolved_profile,
            "mode": "deterministic",
            "timing_ms": {"librarian": elapsed_ms},
        }

    def grounded_memory_answer(
        self,
        *,
        session_id: str,
        query: str,
        agent_name: Optional[str] = None,
        retrieval_profile: Optional[str] = None,
        analysis_mode: Optional[str] = None,
        session_messages: Optional[List[Dict[str, Any]]] = None,
        session_summaries: Optional[List[Dict[str, Any]]] = None,
        global_memories: Optional[List[Dict[str, Any]]] = None,
        recent_events: Optional[List[Dict[str, Any]]] = None,
        operator_profile: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        recall_kind = self._detect_grounded_memory_query(query)
        if recall_kind is None:
            return None

        started = time.perf_counter()
        resolved_retrieval_profile = normalize_retrieval_profile(retrieval_profile, analysis_mode)
        messages = list(session_messages or self.get_session_messages(session_id, limit=24))
        summaries = list(session_summaries or self._get_session_summaries(session_id, limit=3))
        if global_memories is None:
            memory_query = ""
            memories = self.list_global_memories(
                query=memory_query,
                limit=6 if resolved_retrieval_profile != "fast" else 4,
                agent_name=agent_name,
                session_id=session_id,
            )
        else:
            memories = list(global_memories)
        events = list(
            recent_events
            or self._get_recent_events_for_agent(
                query=query,
                agent_name=agent_name,
                session_id=session_id,
                limit=4,
            )
        )
        recent_global_summaries = self._list_recent_session_summaries(limit=4, exclude_session_id=session_id)
        resolved_operator_profile = operator_profile or self.operator_profile_snapshot(
            session_id=session_id,
            global_memories=memories,
            session_messages=messages,
        )
        operator_facts = list(resolved_operator_profile.get("fact_statements") or self._recent_operator_facts(limit=4))
        answer, provenance = self._compose_grounded_memory_answer(
            session_id=session_id,
            recall_kind=recall_kind,
            query=query,
            session_messages=messages,
            session_summaries=summaries,
            global_memories=memories,
            recent_events=events,
            recent_global_summaries=recent_global_summaries,
            operator_facts=operator_facts,
            operator_profile=resolved_operator_profile,
        )
        if not answer:
            return None

        timing_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "kind": recall_kind,
            "answer": answer,
            "retrieval_profile": resolved_retrieval_profile,
            "timing_ms": {"memory_grounding": timing_ms},
            "provenance": provenance,
            "operator_profile": resolved_operator_profile,
            "evidence_counts": {
                "session_messages": len(messages),
                "session_summaries": len(summaries),
                "global_memories": len(memories),
                "recent_events": len(events),
                "global_summaries": len(recent_global_summaries),
                "operator_facts": len(operator_facts),
            },
        }

    def grounded_conversational_answer(
        self,
        *,
        session_id: str,
        query: str,
        agent_name: Optional[str] = None,
        retrieval_profile: Optional[str] = None,
        analysis_mode: Optional[str] = None,
        session_messages: Optional[List[Dict[str, Any]]] = None,
        session_summaries: Optional[List[Dict[str, Any]]] = None,
        global_memories: Optional[List[Dict[str, Any]]] = None,
        recent_events: Optional[List[Dict[str, Any]]] = None,
        operator_profile: Optional[Dict[str, Any]] = None,
        knowledge_hits: Optional[List[Dict[str, Any]]] = None,
        citations: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        messages = list(session_messages or self.get_session_messages(session_id, limit=24))
        frame = self._detect_conversational_recall_frame(query)
        if frame is None:
            frame = self._detect_implicit_conversational_frame(query, messages)
        if frame is None:
            return None

        started = time.perf_counter()
        resolved_retrieval_profile = normalize_retrieval_profile(retrieval_profile, analysis_mode)
        summaries = list(session_summaries or self._get_session_summaries(session_id, limit=3))
        if global_memories is None:
            memories = self.list_global_memories(
                query="",
                limit=6 if resolved_retrieval_profile != "fast" else 4,
                agent_name=agent_name,
                session_id=session_id,
            )
        else:
            memories = list(global_memories)
        events = list(
            recent_events
            or self._get_recent_events_for_agent(
                query=query,
                agent_name=agent_name,
                session_id=session_id,
                limit=4,
            )
        )
        resolved_operator_profile = operator_profile or self.operator_profile_snapshot(
            session_id=session_id,
            global_memories=memories,
            session_messages=messages,
        )
        answer, provenance = self._compose_grounded_conversational_answer(
            session_id=session_id,
            frame=frame,
            query=query,
            session_messages=messages,
            session_summaries=summaries,
            global_memories=memories,
            recent_events=events,
            operator_profile=resolved_operator_profile,
            knowledge_hits=list(knowledge_hits or []),
            citations=list(citations or []),
        )
        if not answer:
            return None

        timing_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "kind": frame,
            "answer": answer,
            "retrieval_profile": resolved_retrieval_profile,
            "timing_ms": {"conversation_grounding": timing_ms},
            "provenance": provenance,
            "operator_profile": resolved_operator_profile,
            "evidence_counts": {
                "session_messages": len(messages),
                "session_summaries": len(summaries),
                "global_memories": len(memories),
                "recent_events": len(events),
                "knowledge_hits": len(list(knowledge_hits or [])),
            },
        }

    def knowledge_status(
        self,
        *,
        ensure_ecosystem: bool = False,
        max_files_per_root: int = 80,
    ) -> Dict[str, Any]:
        del ensure_ecosystem, max_files_per_root
        source_registry, collection_registry = self._knowledge_registries()
        source_count = len(source_registry.all()) if source_registry is not None else 0
        collections = collection_registry.all() if collection_registry is not None else []
        return {
            "status": "ok",
            "offline_first": True,
            "vector_authority": str(paths.vector_db_path),
            "events_authority": str(paths.events_db),
            "source_registry": str(paths.source_registry_path),
            "collection_registry": str(paths.collection_registry_path),
            "bundle_root": str(paths.knowledge_bundle_root),
            "bundle_available": self._knowledge_bundle_available(),
            "vector_backend": self.vector_store.__class__.__name__,
            "vector_available": not isinstance(self.vector_store, NullVectorStore),
            "registered_source_count": source_count,
            "collection_count": len(collections),
            "sync_endpoint": "/api/knowledge/sync",
            "collections": [
                {
                    "collection": item.collection,
                    "domain": item.domain,
                    "description": item.description,
                    "subdomain": item.subdomain,
                    "category": item.category,
                }
                for item in collections
            ],
        }

    def hydrate_knowledge_runtime(
        self,
        *,
        workspace_root: Optional[Path] = None,
        max_files_per_root: int = 80,
        force: bool = False,
    ) -> Dict[str, Any]:
        source_registry, collection_registry = self._knowledge_registries()
        source_count = len(source_registry.all()) if source_registry is not None else 0
        collection_count = len(collection_registry.all()) if collection_registry is not None else 0
        should_import_bundle = force or source_count == 0 or collection_count == 0
        bundle_report = {
            "status": "skipped",
            "bundle_root": str(paths.knowledge_bundle_root),
            "reason": "runtime_already_seeded",
            "bundle_available": self._knowledge_bundle_available(),
        }
        if should_import_bundle and self._knowledge_bundle_available():
            bundle_report = self.import_knowledge_bundle(paths.knowledge_bundle_root, force=force)

        ecosystem_sync = self.ensure_ecosystem_knowledge_collections(
            workspace_root=workspace_root,
            max_files_per_root=max_files_per_root,
            force=force,
        )
        final_status = "ok"
        if bundle_report.get("status") == "failed" or ecosystem_sync.get("status") != "ok":
            final_status = "failed"
        return {
            "status": final_status,
            "bundle_import": bundle_report,
            "ecosystem_sync": ecosystem_sync,
        }

    def import_knowledge_bundle(self, bundle_root: Path, *, force: bool = False) -> Dict[str, Any]:
        from .ingestion.source_registry import SourceRecord, SourceRegistry
        from .knowledge_intel.collection_registry import CollectionMetadata, CollectionRegistry

        bundle = Path(bundle_root)
        bundle_status = self._knowledge_bundle_status(bundle)
        if not bundle_status["bundle_available"]:
            return {
                "status": "skipped",
                "bundle_root": str(bundle),
                "reason": "bundle_missing",
                **bundle_status,
            }

        source_registry = self._source_registry or SourceRegistry(paths.source_registry_path)
        collection_registry = self._collection_registry or CollectionRegistry(paths.collection_registry_path)
        self._source_registry = source_registry
        self._collection_registry = collection_registry

        imported_collections = 0
        imported_sources = 0
        imported_chunks = 0
        copied_cleaned = 0
        copied_intel_files = 0

        bundle_collection_registry = bundle / "collections.json"
        if bundle_collection_registry.exists():
            collection_payload = json.loads(bundle_collection_registry.read_text(encoding="utf-8"))
            collection_items = collection_payload.get("collections", []) if isinstance(collection_payload, dict) else collection_payload
            for item in collection_items:
                meta = CollectionMetadata.from_dict(item)
                collection_registry.upsert(
                    meta.collection,
                    domain=meta.domain,
                    description=meta.description,
                    subdomain=meta.subdomain,
                    category=meta.category,
                )
                imported_collections += 1

        bundle_source_registry = bundle / "source_registry.json"
        if bundle_source_registry.exists():
            source_payload = json.loads(bundle_source_registry.read_text(encoding="utf-8"))
            source_items = source_payload.get("sources", []) if isinstance(source_payload, dict) else source_payload
            for item in source_items:
                record = SourceRecord.from_dict(item)
                if not force:
                    existing = source_registry.get(record.source_id)
                    if existing is not None and existing.checksum == record.checksum and existing.chunk_count >= record.chunk_count:
                        continue
                source_registry.upsert(record)
                imported_sources += 1

        bundle_chunks_root = bundle / "chunks"
        paths.knowledge_chunks_path.mkdir(parents=True, exist_ok=True)
        if bundle_chunks_root.exists():
            for chunk_file in sorted(bundle_chunks_root.glob("*.jsonl")):
                destination = paths.knowledge_chunks_path / chunk_file.name
                if force or not destination.exists():
                    shutil.copy2(chunk_file, destination)
                chunk_records = self._read_chunk_records(chunk_file)
                if chunk_records and not isinstance(self.vector_store, NullVectorStore):
                    self.vector_store.upsert(
                        ids=[item["chunk_id"] for item in chunk_records],
                        texts=[item["text"] for item in chunk_records],
                        metadatas=[item["metadata"] for item in chunk_records],
                    )
                imported_chunks += len(chunk_records)

        bundle_cleaned_root = bundle / "cleaned"
        paths.knowledge_cleaned_path.mkdir(parents=True, exist_ok=True)
        if bundle_cleaned_root.exists():
            for cleaned_file in sorted(bundle_cleaned_root.glob("*")):
                if not cleaned_file.is_file():
                    continue
                destination = paths.knowledge_cleaned_path / cleaned_file.name
                if force or not destination.exists():
                    shutil.copy2(cleaned_file, destination)
                    copied_cleaned += 1

        bundle_intel_root = bundle / "intel"
        if bundle_intel_root.exists():
            paths.knowledge_intel_path.mkdir(parents=True, exist_ok=True)
            for item in sorted(bundle_intel_root.rglob("*")):
                if not item.is_file():
                    continue
                relative = item.relative_to(bundle_intel_root)
                destination = paths.knowledge_intel_path / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if force or not destination.exists():
                    shutil.copy2(item, destination)
                    copied_intel_files += 1

        source_registry.save()
        collection_registry.save()
        self.persist_workflow_event(
            source="daise",
            event_type="knowledge_embedded",
            payload={
                "bundle_root": str(bundle),
                "imported_collections": imported_collections,
                "imported_sources": imported_sources,
                "imported_chunks": imported_chunks,
                "copied_cleaned": copied_cleaned,
                "copied_intel_files": copied_intel_files,
                "force": force,
            },
            tags=["knowledge", "bundle_import"],
        )
        return {
            "status": "ok",
            "bundle_root": str(bundle),
            "bundle_available": True,
            "imported_collections": imported_collections,
            "imported_sources": imported_sources,
            "imported_chunks": imported_chunks,
            "copied_cleaned": copied_cleaned,
            "copied_intel_files": copied_intel_files,
        }

    def export_knowledge_bundle(
        self,
        bundle_root: Path,
        *,
        collections: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        from .ingestion.source_registry import SourceRegistry, slugify_collection
        from .knowledge_intel.collection_registry import CollectionRegistry

        bundle = Path(bundle_root)
        if bundle.exists():
            shutil.rmtree(bundle)
        (bundle / "chunks").mkdir(parents=True, exist_ok=True)
        (bundle / "cleaned").mkdir(parents=True, exist_ok=True)
        (bundle / "intel").mkdir(parents=True, exist_ok=True)

        source_registry = self._source_registry or SourceRegistry(paths.source_registry_path)
        collection_registry = self._collection_registry or CollectionRegistry(paths.collection_registry_path)
        self._source_registry = source_registry
        self._collection_registry = collection_registry

        allowed_collections = {slugify_collection(item) for item in (collections or []) if item}
        source_records = []
        referenced_collections = set()
        for record in source_registry.all():
            if allowed_collections and record.resolved_collection() not in allowed_collections:
                continue
            source_records.append(record.to_dict())
            referenced_collections.add(record.resolved_collection())
            chunk_path = paths.knowledge_chunks_path / f"{record.source_id}.jsonl"
            if chunk_path.exists():
                shutil.copy2(chunk_path, bundle / "chunks" / chunk_path.name)
            cleaned_path = paths.knowledge_cleaned_path / f"{record.source_id}.txt"
            if cleaned_path.exists():
                shutil.copy2(cleaned_path, bundle / "cleaned" / cleaned_path.name)

        collection_items = []
        for meta in collection_registry.all():
            if referenced_collections and meta.collection not in referenced_collections:
                continue
            collection_items.append(meta.to_dict())
            intel_dir = paths.knowledge_intel_path / meta.collection
            if intel_dir.exists():
                shutil.copytree(intel_dir, bundle / "intel" / meta.collection, dirs_exist_ok=True)

        (bundle / "source_registry.json").write_text(
            json.dumps({"version": 1, "sources": source_records}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (bundle / "collections.json").write_text(
            json.dumps({"version": 1, "collections": collection_items}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return {
            "status": "ok",
            "bundle_root": str(bundle),
            "exported_sources": len(source_records),
            "exported_collections": len(collection_items),
        }

    def knowledge_search(
        self,
        *,
        query: str,
        retrieval_profile: Optional[str] = None,
        analysis_mode: Optional[str] = None,
        skip_vector_search: bool = False,
        max_context_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        resolved_retrieval_profile = normalize_retrieval_profile(retrieval_profile, analysis_mode)
        retrieval_settings = retrieval_profile_settings(resolved_retrieval_profile)
        semantic_bundle = self._retrieve_semantic_context(
            query=query,
            retrieval_profile=resolved_retrieval_profile,
            skip_vector_search=skip_vector_search,
        )
        context = semantic_bundle["context"]
        if max_context_chars is not None:
            context = self._render_retrieval_context(
                semantic_bundle["hits"],
                max_chars=max(0, int(max_context_chars)),
            )
        return {
            "query": query,
            "retrieval_profile": resolved_retrieval_profile,
            "retrieval_contract": self._build_retrieval_contract(
                resolved_retrieval_profile,
                retrieval_settings=retrieval_settings,
            ),
            "context": context,
            "citations": semantic_bundle["citations"],
            "hits": semantic_bundle["hits"],
            "timing_ms": semantic_bundle["timing_ms"],
        }

    def ingest_knowledge_text(
        self,
        *,
        text: str,
        title: Optional[str] = None,
        collection: Optional[str] = None,
        domain: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source_path: Optional[str] = None,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        cleaned = str(text or "").strip()
        if not cleaned:
            raise ValueError("Knowledge text is required")

        from .ingestion.source_registry import STATUS_EMBEDDED, SourceRegistry, slugify_collection
        from .knowledge_intel.collection_registry import CollectionRegistry

        collection_slug = slugify_collection(collection or "obeos")
        paths.text_inbox_path.mkdir(parents=True, exist_ok=True)
        source_registry = self._source_registry or SourceRegistry(paths.source_registry_path)
        collection_registry = self._collection_registry or CollectionRegistry(paths.collection_registry_path)
        self._source_registry = source_registry
        self._collection_registry = collection_registry

        ingest_id = uuid.uuid4().hex[:12]
        safe_title = re.sub(r"[^A-Za-z0-9_.-]+", "-", (title or collection_slug or "knowledge-note")).strip("-") or "knowledge-note"
        resolved_source_path = Path(source_path) if source_path else (paths.text_inbox_path / f"{safe_title}-{ingest_id}.md")
        resolved_source_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_source_path.write_text(cleaned, encoding="utf-8")

        collection_registry.upsert(
            collection_slug,
            domain=domain or collection_registry.domain_for(collection_slug) or "obeos_ecosystem",
        )
        record, _changed = source_registry.register(
            source_type="repo_text",
            path=resolved_source_path,
            series=collection_slug.replace("_", " ").title(),
            issue_range="adhoc",
            collection=collection_slug,
            domain=domain or collection_registry.domain_for(collection_slug),
            compute_hash=True,
        )
        record.embedded = True
        record.processing_status = STATUS_EMBEDDED

        chunks = self._chunk_knowledge_text(cleaned)
        chunk_records: List[Dict[str, Any]] = []
        vector_ids: List[str] = []
        texts: List[str] = []
        metadatas: List[Dict[str, Any]] = []
        for chunk_index, chunk_text in enumerate(chunks):
            chunk_id = f"knowledge:{record.source_id}:{chunk_index}"
            chunk_metadata = {
                "source_id": record.source_id,
                "collection": collection_slug,
                "domain": domain or collection_registry.domain_for(collection_slug),
                "source_path": str(resolved_source_path),
                "title": title or resolved_source_path.name,
                "section": f"chunk_{chunk_index}",
                "chunk_index": chunk_index,
                "source_type": "repo_text",
                "tags": ",".join(tags or []),
            }
            vector_ids.append(chunk_id)
            texts.append(chunk_text)
            metadatas.append(chunk_metadata)
            chunk_records.append(
                {
                    "chunk_id": chunk_id,
                    "source": title or resolved_source_path.name,
                    "text": chunk_text,
                    "metadata": chunk_metadata,
                }
            )

        record.chunk_count = len(chunk_records)
        source_registry.save()
        collection_registry.save()

        chunk_file = paths.knowledge_chunks_path / f"{record.source_id}.jsonl"
        paths.knowledge_chunks_path.mkdir(parents=True, exist_ok=True)
        chunk_file.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in chunk_records) + ("\n" if chunk_records else ""),
            encoding="utf-8",
        )

        if not isinstance(self.vector_store, NullVectorStore) and vector_ids:
            self.vector_store.upsert(ids=vector_ids, texts=texts, metadatas=metadatas)

        event = Event.new(
            source="daise",
            type="knowledge_ingested",
            payload={
                "source_id": record.source_id,
                "source_path": str(resolved_source_path),
                "collection": collection_slug,
                "domain": domain or collection_registry.domain_for(collection_slug),
                "title": title or resolved_source_path.name,
                "chunk_count": len(chunk_records),
                "tags": tags or [],
                "ingested_via": "api_knowledge_text",
            },
            confidence=1.0,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["knowledge", "api", *(tags or [])],
        )
        self.event_store.add_event(event)
        self._store_lifecycle_item(event, "indexed")
        return {
            "status": "ok",
            "source_id": record.source_id,
            "collection": collection_slug,
            "source_path": str(resolved_source_path),
            "chunk_count": len(chunk_records),
            "vector_ids": vector_ids,
            "event_id": event.id,
        }

    def ensure_ecosystem_knowledge_collections(
        self,
        *,
        workspace_root: Optional[Path] = None,
        max_files_per_root: int = 80,
        force: bool = False,
    ) -> Dict[str, Any]:
        from .ecosystem_knowledge import sync_governed_ecosystem_collections

        return sync_governed_ecosystem_collections(
            memory_service=self,
            workspace_root=workspace_root or paths.root,
            max_files_per_root=max_files_per_root,
            force=force,
        )

    def benchmark_retrieval(
        self,
        *,
        query: str,
        session_id: Optional[str] = None,
        agent_name: str = "daise",
        profiles: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        target_session = session_id or self.create_session_id()
        selected_profiles = profiles or ["fast", "standard", "deep"]
        results: List[Dict[str, Any]] = []
        for raw_profile in selected_profiles:
            profile = normalize_retrieval_profile(raw_profile)
            started = time.perf_counter()
            bundle = self.build_context(
                session_id=target_session,
                query=query,
                agent_name=agent_name,
                analysis_mode=profile,
                retrieval_profile=profile,
                skip_vector_search=False,
            )
            completed = time.perf_counter()
            timing_ms = dict(bundle.get("timing_ms") or {})
            timing_ms["total_context_build"] = round((completed - started) * 1000, 2)
            results.append(
                {
                    "retrieval_profile": profile,
                    "knowledge_hits": len(bundle.get("knowledge_hits") or []),
                    "citations": len(bundle.get("citations") or []),
                    "context_chars": len(str(bundle.get("knowledge_context") or "")),
                    "prompt_context_chars": len(str(bundle.get("prompt_context") or "")),
                    "timing_ms": timing_ms,
                    "prompt_metrics": bundle.get("prompt_metrics") or {},
                }
            )
        return {
            "status": "ok",
            "query": query,
            "session_id": target_session,
            "profiles": results,
            "vector_authority": str(paths.vector_db_path),
        }

    def get_session_messages(self, session_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        events = self.event_store.list_events_for_session(
            session_id,
            types=["agent_query", "agent_response", "operator_action_result"],
            ascending=False,
            limit=limit,
        )
        messages = []
        for event in reversed(events):
            text = str(event.payload.get("text") or event.payload.get("response") or "")
            if event.type == "agent_response":
                role = "assistant"
            elif event.type == "operator_action_result":
                role = "system"
            else:
                role = "user"
            messages.append(
                {
                    "event_id": event.id,
                    "event_type": event.type,
                    "role": role,
                    "text": text,
                    "agent": event.payload.get("agent") or event.payload.get("owned_by") or event.source,
                    "timestamp": event.timestamp / 1000.0,
                    "metadata": event.metadata,
                    "prompt_eligible": self._response_prompt_eligible(event.type, event.payload),
                }
            )
        return messages[-limit:]

    @staticmethod
    def _response_prompt_eligible(event_type: str, payload: Dict[str, Any]) -> bool:
        if event_type != "agent_response":
            return True
        metadata = payload.get("backend_metadata") or payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        return not (
            payload.get("error") or payload.get("backend_error")
            or payload.get("success") is False
            or payload.get("status") in {"error", "failed", "cancelled"}
            or payload.get("demonstrably_incorrect") is True
            or payload.get("response_complete") is False
            or metadata.get("response_complete") is False
            or metadata.get("finish_reason") == "length"
            or metadata.get("fallback_mode") in {"reactive", "preemptive"}
        )

    def list_sessions(self, limit: int = 100) -> List[Dict[str, Any]]:
        sessions = self.event_store.list_recent_sessions(limit=limit)
        enriched = []
        for session in sessions:
            duration = max(0.0, session["last_activity"] - session["start_time"])
            enriched.append(
                {
                    "session_id": session["session_id"],
                    "start_time": session["start_time"],
                    "last_activity": session["last_activity"],
                    "duration": duration,
                    "total_requests": session["message_count"],
                    "total_errors": 0,
                    "agents_used": session["agents"],
                    "status": "active",
                    "preview": session["preview"],
                    "event_count": session["event_count"],
                }
            )
        return enriched

    def get_session_details(self, session_id: str) -> Optional[Dict[str, Any]]:
        sessions = {session["session_id"]: session for session in self.list_sessions(limit=500)}
        session = sessions.get(session_id)
        if session is None:
            return None
        messages = self.get_session_messages(session_id, limit=200)
        summaries = self._get_session_summaries(session_id, limit=10)
        return {
            "session": session,
            "messages": messages,
            "summaries": summaries,
            "event_count": session["event_count"],
        }

    def review_conversations(
        self,
        *,
        limit: int = 8,
        session_id: Optional[str] = None,
        transport: Optional[str] = None,
        message_limit: int = 3,
    ) -> Dict[str, Any]:
        """Summarize real session health across local and transport-backed chats."""
        normalized_transport = str(transport or "").strip().lower() or None
        bounded_limit = max(1, int(limit or 1))
        bounded_message_limit = max(1, min(int(message_limit or 3), 8))
        if session_id:
            session_ids = [str(session_id)]
        else:
            pool = self.list_sessions(limit=max(bounded_limit * 12, 120))
            session_ids = [str(item["session_id"]) for item in pool]

        summaries: List[Dict[str, Any]] = []
        for current_session_id in session_ids:
            details = self.get_session_details(current_session_id)
            if details is None:
                continue
            events = self.event_store.list_events_for_session(
                current_session_id,
                limit=600,
                ascending=True,
            )
            summary = self._summarize_conversation_session(
                details["session"],
                events,
                message_limit=bounded_message_limit,
            )
            if normalized_transport and normalized_transport not in summary["transports"]:
                continue
            summaries.append(summary)
            if not session_id and len(summaries) >= bounded_limit:
                break

        return {
            "generated_at": time.time(),
            "filters": {
                "session_id": session_id,
                "transport": normalized_transport,
                "limit": bounded_limit,
                "message_limit": bounded_message_limit,
            },
            "sessions": summaries,
            "matched_sessions": len(summaries),
        }

    def list_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        events = self.event_store.recent_events(limit=limit)
        payloads = []
        for event in events:
            message = self._summarize_recent_event(event)
            payloads.append(
                {
                    "event_id": event.id,
                    "event_type": event.type,
                    "source": event.source,
                    "timestamp": event.timestamp / 1000.0,
                    "session_id": event.metadata.get("session_id"),
                    "correlation_id": event.metadata.get("correlation_id"),
                    "request_id": event.metadata.get("request_id"),
                    "message": message,
                    "payload": event.payload,
                    "metadata": event.metadata,
                }
            )
        return payloads

    def get_request_trace(self, request_id: str, limit: int = 400) -> Dict[str, Any]:
        events = self.event_store.list_events_for_request(
            request_id,
            limit=limit,
            ascending=True,
        )
        trace_events = [self._format_trace_event(event) for event in events]
        status = "pending"
        phase = "accepted"
        result_event_id = None
        session_id = None
        correlation_id = None

        for event in events:
            session_id = session_id or event.metadata.get("session_id")
            correlation_id = correlation_id or event.metadata.get("correlation_id")
            if event.type == "agent_response":
                result_event_id = event.id
                status = "error" if event.payload.get("success") is False else "complete"
                phase = "error" if status == "error" else "complete"
            elif event.type == "agent_selection":
                phase = "evaluation"
            elif event.type == "agent_evaluation":
                phase = "evaluation"
            elif event.type == "agent_report":
                phase = "agent"
            elif event.type == "memory_retrieval":
                phase = "memory"
            elif event.type == "routing_decision":
                phase = "routing"
            elif event.type == "policy_decision":
                phase = "policy"

        phases = self._normalize_request_phases(events)
        policy_decisions = [event.payload for event in events if event.type == "policy_decision"]
        queue_events = [event.payload for event in events if event.type == "queue_pressure_observation"]
        latency_events = [event.payload for event in events if event.type == "latency_observation"]
        retrieval_events = [event.payload for event in events if event.type == "memory_retrieval"]
        retrieval_observations = [event.payload for event in events if event.type == "memory_retrieval_observation"]
        request_outcomes = [event.payload for event in events if event.type == "request_outcome"]
        contradictions = [event.payload for event in events if event.type == "consistency_conflict"]
        diagnostic_hints = [event.payload for event in events if event.type in {"performance_snapshot", "backend_health_observation"}]
        provenance = [
            payload.get("provenance")
            for payload in (event.payload for event in events)
            if isinstance(payload.get("provenance"), dict)
        ]

        return {
            "request_id": request_id,
            "session_id": session_id,
            "correlation_id": correlation_id,
            "status": status,
            "phase": phase,
            "result_event_id": result_event_id,
            "events": trace_events,
            "phases": phases,
            "policy_decisions": policy_decisions,
            "memory_retrieval": retrieval_events[-1] if retrieval_events else None,
            "memory_retrieval_observation": retrieval_observations[-1] if retrieval_observations else None,
            "queue_pressure": queue_events[-1] if queue_events else None,
            "latency_observation": latency_events[-1] if latency_events else None,
            "request_outcome": request_outcomes[-1] if request_outcomes else None,
            "contradictions": contradictions,
            "diagnostic_hints": diagnostic_hints[-3:],
            "provenance": provenance[-8:],
        }

    def list_global_memories(
        self,
        query: str = "",
        limit: int = 6,
        *,
        agent_name: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        allowed_memory_types = {"session_summary", "pinned_memory", "operator_fact", "active_work_item"}
        candidates: List[Dict[str, Any]] = []
        if query and not isinstance(self.vector_store, NullVectorStore):
            try:
                results = self.vector_store.search(query, k=max(limit * 4, 8))
            except Exception:  # pragma: no cover - vector backend failure
                results = []
            for result in results:
                metadata = result.get("metadata", {})
                if metadata.get("memory_type") not in allowed_memory_types:
                    continue
                candidates.append(
                    {
                        "id": result.get("id"),
                        "memory_type": metadata.get("memory_type"),
                        "text": result.get("text", ""),
                        "metadata": metadata,
                        "relevance": float(result.get("relevance", 0.0)),
                    }
                )

        pinned_events = self.event_store.list_events_by_type("knowledge_ingested", limit=100)
        summary_events = self.event_store.list_events_by_type("session_summary", limit=100)
        for event in pinned_events:
            memory_kind = str(event.payload.get("memory_kind") or "")
            if memory_kind not in {"pinned_memory", "operator_fact", "active_work_item"}:
                continue
            candidates.append(
                {
                    "id": event.id,
                    "memory_type": memory_kind,
                    "text": str(event.payload.get("text", "")),
                    "metadata": event.payload,
                    "relevance": 0.58 if memory_kind == "operator_fact" else (0.54 if memory_kind == "active_work_item" else 0.5),
                }
            )
        for event in summary_events:
            candidates.append(
                {
                    "id": event.id,
                    "memory_type": "session_summary",
                    "text": str(event.payload.get("summary", "")),
                    "metadata": event.payload,
                    "relevance": 0.4,
                }
            )

        ranked_memories = self._rank_memory_candidates(
            candidates,
            query=query,
            agent_name=agent_name,
            session_id=session_id,
        )
        return ranked_memories[:limit]

    def get_memory_stats(self) -> Dict[str, Any]:
        pinned_memories = [
            event
            for event in self.event_store.list_events_by_type("knowledge_ingested", limit=200)
            if event.payload.get("memory_kind") == "pinned_memory"
        ]
        return {
            "sessions": len(self.list_sessions(limit=500)),
            "pinned_memories": len(pinned_memories),
            "recent_summaries": len(self.event_store.list_events_by_type("session_summary", limit=200)),
        }

    def recent_backend_health(
        self,
        *,
        transport: Optional[str] = None,
        lookback_seconds: float = 900.0,
        limit: int = 24,
    ) -> Dict[str, Any]:
        now = time.time()
        normalized_transport = str(transport or "").strip().lower()
        normalized_lookback = max(30.0, float(lookback_seconds))
        cutoff_ms = (now - normalized_lookback) * 1000.0
        timeout_window_seconds = min(180.0, normalized_lookback)
        state = "unknown"
        provider = ""
        configured_model = ""
        last_backend_error = ""
        last_observed_at = 0.0
        recent_timeout_count = 0
        recent_fallback_count = 0
        latest_failure_at = 0.0
        latest_transport_success_at = 0.0
        latest_global_ollama_success_at = 0.0
        latest_global_ollama_model = ""
        degraded_reason = ""
        recovery_reason = ""
        repeated_recent_failures = False
        allow_recovery_probe = False
        latest_degraded_observation_at = 0.0
        latest_unavailable_observation_at = 0.0

        latency_events = self.event_store.list_events_by_type("latency_observation", limit=limit)
        for event in latency_events:
            if event.timestamp < cutoff_ms:
                continue
            payload = event.payload or {}
            backend_metadata = payload.get("backend_metadata") or {}
            primary_provider = str(
                (backend_metadata.get("provider") or payload.get("provider") or "")
            ).strip()
            primary_model = str(
                (
                    backend_metadata.get("selected_model")
                    or backend_metadata.get("model")
                    or payload.get("selected_model")
                    or payload.get("model")
                    or ""
                )
            ).strip()
            event_transport = str(payload.get("transport") or "").strip().lower()
            execution_time_ms = float(payload.get("execution_time_ms") or 0.0)
            fallback_used = bool(payload.get("fallback_used"))
            backend_error = str(payload.get("backend_error") or "").strip()
            fallback_reason = str(payload.get("fallback_reason") or payload.get("timeout_reason") or "").strip()
            provider_name = primary_provider.lower()
            observed_at = event.timestamp / 1000.0
            age_seconds = max(0.0, now - observed_at)
            within_timeout_window = age_seconds <= timeout_window_seconds

            if execution_time_ms > 0 and not fallback_used and not backend_error and provider_name == "ollama":
                latest_global_ollama_success_at = max(latest_global_ollama_success_at, observed_at)
                if latest_global_ollama_success_at == observed_at:
                    latest_global_ollama_model = primary_model or latest_global_ollama_model or ""

            if normalized_transport and event_transport and event_transport != normalized_transport:
                continue

            if fallback_used or backend_error:
                if within_timeout_window:
                    recent_fallback_count += 1
                if within_timeout_window and ("timed out" in backend_error.lower() or "timed out" in fallback_reason.lower()):
                    recent_timeout_count += 1
                provider = primary_provider or provider or ""
                configured_model = primary_model or configured_model or ""
                last_backend_error = backend_error or fallback_reason or last_backend_error
                last_observed_at = max(last_observed_at, observed_at)
                latest_failure_at = max(latest_failure_at, observed_at)
            elif execution_time_ms > 0 and last_observed_at <= 0:
                provider = primary_provider or provider or ""
                configured_model = primary_model or configured_model or ""
                last_observed_at = max(last_observed_at, observed_at)
                if state == "unknown":
                    state = "healthy"
            if execution_time_ms > 0 and not fallback_used and not backend_error and provider_name == "ollama":
                latest_transport_success_at = max(latest_transport_success_at, observed_at)

        health_events = self.event_store.list_events_by_type("backend_health_observation", limit=max(limit, 8))
        for event in health_events:
            if event.timestamp < cutoff_ms:
                continue
            payload = event.payload or {}
            if payload.get("grounded_truth"):
                continue
            observed_status = str(payload.get("status") or "").strip().lower()
            if not observed_status:
                continue
            provider = str(payload.get("provider") or provider or "")
            configured_model = str(payload.get("configured_model") or configured_model or "")
            error = str(payload.get("error") or "").strip()
            observed_at = event.timestamp / 1000.0
            age_seconds = max(0.0, now - observed_at)
            within_timeout_window = age_seconds <= timeout_window_seconds
            last_observed_at = max(last_observed_at, observed_at)
            if observed_status in {"unavailable", "offline"}:
                latest_unavailable_observation_at = max(latest_unavailable_observation_at, observed_at)
                last_backend_error = error or last_backend_error
                if within_timeout_window:
                    state = "unavailable"
                    degraded_reason = "backend_unavailable"
                    break
            if observed_status == "degraded":
                latest_degraded_observation_at = max(latest_degraded_observation_at, observed_at)
                last_backend_error = error or last_backend_error
                if within_timeout_window and state != "unavailable":
                    state = "degraded"
                    degraded_reason = degraded_reason or "health_observation_degraded"
            elif observed_status == "available" and state == "unknown":
                state = "healthy"

        same_transport_recovered = latest_transport_success_at > latest_failure_at > 0
        global_recovered = latest_global_ollama_success_at > latest_failure_at > 0
        repeated_recent_failures = recent_timeout_count >= 2

        if latest_unavailable_observation_at > 0 and (now - latest_unavailable_observation_at) <= timeout_window_seconds:
            state = "unavailable"
            degraded_reason = degraded_reason or "backend_unavailable"
        elif same_transport_recovered:
            state = "healthy"
            provider = "ollama"
            configured_model = configured_model or latest_global_ollama_model
            last_backend_error = ""
            last_observed_at = max(last_observed_at, latest_transport_success_at)
            recovery_reason = "same_transport_success"
        elif global_recovered:
            state = "healthy"
            provider = "ollama"
            configured_model = latest_global_ollama_model or configured_model
            last_backend_error = ""
            last_observed_at = max(last_observed_at, latest_global_ollama_success_at)
            recovery_reason = "global_ollama_success"
        elif repeated_recent_failures:
            state = "degraded"
            degraded_reason = degraded_reason or "recent_repeated_timeouts"
        elif recent_timeout_count > 0:
            state = "degraded"
            degraded_reason = degraded_reason or "recent_transient_timeout"
            allow_recovery_probe = True
        elif latest_failure_at > 0 and (now - latest_failure_at) > timeout_window_seconds:
            state = "healthy"
            provider = provider or "ollama"
            recovery_reason = "stale_timeout_window_expired"
            last_backend_error = ""
        elif latest_degraded_observation_at > 0 and (now - latest_degraded_observation_at) <= timeout_window_seconds:
            state = "degraded"
            degraded_reason = degraded_reason or "health_observation_degraded"
            allow_recovery_probe = True

        if state == "degraded" and repeated_recent_failures:
            allow_recovery_probe = False
        elif state == "degraded" and latest_unavailable_observation_at > 0:
            allow_recovery_probe = False

        return {
            "state": state,
            "transport": normalized_transport or None,
            "provider": provider,
            "configured_model": configured_model,
            "recent_timeout_count": recent_timeout_count,
            "recent_fallback_count": recent_fallback_count,
            "last_backend_error": last_backend_error,
            "last_observed_at": last_observed_at or None,
            "lookback_seconds": normalized_lookback,
            "timeout_window_seconds": timeout_window_seconds,
            "latest_failure_at": latest_failure_at or None,
            "latest_transport_success_at": latest_transport_success_at or None,
            "latest_global_ollama_success_at": latest_global_ollama_success_at or None,
            "degraded_reason": degraded_reason or None,
            "recovery_reason": recovery_reason or None,
            "repeated_recent_failures": repeated_recent_failures,
            "allow_recovery_probe": allow_recovery_probe,
        }

    def _summarize_conversation_session(
        self,
        session: Dict[str, Any],
        events: List[Event],
        *,
        message_limit: int,
    ) -> Dict[str, Any]:
        transport_counts: Counter[str] = Counter()
        provider_counts: Counter[str] = Counter()
        retrieval_profile_counts: Counter[str] = Counter()
        execution_times: List[float] = []
        recent_queries: List[Dict[str, Any]] = []
        recent_responses: List[Dict[str, Any]] = []
        fallback_requests: List[Dict[str, Any]] = []
        last_query_by_request: Dict[str, str] = {}
        grounded_truth_count = 0
        fallback_count = 0
        timeout_count = 0

        for event in events:
            payload = event.payload or {}
            request_id = str(event.metadata.get("request_id") or "")
            if event.type == "agent_query":
                query_text = self._compact_review_text(payload.get("text"))
                if query_text:
                    last_query_by_request[request_id] = query_text
                    recent_queries.append(
                        {
                            "request_id": request_id,
                            "timestamp": event.timestamp / 1000.0,
                            "text": query_text,
                        }
                    )
                continue

            if event.type == "agent_response":
                response_text = self._compact_review_text(payload.get("text") or payload.get("response"))
                if response_text:
                    recent_responses.append(
                        {
                            "request_id": request_id,
                            "timestamp": event.timestamp / 1000.0,
                            "text": response_text,
                            "provider": str(payload.get("provider") or ""),
                        }
                    )
                continue

            if event.type != "request_outcome":
                continue

            resolved_transport = str(payload.get("transport") or "local").strip().lower() or "local"
            provider = str(payload.get("provider") or "unknown").strip() or "unknown"
            retrieval_profile = str(payload.get("retrieval_profile") or "unknown").strip() or "unknown"
            backend_error = str(payload.get("backend_error") or "").strip()
            fallback_reason = str(payload.get("fallback_reason") or "").strip()
            execution_time_ms = float(payload.get("execution_time_ms") or 0.0)
            fallback_used = bool(payload.get("fallback_used"))

            transport_counts[resolved_transport] += 1
            provider_counts[provider] += 1
            retrieval_profile_counts[retrieval_profile] += 1
            if execution_time_ms > 0:
                execution_times.append(execution_time_ms)
            if payload.get("reason") == "grounded_truth" or payload.get("grounded_truth_kind"):
                grounded_truth_count += 1
            if fallback_used or backend_error:
                fallback_count += 1
                if "timed out" in backend_error.lower() or "timed out" in fallback_reason.lower():
                    timeout_count += 1
                fallback_requests.append(
                    {
                        "request_id": request_id,
                        "timestamp": event.timestamp / 1000.0,
                        "query": last_query_by_request.get(request_id, ""),
                        "provider": provider,
                        "model": str(payload.get("model") or ""),
                        "backend_error": backend_error,
                        "execution_time_ms": round(execution_time_ms, 2) if execution_time_ms > 0 else 0.0,
                    }
                )

        primary_transport = "local"
        if transport_counts:
            primary_transport = max(transport_counts.items(), key=lambda item: (item[1], item[0]))[0]
        average_execution_time_ms = round(sum(execution_times) / len(execution_times), 2) if execution_times else 0.0
        latest_execution_time_ms = round(execution_times[-1], 2) if execution_times else 0.0
        health = "healthy"
        if timeout_count > 0:
            health = "degraded"
        elif fallback_count > 0:
            health = "watch"

        return {
            "session_id": session["session_id"],
            "status": health,
            "start_time": session["start_time"],
            "last_activity": session["last_activity"],
            "duration": session["duration"],
            "event_count": session["event_count"],
            "total_requests": session["total_requests"],
            "preview": session.get("preview") or "",
            "primary_transport": primary_transport,
            "transports": dict(transport_counts),
            "providers": dict(provider_counts),
            "retrieval_profiles": dict(retrieval_profile_counts),
            "fallback_count": fallback_count,
            "timeout_count": timeout_count,
            "grounded_truth_count": grounded_truth_count,
            "average_execution_time_ms": average_execution_time_ms,
            "latest_execution_time_ms": latest_execution_time_ms,
            "recent_queries": recent_queries[-message_limit:],
            "recent_responses": recent_responses[-message_limit:],
            "fallback_requests": fallback_requests[-message_limit:],
        }

    @staticmethod
    def _compact_review_text(value: Any, *, max_chars: int = 220) -> str:
        cleaned = " ".join(str(value or "").split()).strip()
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[: max_chars - 3].rstrip(" ,;:.") + "..."

    def _annotate_semantic_hit(
        self,
        result: Dict[str, Any],
        *,
        query_tokens: List[str],
        retrieval_profile: str,
        hinted_collections: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        metadata = dict(result.get("metadata") or {})
        hit_id = str(result.get("id") or "")
        text = str(result.get("text") or "")
        kind = self._classify_semantic_hit(hit_id, metadata)
        if kind == "conversation_memory":
            return None

        title = str(
            metadata.get("title")
            or metadata.get("path")
            or metadata.get("source_path")
            or metadata.get("source_file")
            or metadata.get("source_id")
            or hit_id
        )
        source_path = str(
            metadata.get("source_path")
            or metadata.get("path")
            or metadata.get("source_file")
            or ""
        )
        collection = str(metadata.get("collection") or metadata.get("collection_name") or "")
        section = str(metadata.get("section") or "")
        provenance = self._annotate_provenance(result)
        if provenance:
            collection = collection or str(provenance.get("collection") or "")
            source_path = source_path or str(provenance.get("source_file") or "")
            section = section or str(provenance.get("section") or "")

        score = float(result.get("relevance") or 0.0)
        score += self._token_overlap_score(query_tokens, f"{title} {text}".lower(), weight=0.04)
        hinted = set(hinted_collections or [])
        targeted_collection = str(metadata.get("_hinted_collection") or "")
        if collection and collection in hinted:
            score += 0.18
        if collection and targeted_collection and collection == targeted_collection:
            score += 0.14
        elif hinted and kind == "memory_chamber_document" and not collection:
            score -= 0.12
        if retrieval_profile == "architecture":
            if not self._is_architecture_hit(title=title, text=text, metadata=metadata, collection=collection):
                return None
            score += 0.12
        elif collection == "obeos":
            score += 0.03

        provenance_source_id = ""
        if provenance:
            provenance_source_id = str(provenance.get("source_id") or "")
        metadata_source_id = str(metadata.get("source_id") or "")

        citation = {
            "vector_id": hit_id,
            "kind": kind,
            "title": title,
            "collection": collection,
            "source_path": source_path or None,
            "source_id": metadata_source_id or provenance_source_id or None,
            "section": section or None,
            "relevance": round(float(result.get("relevance") or 0.0), 6),
            "distance": round(float(result.get("distance") or 0.0), 6),
            "text_preview": text[:280],
        }
        return {
            "id": hit_id,
            "kind": kind,
            "title": title,
            "collection": collection,
            "text": text,
            "relevance": float(result.get("relevance") or 0.0),
            "distance": float(result.get("distance") or 0.0),
            "score": round(score, 6),
            "metadata": metadata,
            "citation": citation,
        }

    def _detect_collection_hints(self, query: str) -> List[str]:
        normalized_query = re.sub(r"[^a-z0-9]+", " ", str(query or "").lower()).strip()
        if not normalized_query:
            return []
        query_haystack = f" {normalized_query} "
        source_registry, collection_registry = self._knowledge_registries()
        hints: List[str] = []
        generic_aliases = {
            "main",
            "tooling",
            "runtime",
            "transfer",
            "companion",
            "future hook",
            "architecture",
            "programming",
            "cybersecurity",
            "comics",
        }

        def register_hint(collection: str, *aliases: str) -> None:
            normalized_collection = re.sub(r"[^a-z0-9]+", "_", str(collection or "").lower()).strip("_")
            if not normalized_collection:
                return
            collection_aliases = {
                re.sub(r"[^a-z0-9]+", " ", normalized_collection).strip(),
                re.sub(r"[^a-z0-9]+", " ", str(collection or "").lower()).strip(),
            }
            for alias in aliases:
                alias_text = re.sub(r"[^a-z0-9]+", " ", str(alias or "").lower()).strip()
                if not alias_text:
                    continue
                alias_tokens = alias_text.split()
                if (
                    alias_text not in collection_aliases
                    and (alias_text in generic_aliases or (len(alias_tokens) == 1 and len(alias_text) < 8))
                ):
                    continue
                if f" {alias_text} " in query_haystack and normalized_collection not in hints:
                    hints.append(normalized_collection)
                    return

        if collection_registry is not None:
            for meta in collection_registry.all():
                register_hint(
                    meta.collection,
                    meta.collection,
                    meta.collection.replace("_", " "),
                    meta.description,
                    meta.domain,
                    meta.subdomain,
                    meta.category,
                )
        if source_registry is not None:
            for record in source_registry.all():
                register_hint(
                    record.collection or record.resolved_collection(),
                    record.collection,
                    record.series,
                    Path(record.path).stem,
                )
        return hints[:4]

    def _is_collection_overview_query(self, query: str, hinted_collections: List[str]) -> bool:
        normalized = " ".join(str(query or "").strip().lower().split())
        if not normalized or not hinted_collections:
            return False
        compact = re.sub(r"[^a-z0-9'\s]+", " ", normalized)
        compact = " ".join(compact.split())
        tokens = set(self._tokenize(compact))
        overview_phrases = (
            "tell me about",
            "what is",
            "what's",
            "who is",
            "who's",
            "explain",
            "summarize",
            "summary of",
            "overview of",
            "recap",
            "remind me about",
            "what resources",
            "what tools",
            "what do we have",
            "what do i have",
            "what exists in",
            "what is in",
            "what do we know about",
            "what have we got on",
            "what do you know about",
            "do you know anything about",
            "do you still know anything about",
        )
        if any(phrase in normalized for phrase in overview_phrases):
            return True
        focus_terms = {
            collection.replace("_", " ").strip().lower()
            for collection in hinted_collections
            if str(collection or "").strip()
        }
        normalized_focus = normalized.rstrip("?.! ")
        if normalized_focus in focus_terms:
            return True
        token_count = len(self._tokenize(normalized_focus))
        knowledge_intent_tokens = {"know", "got", "have", "anything", "resources", "tools", "exists"}
        actor_tokens = {"we", "i", "you", "still", "do"}
        if (
            bool(tokens.intersection(knowledge_intent_tokens))
            and bool(tokens.intersection(actor_tokens))
            and any(term in normalized_focus for term in focus_terms)
        ):
            return True
        return token_count <= 4 and any(term in normalized_focus for term in focus_terms)

    def _detect_grounded_memory_query(self, query: str) -> Optional[str]:
        normalized = " ".join(str(query or "").strip().lower().split())
        if not normalized:
            return None
        if normalized.startswith("remember to ") or " and remember to " in normalized:
            return None
        compact = re.sub(r"[^a-z0-9'\s]+", " ", normalized)
        compact = " ".join(compact.split())
        tokens = set(self._tokenize(compact))

        operator_phrases = (
            "what do you remember about me",
            "what do you know about me",
            "what can you recall about me",
            "what do you still know about me",
            "what are you still remembering about me",
            "what do you still carry about who i am",
            "what do you still have on me",
            "what do you still know about who i am",
            "what are you still holding onto about me",
            "what still seems true about me",
            "what do you still remember about my role",
            "what am i to you in obeos",
            "what am i to you again",
            "who am i to you",
            "who am i",
            "what is my name",
            "what's my name",
            "do you remember my name",
            "do you know my name",
            "remember about me",
        )
        hinted_collections = self._detect_collection_hints(query)
        memory_specific = (
            "about me",
            "about us",
            "our side",
            "our place",
            "our work",
            "in your memory",
            "our conversation",
            "we leave off",
            "we left off",
            "working on",
            "recall about me",
            "who i am",
            "my role",
            "am i to you",
            "on me",
            "true about me",
        )
        if hinted_collections and not any(token in normalized for token in memory_specific):
            return None
        session_phrases = (
            "where did we leave off",
            "where we left off",
            "where we were",
            "what are we working on",
            "what are we working on now",
            "what were we working on",
            "what have we been working on",
            "what have we been up to",
            "what have we done lately",
            "what have we changed lately",
            "what changed recently",
            "what have the recent updates been",
            "what are the recent updates",
            "are you aware of recent updates",
            "are you aware of the recent updates",
            "are you aware of our recent updates",
            "do you know the recent updates",
            "do you remember the recent updates",
            "give me a recap",
            "give me a quick recap",
            "give me an update",
            "give me a quick update",
            "catch me up",
            "bring me up to speed",
            "what were we doing",
            "what do you still know about where we were",
            "what are you still carrying forward from all this",
            "what was the thing we were tightening again",
            "what were we circling around before",
            "what do you still have in mind from our recent work",
            "what were you just anchoring to a second ago",
            "what thread are you still holding onto here",
            "what are you still centered on",
            "what are you still orbiting around",
            "what part of the recent work are you still remembering most clearly",
            "what still seems live from our side of this",
            "what do you still have on us from all this",
            "what still seems true about our side of this",
            "what do you still remember about our place in this system",
            "what were we trying to stabilize again",
            "what changed",
            "what were the last changes",
        )
        general_phrases = (
            "what do you remember",
            "what can you remember",
            "what do you recall",
            "what can you recall",
            "do you remember",
        )
        if any(phrase in normalized for phrase in operator_phrases):
            return "operator"
        if any(phrase in normalized for phrase in session_phrases):
            return "session"
        if any(phrase in normalized for phrase in general_phrases):
            return "general"
        if (
            ({"name", "give"} <= tokens or {"name", "gave"} <= tokens)
            and bool(tokens.intersection({"i", "you", "me", "my"}))
        ):
            return "operator"
        if (
            bool(tokens.intersection({"know", "remember", "recall"}))
            and bool(tokens.intersection({"me", "my"}))
        ):
            return "operator"
        if (
            bool(tokens.intersection({"carry", "carrying", "holding", "onto", "have", "true"}))
            and bool(tokens.intersection({"me", "my", "role"}))
        ):
            return "operator"
        if (
            bool(tokens.intersection({"who", "role"}))
            and bool(tokens.intersection({"i", "am", "me", "my", "project", "system", "obeos"}))
        ):
            return "operator"
        if (
            bool(tokens.intersection({"anchor", "reanchor", "re", "recap", "update"}))
            and bool(tokens.intersection({"recent", "work", "session", "conversation", "us", "our"}))
        ):
            return "session"
        if (
            bool(tokens.intersection({"track", "tracking", "tracked", "focus", "focused", "keeping", "straight", "oriented", "grounded", "retained", "retain", "keyed", "anchored", "centered", "orbiting"}))
            and bool(tokens.intersection({"recent", "work", "session", "before", "last", "thread", "stretch", "here", "this", "our", "still", "around", "on"}))
        ):
            return "session"
        if (
            bool(tokens.intersection({"carrying", "carry", "forward", "holding", "onto", "anchoring", "anchored", "tightening", "circling", "stabilize", "stabilizing", "live"}))
            and bool(tokens.intersection({"we", "our", "recent", "work", "again", "before", "still", "thread", "side", "main", "here"}))
        ):
            return "session"
        if (
            bool(tokens.intersection({"carrying", "carry", "holding"}))
            and {"main", "thread"} <= tokens
            and "still" in tokens
        ):
            return "session"
        if (
            bool(tokens.intersection({"have", "holding", "onto", "remember", "know", "true", "live"}))
            and bool(tokens.intersection({"us", "our"}))
            and bool(tokens.intersection({"side", "place", "system", "project", "this", "here", "all"}))
        ):
            return "session"
        if (
            bool(tokens.intersection({"memory", "remember", "remembering", "recall", "know"}))
            and bool(tokens.intersection({"our", "we"}))
            and bool(tokens.intersection({"work", "session", "conversation", "recent", "before", "this"}))
        ):
            return "session"
        if (
            bool(tokens.intersection({"remember", "remembering", "know", "recall"}))
            and bool(tokens.intersection({"our", "us"}))
            and bool(tokens.intersection({"place", "system", "project", "here", "this"}))
        ):
            return "session"
        if (
            bool(tokens.intersection({"know", "remember", "remembering", "recall", "mind"}))
            and bool(tokens.intersection({"where", "were", "work", "recent", "still", "clearly", "again", "before"}))
        ):
            return "session"
        if (
            bool(tokens.intersection({"leave", "left"}))
            and bool(tokens.intersection({"off", "that", "there", "where", "we"}))
        ):
            return "session"
        if (
            bool(tokens.intersection({"doing", "working", "updates", "changed"}))
            and bool(tokens.intersection({"we", "our", "recent", "lately", "again", "there"}))
        ):
            return "session"
        return None

    def _detect_conversational_recall_frame(self, query: str) -> Optional[str]:
        normalized = " ".join(str(query or "").strip().lower().split())
        compact = re.sub(r"[^a-z0-9'\s]+", " ", normalized)
        compact = " ".join(compact.split())
        if not compact:
            return None
        if len(compact) > 260:
            return None
        compact_tokens = set(compact.split())
        followup_phrases = (
            "tell me more",
            "say more",
            "go on",
            "what do you mean",
            "what did you mean",
            "why do you say that",
            "why do you think that",
            "what makes you say that",
            "how so",
            "why is that",
            "why though",
            "expand on that",
            "elaborate",
            "continue that",
            "what else",
            "and then",
        )
        if any(phrase in compact for phrase in followup_phrases):
            return "followup"
        if self._looks_like_structural_explanatory_followup(compact, compact_tokens):
            return "followup"
        if len(compact_tokens) <= 3 and (
            {"why", "though"} <= compact_tokens
            or {"what", "else"} <= compact_tokens
            or {"and", "then"} <= compact_tokens
            or {"go", "on"} <= compact_tokens
        ):
            return "followup"
        if len(compact_tokens) <= 8 and (
            ({"what", "part", "that"} <= compact_tokens)
            or ({"what", "part", "this"} <= compact_tokens)
            or ({"which", "part", "that"} <= compact_tokens)
            or ({"which", "part", "this"} <= compact_tokens)
            or ({"that", "matters", "most"} <= compact_tokens)
            or ({"this", "matters", "most"} <= compact_tokens)
        ):
            return "followup"
        if len(compact.split()) < 4:
            return None
        if self._detect_grounded_memory_query(query) is not None:
            return None
        hinted_collections = self._detect_collection_hints(query)
        if hinted_collections and self._is_collection_overview_query(query, hinted_collections):
            return None
        if any(token in compact for token in ("today", "time", "date", "day of the week")):
            return None
        if any(token in compact for token in ("def ", "class ", "import ", "traceback", "/api/", "http://", "https://")):
            return None
        anchor_terms = {
            "obeos", "o b e o s", "daise", "system", "project", "memory", "work", "working",
            "progress", "direction", "future", "next", "risk", "fragile", "creator", "mission",
            "success", "improve", "latency", "hud", "telegram", "tinyprogrammer", "tiny",
            "recall", "response", "operator", "surface", "thinking", "headed", "becoming",
            "stable", "stabilize", "stabilizing", "grounded",
            "everything", "overall", "together", "us", "our", "we", "you", "me",
        }
        if not any(term in compact for term in anchor_terms):
            conversational_bridge_tokens = {
                "this",
                "that",
                "real",
                "weak",
                "weakest",
                "fragile",
                "solid",
                "direction",
                "headed",
                "going",
                "next",
                "tighten",
                "land",
                "landing",
                "stable",
                "stabilize",
                "stabilizing",
                "matter",
                "matters",
                "important",
                "feel",
                "feels",
                "think",
                "seems",
            }
            if not bool(set(self._tokenize(compact)).intersection(conversational_bridge_tokens)):
                return None

        state_score = self._count_phrase_matches(
            compact,
            (
                "talk to me",
                "honestly",
                "everything",
                "overall",
                "what stands out",
                "what is happening",
                "take shape",
                "taking shape",
                "seeing take shape",
                "sitting with you",
                "locking in",
                "stabilize",
                "stabilizing",
                "most grounded",
                "situation",
                "sum up",
                "summarize",
                "describe what",
            ),
        )
        direction_score = self._count_phrase_matches(
            compact,
            (
                "what next",
                "do next",
                "headed",
                "heading",
                "becoming",
                "where this is going",
                "future",
                "strongest move",
                "tighten next",
                "most important next",
                "starting to land",
            ),
        )
        risk_score = self._count_phrase_matches(
            compact,
            (
                "throwing this off",
                "throws this off",
                "fighting itself",
                "getting in our own way",
                "weakest",
                "fragile",
                "risk",
                "risks",
                "wobble",
                "wobbly",
                "worry",
                "worries",
                "concern",
                "unresolved",
                "drag on coherence",
                "drag on",
                "pressure point",
                "snags",
                "snagging",
                "messy",
                "shaky",
                "breaking",
                "failing",
                "unstable",
            ),
        )
        mission_score = self._count_phrase_matches(
            compact,
            (
                "creator",
                "mission",
                "purpose",
                "trying to accomplish",
                "what this is for",
                "what makes",
                "different from",
                "describe me",
                "describe this project",
            ),
        )
        values_score = self._count_phrase_matches(
            compact,
            (
                "proud",
                "admire",
                "excites",
                "success",
                "matters most",
                "important",
                "feel about",
                "feel like",
                "most real",
                "close to something real",
                "progress we have made",
            ),
        )
        tokens = set(self._tokenize(compact))
        direction_score += self._count_token_matches(
            tokens,
            {
                "heading",
                "headed",
                "going",
                "direction",
                "future",
                "becoming",
                "become",
                "trajectory",
                "evolving",
                "evolve",
                "next",
                "land",
                "landing",
                "tighten",
            },
        )
        if tokens.intersection({"becoming", "become", "turning", "growing", "evolving", "headed", "heading"}) and tokens.intersection({"what", "where", "this", "all", "us"}):
            direction_score += 2
        if tokens.intersection({"solid", "strong", "stable", "grounded", "coherent"}) and tokens.intersection({"what", "feels", "seems", "now", "most"}):
            state_score += 2
        risk_score += self._count_token_matches(
            tokens,
            {
                "weak",
                "weakest",
                "fragile",
                "risk",
                "risks",
                "wobble",
                "wobbly",
                "worry",
                "worried",
                "concern",
                "concerns",
                "unresolved",
                "drag",
                "pressure",
                "point",
                "snag",
                "snags",
                "messy",
                "shaky",
                "off",
                "unstable",
                "failing",
                "broken",
            },
        )
        if "point" in tokens and tokens.intersection({"what", "why", "for", "purpose"}):
            risk_score = max(0, risk_score - 1)
        mission_score += self._count_token_matches(
            tokens,
            {
                "creator",
                "mission",
                "purpose",
                "accomplish",
                "difference",
                "different",
                "why",
                "built",
                "building",
            },
        )
        if tokens.intersection({"purpose", "for", "point", "built", "building"}) and tokens.intersection({"what", "why", "this", "project", "system"}):
            mission_score += 2
        values_score += self._count_token_matches(
            tokens,
            {
                "proud",
                "admire",
                "excited",
                "excites",
                "success",
                "important",
                "matters",
                "real",
                "understand",
                "understanding",
                "close",
            },
        )
        if tokens.intersection({"important", "matter", "matters", "real", "alive"}) and tokens.intersection({"what", "feels", "seems", "most", "now"}):
            values_score += 2
        broad_state_cues = self._count_token_matches(
            tokens,
            {
                "overall",
                "project",
                "system",
                "state",
                "situation",
                "describe",
                "shape",
                "sitting",
                "locking",
                "stabilize",
                "stabilizing",
                "grounded",
                "honestly",
                "together",
                "working",
                "going",
            },
        )
        state_score += broad_state_cues
        scores = {
            "state": state_score,
            "direction": direction_score,
            "risk": risk_score,
            "mission": mission_score,
            "values": values_score,
        }
        if risk_score > 0:
            return "risk"
        if direction_score > 0:
            return "direction"
        if mission_score > 0:
            return "mission"
        if values_score > 0:
            return "values"
        best_frame = max(scores, key=scores.get)
        if scores[best_frame] > 0:
            return best_frame
        if any(token in compact for token in ("think", "feel", "describe", "honestly", "overall", "together")):
            return "state"
        return None

    def _detect_implicit_conversational_frame(
        self,
        query: str,
        session_messages: List[Dict[str, Any]],
    ) -> Optional[str]:
        normalized = " ".join(str(query or "").strip().lower().split())
        compact = re.sub(r"[^a-z0-9'\s]+", " ", normalized)
        compact = " ".join(compact.split())
        if not compact or len(compact) > 180:
            return None
        if self._detect_grounded_memory_query(query) is not None:
            return None
        if any(token in compact for token in ("today", "time", "date", "day of the week")):
            return None
        if any(token in compact for token in ("def ", "class ", "import ", "traceback", "/api/", "http://", "https://")):
            return None
        assistant_snapshot = self._recent_assistant_snapshot(session_messages, current_query=query)
        if not assistant_snapshot:
            return None
        active_topics = self._recent_user_topics(session_messages, current_query=query, limit=3)
        if not active_topics and not assistant_snapshot:
            return None
        compact_tokens = set(compact.split())
        continuity_flags = self._conversational_followup_flags(compact)
        deictic_tokens = continuity_flags["deictic_tokens"]
        scope_tokens = continuity_flags["scope_tokens"]
        implicit_phrases = (
            "stay with that thought",
            "stay with that",
            "stay with this thought",
            "stay with this",
            "same for",
            "same with",
            "same here",
            "for the python one",
            "for the code one",
            "for that one",
            "on the hud too",
            "on telegram",
            "does that work there too",
            "what about that case",
            "same thing there",
            "would that still be true there",
            "does that line up with the hud",
            "over there too",
            "same on that side",
            "locally too",
            "same in browser",
            "same in the cloud",
            "on desktop too",
            "same on this machine",
            "what about in the browser hud",
            "does that also work locally",
            "same thing in telegram",
            "does it still work over there",
            "what about in that path",
            "does that also fit there",
            "is that still how it works there",
            "same for that route",
            "what about that flow",
            "what still carries over there",
            "does that still hold here",
            "does this still hold here",
            "does that carry over",
            "does this carry over",
            "so that still applies",
            "so this still applies",
            "does that apply here too",
            "does this apply here too",
            "keep talking about that",
            "keep talking about this",
            "that feels important",
            "this feels important",
            "that seems important",
            "this seems important",
            "is that what worries you most",
            "is that what worries you",
            "does that still worry you",
            "does this still worry you",
            "is that what you mean",
            "is this what you mean",
            "is that still the problem",
            "is this still the problem",
            "is that still the main thing",
            "is this still the main thing",
            "what about the other part",
            "what about that part",
            "what about this side of it",
            "what about that side of it",
            "what part matters most",
            "which part matters most",
            "what part is still fragile",
            "which part is still fragile",
        )
        if any(phrase in compact for phrase in implicit_phrases):
            return "followup"
        if continuity_flags["ultra_short_followup"] and assistant_snapshot and active_topics:
            return "followup"
        if continuity_flags["short_scope_followup"]:
            return "followup"
        if continuity_flags["environment_followup"]:
            return "followup"
        if continuity_flags["referential_followup"]:
            return "followup"
        if len(compact_tokens) <= 8 and bool(compact_tokens.intersection({"carry", "carries", "over"})):
            if compact_tokens.intersection({"there", "here", "across", "still"}):
                return "followup"
        if len(compact_tokens) <= 8 and (
            ({"line", "up"} <= compact_tokens)
            or ({"starting", "land"} <= compact_tokens)
            or ({"most", "real"} <= compact_tokens)
        ):
            if compact_tokens.intersection(scope_tokens.union({"there", "too", "here"})):
                return "followup"
            if compact_tokens.intersection({"next", "tighten", "going", "headed", "land", "starting"}):
                return "direction"
            if compact_tokens.intersection({"real", "important", "matters"}):
                return "values"
            return "state"
        if len(compact_tokens) <= 8 and (
            ({"that", "important"} <= compact_tokens)
            or ({"this", "important"} <= compact_tokens)
            or ({"that", "mean"} <= compact_tokens)
            or ({"this", "mean"} <= compact_tokens)
            or ({"that", "worries"} <= compact_tokens)
            or ({"this", "worries"} <= compact_tokens)
            or ({"that", "problem"} <= compact_tokens)
            or ({"this", "problem"} <= compact_tokens)
            or ({"part", "matters"} <= compact_tokens)
            or ({"part", "fragile"} <= compact_tokens)
            or ({"stay", "that"} <= compact_tokens)
            or ({"stay", "this"} <= compact_tokens)
        ):
            return "followup"
        return None

    def _looks_like_structural_explanatory_followup(
        self,
        compact: str,
        compact_tokens: set[str],
    ) -> bool:
        if not compact_tokens or len(compact_tokens) > 8:
            return False
        explanation_starters = {"why", "how", "what", "can", "could", "would"}
        explanation_targets = {"that", "this", "it", "there", "here"}
        explanation_reason_tokens = {
            "mean",
            "meant",
            "meaning",
            "saying",
            "say",
            "makes",
            "making",
            "reason",
            "reasons",
        }
        elaboration_action_tokens = {
            "thinking",
            "unpack",
            "expand",
            "elaborate",
            "continue",
            "deeper",
            "further",
            "detail",
            "details",
            "through",
        }
        elaboration_context_tokens = {
            "more",
            "else",
            "thought",
            "point",
            "part",
            "little",
            "exactly",
            "again",
        }
        if compact_tokens.intersection(explanation_starters) and compact_tokens.intersection(explanation_targets):
            if compact_tokens.intersection(explanation_reason_tokens):
                return True
        if (
            "how" in compact_tokens
            and "thinking" in compact_tokens
            and compact_tokens.intersection(explanation_targets)
        ):
            return True
        if compact_tokens.intersection(elaboration_action_tokens):
            if compact_tokens.intersection(explanation_targets.union(elaboration_context_tokens)):
                return True
        if compact.startswith("tell me") or compact.startswith("walk me"):
            if compact_tokens.intersection({"more", "through", "deeper", "further", "again"}):
                return True
        if (
            compact_tokens.intersection({"expand", "elaborate", "unpack"})
            and compact_tokens.intersection({"point", "part", "thought", "bit", "little"})
        ):
            return True
        if (
            compact_tokens.intersection({"meaning", "meant"})
            and compact_tokens.intersection(explanation_targets.union({"you"}))
        ):
            return True
        return False

    def _conversational_followup_flags(self, text: str) -> Dict[str, Any]:
        compact = " ".join(str(text or "").strip().lower().split())
        tokens = set(re.findall(r"[a-z0-9']+", compact))
        deictic_tokens = {"that", "this", "it", "there", "here", "same", "also", "too"}
        referential_tokens = {"that", "this", "it"}
        scope_tokens = {
            "browser",
            "hud",
            "telegram",
            "bot",
            "cloud",
            "desktop",
            "legion",
            "local",
            "locally",
            "machine",
            "remote",
            "remotely",
            "route",
            "path",
            "flow",
            "side",
            "lane",
        }
        environment_tokens = {
            "browser",
            "hud",
            "telegram",
            "bot",
            "cloud",
            "desktop",
            "legion",
            "local",
            "locally",
            "machine",
            "remote",
            "remotely",
        }
        continuity_signal_tokens = {
            "important",
            "mean",
            "problem",
            "apply",
            "python",
            "code",
            "hold",
            "carry",
            "over",
            "side",
            "there",
            "case",
            "telegram",
            "hud",
            "work",
            "true",
            "route",
            "path",
            "flow",
            "fit",
            "local",
            "locally",
            "browser",
            "cloud",
            "desktop",
            "legion",
            "machine",
            "risk",
            "fragile",
            "worry",
            "worries",
            "matters",
            "matter",
            "part",
            "same",
            "other",
            "main",
            "thing",
            "one",
            "too",
            "thought",
        }
        environment_continuity_tokens = {"same", "too", "there", "here", "also", "work", "apply", "true"}
        bridge_followup_tokens = {"and", "then", "what", "about", "on", "in"}
        token_count = len(tokens)
        return {
            "tokens": tokens,
            "token_count": token_count,
            "deictic_tokens": deictic_tokens,
            "scope_tokens": scope_tokens,
            "referential_tokens": referential_tokens,
            "environment_tokens": environment_tokens,
            "ultra_short_followup": (
                token_count <= 3 and bool(tokens.intersection({"same", "there", "here", "too", "this", "that"}))
            ),
            "short_scope_followup": (
                token_count <= 5
                and (
                    (
                        bool(tokens.intersection(deictic_tokens))
                        and bool(tokens.intersection(scope_tokens))
                    )
                    or (
                        bool(tokens.intersection(scope_tokens))
                        and bool(tokens.intersection(bridge_followup_tokens))
                    )
                )
            ),
            "environment_followup": (
                token_count <= 8
                and bool(tokens.intersection(environment_tokens))
                and (
                    bool(tokens.intersection(environment_continuity_tokens))
                    or bool(tokens.intersection(bridge_followup_tokens))
                )
            ),
            "referential_followup": (
                token_count <= 10
                and bool(tokens.intersection(referential_tokens))
                and bool(tokens.intersection(continuity_signal_tokens))
            ),
        }

    def _compose_grounded_memory_answer(
        self,
        *,
        session_id: str,
        recall_kind: str,
        query: str,
        session_messages: List[Dict[str, Any]],
        session_summaries: List[Dict[str, Any]],
        global_memories: List[Dict[str, Any]],
        recent_events: List[Dict[str, Any]],
        recent_global_summaries: List[Dict[str, Any]],
        operator_facts: List[str],
        operator_profile: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, List[Dict[str, Any]]]:
        profile = dict(operator_profile or {})
        preferred_name = str(profile.get("preferred_name") or "").strip()
        operator_role = str(profile.get("operator_role") or "").strip().rstrip(".")
        asking_for_name = "my name" in query.lower()
        profile_topics = [
            str(item).strip()
            for item in list(profile.get("active_work") or [])
            if str(item).strip()
        ]
        profile_facts = [
            str(item).strip()
            for item in list(profile.get("fact_statements") or operator_facts)
            if str(item).strip()
        ]
        direct_topics = self._recent_user_topics(session_messages, current_query=query, limit=3)
        stored_work_topics = self._stored_work_topics(global_memories, limit=3, session_id=session_id)
        summary_topics = [
            topic
            for summary in session_summaries
            for topic in self._extract_summary_topics(summary.get("summary"))
        ]
        global_summary_topics = [
            topic
            for summary in recent_global_summaries
            for topic in self._extract_summary_topics(summary.get("summary"))
        ]
        topic_pool: List[str] = []
        topic_pool.extend(profile_topics)
        topic_pool.extend(stored_work_topics)
        if recall_kind in {"session", "general"}:
            topic_pool.extend(direct_topics)
        if recall_kind == "general" and not topic_pool:
            topic_pool.extend(summary_topics)
            topic_pool.extend(global_summary_topics)
        elif recall_kind == "session" and not topic_pool:
            topic_pool.extend(summary_topics)
            topic_pool.extend(global_summary_topics)
        recent_topics = self._unique_grounded_memory_items(topic_pool, limit=4)
        assistant_snapshot = self._recent_assistant_snapshot(session_messages, current_query=query)
        memory_snippets = self._unique_grounded_memory_items(
            [
                self._trim_grounded_memory_snippet(str(item.get("text") or ""), max_chars=150)
                for item in global_memories
                if str(item.get("text") or "").strip() and str(item.get("memory_type") or "") == "pinned_memory"
            ],
            limit=2,
        )
        event_snippets = self._unique_grounded_memory_items(
            [
                self._trim_grounded_memory_snippet(str(item.get("message") or ""), max_chars=140)
                for item in recent_events
                if str(item.get("message") or "").strip()
            ],
            limit=2,
        )
        additional_operator_facts: List[str] = []
        for fact in profile_facts:
            lowered_fact = fact.lower()
            if preferred_name and preferred_name.lower() in lowered_fact and "call" in lowered_fact:
                continue
            if operator_role and "creator of o.b.e.o.s" in lowered_fact:
                continue
            if "d.a.i.s.e" in lowered_fact and "direct artificial intelligence system environment" in lowered_fact:
                continue
            additional_operator_facts.append(fact)

        parts: List[str] = []
        if recall_kind == "operator":
            if asking_for_name and preferred_name:
                parts.append(f'You asked me to call you "{preferred_name}".')
            else:
                parts.append("I remember a few concrete things about you in O.B.E.O.S.")
            if preferred_name:
                if not asking_for_name:
                    parts.append(f'You asked me to call you "{preferred_name}".')
            if operator_role:
                parts.append(f"You described yourself as the {operator_role}.")
            if additional_operator_facts:
                parts.append(f"I also remember that {self._join_grounded_memory_items(additional_operator_facts[:2])}.")
            if recent_topics:
                parts.append(f"More recently, you were working on {self._join_grounded_memory_items(recent_topics[:3])}.")
        elif recall_kind == "session":
            normalized_query = " ".join(str(query or "").strip().lower().split())
            if recent_topics:
                if any(
                    phrase in normalized_query
                    for phrase in (
                        "are you aware of recent updates",
                        "recent updates",
                        "quick update",
                        "quick recap",
                        "catch me up",
                        "bring me up to speed",
                        "what changed",
                        "what have we changed lately",
                        "what have we done lately",
                    )
                ):
                    parts.append(
                        f"Yes, I am grounded in our recent O.B.E.O.S. updates. We have been working on {self._join_grounded_memory_items(recent_topics[:3])}."
                    )
                else:
                    parts.append(
                        f"Where we left off, we were working on {self._join_grounded_memory_items(recent_topics[:3])}."
                    )
            else:
                parts.append("I can anchor us from the stored session history.")
            if preferred_name:
                parts.append(f'You still prefer that I call you "{preferred_name}".')
            elif operator_role:
                parts.append(f"I also remember that you described yourself as the {operator_role}.")
        else:
            parts.append("I remember a few concrete things from our shared O.B.E.O.S history.")
            if recent_topics:
                parts.append(f"Recently we were discussing {self._join_grounded_memory_items(recent_topics[:3])}.")
            if preferred_name:
                parts.append(f'You asked me to call you "{preferred_name}".')
            if operator_role:
                parts.append(f"You described yourself as the {operator_role}.")
            elif additional_operator_facts:
                parts.append(f"I also remember that {self._join_grounded_memory_items(additional_operator_facts[:2])}.")

        if assistant_snapshot and recall_kind == "session" and not recent_topics:
            parts.append(f"The last assistant reply before this was about {assistant_snapshot.rstrip('.')}.")
        if memory_snippets and recall_kind == "general" and not recent_topics:
            parts.append(f"Summarized memory also notes {self._join_grounded_memory_items(memory_snippets)}.")
        elif event_snippets and recall_kind == "general" and not recent_topics:
            parts.append(f"Recent ecosystem traces mention {self._join_grounded_memory_items(event_snippets)}.")

        answer = " ".join(part.strip() for part in parts if part.strip()).strip()
        if not answer or answer in {
            "I remember a few concrete things from our shared O.B.E.O.S history.",
            "I remember a few concrete things about you in O.B.E.O.S.",
            "I can anchor us from the stored session history.",
        }:
            return "", []

        provenance: List[Dict[str, Any]] = []
        for summary in session_summaries[:2]:
            provenance.append(
                {
                    "kind": "session_summary",
                    "event_id": summary.get("event_id"),
                    "timestamp": summary.get("timestamp"),
                }
            )
        for summary in recent_global_summaries[:2]:
            provenance.append(
                {
                    "kind": "global_session_summary",
                    "event_id": summary.get("event_id"),
                    "session_id": summary.get("session_id"),
                    "timestamp": summary.get("timestamp"),
                }
            )
        for item in global_memories[:2]:
            provenance.append(
                {
                    "kind": str(item.get("memory_type") or "memory"),
                    "event_id": (item.get("metadata") or {}).get("event_id") or item.get("id"),
                    "session_id": (item.get("metadata") or {}).get("session_id"),
                }
            )
        return answer, provenance

    def _compose_grounded_conversational_answer(
        self,
        *,
        session_id: str,
        frame: str,
        query: str,
        session_messages: List[Dict[str, Any]],
        session_summaries: List[Dict[str, Any]],
        global_memories: List[Dict[str, Any]],
        recent_events: List[Dict[str, Any]],
        operator_profile: Optional[Dict[str, Any]],
        knowledge_hits: List[Dict[str, Any]],
        citations: List[Dict[str, Any]],
    ) -> tuple[str, List[Dict[str, Any]]]:
        del knowledge_hits
        profile = dict(operator_profile or {})
        preferred_name = str(profile.get("preferred_name") or "").strip()
        operator_role = str(profile.get("operator_role") or "").strip()
        recent_topics = self._recent_user_topics(session_messages, current_query=query, limit=4)
        active_work = [
            str(item).strip()
            for item in list(profile.get("active_work") or [])
            if str(item).strip()
        ]
        if recent_topics:
            combined_topics: List[str] = []
            combined_topics.extend(recent_topics)
            combined_topics.extend(active_work)
            active_work = self._unique_grounded_memory_items(combined_topics, limit=4)
        if not active_work:
            active_work = self._stored_work_topics(global_memories, limit=4)
        if not active_work:
            active_work = ["shared memory grounding", "response latency", "operator surface cohesion"]

        assistant_snapshot = self._recent_assistant_snapshot(session_messages, current_query=query)
        event_snippets = self._unique_grounded_memory_items(
            [
                self._trim_grounded_memory_snippet(str(item.get("message") or ""), max_chars=120)
                for item in recent_events
                if str(item.get("message") or "").strip()
                and not self._looks_like_low_value_event_snippet(str(item.get("message") or ""))
            ],
            limit=2,
        )
        summary_topics = self._unique_grounded_memory_items(
            [
                topic
                for summary in session_summaries
                for topic in self._extract_summary_topics(summary.get("summary"))
            ],
            limit=3,
        )
        citation_titles = self._unique_grounded_memory_items(
            [
                str(item.get("title") or item.get("source_file") or "").strip()
                for item in citations[:3]
                if str(item.get("title") or item.get("source_file") or "").strip()
            ],
            limit=2,
        )
        specialist_continuity = self._specialist_continuity_snapshot(session_id=session_id)
        specialist_name = str(specialist_continuity.get("selected_agent") or "").strip()
        specialist_task_focus = str(specialist_continuity.get("task_type") or "").strip()

        salutation = f"{preferred_name}, " if preferred_name else ""
        focus = self._join_grounded_memory_items(active_work[:3])
        recent_focus = self._join_grounded_memory_items(recent_topics[:3]) if recent_topics else focus
        followup_flags = self._conversational_followup_flags(query)
        normalized_query = " ".join(str(query or "").strip().lower().split())
        honest_tone = any(token in normalized_query for token in ("honestly", "honest", "real"))
        direct_tone = any(token in normalized_query for token in ("talk to me", "what do you think", "how do you feel"))
        opening = salutation or ""

        if frame == "direction":
            if honest_tone:
                answer = (
                    f"{opening}honestly, I think O.B.E.O.S. is moving toward a more coherent living system instead of a fragile chat shell. "
                    f"The clearest tracks are {focus}, and the next best move is to keep tightening those until recall, latency, and surface behavior feel consistent together."
                )
            else:
                answer = (
                    f"{opening}I think O.B.E.O.S. is moving toward a more coherent living system instead of a fragile chat shell. "
                    f"The clearest tracks right now are {focus}, and they point toward a system that can carry memory and intent more consistently."
                )
        elif frame == "risk":
            answer = (
                f"{opening}the weakest areas right now are wherever {focus} are not yet behaving as one system. "
                "The real risk is not a lack of features. It is inconsistency between memory, retrieval, runtime health, and the operator surfaces."
            )
        elif frame == "followup":
            if assistant_snapshot:
                snapshot_text = assistant_snapshot.rstrip(".").strip()
                lowered_snapshot = snapshot_text.lower()
                if lowered_snapshot.startswith("more specifically, i mean "):
                    snapshot_text = snapshot_text[len("more specifically, I mean ") :].strip(" ,.;:-")
                elif lowered_snapshot.startswith("i mean "):
                    snapshot_text = snapshot_text[len("I mean ") :].strip(" ,.;:-")
                normalized_followup = " ".join(str(query or "").strip().lower().split())
                if any(token in normalized_followup for token in ("why", "how so", "what makes you say")):
                    answer = f"{opening}because {snapshot_text[:1].lower() + snapshot_text[1:] if snapshot_text else snapshot_text}."
                elif any(token in normalized_followup for token in ("what else", "and then")):
                    answer = f"{opening}another part of it is {snapshot_text[:1].lower() + snapshot_text[1:] if snapshot_text else snapshot_text}."
                else:
                    answer = f"{opening}more specifically, I mean {snapshot_text}."
                continuity_focus = recent_focus if recent_topics else focus
                terse_followup = any(
                    (
                        followup_flags.get("ultra_short_followup"),
                        followup_flags.get("short_scope_followup"),
                        followup_flags.get("environment_followup"),
                        followup_flags.get("referential_followup"),
                    )
                )
                if continuity_focus and terse_followup:
                    if continuity_focus.lower() not in answer.lower():
                        answer += f" More concretely, I am tying that to {continuity_focus}."
                if continuity_focus and not any(
                    (
                        followup_flags.get("ultra_short_followup"),
                        followup_flags.get("short_scope_followup"),
                        followup_flags.get("environment_followup"),
                        followup_flags.get("referential_followup"),
                    )
                ):
                    answer += f" That still connects back to {continuity_focus}."
                if specialist_name:
                    answer += (
                        f" I am still treating this as part of the {specialist_name} lane"
                        f"{' focused on ' + specialist_task_focus if specialist_task_focus else ''}."
                    )
            else:
                answer = (
                    f"{opening}more specifically, I am still talking about {focus}. "
                    "That is the thread I am carrying forward here."
                )
                if specialist_name:
                    answer += (
                        f" I am still treating it as part of the {specialist_name} lane"
                        f"{' focused on ' + specialist_task_focus if specialist_task_focus else ''}."
                    )
        elif frame == "mission":
            role_fragment = f" As I remember it, you are the {operator_role}." if operator_role else ""
            answer = (
                f"{opening}this project is about turning O.B.E.O.S. into a grounded operator environment instead of a disposable chatbot.{role_fragment} "
                f"Right now that mission is showing up most clearly through {focus}."
            )
        elif frame == "values":
            answer = (
                f"{opening}what matters most right now is whether O.B.E.O.S. can carry memory and intent forward in a way that feels dependable. "
                f"The clearest proof of progress I still have is around {focus}."
            )
        else:
            if direct_tone:
                answer = (
                    f"{opening}what stands out most to me right now is {focus}. "
                    "That is the work most directly shaping whether O.B.E.O.S. feels grounded, conversational, and coherent across every surface."
                )
            else:
                answer = (
                    f"{opening}the overall situation right now centers on {focus}. "
                    "That is the work most directly shaping whether O.B.E.O.S. feels grounded, conversational, and coherent across every surface."
                )

        if recent_topics and recent_focus.lower() not in focus.lower():
            answer += f" More recently, we were also pushing on {recent_focus}."
        elif summary_topics and not recent_topics:
            answer += f" The recent summaries still point through {self._join_grounded_memory_items(summary_topics[:2])}."
        if assistant_snapshot and frame in {"state", "direction"}:
            answer += f" The last stable assistant context I can lean on was about {assistant_snapshot.rstrip('.')}."
        if event_snippets and frame in {"risk", "state"}:
            answer += f" Recent runtime traces also mention {self._join_grounded_memory_items(event_snippets[:2])}."
        if citation_titles and frame in {"mission", "direction"}:
            answer += f" I also still have supporting knowledge nearby through {self._join_grounded_memory_items(citation_titles)}."

        provenance: List[Dict[str, Any]] = []
        for summary in session_summaries[:2]:
            provenance.append({"kind": "session_summary", "event_id": summary.get("event_id"), "timestamp": summary.get("timestamp")})
        for item in global_memories[:2]:
            provenance.append(
                {
                    "kind": str(item.get("memory_type") or "memory"),
                    "event_id": (item.get("metadata") or {}).get("event_id") or item.get("id"),
                    "session_id": (item.get("metadata") or {}).get("session_id"),
                }
            )
        for item in citations[:2]:
            provenance.append(
                {
                    "kind": str(item.get("kind") or "knowledge"),
                    "title": item.get("title"),
                    "collection": item.get("collection"),
                    "source_path": item.get("source_path"),
                }
            )
        if specialist_continuity.get("event_id"):
            provenance.append(
                {
                    "kind": "agent_selection",
                    "event_id": specialist_continuity.get("event_id"),
                    "selected_agent": specialist_name,
                    "task_type": specialist_task_focus,
                }
            )
        return answer, provenance

    def operator_profile_snapshot(
        self,
        *,
        session_id: str,
        global_memories: Optional[List[Dict[str, Any]]] = None,
        session_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        messages = list(session_messages or self.get_session_messages(session_id, limit=24))
        memories = list(
            global_memories
            or self.list_global_memories(
                query="",
                limit=8,
                agent_name="jarvis",
                session_id=session_id,
            )
        )
        fact_statements: List[str] = []
        preferred_name = ""
        operator_role = ""
        assistant_identity = ""
        for item in memories:
            if str(item.get("memory_type") or "") != "operator_fact":
                continue
            statement = self._render_operator_fact_statement(str(item.get("text") or ""))
            if statement:
                fact_statements.append(statement)
            lowered_statement = statement.lower()
            if not preferred_name:
                preferred_name = self._extract_operator_preferred_name(statement)
            if not operator_role and "creator of o.b.e.o.s" in lowered_statement:
                operator_role = "creator of O.B.E.O.S"
            if (
                not assistant_identity
                and "d.a.i.s.e" in lowered_statement
                and "direct artificial intelligence system environment" in lowered_statement
            ):
                assistant_identity = "D.A.I.S.E. (Direct Artificial Intelligence System Environment)"

        if not preferred_name or not operator_role or not assistant_identity:
            history_messages = messages if len(messages) >= 40 else self.get_session_messages(session_id, limit=80)
            for message in history_messages:
                if str(message.get("role") or "") != "user":
                    continue
                text = self._clean_grounded_memory_text(str(message.get("text") or ""))
                lowered_text = text.lower()
                if not preferred_name:
                    preferred_name = self._extract_operator_preferred_name(text)
                if not operator_role and (
                    "i am your creator" in lowered_text
                    or "creator of obeos" in lowered_text
                    or "creator of this blackfong ecosystem" in lowered_text
                    or "i made the whole system" in lowered_text
                ):
                    operator_role = "creator of O.B.E.O.S"
                if not assistant_identity and (
                    "you are d.a.i.s.e" in lowered_text
                    or (
                        "d.a.i.s.e" in lowered_text
                        and "direct artificial intelligence system environment" in lowered_text
                    )
                ):
                    assistant_identity = "D.A.I.S.E. (Direct Artificial Intelligence System Environment)"

        fallback_facts = self._recent_operator_facts(limit=4)
        fact_statements = self._unique_grounded_memory_items(fact_statements + fallback_facts, limit=5)
        if not preferred_name:
            for fact in fact_statements:
                preferred_name = self._extract_operator_preferred_name(fact)
                if preferred_name:
                    break
        if not operator_role:
            for fact in fact_statements:
                if "creator of o.b.e.o.s" in fact.lower():
                    operator_role = "creator of O.B.E.O.S"
                    break
        if not assistant_identity:
            for fact in fact_statements:
                lowered_fact = fact.lower()
                if "d.a.i.s.e" in lowered_fact and "direct artificial intelligence system environment" in lowered_fact:
                    assistant_identity = "D.A.I.S.E. (Direct Artificial Intelligence System Environment)"
                    break

        recent_topics = self._recent_user_topics(messages, current_query="", limit=4)
        stored_topics = self._stored_work_topics(memories, limit=4, session_id=session_id)
        if len(recent_topics) >= 2:
            active_work_seed = recent_topics
        elif recent_topics:
            active_work_seed = recent_topics + stored_topics
        else:
            active_work_seed = stored_topics
        active_work = self._unique_grounded_memory_items(active_work_seed, limit=4)
        return {
            "session_id": session_id,
            "preferred_name": preferred_name,
            "operator_role": operator_role,
            "assistant_identity": assistant_identity,
            "fact_statements": fact_statements,
            "active_work": active_work,
        }

    def _list_recent_session_summaries(
        self,
        *,
        limit: int = 4,
        exclude_session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        summaries: List[Dict[str, Any]] = []
        seen_sessions = set()
        for event in self.event_store.list_events_by_type("session_summary", limit=max(limit * 6, 12)):
            session_id = str(event.payload.get("session_id") or event.session_id or "")
            if exclude_session_id and session_id == exclude_session_id:
                continue
            if session_id and session_id in seen_sessions:
                continue
            seen_sessions.add(session_id)
            summaries.append(
                {
                    "event_id": event.id,
                    "session_id": session_id,
                    "summary": str(event.payload.get("summary", "")),
                    "timestamp": event.timestamp / 1000.0,
                }
            )
            if len(summaries) >= limit:
                break
        return summaries

    def _extract_summary_topics(self, summary_text: Any) -> List[str]:
        cleaned = self._clean_grounded_memory_text(str(summary_text or ""))
        if not cleaned:
            return []
        match = re.search(
            r"user recently discussed (.+?)\. latest assistant response:",
            cleaned,
            re.IGNORECASE,
        )
        if not match:
            snippet = self._trim_grounded_memory_snippet(cleaned, max_chars=110)
            if snippet and not self._looks_like_question_topic(snippet):
                return [snippet]
            return []
        topics = [
            self._trim_grounded_memory_snippet(part, max_chars=100)
            for part in match.group(1).split(";")
        ]
        return [topic for topic in topics if topic and not self._looks_like_question_topic(topic)]

    def _recent_user_topics(
        self,
        session_messages: List[Dict[str, Any]],
        *,
        current_query: str,
        limit: int,
    ) -> List[str]:
        normalized_query = self._clean_grounded_memory_text(current_query).lower()
        topics: List[str] = []
        for message in reversed(session_messages):
            if message.get("role") != "user":
                continue
            text = str(message.get("text") or "")
            extracted_topics = self._extract_work_topics(text)
            message_topics: List[str] = []
            for item in extracted_topics:
                cleaned_item = self._trim_grounded_memory_snippet(item, max_chars=90)
                if (
                    cleaned_item
                    and cleaned_item.lower() != normalized_query
                    and not self._looks_like_noise_topic(cleaned_item)
                    and not self._looks_like_identity_fact(cleaned_item)
                ):
                    message_topics.append(cleaned_item)
            if message_topics:
                topics.extend(message_topics)
                if len(topics) >= limit:
                    return topics[:limit]
            if extracted_topics:
                continue
            snippet = self._trim_grounded_memory_snippet(text, max_chars=100)
            if not snippet or snippet.lower() == normalized_query:
                continue
            if self._looks_like_question_topic(snippet):
                continue
            if self._looks_like_identity_fact(snippet):
                continue
            if self._looks_like_noise_topic(snippet):
                continue
            topics.append(snippet)
            if len(topics) >= limit:
                break
        return topics[:limit]

    def _recent_assistant_snapshot(
        self,
        session_messages: List[Dict[str, Any]],
        *,
        current_query: str = "",
    ) -> str:
        recent_topics = self._recent_user_topics(session_messages, current_query=current_query, limit=4)
        prioritized_topic_tokens = {
            token
            for topic in recent_topics
            for token in self._tokenize(topic)
            if len(token) >= 4
        }
        fallback_snapshot = ""
        for message in reversed(session_messages):
            if message.get("role") != "assistant":
                continue
            snippet = self._trim_grounded_memory_snippet(str(message.get("text") or ""), max_chars=150)
            if not snippet or self._looks_like_low_value_assistant_snapshot(snippet):
                continue
            if not fallback_snapshot:
                fallback_snapshot = snippet
            if prioritized_topic_tokens:
                snippet_tokens = {
                    token
                    for token in self._tokenize(snippet)
                    if len(token) >= 4
                }
                if snippet_tokens.intersection(prioritized_topic_tokens):
                    return snippet
            elif fallback_snapshot:
                return fallback_snapshot
        return fallback_snapshot

    def _recent_operator_facts(self, *, limit: int = 4) -> List[str]:
        facts: List[str] = []
        for event in reversed(self.event_store.list_events_by_type("agent_query", limit=240)):
            text = self._clean_grounded_memory_text(str(event.payload.get("text") or ""))
            lowered = text.lower()
            if not lowered:
                continue
            if (
                "direct artificial intelligence system environment" in lowered
                or "you are d.a.i.s.e" in lowered
                or "you are daise" in lowered
            ):
                facts.append("you named me D.A.I.S.E. and defined it as Direct Artificial Intelligence System Environment")
            if (
                "i am your creator" in lowered
                or "creator of this blackfong ecosystem" in lowered
                or "i made the whole system" in lowered
                or "creator of obeos" in lowered
            ):
                facts.append("you described yourself as the creator of O.B.E.O.S.")
            match = re.search(r'call+\s+me ["“]?([a-z0-9 _-]{2,24})["”]?', text, re.IGNORECASE)
            if match:
                facts.append(f'you asked me to call you "{match.group(1).strip()}"')
            if len(self._unique_grounded_memory_items(facts, limit=limit)) >= limit:
                break
        return self._unique_grounded_memory_items(facts, limit=limit)

    def _render_operator_fact_statement(self, text: str) -> str:
        cleaned = self._clean_grounded_memory_text(text)
        lowered = cleaned.lower()
        if not cleaned:
            return ""
        if lowered.startswith("the operator named the assistant d.a.i.s.e."):
            return "you named me D.A.I.S.E. and defined it as Direct Artificial Intelligence System Environment"
        if lowered.startswith("the operator described themself as "):
            suffix = cleaned[len("The operator described themself as ") :].strip()
            if suffix:
                return f"you described yourself as {suffix}".rstrip(".")
        if lowered.startswith("the operator asked to be called "):
            preferred_name = self._extract_operator_preferred_name(cleaned)
            if preferred_name:
                return f'you asked me to call you "{preferred_name}"'
        return cleaned.rstrip(".")

    def _extract_operator_preferred_name(self, text: str) -> str:
        cleaned = self._clean_grounded_memory_text(text)
        if not cleaned:
            return ""
        for pattern in (
            r'calling you ["“]?([^"”]{2,24})["”]?',
            r'call+\s+you ["“]?([^"”]{2,24})["”]?',
            r'call+\s+me ["“]?([^"”]{2,24})["”]?',
            r'be called ["“]?([^"”]{2,24})["”]?',
        ):
            match = re.search(pattern, cleaned, re.IGNORECASE)
            if match:
                candidate = " ".join(match.group(1).split()).strip()
                candidate = re.sub(r"^by my name\s+", "", candidate, flags=re.IGNORECASE)
                candidate = re.split(r"[,.;!?]", candidate, maxsplit=1)[0].strip()
                if self._is_plausible_operator_name(candidate):
                    return self._normalize_operator_display_name(candidate)
        return ""

    def _is_plausible_operator_name(self, value: str) -> bool:
        cleaned = " ".join(str(value or "").split()).strip().strip('"\'')
        lowered = cleaned.lower()
        if not cleaned or len(cleaned) > 24:
            return False
        if len(cleaned.split()) > 4:
            return False
        if any(
            token in lowered
            for token in (
                "http",
                "api/",
                "127.0.0.1",
                "info:",
                "if that's",
                "if thats",
            )
        ):
            return False
        return True

    def _normalize_operator_display_name(self, value: str) -> str:
        cleaned = " ".join(str(value or "").split()).strip()
        if cleaned.islower() and re.fullmatch(r"[a-z]+(?: [a-z]+){0,2}", cleaned):
            return " ".join(part.capitalize() for part in cleaned.split())
        return cleaned

    def _stored_work_topics(
        self,
        memories: List[Dict[str, Any]],
        *,
        limit: int,
        session_id: Optional[str] = None,
    ) -> List[str]:
        topics: List[str] = []
        for item in memories:
            if str(item.get("memory_type") or "") != "active_work_item":
                continue
            memory_session_id = str(
                ((item.get("metadata") or {}).get("session_id") or item.get("session_id") or "")
            ).strip()
            if session_id and memory_session_id != session_id:
                continue
            text = self._clean_grounded_memory_text(str(item.get("text") or ""))
            working_match = re.search(r"working on (.+?)(?:[.?!]|$)", text, re.IGNORECASE)
            if working_match:
                for topic in self._canonical_work_topics(working_match.group(1)):
                    topics.append(topic)
                continue
            for topic in self._canonical_work_topics(text):
                topics.append(topic)
        return self._unique_grounded_memory_items(topics, limit=limit)

    def _extract_work_topics(self, text: str) -> List[str]:
        cleaned = self._clean_grounded_memory_text(text)
        if not cleaned:
            return []
        cleaned = self._strip_work_topic_greeting_prefix(cleaned)
        if self._looks_like_question_topic(cleaned) and not any(
            marker in cleaned.lower()
            for marker in (
                "improve ",
                "improving ",
                "working on ",
                "trying to ",
                "keeps failing",
                "fix ",
                "build ",
                "implement ",
                "enable ",
                "set up ",
                "configure ",
                "update ",
            )
        ):
            return []
        topics: List[str] = list(self._canonical_work_topics(cleaned))
        lowered = cleaned.lower()
        for pattern in (
            r"working on ([^.]+)",
            r"trying to improve ([^.]+)",
            r"improve ([^.]+)",
            r"updates? to ([^.]+)",
        ):
            for match in re.finditer(pattern, lowered):
                fragment = cleaned[match.start(1) : match.end(1)]
                for topic in self._canonical_work_topics(fragment):
                    topics.append(topic)
        return self._unique_grounded_memory_items(topics, limit=4)

    def _strip_work_topic_greeting_prefix(self, text: str) -> str:
        cleaned = self._clean_grounded_memory_text(text)
        if not cleaned:
            return ""
        lowered = cleaned.lower()
        greeting_prefixes = (
            "hello, ",
            "hello ",
            "hi, ",
            "hi ",
            "hey, ",
            "hey ",
            "good morning, ",
            "good morning ",
            "good afternoon, ",
            "good afternoon ",
            "good evening, ",
            "good evening ",
            "im back, ",
            "im back ",
            "i'm back, ",
            "i'm back ",
            "we are back, ",
            "we are back ",
            "we're back, ",
            "we're back ",
        )
        for prefix in greeting_prefixes:
            if lowered.startswith(prefix) and len(cleaned) > len(prefix):
                return cleaned[len(prefix) :].strip(" ,.;:-")
        return cleaned

    def _canonical_work_topics(self, text: str) -> List[str]:
        cleaned = self._strip_work_topic_greeting_prefix(text).strip(" ,.;:-")
        lowered = cleaned.lower()
        if not cleaned or len(cleaned) <= 1:
            return []
        if lowered in {
            "hello",
            "hi",
            "im back",
            "i'm back",
            "we are back",
            "we're back",
            "were back",
            "good afternoon",
            "good evening",
            "good morning",
        }:
            return []
        if any(
            token in lowered
            for token in (
                "test message",
                "im back",
                "i'm back",
                "what is your name",
                "what's your name",
                "what do you remember",
                "what can you recall",
                "what about a memory",
                "do you remember your name",
                "did you get",
                "you can get this message",
                "100% offline",
                "debug test session",
                "lets test",
                "let's test",
                "max amount of characters",
                "hint",
                "you never responded",
                "know about obeos",
                "jarvis or daise",
                "shared memory from my computer",
                "communicating to you now through telegram",
                "communicating to you through telegram",
                "through telegram you just have",
                "just have a shared memory",
            )
        ):
            return []
        if (
            "spawn" in lowered
            and any(
                phrase in lowered
                for phrase in (
                    "tell me about spawn",
                    "what do we know about spawn",
                    "what have we got on spawn",
                    "what is in our spawn knowledge",
                    "do you still know anything about spawn",
                )
            )
        ):
            return []
        if lowered in {"obeos", "obeos?", "your system", "the system", "d"}:
            return []

        topics: List[str] = []
        if "tinyprogrammer" in lowered or "tiny programmer" in lowered:
            if "integration" in lowered:
                topics.append("TinyProgrammer integration")
            if "up and running" in lowered or "online" in lowered or "availability" in lowered:
                topics.append("TinyProgrammer availability")
            if "startup" in lowered or "auto starting" in lowered:
                topics.append("TinyProgrammer startup")
            if "failing" in lowered or "failure" in lowered or "keeps failing" in lowered:
                topics.append("TinyProgrammer reliability")
            if not topics:
                topics.append("TinyProgrammer updates")
        if "improve obeos" in lowered or "improve your system" in lowered or "improve the ecosystem" in lowered:
            topics.append("OBEOS improvements")
        if "files in obeos" in lowered or "see the files" in lowered:
            topics.append("OBEOS file visibility")
        if "file system" in lowered or "filesystem" in lowered:
            topics.append("OBEOS file visibility")
        if "enable the hud" in lowered or ("notification" in lowered and ("prefrence" in lowered or "preference" in lowered or "preferences" in lowered)):
            topics.append("HUD notifications")
        if "building you" in lowered or "learn more about building you" in lowered:
            topics.append("D.A.I.S.E. knowledge growth")
        if (
            "memory is something we are working on" in lowered
            or "overall memory" in lowered
            or ("working on" in lowered and "memory" in lowered)
            or ("remembered it" in lowered and "name" in lowered)
            or "some kind of memory" in lowered
            or ("memory" in lowered and "milestone" in lowered)
        ):
            topics.append("OBEOS memory system")
        if "latency" in lowered or "response time" in lowered or "delay in your reply time" in lowered or "what happened to your response" in lowered:
            topics.append("response latency")
        if "chat rendering" in lowered or "chatbox" in lowered:
            topics.append("chat rendering")
        if "hud" in lowered and ("design" in lowered or "layout" in lowered or "workspace" in lowered):
            topics.append("HUD layout improvements")
        if "spawn" in lowered:
            topics.append("Spawn knowledge recall")
        if "backend" in lowered and any(
            token in lowered
            for token in (
                "working",
                "changes",
                "change",
                "update",
                "updates",
                "improve",
                "improving",
                "refactor",
                "latency",
            )
        ):
            topics.append("OBEOS backend work")
        for fragment in re.split(r",|\band\b", cleaned, flags=re.IGNORECASE):
            candidate = fragment.strip(" ,.;:-")
            if not self._looks_like_plausible_work_fragment(candidate):
                continue
            if self._fragment_is_covered_by_existing_topic(candidate, topics):
                continue
            if any(candidate.lower() == existing.lower() for existing in topics):
                continue
            topics.append(candidate)
        deduped = self._unique_grounded_memory_items(topics, limit=4)
        if deduped:
            return deduped
        return []

    def _looks_like_plausible_work_fragment(self, text: str) -> bool:
        cleaned = self._clean_grounded_memory_text(text)
        lowered = cleaned.lower()
        if not cleaned:
            return False
        if len(cleaned) > 48 or len(cleaned.split()) > 4:
            return False
        if len(cleaned.split()) < 2:
            return False
        if self._looks_like_question_topic(cleaned):
            return False
        if self._looks_like_identity_fact(cleaned):
            return False
        if self._looks_like_noise_topic(cleaned):
            return False
        boilerplate_prefixes = (
            "call me",
            "i am working on",
            "i'm working on",
            "working on",
            "i am",
            "i'm",
        )
        if lowered.startswith(boilerplate_prefixes):
            return False
        if lowered.startswith(("you ", "your ", "yes ", "add ", "as of ", "from ")):
            return False
        if any(
            token in lowered
            for token in (
                "jarvis or daise",
                "shared memory",
                "through telegram",
                "communicating to you",
                "from my computer",
                "good evening",
                "good morning",
                "good afternoon",
                "still there",
                "are you hungry",
            )
        ):
            return False
        if lowered in {
            "yes for now",
            "add to you",
            "you see",
            "your latency",
            "as of now",
            "from hud smoke",
            "you adapt",
            "you are not static",
        }:
            return False
        return True

    def _fragment_is_covered_by_existing_topic(self, fragment: str, existing_topics: List[str]) -> bool:
        lowered_fragment = self._clean_grounded_memory_text(fragment).lower()
        lowered_topics = [self._clean_grounded_memory_text(item).lower() for item in existing_topics]
        anchor_groups = (
            ("latency",),
            ("hud",),
            ("notification",),
            ("spawn",),
            ("memory",),
            ("backend",),
            ("telegram",),
            ("tinyprogrammer", "tiny programmer"),
            ("file", "filesystem"),
        )
        for anchors in anchor_groups:
            if any(anchor in lowered_fragment for anchor in anchors) and any(
                any(anchor in topic for anchor in anchors) for topic in lowered_topics
            ):
                return True
        return False

    def _looks_like_identity_fact(self, text: str) -> bool:
        lowered = self._clean_grounded_memory_text(text).lower()
        return any(
            token in lowered
            for token in (
                "you are d.a.i.s.e",
                "direct artificial intelligence system environment",
                "i am your creator",
                "call me",
            )
        )

    def _looks_like_noise_topic(self, text: str) -> bool:
        lowered = self._clean_grounded_memory_text(text).lower()
        if not lowered:
            return True
        if lowered in {
            "hello",
            "hi",
            "im back",
            "i'm back",
            "we are back",
            "we're back",
            "hello we are back",
            "hello, we are back",
            "hi we are back",
            "hi, we are back",
            "hey we are back",
            "hey, we are back",
            "good afternoon",
            "good evening",
        }:
            return True
        return any(
            token in lowered
            for token in (
                "test message",
                "debug test session",
                "lets test",
                "let's test",
                "max amount of characters",
                "respond if so",
                "did you get that",
                "you never responded",
                "interesting that you were able to detect that",
                "info:werkzeug",
                "127.0.0.1",
                "http/1.1",
                "/api/",
                "made a good amount of changes",
                "they should reflect",
                "congratulate you on that milestone",
                "some kind of memory",
                "jarvis or daise",
                "shared memory from my computer",
                "communicating to you now through telegram",
                "communicating to you through telegram",
                "through telegram you just have",
                "are you hungry",
                "are you still there",
                "still there",
                "that wasnt my question",
                "that wasn't my question",
                "not my question",
                "now you are just saying things",
                "you are just saying things",
                "better conversing than remembering",
                "i wont ask you to recall",
                "i won't ask you to recall",
                "not answering me",
                "didnt answer me",
                "didn't answer me",
                "regrouping and assessing your approach",
            )
        )

    def _looks_like_low_value_assistant_snapshot(self, text: str) -> bool:
        lowered = self._clean_grounded_memory_text(text).lower()
        if not lowered:
            return True
        if lowered in {
            "hello, five.",
            "hello, five",
            "welcome back, five.",
            "welcome back, five",
            "welcome back.",
            "welcome back",
        }:
            return True
        return any(
            token in lowered
            for token in (
                "this appears to be a test message",
                "would you like to proceed with a legitimate question",
                "i see multiple possible intents here",
                "i will keep that in mind",
                "offline-first assistant",
                "yes, i can see the difference",
                "shared o.b.e.o.s memory layer",
                "separate hidden notebook",
                "conversation memory backed by events.db",
                "d.a.i.s.e is online locally",
                "route work through the rest of the ecosystem",
                "the weakest areas right now are wherever",
                "what stands out most to me right now is",
                "the overall situation right now centers on",
                "i think o.b.e.o.s. is moving toward a more coherent living system",
                "honestly, i think o.b.e.o.s.",
                "five, honestly, i think o.b.e.o.s.",
                "hello, five. i'm here in o.b.e.o.s and ready",
                "hello, i’m here in o.b.e.o.s and ready",
                "welcome back, five. i'm here in o.b.e.o.s and ready",
                "welcome back. i'm here in o.b.e.o.s and ready",
            )
        )

    def _looks_like_low_value_event_snippet(self, text: str) -> bool:
        lowered = self._clean_grounded_memory_text(text).lower()
        if not lowered:
            return True
        return any(
            token in lowered
            for token in (
                "routing chose ",
                "active work:",
                "memory retrieval (",
                "request complete",
                "phase=",
                "decision_state",
                "workers=",
                "reported confidence",
                "response_event_id",
                "selected_agent",
            )
        )

    def _looks_like_question_topic(self, text: str) -> bool:
        cleaned = self._clean_grounded_memory_text(text)
        lowered = cleaned.lower()
        if not cleaned:
            return False
        if "?" in str(text or ""):
            return True
        question_prefixes = (
            "what ",
            "what's ",
            "whats ",
            "who ",
            "who's ",
            "whos ",
            "why ",
            "how ",
            "when ",
            "where ",
            "can you ",
            "could you ",
            "would you ",
            "do you ",
            "did you ",
            "are you ",
            "is tinyprogrammer ",
            "is tiny programmer ",
            "tell me ",
            "explain ",
            "describe ",
            "summarize ",
            "show me ",
        )
        return any(lowered.startswith(prefix) for prefix in question_prefixes)

    def _clean_grounded_memory_text(self, text: str) -> str:
        normalized = " ".join(str(text or "").strip().split())
        normalized = normalized.replace("INFO:werkzeug:", "").strip()
        return normalized

    def _trim_grounded_memory_snippet(self, text: str, *, max_chars: int = 120) -> str:
        cleaned = self._clean_grounded_memory_text(text)
        if not cleaned:
            return ""
        sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
        candidate = sentence or cleaned
        candidate = re.sub(r"^Session summary:\s*", "", candidate, flags=re.IGNORECASE)
        candidate = candidate.strip(" -")
        if len(candidate) > max_chars:
            candidate = candidate[: max_chars - 3].rstrip(" ,;:-") + "..."
        return candidate

    def _unique_grounded_memory_items(self, items: List[str], *, limit: int) -> List[str]:
        deduped: List[str] = []
        seen = set()
        for item in items:
            cleaned = self._clean_grounded_memory_text(item)
            if not cleaned:
                continue
            key = cleaned.lower().strip(" .")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(cleaned.rstrip("."))
            if len(deduped) >= limit:
                break
        return deduped

    @staticmethod
    def _join_grounded_memory_items(items: List[str]) -> str:
        cleaned = [str(item).strip().rstrip(".") for item in items if str(item).strip()]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        if len(cleaned) == 2:
            return f"{cleaned[0]} and {cleaned[1]}"
        return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"

    @staticmethod
    def _count_phrase_matches(text: str, phrases: Sequence[str]) -> int:
        return sum(1 for phrase in phrases if phrase in text)

    @staticmethod
    def _count_token_matches(tokens: set[str], candidates: set[str]) -> int:
        return sum(1 for token in tokens if token in candidates)

    def _collection_summary_hit(
        self,
        collection: str,
        *,
        query: str,
        query_tokens: List[str],
        retrieval_profile: str,
    ) -> Optional[Dict[str, Any]]:
        payload, summary_path = self._collection_summary_payload(collection)
        if payload is None:
            return None
        text = self._compact_collection_summary_text(
            payload,
            query=query,
            collection_label=self._collection_display_name(collection),
        )
        if not text:
            return None
        collection_slug = str(payload.get("collection") or collection or "").strip().lower().replace("-", "_")
        title = str(payload.get("title") or self._collection_display_name(collection_slug))
        score = 1.1 + self._token_overlap_score(query_tokens, f"{title} {text}".lower(), weight=0.03)
        if retrieval_profile == "architecture" and not self._is_architecture_hit(
            title=title,
            text=text,
            metadata={"collection": collection_slug, "source_path": str(summary_path)},
            collection=collection_slug,
        ):
            return None
        citation = {
            "vector_id": f"collection-summary:{collection_slug}",
            "kind": "collection_summary",
            "title": title,
            "collection": collection_slug,
            "source_path": str(summary_path),
            "source_id": None,
            "section": payload.get("kind") or "collection_summary",
            "relevance": 0.98,
            "distance": 0.02,
            "text_preview": text[:280],
        }
        return {
            "id": f"collection-summary:{collection_slug}",
            "kind": "collection_summary",
            "title": title,
            "collection": collection_slug,
            "text": text,
            "relevance": 0.98,
            "distance": 0.02,
            "score": round(score, 6),
            "metadata": {
                "collection": collection_slug,
                "source_path": str(summary_path),
                "summary_kind": payload.get("kind") or "collection_summary",
                "domain": payload.get("domain") or "",
            },
            "citation": citation,
        }

    def _search_hinted_collection_results(
        self,
        *,
        query: str,
        hinted_collections: List[str],
        retrieval_profile: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        if not hinted_collections or limit <= 0:
            return []
        _source_registry, collection_registry = self._knowledge_registries()
        targeted_results: List[Dict[str, Any]] = []
        for collection in hinted_collections[:4]:
            description = ""
            domain = ""
            subdomain = ""
            category = ""
            if collection_registry is not None:
                meta = collection_registry.get(collection)
                if meta is not None:
                    description = str(meta.description or "")
                    domain = str(meta.domain or "")
                    subdomain = str(meta.subdomain or "")
                    category = str(meta.category or "")
            suffix = (
                "architecture topology graph contract interface routing"
                if retrieval_profile == "architecture"
                else "overview characters plot context"
            )
            collection_query = " ".join(
                part
                for part in [
                    query,
                    collection.replace("_", " "),
                    description,
                    domain,
                    subdomain,
                    category,
                    suffix,
                ]
                if str(part or "").strip()
            )
            try:
                matches = self.vector_store.search(
                    collection_query,
                    k=limit,
                    metadata_filter={"collection": collection},
                )
            except TypeError:
                matches = self.vector_store.search(collection_query, k=limit)
            except Exception:
                matches = []
            for result in matches:
                enriched_result = dict(result)
                metadata = dict(enriched_result.get("metadata") or {})
                metadata["_hinted_collection"] = collection
                enriched_result["metadata"] = metadata
                targeted_results.append(enriched_result)
        return targeted_results

    def _collection_summary_payload(self, collection: str) -> tuple[Optional[Dict[str, Any]], Optional[Path]]:
        candidates = [
            paths.knowledge_intel_path / collection / "summary.json",
            paths.knowledge_intel_path / collection / "collection_summary.json",
            paths.knowledge_intel_path / collection / "summaries" / "collection_summary.json",
        ]
        summary_path = next((path for path in candidates if path.exists()), None)
        if summary_path is not None:
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload.setdefault("fallback_generated", False)
                    return payload, summary_path
            except Exception:
                pass
        fallback = self._build_collection_summary_fallback(collection)
        if fallback is None:
            return None, None
        return fallback, paths.knowledge_intel_path / collection / "summary.fallback.json"

    def _compact_collection_summary_text(
        self,
        payload: Dict[str, Any],
        *,
        query: str = "",
        collection_label: str = "",
    ) -> str:
        if not isinstance(payload, dict):
            return ""
        overview = self._clean_grounded_collection_text(str(payload.get("overview") or ""))
        description = self._clean_grounded_collection_text(str(payload.get("description") or ""))
        title = str(collection_label or payload.get("title") or payload.get("collection") or "Collection").strip()
        domain = str(payload.get("domain") or "").strip()
        sources = int(payload.get("sources") or 0)
        embedded = int(payload.get("embedded") or 0)
        total_chunks = int(payload.get("total_chunks") or 0)
        issue_ranges = list(payload.get("issue_ranges") or [])
        timeline_events = int(payload.get("timeline_events") or payload.get("event_count") or 0)
        fallback_generated = bool(payload.get("fallback_generated"))
        wants_coverage = self._collection_query_wants_coverage(query)
        cleaned_text = self._clean_grounded_collection_text(
            str(payload.get("text") or payload.get("extractive_text") or payload.get("summary") or "")
        )
        if cleaned_text and not fallback_generated:
            return cleaned_text

        lines: List[str] = []
        if overview:
            lines.append(overview.rstrip(".") + ".")
        if description:
            lines.append(f"{title} is an O.B.E.O.S. knowledge collection focused on {description.rstrip('.')}.")
        elif domain:
            lines.append(f"{title} is covered in the local {domain} collection.")
        if wants_coverage:
            coverage_bits = []
            if sources:
                coverage_bits.append(f"{sources} source(s)")
            if embedded:
                coverage_bits.append(f"{embedded} embedded")
            if total_chunks:
                coverage_bits.append(f"{total_chunks} searchable chunk(s)")
            if coverage_bits:
                lines.append(f"Coverage: {', '.join(coverage_bits)}.")
            if issue_ranges:
                preview = ", ".join(issue_ranges[:6])
                suffix = ", and more" if len(issue_ranges) > 6 else ""
                lines.append(f"Issue ranges: {preview}{suffix}.")
            if timeline_events:
                lines.append(f"Structured timeline entries: {timeline_events}.")
        return " ".join(line.strip() for line in lines if line.strip())

    def _compose_grounded_collection_answer(
        self,
        *,
        query: str,
        collection: str,
        summary_payload: Optional[Dict[str, Any]],
        fallback_text: str,
    ) -> str:
        collection_label = self._collection_display_name(collection)
        description = self._clean_grounded_collection_text(str((summary_payload or {}).get("description") or ""))
        domain = str((summary_payload or {}).get("domain") or "").strip()
        sources = int((summary_payload or {}).get("sources") or 0)
        embedded = int((summary_payload or {}).get("embedded") or 0)
        total_chunks = int((summary_payload or {}).get("total_chunks") or 0)
        issue_ranges = list((summary_payload or {}).get("issue_ranges") or [])
        representative_passages = list((summary_payload or {}).get("representative_passages") or [])
        fallback_generated = bool((summary_payload or {}).get("fallback_generated"))
        parts: List[str] = []
        cleaned_summary = self._clean_grounded_collection_text(
            str((summary_payload or {}).get("text") or (summary_payload or {}).get("extractive_text") or (summary_payload or {}).get("summary") or "")
        )
        if cleaned_summary and not fallback_generated:
            parts.append(cleaned_summary.rstrip(".") + ".")
        raw_support_excerpt = self._clean_grounded_collection_text(
            str(representative_passages[0] if representative_passages else "")
        )
        support_summary = self._summarize_collection_excerpt(
            raw_support_excerpt,
            collection_label=collection_label,
        ) if raw_support_excerpt else ""
        support_excerpt = self._focus_collection_excerpt(
            raw_support_excerpt,
            collection_label=collection_label,
        )
        wants_overview = self._is_collection_overview_style_query(query, collection_label=collection_label)
        wants_coverage = self._collection_query_wants_coverage(query)
        wants_resources = self._collection_query_wants_resources(query)
        architecture_summary = self._compose_architecture_collection_summary(
            collection=collection,
            collection_label=collection_label,
            domain=domain,
            description=description,
            cleaned_summary=cleaned_summary,
            support_summary=support_summary,
            support_excerpt=support_excerpt,
            representative_passages=representative_passages,
        )
        if architecture_summary and wants_overview:
            parts.append(architecture_summary.rstrip(".") + ".")
            if wants_resources and str(collection or "").strip().lower() == "obeos":
                parts.append(
                    "Operator-facing resources currently include Blackfong governance, D.A.I.S.E. conversation, Thoughtglass, Memory Chamber, the browser HUD, TinyProgrammer, and the shared event-and-memory stack."
                )
        elif support_summary:
            if cleaned_summary and not fallback_generated and support_summary.lower() in cleaned_summary.lower():
                pass
            elif collection_label.lower() in support_summary.lower():
                parts.append(support_summary.rstrip(".") + ".")
            elif wants_overview:
                parts.append(f"{collection_label} centers on {support_summary.rstrip('.')}.")
            else:
                parts.append(f"A grounded passage frames {collection_label} as {support_summary.rstrip('.')}.")
        elif support_excerpt and not self._is_collection_excerpt_boilerplate(support_excerpt):
            if wants_overview and not cleaned_summary:
                parts.append(f"{collection_label} is described here as {support_excerpt.rstrip('.')}.")
            elif not cleaned_summary:
                parts.append(f"One retrieved passage describes {support_excerpt.rstrip('.')}.")
        if not parts:
            if architecture_summary:
                parts.append(architecture_summary.rstrip(".") + ".")
            elif description:
                parts.append(f"{collection_label} is covered in local O.B.E.O.S. knowledge around {description.rstrip('.')}.")
            elif domain:
                parts.append(f"{collection_label} is a local {domain} collection inside O.B.E.O.S.")
        if wants_coverage and (sources or embedded or total_chunks):
            coverage_bits = []
            if sources:
                coverage_bits.append(f"{sources} registered source(s)")
            if embedded:
                coverage_bits.append(f"{embedded} embedded")
            if total_chunks:
                coverage_bits.append(f"{total_chunks} searchable chunk(s)")
            if coverage_bits:
                parts.append(f"Current local coverage includes {', '.join(coverage_bits)}.")
        if wants_coverage and issue_ranges:
            preview = ", ".join(issue_ranges[:6])
            suffix = ", and more" if len(issue_ranges) > 6 else ""
            parts.append(f"The current collection spans issue ranges {preview}{suffix}.")
        if parts:
            return " ".join(parts).strip()
        if not cleaned_summary:
            return fallback_text
        return cleaned_summary

    def _compose_architecture_collection_summary(
        self,
        *,
        collection: str,
        collection_label: str,
        domain: str,
        description: str,
        cleaned_summary: str,
        support_summary: str,
        support_excerpt: str,
        representative_passages: List[str],
    ) -> str:
        normalized_collection = str(collection or "").strip().lower()
        lowered = " ".join(
            part
            for part in (
                cleaned_summary,
                support_summary,
                support_excerpt,
                description,
                *[self._clean_grounded_collection_text(str(passage or "")) for passage in representative_passages[:3]],
            )
            if str(part or "").strip()
        ).lower()
        if str(domain or "").strip().lower() != "architecture" and normalized_collection not in {"obeos", "blackfong"}:
            return ""
        if (
            "official blackfong environment operating system" in lowered
            or normalized_collection == "obeos"
        ):
            summary = (
                f"{collection_label} is the Official Blackfong Environment Operating System: "
                "an offline-first intelligence environment built around one governing spine, "
                "one shared memory system, and multiple operator surfaces"
            )
            if normalized_collection == "obeos" or any(
                token in lowered for token in ("d.a.i.s.e", "daise", "thoughtglass", "memory chamber")
            ):
                summary += (
                    ", with Blackfong as the governing spine, D.A.I.S.E. as the visible assistant, "
                    "Thoughtglass as the live cognition surface, and Memory Chamber as the deeper workbench"
                )
            return summary
        if "governing spine" in lowered and "shared memory" in lowered:
            return (
                f"{collection_label} is an offline-first architecture built around one governing spine, "
                "one shared memory system, and multiple operator surfaces"
            )
        if "operator shell" in lowered or "canonical native operator shell" in lowered:
            return (
                f"{collection_label} is described as a multi-surface operating environment "
                "with a canonical native workspace and secondary remote operator surfaces"
            )
        return ""

    def _is_collection_overview_style_query(self, query: str, *, collection_label: str) -> bool:
        normalized = " ".join(str(query or "").strip().lower().split())
        if not normalized:
            return False
        raw_label = str(collection_label or "").strip().lower()
        collection_forms = {
            raw_label,
            raw_label.replace("_", " "),
            re.sub(r"[^a-z0-9]+", "", raw_label),
            " ".join(re.sub(r"[^a-z0-9]+", " ", raw_label).split()),
        }
        mentions_collection = any(form and form in normalized for form in collection_forms)
        if not mentions_collection:
            return False
        starters = (
            "tell me about",
            "who is",
            "what is",
            "what's",
            "what resources",
            "which resources",
            "what tools",
            "which tools",
            "what do we have",
            "what exists in",
            "what is in",
            "give me an overview of",
            "explain",
        )
        return any(starter in normalized for starter in starters)

    def _collection_query_wants_coverage(self, query: str) -> bool:
        normalized = " ".join(str(query or "").strip().lower().split())
        if not normalized:
            return False
        coverage_markers = (
            "how much do you know",
            "what do you have on",
            "coverage",
            "sources",
            "issue range",
            "issue ranges",
            "which issues",
            "span",
            "how many",
            "what is in the collection",
        )
        return any(marker in normalized for marker in coverage_markers)

    def _collection_query_wants_resources(self, query: str) -> bool:
        normalized = " ".join(str(query or "").strip().lower().split())
        if not normalized:
            return False
        resource_markers = (
            "what resources",
            "which resources",
            "what tools",
            "which tools",
            "what do we have",
            "what is in obeos",
            "what's in obeos",
            "what exists in obeos",
            "within obeos do we have",
        )
        return any(marker in normalized for marker in resource_markers)

    def _clean_grounded_collection_text(self, text: str) -> str:
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return ""
        for stopper in (
            "Representative passages:",
            "Structured timeline entries:",
            "Structured timeline",
            "Open questions:",
            "Known gaps:",
        ):
            index = cleaned.lower().find(stopper.lower())
            if index > 0:
                cleaned = cleaned[:index].rstrip(" ,.;:-")
                break
        cleaned = re.sub(r"[_*#~`|]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:-")
        if len(cleaned) > 420:
            cleaned = cleaned[:417].rstrip(" ,.;:-") + "..."
        return cleaned

    def _focus_collection_excerpt(self, text: str, *, collection_label: str) -> str:
        cleaned = self._clean_grounded_collection_text(text)
        if not cleaned:
            return ""
        lowered = cleaned.lower()
        label = str(collection_label or "").strip().lower()
        if label and label in lowered:
            index = lowered.find(label)
            if index > 24:
                prefix = cleaned[:index]
                cut = max(prefix.rfind(". "), prefix.rfind("! "), prefix.rfind("? "), prefix.rfind("; "))
                if cut >= 0:
                    cleaned = cleaned[cut + 2 :].lstrip(" ,.;:-")
                else:
                    cleaned = cleaned[index:].lstrip(" ,.;:-")
        return self._soften_ocr_excerpt(cleaned)

    def _soften_ocr_excerpt(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "").strip())
        if not cleaned:
            return ""
        words = re.findall(r"[A-Za-z]{3,}", cleaned)
        uppercase_ratio = (
            sum(1 for word in words if word.isupper()) / len(words)
            if words
            else 0.0
        )
        if uppercase_ratio >= 0.25:
            cleaned = cleaned.lower()
            cleaned = cleaned[0].upper() + cleaned[1:]
            cleaned = re.sub(r"([.!?]\s+)([a-z])", lambda match: match.group(1) + match.group(2).upper(), cleaned)
        cleaned = re.sub(r"\s*-\s*", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = re.sub(r"\s+\.\s*", ". ", cleaned)
        cleaned = re.sub(r"\s+,", ",", cleaned)
        cleaned = re.sub(r"\.\s*,", ".", cleaned)
        cleaned = re.sub(r"([a-z])\s+([A-Z])", r"\1. \2", cleaned)
        return cleaned.strip(" ,.;:-")

    def _summarize_collection_excerpt(self, text: str, *, collection_label: str) -> str:
        lowered = str(text or "").lower()
        label = str(collection_label or "").strip() or "This collection"
        if "official blackfong environment operating system" in lowered:
            return (
                f"{label} is the Official Blackfong Environment Operating System, "
                "an offline-first intelligence environment built around one governing spine, "
                "one shared memory system, and multiple operator surfaces"
            )
        if "governing spine" in lowered and "shared memory" in lowered:
            return (
                f"{label} is an offline-first architecture built around one governing spine "
                "and one shared memory system"
            )
        if "visible assistant" in lowered and ("thoughtglass" in lowered or "memory chamber" in lowered):
            return (
                f"{label} keeps Blackfong as the governing spine, D.A.I.S.E. as the visible assistant, "
                "Thoughtglass as the live cognition surface, and Memory Chamber as the deeper workbench"
            )
        if "brought back to life" in lowered and "identity" in lowered:
            return f"{label} follows a dead man brought back to life, stripped of his identity, and charged with dangerous power"
        if "agent of hell" in lowered:
            return f"{label} is an agent of hell whose existence sets dangerous consequences in motion"
        if "al simmons" in lowered and ("soldier of h" in lowered or "hell" in lowered):
            return f"{label} centers on Al Simmons, who struggles against the role of a soldier of hell"
        if "al simmons" in lowered and "wanda" in lowered:
            return f"{label} follows Al Simmons and his ties to Wanda and Terry"
        if collection_label.lower() in lowered and "understood" in lowered:
            return f"{label} is treated as a figure the story itself says people never fully understood"
        return ""

    def _build_collection_summary_fallback(self, collection: str) -> Optional[Dict[str, Any]]:
        collection_slug = str(collection or "").strip().lower().replace("-", "_")
        if not collection_slug:
            return None
        intel_dir = paths.knowledge_intel_path / collection_slug
        if not intel_dir.exists():
            return None
        inventory = self._load_collection_intel_json(collection_slug, "inventory.json")
        coverage = self._load_collection_intel_json(collection_slug, "coverage.json")
        timeline = self._load_collection_intel_json(collection_slug, "timeline.json")
        _source_registry, collection_registry = self._knowledge_registries()
        meta = collection_registry.get(collection_slug) if collection_registry is not None else None
        title = self._collection_display_name(collection_slug)
        summary_title = title
        description = str(getattr(meta, "description", "") or "")
        domain = str(
            (inventory or {}).get("domain")
            or (coverage or {}).get("domain")
            or getattr(meta, "domain", "")
            or ""
        ).strip()
        sources = int((inventory or {}).get("sources") or (coverage or {}).get("total_sources") or 0)
        embedded = int((inventory or {}).get("embedded") or (coverage or {}).get("embedded_sources") or 0)
        total_chunks = int((inventory or {}).get("total_chunks") or (coverage or {}).get("total_chunks") or 0)
        issue_ranges = list((inventory or {}).get("issue_ranges") or [])
        timeline_events = int((timeline or {}).get("event_count") or 0)
        representative = self._collection_summary_passages(collection_slug, description=description, domain=domain, limit=2)
        overview = ""
        for excerpt in representative:
            overview = self._summarize_collection_excerpt(excerpt, collection_label=title)
            if overview:
                break

        lines: List[str] = []
        if overview:
            lines.append(f"Overview: {overview.rstrip('.')}.")
        if description:
            lines.append(f"{title} is an OBEOS knowledge collection focused on {description.rstrip('.')}.")
        else:
            lines.append(f"{title} is an OBEOS knowledge collection.")
        if domain:
            lines.append(f"Domain: {domain}.")
        if sources or embedded or total_chunks:
            coverage_bits = []
            if sources:
                coverage_bits.append(f"{sources} source(s)")
            if embedded:
                coverage_bits.append(f"{embedded} embedded")
            if total_chunks:
                coverage_bits.append(f"{total_chunks} searchable chunk(s)")
            if coverage_bits:
                lines.append(f"Coverage: {', '.join(coverage_bits)}.")
        if issue_ranges:
            preview = ", ".join(issue_ranges[:6])
            suffix = "..." if len(issue_ranges) > 6 else ""
            lines.append(f"Issue ranges: {preview}{suffix}.")
        if timeline_events:
            lines.append(f"Structured timeline entries: {timeline_events}.")
        if representative:
            lines.append("Representative passages:")
            for excerpt in representative:
                lines.append(f"- {excerpt}")
        text = "\n".join(lines).strip()
        if not text:
            return None
        if domain.lower() == "architecture" and "architecture" not in summary_title.lower():
            summary_title = f"{title} Architecture"
        return {
            "collection": collection_slug,
            "domain": domain,
            "kind": "collection_summary",
            "title": summary_title,
            "overview": overview,
            "description": description,
            "sources": sources,
            "embedded": embedded,
            "total_chunks": total_chunks,
            "issue_ranges": issue_ranges,
            "timeline_events": timeline_events,
            "representative_passages": representative,
            "fallback_generated": True,
            "text": text,
        }

    def _load_collection_intel_json(self, collection: str, name: str) -> Optional[Dict[str, Any]]:
        path = paths.knowledge_intel_path / collection / name
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _collection_summary_passages(
        self,
        collection: str,
        *,
        description: str = "",
        domain: str = "",
        limit: int = 2,
    ) -> List[str]:
        if limit <= 0:
            return []
        query = " ".join(
            part
            for part in [
                collection.replace("_", " "),
                description,
                (
                    "official blackfong environment operating system governing spine shared memory "
                    "operator surfaces daise thoughtglass memory chamber workspace architecture"
                    if str(domain or "").strip().lower() == "architecture"
                    else "overview character story context origin identity protagonist relationships"
                ),
            ]
            if str(part or "").strip()
        )
        fetch_k = max(limit * 25, 60)
        try:
            matches = self.vector_store.search(
                query,
                k=fetch_k,
                metadata_filter={"collection": collection},
            )
        except TypeError:
            matches = self.vector_store.search(query, k=fetch_k)
        except Exception:
            matches = []
        excerpts: List[str] = []
        scored_matches: List[tuple[float, str]] = []
        for match in matches:
            cleaned = " ".join(str(match.get("text") or "").split())
            if not cleaned:
                continue
            excerpt = cleaned[:320] if str(domain or "").strip().lower() == "architecture" else cleaned[:220]
            if self._is_collection_excerpt_boilerplate(excerpt):
                continue
            readability = self._collection_excerpt_readability(excerpt)
            if readability <= 0.0:
                continue
            score = readability + float(match.get("relevance", 0.0)) + self._collection_excerpt_descriptor_score(
                excerpt,
                domain=domain,
            )
            scored_matches.append((score, excerpt))
        seen: set[str] = set()
        for _score, excerpt in sorted(scored_matches, key=lambda item: item[0], reverse=True):
            if excerpt in seen:
                continue
            seen.add(excerpt)
            excerpts.append(excerpt)
            if len(excerpts) >= limit:
                break
        return excerpts

    def _collection_excerpt_readability(self, excerpt: str) -> float:
        text = str(excerpt or "").strip()
        if not text:
            return 0.0
        lowered = text.lower()
        if text.lstrip().startswith("#") and len(re.findall(r"[A-Za-z]{3,}", text)) < 12:
            return 0.0
        alpha_chars = sum(1 for char in text if char.isalpha())
        if alpha_chars <= 0:
            return 0.0
        uppercase_words = sum(
            1
            for token in re.findall(r"[A-Za-z]{3,}", text)
            if token.isupper()
        )
        punctuation_chars = sum(1 for char in text if not char.isalnum() and not char.isspace())
        alpha_ratio = alpha_chars / max(len(text), 1)
        punctuation_ratio = punctuation_chars / max(len(text), 1)
        score = alpha_ratio - (uppercase_words * 0.05) - punctuation_ratio
        if "spawn" in text.lower():
            score += 0.08
        boilerplate_tokens = (
            "digital edition",
            "published by",
            "registered trademark",
            "all rights reserved",
            "image comics",
            "productions, inc",
        )
        if any(token in lowered for token in boilerplate_tokens):
            score -= 0.45
        return round(score, 4)

    def _collection_excerpt_descriptor_score(self, excerpt: str, *, domain: str = "") -> float:
        lowered = str(excerpt or "").lower()
        score = 0.0
        if str(domain or "").lower() == "architecture":
            keyword_weights = {
                "official blackfong environment operating system": 1.4,
                "offline-first": 0.4,
                "governing spine": 0.7,
                "shared memory": 0.7,
                "operator surfaces": 0.45,
                "thoughtglass": 0.25,
                "memory chamber": 0.25,
                "d.a.i.s.e": 0.2,
                "daise": 0.2,
                "blackfong workspace": 0.2,
            }
            for token, weight in keyword_weights.items():
                if token in lowered:
                    score += weight
        if str(domain or "").lower() in {"comics", "fiction", "novels", "stories"}:
            keyword_weights = {
                "brought back to life": 0.9,
                "dead man": 0.7,
                "identity": 0.4,
                "agent of hell": 0.8,
                "hellspawn": 0.8,
                "wanda": 0.35,
                "al simmons": 0.5,
                "terry": 0.25,
                "power": 0.2,
                "consequences": 0.2,
                "return from the gra": 0.45,
            }
            for token, weight in keyword_weights.items():
                if token in lowered:
                    score += weight
        return round(score, 4)

    def _is_collection_excerpt_boilerplate(self, excerpt: str) -> bool:
        lowered = str(excerpt or "").lower()
        boilerplate_tokens = (
            "digital edition",
            "published by",
            "registered trademark",
            "all rights reserved",
            "image comics",
            "productions, inc",
        )
        return any(token in lowered for token in boilerplate_tokens)

    def _dedupe_semantic_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ordered: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for hit in hits:
            citation = hit.get("citation") or {}
            key = (
                str(hit.get("id") or citation.get("vector_id") or ""),
                str(citation.get("source_path") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            ordered.append(hit)
        return ordered

    def _limit_collection_focused_hits(
        self,
        hits: List[Dict[str, Any]],
        *,
        query: str,
        retrieval_profile: str,
        hinted_collections: List[str],
    ) -> List[Dict[str, Any]]:
        if not hits or not hinted_collections:
            return hits
        summary_hits = [hit for hit in hits if str(hit.get("kind") or "") == "collection_summary"]
        if not summary_hits:
            return hits
        focus_collection = str(summary_hits[0].get("collection") or hinted_collections[0] or "")
        if not focus_collection:
            return hits
        focus_label = self._collection_display_name(focus_collection)
        if retrieval_profile not in {"deep", "architecture"} and self._is_collection_overview_style_query(
            query,
            collection_label=focus_label,
        ):
            supporting_cap = 0
        else:
            supporting_cap = 2 if retrieval_profile in {"deep", "architecture"} else 1
        supporting_hits = [
            hit
            for hit in hits
            if hit is not summary_hits[0] and str(hit.get("collection") or "") == focus_collection
        ]
        fallback_hits = [
            hit
            for hit in hits
            if hit is not summary_hits[0] and str(hit.get("collection") or "") != focus_collection
        ]
        limited = [summary_hits[0], *supporting_hits[:supporting_cap]]
        if len(limited) < len(hits) and retrieval_profile in {"deep", "architecture"}:
            limited.extend(fallback_hits[:1])
        return limited

    def _collection_display_name(self, collection: str) -> str:
        normalized = str(collection or "").strip().lower()
        if normalized == "obeos":
            return "O.B.E.O.S."
        _source_registry, collection_registry = self._knowledge_registries()
        if collection_registry is not None:
            meta = collection_registry.get(collection)
            if meta is not None and meta.collection:
                return meta.collection.replace("_", " ").title()
        return str(collection or "collection").replace("_", " ").title()

    def _render_retrieval_context(self, hits: List[Dict[str, Any]], *, max_chars: int) -> str:
        if not hits or max_chars <= 0:
            return ""
        remaining = max_chars
        parts: List[str] = []
        for index, hit in enumerate(hits, start=1):
            citation = hit["citation"]
            header = (
                f"[Source {index}] title={citation['title']} | kind={citation['kind']} | "
                f"collection={citation['collection'] or 'local'}"
            )
            if citation.get("source_path"):
                header += f" | path={citation['source_path']}"
            if citation.get("section"):
                header += f" | section={citation['section']}"
            block = f"{header}\n{str(hit.get('text') or '').strip()}\n"
            if len(block) > remaining:
                if remaining < 220:
                    break
                block = block[:remaining].rsplit(" ", 1)[0] + "\n[truncated]\n"
            parts.append(block)
            remaining -= len(block)
            if remaining <= 0:
                break
        return "\n---\n".join(part.strip() for part in parts if part.strip())

    def _classify_semantic_hit(self, hit_id: str, metadata: Dict[str, Any]) -> str:
        if metadata.get("memory_type") in {"session_summary", "pinned_memory"}:
            return "conversation_memory"
        if hit_id.startswith("memory-chamber:") or metadata.get("doc_type") or metadata.get("path"):
            return "memory_chamber_document"
        if metadata.get("collection") or metadata.get("source_id") or metadata.get("section"):
            return "knowledge_collection"
        return "semantic_result"

    def _is_architecture_hit(
        self,
        *,
        title: str,
        text: str,
        metadata: Dict[str, Any],
        collection: str,
    ) -> bool:
        haystack = " ".join(
            [
                title,
                text[:1400],
                str(metadata.get("doc_type") or ""),
                str(metadata.get("path") or metadata.get("source_path") or ""),
                collection,
            ]
        ).lower()
        strong_tokens = {
            "architecture",
            "topology",
            "graph",
            "routing",
            "contract",
            "interface",
            "integration",
            "design",
        }
        supporting_tokens = {
            "cognition",
            "memory",
            "system",
            "workflow",
            "agent",
            "fault",
            "queue",
            "session",
            "trace",
            "obeos",
            "blackfong",
            "daise",
        }
        strong_hits = sum(1 for token in strong_tokens if token in haystack)
        if strong_hits >= 1:
            return True
        supporting_hits = sum(1 for token in supporting_tokens if token in haystack)
        return supporting_hits >= 3

    def _annotate_provenance(self, result: Dict[str, Any]) -> Dict[str, Any]:
        service = self._knowledge_provenance_service()
        if service is None:
            return {}
        try:
            annotated = service.annotate([result])
        except Exception:
            return {}
        if not annotated:
            return {}
        return dict(annotated[0].get("provenance") or {})

    def _knowledge_provenance_service(self) -> Any:
        if self._provenance_service is not None:
            return self._provenance_service
        try:
            from .ingestion.source_registry import SourceRegistry
            from .knowledge_intel.collection_registry import CollectionRegistry
            from .knowledge_intel.provenance import KnowledgeProvenanceService

            if self._source_registry is None:
                self._source_registry = SourceRegistry(paths.source_registry_path)
            if self._collection_registry is None:
                self._collection_registry = CollectionRegistry(paths.collection_registry_path)
            self._provenance_service = KnowledgeProvenanceService(
                registry=self._source_registry,
                chunks_dir=paths.knowledge_chunks_path,
                vector_store=self.vector_store,
                collection_registry=self._collection_registry,
            )
        except Exception:
            self._provenance_service = None
        return self._provenance_service

    def _knowledge_registries(self) -> tuple[Any, Any]:
        if self._source_registry is None or self._collection_registry is None:
            try:
                from .ingestion.source_registry import SourceRegistry
                from .knowledge_intel.collection_registry import CollectionRegistry

                if self._source_registry is None:
                    self._source_registry = SourceRegistry(paths.source_registry_path)
                if self._collection_registry is None:
                    self._collection_registry = CollectionRegistry(paths.collection_registry_path)
            except Exception:
                return self._source_registry, self._collection_registry
        return self._source_registry, self._collection_registry

    def _knowledge_bundle_status(self, bundle_root: Path) -> Dict[str, Any]:
        bundle = Path(bundle_root)
        chunks_root = bundle / "chunks"
        has_chunks = chunks_root.exists() and any(chunks_root.glob("*.jsonl"))
        bundle_available = bundle.exists() and bundle.is_dir() and (
            (bundle / "collections.json").exists()
            or (bundle / "source_registry.json").exists()
            or has_chunks
        )
        return {
            "bundle_root": str(bundle),
            "bundle_available": bool(bundle_available),
            "has_collection_registry": (bundle / "collections.json").exists(),
            "has_source_registry": (bundle / "source_registry.json").exists(),
            "has_chunks": has_chunks,
        }

    def _knowledge_bundle_available(self) -> bool:
        return bool(self._knowledge_bundle_status(paths.knowledge_bundle_root)["bundle_available"])

    def _read_chunk_records(self, chunk_file: Path) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for raw_line in chunk_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            chunk_id = str(payload.get("chunk_id") or "").strip()
            text = str(payload.get("text") or "").strip()
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            if not chunk_id or not text:
                continue
            records.append({"chunk_id": chunk_id, "text": text, "metadata": metadata})
        return records

    def _build_retrieval_contract(
        self,
        retrieval_profile: str,
        *,
        retrieval_settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        settings = retrieval_settings or retrieval_profile_settings(retrieval_profile)
        return {
            "profile": retrieval_profile,
            "offline_first": True,
            "one_vector_authority": str(paths.vector_db_path),
            "citation_required": bool(settings.get("include_semantic")),
            "graph_enrichment": bool(settings.get("include_graph")),
            "rule": "Inject only the bounded retrieval context and citations. Do not dump the full memory database.",
        }

    def _chunk_knowledge_text(self, text: str, *, chunk_chars: int = 1400, overlap_chars: int = 180) -> List[str]:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return []
        chunks: List[str] = []
        start = 0
        while start < len(normalized):
            end = min(len(normalized), start + chunk_chars)
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(normalized):
                break
            start = max(end - overlap_chars, start + 1)
        return chunks

    def _build_vector_store(self) -> VectorStore:
        try:
            return ChromaVectorStore(paths.vector_db_path)
        except Exception:  # pragma: no cover - defensive fallback
            return NullVectorStore()

    def _store_vector_memory(self, *, vector_id: str, text: str, metadata: Dict[str, Any]) -> None:
        if not text.strip() or isinstance(self.vector_store, NullVectorStore):
            return
        try:
            self.vector_store.upsert(ids=[vector_id], texts=[text], metadatas=[metadata])
        except Exception:  # pragma: no cover - duplicate IDs or backend failures
            log_action(
                LogLevel.WARNING,
                "conversation_memory",
                "vector_memory_store_failed",
                vector_id=vector_id,
            )

    def _store_lifecycle_item(self, event: Event, memory_type: str) -> None:
        try:
            memory_lifecycle_manager.store_memory_item(
                {
                    "event_id": event.id,
                    "type": event.type,
                    "source": event.source,
                    "payload": event.payload,
                    "metadata": event.metadata,
                },
                memory_type,
            )
        except Exception:
            log_action(
                LogLevel.WARNING,
                "conversation_memory",
                "lifecycle_store_failed",
                event_id=event.id,
            )

    def _get_session_summaries(self, session_id: str, limit: int = 3) -> List[Dict[str, Any]]:
        events = self.event_store.list_events_for_session(
            session_id,
            types=["session_summary"],
            ascending=False,
            limit=limit,
        )
        summaries = []
        for event in events:
            summaries.append(
                {
                    "event_id": event.id,
                    "summary": str(event.payload.get("summary", "")),
                    "message_count": int(event.payload.get("message_count", 0)),
                    "timestamp": event.timestamp / 1000.0,
                }
            )
        return list(reversed(summaries))

    def _summarize_messages(self, messages: List[Dict[str, Any]]) -> str:
        messages = [message for message in messages if message.get("prompt_eligible", True)]
        if not messages:
            return ""
        user_messages = [message["text"] for message in messages if message["role"] == "user"]
        assistant_messages = [message["text"] for message in messages if message["role"] == "assistant"]
        recent_user = "; ".join(user_messages[-3:])[:240]
        latest_assistant = assistant_messages[-1][:240] if assistant_messages else ""
        return (
            f"Session summary: user recently discussed {recent_user}. "
            f"Latest assistant response: {latest_assistant}"
        ).strip()

    def _format_prompt_context(
        self,
        *,
        session_id: str,
        agent_name: str,
        query: str,
        session_messages: List[Dict[str, Any]],
        session_summaries: List[Dict[str, Any]],
        global_memories: List[Dict[str, Any]],
        recent_events: List[Dict[str, Any]],
        knowledge_context: str,
        operator_profile: Optional[Dict[str, Any]] = None,
        grounded_conversation_answer: Optional[Dict[str, Any]] = None,
        transcript_window: int,
        prompt_budget_tokens: Optional[int] = None,
        prompt_mode: str = "standard",
        retrieval_profile: str = "fast",
    ) -> str:
        standard_like = prompt_mode == "standard" and retrieval_profile in {"standard", "architecture"}
        sections: List[str] = [f"Retrieval profile: {agent_name} ({prompt_mode}, {retrieval_profile})"]
        if knowledge_context:
            context_text = knowledge_context
            if standard_like and len(context_text) > 900:
                context_text = context_text[:900].rstrip() + "\n[truncated for standard chat latency]"
            sections.append("Bounded retrieval context:\n" + context_text)
        profile_lines: List[str] = []
        profile = dict(operator_profile or {})
        if profile.get("assistant_identity"):
            profile_lines.append(f"- Assistant identity: {profile['assistant_identity']}")
        if profile.get("preferred_name"):
            profile_lines.append(f"- Preferred operator name: {profile['preferred_name']}")
        if profile.get("operator_role"):
            profile_lines.append(f"- Operator role: {profile['operator_role']}")
        if profile.get("active_work"):
            profile_lines.append(
                "- Active work: " + "; ".join(str(item) for item in list(profile.get("active_work") or [])[:3])
            )
        if profile.get("fact_statements"):
            profile_lines.append(
                "- Grounded facts: " + "; ".join(str(item) for item in list(profile.get("fact_statements") or [])[:2])
            )
        if profile_lines:
            sections.append("Operator profile:\n" + "\n".join(profile_lines[: (3 if standard_like else len(profile_lines))]))
        stance_text = self._build_conversational_stance(
            query=query,
            operator_profile=profile,
            grounded_conversation_answer=grounded_conversation_answer,
        )
        if stance_text:
            sections.append("Conversational stance:\n" + stance_text)
        continuity_text = self._build_conversation_continuity(
            session_messages=session_messages,
            operator_profile=profile,
            query=query,
        )
        if continuity_text:
            sections.append("Conversation continuity:\n" + continuity_text)
        specialist_text = self._build_specialist_continuity(session_id=session_id)
        if specialist_text:
            sections.append("Specialist continuity:\n" + specialist_text)
        if session_summaries:
            sections.append(
                "Session summaries:\n" + "\n".join(
                    f"- {summary['summary']}"
                    for summary in session_summaries[: (1 if standard_like else len(session_summaries))]
                    if summary["summary"]
                )
            )
        if global_memories:
            sections.append(
                "Relevant global memory:\n" + "\n".join(
                    f"- [{memory['memory_type']}] {memory['text'][:(140 if standard_like else 220)]}"
                    for memory in global_memories[: (2 if standard_like else len(global_memories))]
                    if memory["text"]
                )
            )
        if recent_events and not standard_like:
            sections.append(
                "Recent ecosystem events:\n" + "\n".join(
                    f"- {event['source']}/{event['event_type']}: {event['message']}"
                    for event in recent_events[:6]
                )
            )
        # Standard chat already carries transcript turns as actual chat messages.
        if session_messages and prompt_mode.startswith("quick"):
            sections.append(
                "Recent transcript:\n" + "\n".join(
                    f"- {message['role']}: {message['text'][:220]}"
                    for message in session_messages[-transcript_window:]
                )
            )
        return self._apply_prompt_budget(sections, prompt_budget_tokens).strip()

    def _build_specialist_continuity(self, *, session_id: str) -> str:
        specialist = self._specialist_continuity_snapshot(session_id=session_id)
        if specialist:
            lines = [f"- Last selected specialist: {specialist['selected_agent']}."]
            if specialist.get("task_type"):
                lines.append(f"- Last task focus: {specialist['task_type']}.")
            if specialist.get("synthesized") is not None:
                lines.append(f"- Synthesized response: {'yes' if bool(specialist.get('synthesized')) else 'no'}.")
            return "\n".join(lines)
        return ""

    def _specialist_continuity_snapshot(self, *, session_id: str) -> Dict[str, Any]:
        if not session_id:
            return {}
        events = self.event_store.list_events_for_session(
            session_id,
            types=["agent_selection"],
            ascending=False,
            limit=3,
        )
        for event in events:
            payload = event.payload or {}
            selected_agent = str(payload.get("selected_agent") or "").strip()
            if not selected_agent:
                continue
            return {
                "event_id": event.id,
                "selected_agent": selected_agent,
                "task_type": str(payload.get("task_type") or "").strip(),
                "synthesized": payload.get("synthesized"),
            }
        return {}

    def _build_conversational_stance(
        self,
        *,
        query: str,
        operator_profile: Dict[str, Any],
        grounded_conversation_answer: Optional[Dict[str, Any]] = None,
    ) -> str:
        profile = dict(operator_profile or {})
        preferred_name = str(profile.get("preferred_name") or "").strip()
        operator_role = str(profile.get("operator_role") or "").strip()
        active_work = [
            str(item).strip()
            for item in list(profile.get("active_work") or [])
            if str(item).strip()
        ]
        active_work_relevant = self._query_needs_active_work_context(query, active_work)
        if not active_work_relevant:
            active_work = []
        if not preferred_name and not operator_role and not active_work and not grounded_conversation_answer:
            return ""
        lines: List[str] = []
        if preferred_name:
            lines.append(f"- Address the operator naturally as {preferred_name} when it fits the moment.")
        if operator_role:
            lines.append(f"- Keep in mind the operator is the {operator_role}.")
        if active_work:
            lines.append(f"- Current shared focus: {self._join_grounded_memory_items(active_work[:3])}.")
        if isinstance(grounded_conversation_answer, dict) and str(grounded_conversation_answer.get("answer") or "").strip():
            frame = str(grounded_conversation_answer.get("kind") or "").strip() or "broad"
            lines.append(f"- Broad conversation frame: {frame}.")
            lines.append("- For open-ended project/system questions, stay anchored in the current shared focus and speak like D.A.I.S.E., not like a trace or retrieval dump.")
        else:
            compact_query = " ".join(str(query or "").strip().lower().split())
            if any(token in compact_query for token in ("project", "system", "obeos", "work", "us", "direction", "risk", "progress", "think", "feel")):
                lines.append("- For broad project or system questions, ground the reply in current shared focus before expanding outward.")
        return "\n".join(lines)

    @staticmethod
    def _query_needs_active_work_context(query: str, active_work: List[str]) -> bool:
        compact = " ".join(str(query or "").lower().split())
        if not compact or not active_work:
            return False
        if any(
            marker in compact
            for marker in (
                "what are we working on",
                "what have we been working on",
                "where did we leave off",
                "what changed",
                "recent update",
                "current task",
                "project",
                "obeos",
                "system",
                "latency",
                "hud",
                "telegram",
                "memory",
                "backend",
            )
        ):
            return True
        query_tokens = set(re.findall(r"[a-z0-9]{3,}", compact))
        work_tokens = set(
            token
            for item in active_work
            for token in re.findall(r"[a-z0-9]{3,}", str(item).lower())
        )
        return bool(query_tokens & work_tokens)

    def _build_conversation_continuity(
        self,
        *,
        session_messages: List[Dict[str, Any]],
        operator_profile: Dict[str, Any],
        query: str,
    ) -> str:
        profile = dict(operator_profile or {})
        followup_flags = self._conversational_followup_flags(query)
        recent_topics = self._recent_user_topics(session_messages, current_query=query, limit=3)
        active_work = [
            str(item).strip()
            for item in list(profile.get("active_work") or [])
            if str(item).strip()
        ]
        active_work_relevant = self._query_needs_active_work_context(query, active_work)
        if recent_topics:
            if any(
                (
                    followup_flags.get("ultra_short_followup"),
                    followup_flags.get("short_scope_followup"),
                    followup_flags.get("environment_followup"),
                    followup_flags.get("referential_followup"),
                )
            ):
                active_work = recent_topics
            else:
                active_work = self._unique_grounded_memory_items(recent_topics + active_work, limit=4)
        if not active_work_relevant and not any(
            (
                followup_flags.get("ultra_short_followup"),
                followup_flags.get("short_scope_followup"),
                followup_flags.get("environment_followup"),
                followup_flags.get("referential_followup"),
            )
        ):
            active_work = []
        assistant_snapshot = self._recent_assistant_snapshot(session_messages, current_query=query)
        lines: List[str] = []
        if active_work:
            lines.append(f"- Ongoing thread: {self._join_grounded_memory_items(active_work[:3])}.")
        if assistant_snapshot:
            lines.append(f"- Last stable assistant context: {assistant_snapshot.rstrip('.')}.")
        return "\n".join(lines)

    def _resolve_prompt_mode(self, agent_name: str, analysis_mode: str) -> str:
        normalized_agent = self._normalize_agent_name(agent_name)
        normalized_analysis = (analysis_mode or "fast").strip().lower()
        if normalized_analysis in {"quick", "fast"}:
            normalized_analysis = "fast"
        if normalized_agent in {"jarvis", "daise"} and normalized_analysis == "fast":
            return "quick_compact"
        return "deep_full" if normalized_analysis == "deep" else "standard"

    def _apply_prompt_budget(self, sections: List[str], prompt_budget_tokens: Optional[int]) -> str:
        if not prompt_budget_tokens or prompt_budget_tokens <= 0:
            return "\n\n".join(section for section in sections if section)
        budget_chars = int(prompt_budget_tokens) * 4
        kept: List[str] = []
        used = 0
        for section in sections:
            if not section:
                continue
            separator_chars = 2 if kept else 0
            section_budget = budget_chars - used - separator_chars
            if section_budget <= 0:
                break
            if len(section) > section_budget:
                suffix = "\n[truncated for quick HUD latency]"
                if section_budget > len(suffix) + 80:
                    kept.append(section[: section_budget - len(suffix)].rstrip() + suffix)
                break
            kept.append(section)
            used += len(section) + separator_chars
        return "\n\n".join(kept)

    def _normalize_agent_name(self, agent_name: Optional[str]) -> str:
        normalized = (agent_name or "").strip().lower()
        alias_map = {
            "daise": "daise",
            "jarvis_agent": "jarvis",
            "jarvis": "jarvis",
            "coding_agent": "coding",
            "voice_agent": "voice",
        }
        return alias_map.get(normalized, normalized) or "default"

    def _resolve_memory_profile(
        self,
        *,
        agent_name: Optional[str],
        transcript_window: Optional[int],
        global_memory_limit: Optional[int],
    ) -> Dict[str, Any]:
        normalized_agent = self._normalize_agent_name(agent_name)
        profile = dict(MEMORY_PROFILE_DEFAULTS)
        profile.update(AGENT_MEMORY_PROFILES.get(normalized_agent, {}))
        profile["agent"] = normalized_agent
        if transcript_window is not None:
            profile["transcript_window"] = transcript_window
        if global_memory_limit is not None:
            profile["global_memory_limit"] = global_memory_limit
        return profile

    def _get_recent_events_for_agent(
        self,
        *,
        query: str,
        agent_name: Optional[str],
        session_id: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        profile = self._resolve_memory_profile(
            agent_name=agent_name,
            transcript_window=None,
            global_memory_limit=None,
        )
        scored_events = []
        for event in self.list_recent_events(limit=max(limit * 6, 24)):
            if event["event_type"] in CHAT_EVENT_TYPES:
                continue
            event_score = self._score_recent_event(
                event,
                query=query,
                session_id=session_id,
                profile=profile,
            )
            enriched_event = dict(event)
            enriched_event["relevance"] = round(event_score, 3)
            scored_events.append(enriched_event)
        scored_events.sort(key=lambda item: (item["relevance"], item["timestamp"]), reverse=True)
        return scored_events[:limit]

    def _rank_memory_candidates(
        self,
        candidates: List[Dict[str, Any]],
        *,
        query: str,
        agent_name: Optional[str],
        session_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        profile = self._resolve_memory_profile(
            agent_name=agent_name,
            transcript_window=None,
            global_memory_limit=None,
        )
        ranked: List[Dict[str, Any]] = []
        seen_ids = set()
        for candidate in candidates:
            metadata = candidate.get("metadata", {})
            dedupe_key = metadata.get("event_id") or candidate.get("id")
            if dedupe_key in seen_ids:
                continue
            seen_ids.add(dedupe_key)
            enriched_candidate = dict(candidate)
            enriched_candidate["relevance"] = round(
                self._score_memory_candidate(
                    candidate,
                    query=query,
                    session_id=session_id,
                    profile=profile,
                ),
                3,
            )
            ranked.append(enriched_candidate)
        ranked.sort(key=lambda item: (item["relevance"], item.get("id", "")), reverse=True)
        return ranked

    def _score_memory_candidate(
        self,
        candidate: Dict[str, Any],
        *,
        query: str,
        session_id: Optional[str],
        profile: Dict[str, Any],
    ) -> float:
        metadata = candidate.get("metadata", {}) or {}
        text = str(candidate.get("text", ""))
        title = str(metadata.get("title", ""))
        haystack = f"{text}\n{title}\n{metadata}".lower()
        query_tokens = self._tokenize(query)
        keyword_tokens = [str(token).lower() for token in profile.get("memory_keywords", ())]
        recall_kind = self._detect_grounded_memory_query(query)
        memory_type = str(candidate.get("memory_type") or "")
        candidate_session_id = str(metadata.get("session_id", ""))

        score = float(candidate.get("relevance", 0.0))
        if candidate.get("memory_type") == "pinned_memory":
            score += 0.18
        if memory_type == "operator_fact":
            score += 0.26
        if memory_type == "active_work_item":
            score += 0.22
            match = re.search(r"working on (.+?)(?:[.?!]|$)", text, re.IGNORECASE)
            if match and not self._canonical_work_topics(match.group(1)):
                score -= 0.42
        if session_id and candidate_session_id == session_id:
            score += 0.22
        elif memory_type == "active_work_item" and session_id and not query_tokens:
            score -= 0.26
        if recall_kind == "operator" and memory_type == "operator_fact":
            score += 0.32
        if recall_kind == "session" and memory_type == "active_work_item":
            score += 0.28
        if recall_kind == "operator" and memory_type == "active_work_item":
            score += 0.16
        if "name" in query.lower() and any(token in haystack for token in ("d.a.i.s.e", "preferred name", "call you")):
            score += 0.18
        score += self._token_overlap_score(query_tokens, haystack, weight=0.08)
        score += self._token_overlap_score(keyword_tokens, haystack, weight=0.05)
        return score

    def _score_recent_event(
        self,
        event: Dict[str, Any],
        *,
        query: str,
        session_id: Optional[str],
        profile: Dict[str, Any],
    ) -> float:
        haystack = f"{event.get('source', '')} {event.get('event_type', '')} {event.get('message', '')} {event.get('payload', {})}".lower()
        query_tokens = self._tokenize(query)
        keyword_tokens = [str(token).lower() for token in profile.get("memory_keywords", ())]
        preferred_sources = {str(token).lower() for token in profile.get("preferred_event_sources", ())}
        preferred_types = {str(token).lower() for token in profile.get("preferred_event_types", ())}

        score = 0.1
        if str(event.get("source", "")).lower() in preferred_sources:
            score += 0.14
        if str(event.get("event_type", "")).lower() in preferred_types:
            score += 0.14
        if session_id and str(event.get("session_id") or "") == session_id:
            score += 0.12
        score += self._token_overlap_score(query_tokens, haystack, weight=0.08)
        score += self._token_overlap_score(keyword_tokens, haystack, weight=0.04)
        return score

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[a-z0-9_]+", text.lower())

    def _salient_query_tokens(self, text: str) -> List[str]:
        stopwords = {
            "tell",
            "about",
            "what",
            "when",
            "where",
            "which",
            "who",
            "does",
            "work",
            "works",
            "explain",
            "show",
            "give",
            "please",
            "would",
            "could",
            "should",
            "into",
            "from",
            "have",
            "with",
        }
        salient: List[str] = []
        for token in self._tokenize(text):
            if len(token) <= 3 or token in stopwords:
                continue
            if token not in salient:
                salient.append(token)
        return salient[:8]

    def _token_overlap_score(self, tokens: List[str], haystack: str, *, weight: float) -> float:
        if not tokens or not haystack:
            return 0.0
        unique_tokens = {
            token
            for token in tokens
            if len(token) > 2 and token not in {"the", "and", "with", "that", "this"}
        }
        overlap = sum(1 for token in unique_tokens if token in haystack)
        return overlap * weight

    def _filter_session_summaries_for_query(
        self,
        summaries: List[Dict[str, Any]],
        *,
        focus_tokens: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        return self._filter_context_records(
            summaries,
            focus_tokens=focus_tokens,
            limit=limit,
            text_getter=lambda item: str(item.get("summary") or ""),
            tie_breaker=lambda item: float(item.get("timestamp") or 0.0),
        )

    def _filter_global_memories_for_query(
        self,
        memories: List[Dict[str, Any]],
        *,
        focus_tokens: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        return self._filter_context_records(
            memories,
            focus_tokens=focus_tokens,
            limit=limit,
            text_getter=lambda item: " ".join(
                [
                    str(item.get("text") or ""),
                    str((item.get("metadata") or {}).get("title") or ""),
                ]
            ),
            tie_breaker=lambda item: float(item.get("relevance") or 0.0),
        )

    def _filter_recent_events_for_query(
        self,
        events: List[Dict[str, Any]],
        *,
        focus_tokens: List[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        return self._filter_context_records(
            events,
            focus_tokens=focus_tokens,
            limit=limit,
            text_getter=lambda item: " ".join(
                [
                    str(item.get("source") or ""),
                    str(item.get("event_type") or ""),
                    str(item.get("message") or ""),
                    str(item.get("payload") or ""),
                ]
            ),
            tie_breaker=lambda item: float(item.get("relevance") or item.get("timestamp") or 0.0),
        )

    def _filter_context_records(
        self,
        records: List[Dict[str, Any]],
        *,
        focus_tokens: List[str],
        limit: int,
        text_getter: Any,
        tie_breaker: Any,
    ) -> List[Dict[str, Any]]:
        if limit <= 0 or not records:
            return []
        if not focus_tokens:
            return records[:limit]
        ranked: List[tuple[float, float, Dict[str, Any]]] = []
        for record in records:
            haystack = str(text_getter(record) or "").lower()
            overlap = self._token_overlap_score(focus_tokens, haystack, weight=1.0)
            if overlap <= 0:
                continue
            ranked.append((overlap, float(tie_breaker(record) or 0.0), record))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in ranked[:limit]]

    def _normalize_request_phases(self, events: List[Event]) -> List[Dict[str, Any]]:
        phase_for_type = {
            "agent_query": "accepted",
            "policy_decision": "policy",
            "routing_decision": "routing",
            "memory_retrieval": "memory",
            "agent_report": "agent",
            "agent_evaluation": "evaluation",
            "agent_selection": "evaluation",
            "agent_response": "complete",
            "request_outcome": "complete",
        }
        ordered_phases = ["accepted", "policy", "routing", "memory", "agent", "evaluation", "persisting", "complete"]
        grouped: Dict[str, List[Event]] = {phase: [] for phase in ordered_phases}
        for event in events:
            phase = phase_for_type.get(event.type)
            if phase:
                grouped.setdefault(phase, []).append(event)

        normalized = []
        for phase in ordered_phases:
            phase_events = grouped.get(phase, [])
            if not phase_events:
                normalized.append({"name": phase, "ms": None, "status": "skipped", "count": 0})
                continue
            first = phase_events[0].timestamp
            last = phase_events[-1].timestamp
            normalized.append(
                {
                    "name": phase,
                    "ms": max(0, last - first),
                    "status": "complete",
                    "count": len(phase_events),
                }
            )
        return normalized

    def _summarize_recent_event(self, event: Event) -> str:
        payload = event.payload or {}
        if event.type == "reasoning_trace":
            agent_name = payload.get("agent_name", event.source)
            final_decision = str(payload.get("final_decision", "")).strip()
            if final_decision:
                return f"{agent_name} reasoning concluded: {final_decision[:140]}"
            return f"{agent_name} reasoning trace captured."
        if event.type == "routing_decision":
            candidates = payload.get("candidates") or []
            return f"Routing chose {payload.get('suggested_agent', payload.get('selected_agent', 'daise'))} from {len(candidates)} candidates."
        if event.type == "memory_retrieval":
            prompt_metrics = payload.get("prompt_metrics") or {}
            prompt_bits = []
            if isinstance(prompt_metrics, dict):
                for agent_name, metrics in list(prompt_metrics.items())[:3]:
                    if isinstance(metrics, dict):
                        prompt_bits.append(
                            f"{agent_name}~{metrics.get('prompt_context_tokens_estimate', metrics.get('approx_prompt_tokens', '?'))} tokens"
                        )
            retrieval_profile = payload.get("retrieval_profile") or "unknown"
            transport = payload.get("transport") or "local"
            return (
                f"Memory retrieval ({retrieval_profile}, {transport}) used {payload.get('session_messages', 0)} transcript items, "
                f"{payload.get('session_summaries', 0)} summaries, {payload.get('global_memories', 0)} memories, "
                f"and {payload.get('recent_events', 0)} recent events"
                f"{' (' + ', '.join(prompt_bits) + ')' if prompt_bits else ''}."
            )
        if event.type == "memory_retrieval_observation":
            return (
                f"Retrieval observation profile={payload.get('retrieval_profile', 'unknown')} "
                f"transport={payload.get('transport', 'local')} "
                f"knowledge_hits={payload.get('knowledge_hits', 0)} "
                f"graph_matches={payload.get('graph_matches', 0)}."
            )
        if event.type == "latency_observation":
            prompt_metrics = payload.get("prompt_metrics") or {}
            prompt_tokens = (
                payload.get("prompt_eval_count")
                or prompt_metrics.get("approx_prompt_tokens")
                or prompt_metrics.get("prompt_context_tokens_estimate")
                or "unknown"
            )
            fallback = payload.get("fallback_reason") or payload.get("timeout_reason")
            response_path = payload.get("response_path") or "unknown"
            selected_model = payload.get("selected_model") or payload.get("model") or "unknown"
            model_chain = payload.get("model_chain") or []
            summary = (
                f"Latency {float(payload.get('execution_time_ms', 0.0) or 0.0) / 1000:.1f}s "
                f"via {payload.get('provider', 'local')}/{payload.get('model', 'unknown')} "
                f"(path={response_path}, selected={selected_model}); "
                f"prompt={prompt_tokens}, attempts={payload.get('ollama_attempts', 'unknown')}."
            )
            if model_chain:
                summary += f" Chain={', '.join(str(item) for item in model_chain[:4])}."
            if fallback:
                summary += f" Fallback: {str(fallback)[:120]}."
            return summary
        if event.type == "agent_report":
            return (
                f"{payload.get('agent', event.source)} reported confidence "
                f"{float(payload.get('confidence', 0.0)):.2f}."
            )
        if event.type == "agent_evaluation":
            return (
                f"D.A.I.S.E evaluated {len(payload.get('candidate_scores', []))} candidates "
                f"and selected {payload.get('selected_agent', 'none')}."
            )
        if event.type == "agent_selection":
            return (
                f"Final selection: {payload.get('selected_agent', 'unknown')} "
                f"(synthesized={bool(payload.get('synthesized'))})."
            )
        if event.type == "policy_decision":
            return (
                f"Policy {payload.get('decision_state', 'unknown')}: "
                f"{payload.get('winning_policy', {}).get('rule', 'policy rule')}."
            )
        if event.type == "awareness_snapshot":
            return (
                f"Awareness snapshot health={payload.get('health', {}).get('overall', 'unknown')} "
                f"bottleneck={payload.get('diagnostic_summary', {}).get('top_bottleneck', 'unknown')}."
            )
        if event.type == "performance_snapshot":
            latency = payload.get("latency", {})
            queue = payload.get("queue", {})
            return (
                f"Performance avg={latency.get('average_response_time_s', 0)}s "
                f"pending={queue.get('pending_requests', 0)}."
            )
        if event.type == "backend_health_observation":
            return (
                f"Backend {payload.get('provider', 'local')} is "
                f"{payload.get('status', 'unknown')} with model {payload.get('configured_model', 'unknown')}"
                f" and {payload.get('installed_model_count', 'unknown')} installed models."
            )
        if event.type == "environment_snapshot":
            environment = payload.get("environment", payload)
            return f"Environment snapshot on {environment.get('system', 'unknown')} with Python {environment.get('python', 'unknown')}."
        if event.type == "sensor_capability_snapshot":
            return (
                f"Sensors speak={payload.get('speaker', {}).get('can_speak_state', 'unknown')}, "
                f"hear={payload.get('microphone', {}).get('can_hear_state', 'unknown')}, "
                f"see={payload.get('camera', {}).get('can_see_state', 'unknown')}."
            )
        if event.type == "queue_pressure_observation":
            return (
                f"Queue pending={payload.get('pending_requests', 0)}, "
                f"workers={payload.get('active_workers', 0)}, health={payload.get('backlog_health', 'unknown')}."
            )
        if event.type == "consistency_conflict":
            return f"Consistency conflict: {payload.get('summary', payload.get('risk_flag', 'unknown conflict'))}."
        if event.type == "request_outcome":
            provider = payload.get("provider") or "unknown"
            model = payload.get("model") or "unknown"
            retrieval_profile = payload.get("retrieval_profile") or "unknown"
            transport = payload.get("transport") or "local"
            fallback = payload.get("fallback_used")
            response_path = payload.get("response_path") or "unknown"
            fallback_text = ""
            if fallback is not None:
                fallback_text = f" fallback={bool(fallback)}"
            return (
                f"Request outcome {payload.get('decision_state', 'unknown')} "
                f"success={payload.get('success', 'unknown')} "
                f"via {provider}/{model} "
                f"profile={retrieval_profile} transport={transport} path={response_path}{fallback_text}."
            )
        if event.type == "agent_trust_update":
            return (
                f"Trust update for {payload.get('agent', 'unknown')} "
                f"{payload.get('task_type', 'task')}: {payload.get('effective_trust', 'unknown')}."
            )
        if event.type == "identity_resolution_event":
            return (
                f"Identity resolved {payload.get('input_name', 'unknown')} -> "
                f"{payload.get('canonical_identity', 'unknown')} via {payload.get('resolution_method', 'unknown')}."
            )
        if event.type == "operator_consent":
            return (
                f"Operator consent level {payload.get('level', 'unknown')} for "
                f"{payload.get('action', 'unknown')} on {payload.get('target', 'unknown')}."
            )
        if event.type == "file_workflow_state":
            return (
                f"File workflow {payload.get('proposal_id', payload.get('path', 'unknown'))} "
                f"state={payload.get('state', 'unknown')}."
            )
        if event.type == "file_proposal_created":
            return f"File proposal {payload.get('proposal_id', 'unknown')} created for {payload.get('path', 'unknown')}."
        if event.type == "file_proposal_expired":
            return f"File proposal {payload.get('proposal_id', 'unknown')} expired."
        if event.type == "file_pre_apply_validation":
            return f"Pre-apply validation {payload.get('status', 'unknown')} for {payload.get('proposal_id', 'unknown')}."
        if event.type == "file_change_applied":
            return f"File change applied for {payload.get('proposal_id', 'unknown')}."
        if event.type == "file_post_apply_validation":
            return f"Post-apply validation {payload.get('status', 'unknown')} for {payload.get('proposal_id', 'unknown')}."
        if event.type == "perception_gate_decision":
            return (
                f"Perception gate {payload.get('decision_state', 'unknown')} for "
                f"{payload.get('modality', 'unknown')} ({payload.get('reason', 'no reason')})."
            )
        if event.type in {"vision_sensor_observation", "screen_snapshot_observation", "audio_sensor_observation"}:
            return (
                f"{event.type.replace('_', ' ')} confidence={payload.get('confidence', payload.get('vision_confidence', payload.get('audio_confidence', 'unknown')))} "
                f"freshness={payload.get('freshness', {}).get('age_seconds', 'unknown')}s."
            )
        if event.type == "companion_brief_scope_decision":
            return (
                f"TinyProgrammer brief scope {payload.get('decision_state', 'unknown')}: "
                f"{payload.get('reason', 'scope checked')}."
            )
        if event.type == "tinyprogrammer_process_state":
            return f"TinyProgrammer process state={payload.get('state', 'unknown')}."
        if event.type == "notification_created":
            return f"Notification: {payload.get('title', 'Untitled')} - {payload.get('message', '')}"
        if event.type == "notification_read":
            return f"Notification read: {payload.get('notification_id', 'unknown')}"
        if event.type == "notification_action_taken":
            return (
                f"Notification action {payload.get('action', 'unknown')} "
                f"on {payload.get('notification_id', 'unknown')}."
            )
        if event.type == "notification_delivery_result":
            return f"Notification delivery {payload.get('channel', 'unknown')}: {payload.get('status', 'unknown')}."
        if event.type == "operator_intent_created":
            return (
                f"Operator intent {payload.get('intent_id', 'unknown')} queued for "
                f"{payload.get('action', 'action')} ({payload.get('status', 'pending')})."
            )
        if event.type == "operator_intent_state":
            return (
                f"Operator intent {payload.get('intent_id', 'unknown')} "
                f"is now {payload.get('status', 'unknown')}."
            )
        if event.type == "operator_intent_expired":
            return f"Operator intent {payload.get('intent_id', 'unknown')} expired."
        if event.type == "operator_action_result":
            return str(payload.get("text") or payload.get("human_reason") or "Operator action updated.")[:180]
        if event.type == "state_change_summary":
            return str(payload.get("summary_text") or "State changes summarized.")[:180]
        if event.type == "frame_tap_event":
            return (
                f"Frame {payload.get('tap_count', payload.get('count', 'unknown'))}-tap "
                f"mapped to {payload.get('intent', 'status')}."
            )
        if event.type == "frame_imu_observation":
            return (
                f"Frame IMU pitch={payload.get('pitch', 'unknown')} "
                f"roll={payload.get('roll', 'unknown')} heading={payload.get('heading', payload.get('compass', 'unknown'))}."
            )
        if event.type == "frame_display_update":
            return f"Frame display {payload.get('status', 'sent')}: {payload.get('title', payload.get('text', ''))}"
        if event.type == "capability_snapshot":
            summary = payload.get("summary", {})
            return f"Capability snapshot: {summary.get('total', 0)} declared capabilities."
        if event.type == "agent_response":
            return str(payload.get("response") or payload.get("text") or "")[:180]
        if event.type == "agent_query":
            return str(payload.get("text") or "")[:180]
        return str(
            payload.get("summary")
            or payload.get("message")
            or payload.get("title")
            or payload.get("text")
            or payload.get("response")
            or payload
        )[:180]

    def _format_trace_event(self, event: Event) -> Dict[str, Any]:
        payload = event.payload or {}
        title = event.type.replace("_", " ").title()
        summary = self._summarize_recent_event(event)

        return {
            "event_id": event.id,
            "event_type": event.type,
            "source": event.source,
            "timestamp": event.timestamp / 1000.0,
            "session_id": event.metadata.get("session_id"),
            "correlation_id": event.metadata.get("correlation_id"),
            "request_id": event.metadata.get("request_id"),
            "summary": summary,
            "message": summary,
            "payload": payload,
            "metadata": event.metadata,
        }


conversation_memory_service = ConversationMemoryService()
