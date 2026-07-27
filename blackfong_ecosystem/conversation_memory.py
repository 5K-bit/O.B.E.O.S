from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import chat_settings, paths
from .events import Event
from .memory_lifecycle_management import memory_lifecycle_manager
from .observability import LogLevel, log_action
from .retrieval_profiles import normalize_retrieval_profile, retrieval_profile_settings
from .storage import ChromaVectorStore, EventStore, NullVectorStore, VectorStore


CHAT_EVENT_TYPES = {"agent_query", "agent_response"}
MEMORY_EVENT_TYPES = {"session_summary", "knowledge_ingested"}
WORKFLOW_EVENT_TYPES = {
    "routing_decision",
    "memory_retrieval",
    "agent_report",
    "agent_evaluation",
    "agent_selection",
    "tinyprogrammer_status",
    "tinyprogrammer_state_transition",
    "tinyprogrammer_program_created",
    "tinyprogrammer_program_archived",
    "tinyprogrammer_reflection",
    "tinyprogrammer_brief_applied",
    "tinyprogrammer_feedback",
    "awareness_snapshot",
    "performance_snapshot",
    "latency_observation",
    "routing_quality_observation",
    "memory_retrieval_observation",
    "backend_health_observation",
    "environment_snapshot",
    "sensor_capability_snapshot",
    "audio_device_observation",
    "camera_device_observation",
    "queue_pressure_observation",
    "consistency_conflict",
    "request_outcome",
    "agent_trust_update",
    "policy_decision",
    "capability_snapshot",
    "adapter_health_observation",
    "agent_admission_decision",
    "learner_profile_update",
    "learning_attempt",
    "resource_budget_observation",
    "spine_conflict_detected",
    "spine_mode_changed",
    "vision_tool_result",
    "installer_plan_generated",
    "identity_resolution_event",
    "operator_consent",
    "file_workflow_state",
    "file_proposal_created",
    "file_proposal_expired",
    "file_pre_apply_validation",
    "file_change_applied",
    "file_post_apply_validation",
    "file_scope_discovery",
    "file_scope_root_approved",
    "file_scope_map",
    "file_scope_read",
    "knowledge_source_added",
    "knowledge_cleaned",
    "knowledge_chunked",
    "knowledge_embedded",
    "knowledge_ingestion_failed",
    "knowledge_phase1_started",
    "knowledge_inventory_created",
    "knowledge_coverage_created",
    "knowledge_timeline_created",
    "knowledge_memory_chamber_graph_updated",
    "knowledge_phase1_completed",
    "knowledge_phase1_failed",
    "knowledge_gaps_detected",
    "knowledge_summary_created",
    "knowledge_librarian_answered",
    "perception_gate_decision",
    "vision_sensor_observation",
    "screen_snapshot_observation",
    "audio_sensor_observation",
    "companion_brief_scope_decision",
    "tinyprogrammer_process_state",
    "notification_created",
    "notification_read",
    "notification_action_taken",
    "notification_delivery_result",
    "operator_intent_created",
    "operator_intent_state",
    "operator_intent_expired",
    "operator_action_result",
    "state_change_summary",
    "frame_tap_event",
    "frame_imu_observation",
    "frame_display_update",
}

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


class ConversationMemoryService:
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

    @staticmethod
    def create_session_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def create_correlation_id() -> str:
        return str(uuid.uuid4())

    def persist_user_turn(
        self,
        *,
        session_id: str,
        correlation_id: str,
        text: str,
        modality: str = "text",
        routed_agent: str = "daise",
        analysis_mode: str = "quick",
        agent_override: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Event:
        event = Event.new(
            source="user",
            type="agent_query",
            payload={
                "text": text,
                "modality": modality,
                "agent": routed_agent,
                "analysis_mode": analysis_mode,
                "agent_override": agent_override,
            },
            confidence=1.0,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["conversation", "user_turn", modality],
        )
        self.event_store.add_event(event)
        self._store_lifecycle_item(event, "event")
        return event

    def persist_agent_response(
        self,
        *,
        session_id: str,
        correlation_id: str,
        agent_name: str,
        response_text: str,
        confidence: float,
        modality: str = "text",
        parent_event_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        execution_time_ms: Optional[float] = None,
        success: bool = True,
        extras: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> Event:
        payload = {
            "response": response_text,
            "text": response_text,
            "agent": agent_name,
            "modality": modality,
            "success": success,
            "provider": provider or "",
            "model": model or "",
            "execution_time_ms": execution_time_ms or 0.0,
        }
        if extras:
            payload.update(extras)

        event = Event.new(
            source=agent_name,  # type: ignore[arg-type]
            type="agent_response",
            payload=payload,
            confidence=confidence,
            parent_event=parent_event_id,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["conversation", "agent_response", agent_name],
        )
        self.event_store.add_event(event)
        self._store_lifecycle_item(event, "event")
        return event

    def persist_workflow_event(
        self,
        *,
        source: str,
        event_type: str,
        payload: Dict[str, Any],
        confidence: float = 1.0,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Event:
        event = Event.new(
            source=source,  # type: ignore[arg-type]
            type=event_type,
            payload=payload,
            confidence=confidence,
            parent_event=parent_event_id,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=tags or ["workflow", event_type],
        )
        self.event_store.add_event(event)
        lifecycle_type = "trace" if event_type in WORKFLOW_EVENT_TYPES else "event"
        self._store_lifecycle_item(event, lifecycle_type)
        return event

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
    ) -> Dict[str, Any]:
        resolved_retrieval_profile = normalize_retrieval_profile(retrieval_profile, analysis_mode)
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
        session_summaries = self._get_session_summaries(session_id, limit=summary_limit)
        memory_query = "" if skip_vector_search else query
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
            skip_vector_search=skip_vector_search,
        )

        prompt_context = self._format_prompt_context(
            agent_name=profile["agent"],
            session_messages=session_messages[-transcript_limit:],
            session_summaries=session_summaries,
            global_memories=global_memories,
            recent_events=recent_events,
            knowledge_context=semantic_bundle["context"],
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
            "truncated": (max(1, len(prompt_context) // 4) if prompt_context else 0) >= int(prompt_budget_tokens or 0),
        }
        retrieval_contract = {
            **self._build_retrieval_contract(
                resolved_retrieval_profile,
                retrieval_settings=retrieval_settings,
            ),
            "usage": "prompt_context",
        }

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
            "retrieval_profile": resolved_retrieval_profile,
            "retrieval_contract": retrieval_contract,
            "timing_ms": semantic_bundle["timing_ms"],
            "prompt_context": prompt_context,
            "prompt_mode": prompt_mode,
            "prompt_metrics": prompt_metrics,
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
            ascending=True,
            limit=limit,
        )
        messages = []
        for event in events:
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
                }
            )
        return messages[-limit:]

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
            "queue_pressure": queue_events[-1] if queue_events else None,
            "latency_observation": latency_events[-1] if latency_events else None,
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
        candidates: List[Dict[str, Any]] = []
        if query and not isinstance(self.vector_store, NullVectorStore):
            try:
                results = self.vector_store.search(query, k=max(limit * 4, 8))
            except Exception:  # pragma: no cover - vector backend failure
                results = []
            for result in results:
                metadata = result.get("metadata", {})
                if metadata.get("memory_type") not in {"session_summary", "pinned_memory"}:
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
            if event.payload.get("memory_kind") != "pinned_memory":
                continue
            candidates.append(
                {
                    "id": event.id,
                    "memory_type": "pinned_memory",
                    "text": str(event.payload.get("text", "")),
                    "metadata": event.payload,
                    "relevance": 0.5,
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

    def _retrieve_semantic_context(
        self,
        *,
        query: str,
        retrieval_profile: str,
        skip_vector_search: bool,
    ) -> Dict[str, Any]:
        settings = retrieval_profile_settings(retrieval_profile)
        empty = {
            "hits": [],
            "citations": [],
            "context": "",
            "timing_ms": {
                "embedding": None,
                "vector_query": 0.0,
                "rerank_bounding": 0.0,
                "total_retrieval": 0.0,
            },
        }
        if (
            skip_vector_search
            or not settings.get("include_semantic")
            or not str(query or "").strip()
            or isinstance(self.vector_store, NullVectorStore)
        ):
            return empty

        vector_k = int(settings.get("vector_k") or 0)
        if vector_k <= 0:
            return empty

        query_started = time.perf_counter()
        try:
            results = self.vector_store.search(query, k=max(vector_k * 3, vector_k))
        except Exception:
            results = []
        query_completed = time.perf_counter()

        rerank_started = time.perf_counter()
        query_tokens = self._tokenize(query)
        annotated_hits: List[Dict[str, Any]] = []
        for result in results:
            enriched = self._annotate_semantic_hit(
                result,
                query_tokens=query_tokens,
                retrieval_profile=retrieval_profile,
            )
            if enriched is not None:
                annotated_hits.append(enriched)
        annotated_hits.sort(
            key=lambda item: (float(item["score"]), float(item["relevance"])),
            reverse=True,
        )
        selected_hits = annotated_hits[: int(settings.get("citation_limit") or vector_k)]
        context = self._render_retrieval_context(
            selected_hits,
            max_chars=int(settings.get("context_chars") or 0),
        )
        rerank_completed = time.perf_counter()
        return {
            "hits": selected_hits,
            "citations": [dict(item["citation"]) for item in selected_hits],
            "context": context,
            "timing_ms": {
                "embedding": None,
                "vector_query": round((query_completed - query_started) * 1000, 2),
                "rerank_bounding": round((rerank_completed - rerank_started) * 1000, 2),
                "total_retrieval": round((rerank_completed - query_started) * 1000, 2),
            },
        }

    def _annotate_semantic_hit(
        self,
        result: Dict[str, Any],
        *,
        query_tokens: List[str],
        retrieval_profile: str,
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
        architecture_tokens = {
            "architecture",
            "topology",
            "graph",
            "cognition",
            "memory",
            "system",
            "workflow",
            "routing",
            "contract",
            "interface",
            "integration",
            "agent",
            "fault",
            "queue",
            "session",
            "trace",
            "design",
            "obeos",
            "blackfong",
            "daise",
        }
        return any(token in haystack for token in architecture_tokens)

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
            self.vector_store.add(ids=[vector_id], texts=[text], metadatas=[metadata])
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
        agent_name: str,
        session_messages: List[Dict[str, Any]],
        session_summaries: List[Dict[str, Any]],
        global_memories: List[Dict[str, Any]],
        recent_events: List[Dict[str, Any]],
        knowledge_context: str,
        transcript_window: int,
        prompt_budget_tokens: Optional[int] = None,
        prompt_mode: str = "standard",
        retrieval_profile: str = "fast",
    ) -> str:
        sections: List[str] = [f"Retrieval profile: {agent_name} ({prompt_mode}, {retrieval_profile})"]
        if session_summaries:
            sections.append(
                "Session summaries:\n" + "\n".join(
                    f"- {summary['summary']}" for summary in session_summaries if summary["summary"]
                )
            )
        if global_memories:
            sections.append(
                "Relevant global memory:\n" + "\n".join(
                    f"- [{memory['memory_type']}] {memory['text'][:220]}"
                    for memory in global_memories
                    if memory["text"]
                )
            )
        if recent_events:
            sections.append(
                "Recent ecosystem events:\n" + "\n".join(
                    f"- {event['source']}/{event['event_type']}: {event['message']}"
                    for event in recent_events[:6]
                )
            )
        if knowledge_context:
            sections.append("Bounded retrieval context:\n" + knowledge_context)
        if session_messages:
            sections.append(
                "Recent transcript:\n" + "\n".join(
                    f"- {message['role']}: {message['text'][:220]}"
                    for message in session_messages[-transcript_window:]
                )
            )
        return self._apply_prompt_budget(sections, prompt_budget_tokens).strip()

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

        score = float(candidate.get("relevance", 0.0))
        if candidate.get("memory_type") == "pinned_memory":
            score += 0.18
        if session_id and str(metadata.get("session_id", "")) == session_id:
            score += 0.22
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
            return (
                f"Memory retrieval used {payload.get('session_messages', 0)} transcript items, "
                f"{payload.get('session_summaries', 0)} summaries, {payload.get('global_memories', 0)} memories, "
                f"and {payload.get('recent_events', 0)} recent events"
                f"{' (' + ', '.join(prompt_bits) + ')' if prompt_bits else ''}."
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
            summary = (
                f"Latency {float(payload.get('execution_time_ms', 0.0) or 0.0) / 1000:.1f}s "
                f"via {payload.get('provider', 'local')}/{payload.get('model', 'unknown')}; "
                f"prompt={prompt_tokens}, attempts={payload.get('ollama_attempts', 'unknown')}."
            )
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
                f"{payload.get('status', 'unknown')} with model {payload.get('configured_model', 'unknown')}."
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
            return (
                f"Request outcome {payload.get('decision_state', 'unknown')} "
                f"success={payload.get('success', 'unknown')}."
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
            "payload": payload,
            "metadata": event.metadata,
        }


conversation_memory_service = ConversationMemoryService()
