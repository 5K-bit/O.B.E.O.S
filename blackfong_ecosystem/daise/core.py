from __future__ import annotations

import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from ..config import paths
from ..conversation_memory import ConversationMemoryService, conversation_memory_service
from ..decision_quality_control import decision_quality_controller
from ..events import Event
from ..file_chat_intents import FileChatIntent, parse_file_chat_intent
from ..file_scope_service import FileScopeService
from ..file_workflow import FileWorkflowService
from ..identity_semantics import identity_registry
from ..memory_chamber import MemoryChamberService
from ..model_routing import (
    allows_multi_agent,
    detect_intent,
    normalize_response_mode,
    requires_deep_memory_retrieval,
    requires_memory_chamber,
    resolve_response_mode,
)
from ..gps_service import GPSService
from ..policy_engine import PolicyEngine
from ..retrieval_profiles import normalize_retrieval_profile, retrieval_profile_settings
from ..runtime_ownership import get_shared_memory_chamber
from ..storage import ChromaVectorStore, EventStore, NullVectorStore, VectorStore
from ..system_awareness import SystemAwarenessService
from .engines import EvolutionEngine, IngestionEngine, MemoryEngine, ReflectionEngine, SynthesisEngine


TraceCallback = Callable[[Dict[str, Any]], None]


@dataclass
class AgentReport:
    """Normalized specialist report returned to D.A.I.S.E."""

    agent: str
    response: str
    confidence: float
    trace_event_id: Optional[str]
    tool_activity_summary: List[str]
    provider: str
    model: str
    evidence_summary: str
    task_type: str
    backend_error: Optional[str]
    backend_metadata: Dict[str, Any]
    raw_result: Dict[str, Any]
    routing_relevance: float = 0.0
    trust_score: float = 0.5
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "response": self.response,
            "confidence": self.confidence,
            "trace_event_id": self.trace_event_id,
            "tool_activity_summary": self.tool_activity_summary,
            "provider": self.provider,
            "model": self.model,
            "evidence_summary": self.evidence_summary,
            "task_type": self.task_type,
            "backend_error": self.backend_error,
            "backend_metadata": self.backend_metadata,
            "routing_relevance": self.routing_relevance,
            "trust_score": self.trust_score,
            "score": self.score,
        }


@dataclass
class DAISE:
    """
    DAISE: Direct Artificial Intelligence System Environment.

    D.A.I.S.E remains the always-on brain above the specialist agents:
    - decide what matters
    - choose who to ask
    - evaluate what came back
    - persist what was learned
    """

    event_store: EventStore
    vector_store: VectorStore
    ingestion: IngestionEngine
    memory: MemoryEngine
    synthesis: SynthesisEngine
    reflection: ReflectionEngine
    evolution: EvolutionEngine
    conversation_memory: ConversationMemoryService
    policy_engine: PolicyEngine
    memory_chamber: Optional[MemoryChamberService] = None
    file_workflow: Optional[FileWorkflowService] = None
    file_scope_service: Optional[FileScopeService] = None
    awareness_service: Optional[SystemAwarenessService] = None

    MULTI_AGENT_THRESHOLD = 0.75
    LOW_CONFIDENCE_THRESHOLD = 0.72
    SCORE_GAP_DIRECT_SELECT = 0.08
    MAX_MULTI_AGENT_CANDIDATES = 3
    DEFAULT_TRUST_SCORE = 0.5
    TRUST_HISTORY_MIN = 10
    TRUST_HISTORY_WINDOW = 100

    @classmethod
    def create_default(
        cls,
        *,
        event_store: Optional[EventStore] = None,
        vector_store: Optional[VectorStore] = None,
        memory_service: Optional[ConversationMemoryService] = None,
        file_workflow: Optional[FileWorkflowService] = None,
        file_scope_service: Optional[FileScopeService] = None,
        memory_chamber: Optional[MemoryChamberService] = None,
        awareness_service: Optional[SystemAwarenessService] = None,
    ) -> "DAISE":
        """
        Construct DAISE with default Blackfong-backed storage.
        """
        event_store = event_store or EventStore()
        if vector_store is None:
            try:
                vector_store = ChromaVectorStore(paths.vector_db_path)
            except Exception:
                vector_store = NullVectorStore()
        memory_service = memory_service or conversation_memory_service
        file_workflow = file_workflow or FileWorkflowService(memory_service=memory_service)
        file_scope_service = file_scope_service or FileScopeService(
            memory_service=memory_service,
            governed_roots=getattr(file_workflow, "allowed_roots", [paths.root]),
        )

        ingestion = IngestionEngine(event_store=event_store, vector_store=vector_store)
        memory = MemoryEngine(event_store=event_store)
        synthesis = SynthesisEngine(event_store=event_store, vector_store=vector_store)
        reflection = ReflectionEngine(event_store=event_store)
        evolution = EvolutionEngine(event_store=event_store)
        memory_chamber = memory_chamber or get_shared_memory_chamber(
            event_store=event_store,
            vector_store=vector_store,
            workspace_root=paths.root,
            start_watch=False,
        )
        if awareness_service is None:
            from ..tinyprogrammer_companion import tinyprogrammer_companion

        awareness_service = awareness_service or SystemAwarenessService(
            memory_service=memory_service,
            tinyprogrammer=tinyprogrammer_companion,
            gps_service=GPSService(event_store=event_store),
        )

        return cls(
            event_store=event_store,
            vector_store=vector_store,
            ingestion=ingestion,
            memory=memory,
            synthesis=synthesis,
            reflection=reflection,
            evolution=evolution,
            conversation_memory=memory_service,
            policy_engine=PolicyEngine(),
            memory_chamber=memory_chamber,
            file_workflow=file_workflow,
            file_scope_service=file_scope_service,
            awareness_service=awareness_service,
        )

    def init_storage(self) -> None:
        self.event_store.init_db()

    def ingest_event(self, event: Event) -> None:
        self.ingestion.ingest_event(event)

    def run_session(self, task: str) -> Dict[str, Any]:
        """
        Preserve the earlier D.A.I.S.E v0 session flow for CLI usage.
        """
        context = self.synthesis.build_context(task)
        hypothesis_data = self.synthesis.generate_hypothesis(task, context)
        reflection_payload = self.reflection.reflect(task=task, hypothesis=hypothesis_data["hypothesis"], context=context)
        summary_event = self.reflection.persist_session_summary(reflection_payload)

        return {
            "task": task,
            "hypothesis_data": hypothesis_data,
            "reflection": reflection_payload,
            "context": context,
            "summary_event_id": summary_event.id,
        }

    def process_request(
        self,
        *,
        user_input: str,
        modality: str = "text",
        context: Optional[Dict[str, Any]] = None,
        agents: Optional[Dict[str, Any]] = None,
        routing_keywords: Optional[Dict[str, Sequence[str]]] = None,
    ) -> Dict[str, Any]:
        """
        Run the always-on D.A.I.S.E pipeline for a single request.
        """
        if not agents:
            raise ValueError("D.A.I.S.E requires at least one specialist agent")

        request_started = time.time()
        context = dict(context or {})
        session_id = context.get("session_id") or self.conversation_memory.create_session_id()
        correlation_id = context.get("correlation_id") or self.conversation_memory.create_correlation_id()
        request_id = context.get("request_id") or str(uuid.uuid4())[:8]
        analysis_mode = normalize_response_mode(
            resolve_response_mode(
                user_input,
                context.get("analysis_mode") or context.get("response_mode"),
                agent_override=context.get("agent_override"),
            )
        )
        retrieval_profile = normalize_retrieval_profile(context.get("retrieval_profile"), analysis_mode)
        retrieval_settings = retrieval_profile_settings(retrieval_profile)
        context["analysis_mode"] = analysis_mode
        context["response_mode"] = analysis_mode
        context["retrieval_profile"] = retrieval_profile
        detected_intent = detect_intent(user_input)
        context["detected_intent"] = {
            "casual": detected_intent.casual,
            "coding": detected_intent.coding,
            "deep_reasoning": detected_intent.deep_reasoning,
            "file_ops": detected_intent.file_ops,
            "diagnostics": detected_intent.diagnostics,
            "tinyprogrammer": detected_intent.tinyprogrammer,
        }
        agent_override = context.get("agent_override")
        identity_resolutions = identity_registry.resolve_mentions(user_input)
        if agent_override:
            identity_resolutions.append(identity_registry.resolve(str(agent_override)))

        progress_callback = context.get("progress_callback")
        report_callback = context.get("agent_report_callback")
        selection_callback = context.get("agent_selection_callback")

        query_event = None
        policy_decision: Dict[str, Any] = {}

        try:
            self._emit_phase(
                progress_callback,
                request_id=request_id,
                session_id=session_id,
                phase="accepted",
                agent="daise",
                message="Request accepted by D.A.I.S.E.",
                identity_resolutions=[resolution.to_dict() for resolution in identity_resolutions],
            )

            for resolution in identity_resolutions:
                if resolution.input_name != resolution.canonical_identity or resolution.resolution_method != "canonical":
                    identity_registry.emit_resolution_event(
                        resolution,
                        memory_service=self.conversation_memory,
                        session_id=session_id,
                        correlation_id=correlation_id,
                        request_id=request_id,
                    )

            policy_decision = self.policy_engine.evaluate(
                "request",
                {
                    "text": user_input,
                    "transport": context.get("transport"),
                    "capabilities": context.get("capabilities"),
                    "explicit_capture_mode": bool(context.get("explicit_capture_mode") or context.get("sensor_mode_enabled")),
                    "active_capture_requested": self._requests_active_capture(user_input),
                    "file_mutation_requested": detected_intent.file_ops,
                    "insufficient_evidence": not user_input.strip(),
                },
            ).to_dict()
            policy_event = self.conversation_memory.persist_workflow_event(
                source="daise",
                event_type="policy_decision",
                payload={
                    "schema_version": "1.0",
                    "action": "request",
                    **policy_decision,
                },
                confidence=1.0 if policy_decision.get("approved") else 0.82,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
            )
            self._emit_phase(
                progress_callback,
                request_id=request_id,
                session_id=session_id,
                phase="policy",
                agent="daise",
                message=f"Policy decision: {policy_decision.get('decision_state', 'unknown')}.",
                policy_decision=policy_decision,
            )

            if policy_decision.get("decision_state") in {"reject", "clarify", "unknown"}:
                query_event = self.conversation_memory.persist_user_turn(
                    session_id=session_id,
                    correlation_id=correlation_id,
                    text=user_input,
                    modality=modality,
                    routed_agent="daise",
                    analysis_mode=analysis_mode,
                    agent_override=agent_override,
                    request_id=request_id,
                )
                response_text = self._policy_response(policy_decision)
                execution_time_ms = (time.time() - request_started) * 1000
                response_event = self.conversation_memory.persist_agent_response(
                    session_id=session_id,
                    correlation_id=correlation_id,
                    agent_name="daise",
                    response_text=response_text,
                    confidence=0.72,
                    modality=modality,
                    parent_event_id=query_event.id,
                    provider="policy_engine",
                    model="policy-v1",
                    execution_time_ms=execution_time_ms,
                    success=True,
                    extras={
                        "selected_agent": "daise",
                        "consulted_agents": [],
                        "synthesized": False,
                        "analysis_mode": analysis_mode,
                        "retrieval_profile": retrieval_profile,
                        "transport": context.get("transport"),
                        "decision_state": policy_decision.get("decision_state"),
                        "policy_decision": policy_decision,
                    },
                    request_id=request_id,
                )
                self.conversation_memory.persist_workflow_event(
                    source="daise",
                    event_type="request_outcome",
                    payload={
                        "schema_version": "1.0",
                        "success": True,
                        "decision_state": policy_decision.get("decision_state"),
                        "selected_agent": "daise",
                        "output_agent": "daise",
                        "reason": "policy_gate",
                        "execution_time_ms": execution_time_ms,
                        "provider": "policy_engine",
                        "model": "policy-v1",
                        "backend_error": None,
                        "fallback_used": False,
                        "retrieval_profile": retrieval_profile,
                        "analysis_mode": analysis_mode,
                        "transport": context.get("transport"),
                    },
                    confidence=0.72,
                    session_id=session_id,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    parent_event_id=response_event.id,
                )
                operator_trace = self.conversation_memory.get_request_trace(request_id)
                self._emit_phase(
                    progress_callback,
                    request_id=request_id,
                    session_id=session_id,
                    phase="complete",
                    agent="daise",
                    message="Request completed by policy gate.",
                )
                return {
                    "response": response_text,
                    "confidence": 0.72,
                    "agent": "daise",
                    "output_agent": "daise",
                    "mediated_by": "daise",
                    "selected_agent": "daise",
                    "consulted_agents": [],
                    "synthesized": False,
                    "trace_event_id": policy_event.id,
                    "provider": "policy_engine",
                    "model": "policy-v1",
                    "backend_error": None,
                    "backend_metadata": {},
                    "task_type": "policy_gate",
                    "analysis_mode": analysis_mode,
                    "routing_confidence": 0.0,
                    "complexity_score": 0.0,
                    "multi_agent_mode": False,
                    "quality_evaluation": {"approved": False, "reason": "policy_gate"},
                    "policy_decision": policy_decision,
                    "decision_state": policy_decision.get("decision_state"),
                    "candidate_reports": [],
                    "operator_trace": operator_trace,
                    "session_id": session_id,
                    "correlation_id": correlation_id,
                    "request_id": request_id,
                    "query_event_id": query_event.id,
                    "selection_event_id": None,
                    "response_event_id": response_event.id,
                    "summary_event_id": None,
                    "execution_time_ms": execution_time_ms,
                }

            grounded_result = self._handle_grounded_truth_request(
                user_input=user_input,
                modality=modality,
                context=context,
                policy_decision=policy_decision,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                request_started=request_started,
                progress_callback=progress_callback,
            )
            if grounded_result is not None:
                return grounded_result

            file_intent = parse_file_chat_intent(user_input) if self.file_workflow is not None else None
            if file_intent is not None and self.file_workflow is not None:
                return self._handle_file_workflow_request(
                    intent=file_intent,
                    user_input=user_input,
                    modality=modality,
                    context=context,
                    policy_decision=policy_decision,
                    progress_callback=progress_callback,
                    selection_callback=selection_callback,
                    session_id=session_id,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    query_event=query_event,
                    request_started=request_started,
                )

            route_scores = self._route_agent_scores(
                user_input=user_input,
                modality=modality,
                agent_names=list(agents.keys()),
                routing_keywords=routing_keywords or {},
                agent_override=agent_override,
            )
            suggested_agent = max(route_scores, key=route_scores.get)
            routing_confidence = round(route_scores[suggested_agent], 3)
            routing_gap = self._routing_gap(route_scores)
            ambiguous_routing = self._is_ambiguous_routing(route_scores)
            complexity_score = self._calculate_complexity_score(user_input, analysis_mode)
            critical_request = self._is_critical_request(user_input)
            multi_agent_mode = False
            if not context.get("fast_path") and allows_multi_agent(context, detected_intent):
                multi_agent_mode = self._should_use_multi_agent(
                    analysis_mode=analysis_mode,
                    complexity_score=complexity_score,
                    routing_confidence=routing_confidence,
                    critical_request=critical_request,
                    route_scores=route_scores,
                    agent_override=agent_override,
                )
            candidate_agents = self._select_candidate_agents(
                route_scores=route_scores,
                multi_agent_mode=multi_agent_mode,
                agent_override=agent_override,
            )

            decision_type = "critical_action" if critical_request else "complex_query" if multi_agent_mode else "standard_routing"
            quality_evaluation = decision_quality_controller.evaluate_decision_quality(
                correlation_id=correlation_id,
                user_input=user_input,
                selected_agent=suggested_agent,
                confidence_score=routing_confidence,
                decision_type=decision_type,
            )

            self._emit_phase(
                progress_callback,
                request_id=request_id,
                session_id=session_id,
                phase="routing",
                agent=suggested_agent,
                message=f"Routing through {len(candidate_agents)} candidate agent(s).",
                candidates=candidate_agents,
                routing_confidence=routing_confidence,
                complexity_score=complexity_score,
                multi_agent_mode=multi_agent_mode,
                routing_gap=routing_gap,
                ambiguous_routing=ambiguous_routing,
            )

            routing_event = self.conversation_memory.persist_workflow_event(
                source="daise",
                event_type="routing_decision",
                payload={
                    "query": user_input,
                    "modality": modality,
                    "analysis_mode": analysis_mode,
                    "agent_override": agent_override,
                    "identity_doctrine": {
                        "blackfong": "Blackfong adjudicates truth.",
                        "daise": "D.A.I.S.E communicates truth.",
                        "jarvis": "legacy_alias_for_daise",
                    },
                    "identity_resolutions": [resolution.to_dict() for resolution in identity_resolutions],
                    "suggested_agent": suggested_agent,
                    "candidates": [
                        {"agent": agent_name, "routing_relevance": round(route_scores.get(agent_name, 0.0), 3)}
                        for agent_name in candidate_agents
                    ],
                    "routing_confidence": routing_confidence,
                    "routing_gap": routing_gap,
                    "ambiguous_routing": ambiguous_routing,
                    "complexity_score": complexity_score,
                    "critical_request": critical_request,
                    "multi_agent_mode": multi_agent_mode,
                    "quality_evaluation": {
                        "approved": quality_evaluation.get("approved"),
                        "requires_secondary_evaluation": quality_evaluation.get("requires_secondary_evaluation"),
                        "requires_multi_agent_consensus": quality_evaluation.get("requires_multi_agent_consensus"),
                        "threshold_used": quality_evaluation.get("threshold_used"),
                    },
                },
                confidence=routing_confidence,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
            )

            query_event = self.conversation_memory.persist_user_turn(
                session_id=session_id,
                correlation_id=correlation_id,
                text=user_input,
                modality=modality,
                routed_agent=suggested_agent,
                analysis_mode=analysis_mode,
                agent_override=agent_override,
                request_id=request_id,
            )

            self._emit_phase(
                progress_callback,
                request_id=request_id,
                session_id=session_id,
                phase="memory",
                agent="daise",
                message="Retrieving session memory and ecosystem context.",
            )

            memory_bundles: Dict[str, Dict[str, Any]] = {}
            retrievals = []
            use_chamber = (
                self.memory_chamber is not None
                and bool(retrieval_settings.get("include_graph"))
                and (
                    requires_memory_chamber(context, detected_intent)
                    or retrieval_profile == "architecture"
                )
            )
            skip_vector = (
                not bool(retrieval_settings.get("include_semantic"))
                or bool(context.get("fast_path"))
            )
            agents_for_memory = candidate_agents[:1] if context.get("fast_path") else candidate_agents
            for agent_name in agents_for_memory:
                bundle = self.conversation_memory.build_context(
                    session_id=session_id,
                    query=user_input,
                    agent_name=agent_name,
                    analysis_mode=analysis_mode,
                    skip_vector_search=skip_vector,
                    retrieval_profile=retrieval_profile,
                    request_id=request_id,
                    transport=context.get("transport"),
                )
                if use_chamber:
                    bundle["cognition_graph"] = self.memory_chamber.reasoning_context(
                        user_input,
                        session_id=session_id,
                        request_id=request_id,
                        limit=int(retrieval_settings.get("graph_limit") or 8),
                        profile=retrieval_profile,
                    )
                memory_bundles[agent_name] = bundle
            for agent_name in candidate_agents:
                if agent_name not in memory_bundles:
                    memory_bundles[agent_name] = memory_bundles.get(agents_for_memory[0], {})
                agent_bundle = memory_bundles.get(agent_name, {})
                graph_bundle = agent_bundle.get("cognition_graph") or {}
                retrievals.append(
                    {
                        "agent": agent_name,
                        "retrieval_profile": agent_bundle.get("retrieval_profile", retrieval_profile),
                        "session_messages": len(agent_bundle.get("session_messages", [])),
                        "session_summaries": len(agent_bundle.get("session_summaries", [])),
                        "global_memories": len(agent_bundle.get("global_memories", [])),
                        "recent_events": len(agent_bundle.get("recent_events", [])),
                        "prompt_metrics": agent_bundle.get("prompt_metrics", {}),
                        "knowledge_hits": len(agent_bundle.get("knowledge_hits", [])),
                        "knowledge_citations": len(agent_bundle.get("citations", [])),
                        "graph_matches": len(graph_bundle.get("matched_nodes", [])),
                        "semantic_hits": len(graph_bundle.get("semantic_hits", [])),
                        "timing_ms": {
                            "memory": agent_bundle.get("timing_ms", {}),
                            "graph": graph_bundle.get("timing_ms", {}),
                        },
                    }
                )

            self.conversation_memory.persist_workflow_event(
                source="daise",
                event_type="memory_retrieval",
                payload={
                    "schema_version": "1.0",
                    "query": user_input,
                    "selected_candidates": candidate_agents,
                    "retrieval_profile": retrieval_profile,
                    "analysis_mode": analysis_mode,
                    "transport": context.get("transport"),
                    "retrievals": retrievals,
                    "session_messages": sum(item["session_messages"] for item in retrievals),
                    "session_summaries": sum(item["session_summaries"] for item in retrievals),
                    "global_memories": sum(item["global_memories"] for item in retrievals),
                    "recent_events": sum(item["recent_events"] for item in retrievals),
                    "knowledge_hits": sum(item["knowledge_hits"] for item in retrievals),
                    "knowledge_citations": sum(item["knowledge_citations"] for item in retrievals),
                    "prompt_metrics": {
                        item["agent"]: item.get("prompt_metrics", {})
                        for item in retrievals
                    },
                    "timing_ms": {
                        item["agent"]: item.get("timing_ms", {})
                        for item in retrievals
                    },
                    "graph_neighborhoods": {
                        item["agent"]: memory_bundles.get(item["agent"], {}).get("cognition_graph", {})
                        for item in retrievals
                    },
                },
                confidence=1.0,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                parent_event_id=routing_event.id,
            )

            self._emit_phase(
                progress_callback,
                request_id=request_id,
                session_id=session_id,
                phase="agent",
                agent=suggested_agent,
                message="Consulting specialist agents.",
                candidates=candidate_agents,
            )

            reports = self._collect_agent_reports(
                user_input=user_input,
                context=context,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                candidate_agents=candidate_agents,
                agents=agents,
                memory_bundles=memory_bundles,
                route_scores=route_scores,
                report_callback=report_callback,
            )

            self._emit_phase(
                progress_callback,
                request_id=request_id,
                session_id=session_id,
                phase="evaluation",
                agent="daise",
                message="Evaluating specialist reports.",
            )

            for report in reports:
                report.score = round(
                    (0.45 * report.confidence)
                    + (0.35 * report.routing_relevance)
                    + (0.20 * report.trust_score),
                    3,
                )

            reports.sort(key=lambda report: (report.score, report.confidence), reverse=True)
            top_report = reports[0]
            second_report = reports[1] if len(reports) > 1 else None
            score_gap = round(top_report.score - second_report.score, 3) if second_report else 1.0
            synthesized = False
            output_agent = "daise"
            selected_agent = top_report.agent
            response_text = top_report.response
            response_confidence = round(top_report.confidence, 3)
            synthesis_notes = ""

            if second_report and score_gap < self.SCORE_GAP_DIRECT_SELECT and self._reports_are_compatible(top_report, second_report):
                synthesized = True
                response_text = self._synthesize_reports(top_report, second_report)
                response_confidence = round(min(0.97, (top_report.score + second_report.score) / 2), 3)
                synthesis_notes = f"Synthesized {top_report.agent} with {second_report.agent} because score gap was {score_gap:.3f}."

            response_text = self._wrap_final_response(
                response_text,
                policy_decision,
                selected_agent=selected_agent,
                synthesized=synthesized,
            )

            evaluation_event = self.conversation_memory.persist_workflow_event(
                source="daise",
                event_type="agent_evaluation",
                payload={
                    "selected_agent": selected_agent,
                    "output_agent": output_agent,
                    "synthesized": synthesized,
                    "score_gap": score_gap,
                    "candidate_scores": [
                        {
                            "agent": report.agent,
                            "confidence": report.confidence,
                            "routing_relevance": report.routing_relevance,
                            "trust_score": report.trust_score,
                            "score": report.score,
                            "task_type": report.task_type,
                            "provider": report.provider,
                            "model": report.model,
                        }
                        for report in reports
                    ],
                    "synthesis_notes": synthesis_notes,
                },
                confidence=response_confidence,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                parent_event_id=query_event.id if query_event else None,
            )

            selection_payload = {
                "selected_agent": selected_agent,
                "output_agent": output_agent,
                "synthesized": synthesized,
                "score_gap": score_gap,
                "consulted_agents": candidate_agents,
                "selected_score": top_report.score,
                "routing_confidence": routing_confidence,
                "complexity_score": complexity_score,
                "task_type": top_report.task_type,
                "provider": top_report.provider,
                "model": top_report.model,
                "outcome_success": True,
            }
            selection_event = self.conversation_memory.persist_workflow_event(
                source="daise",
                event_type="agent_selection",
                payload=selection_payload,
                confidence=response_confidence,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                parent_event_id=evaluation_event.id,
            )
            trust_update_event = self.conversation_memory.persist_workflow_event(
                source="daise",
                event_type="agent_trust_update",
                payload={
                    "schema_version": "1.0",
                    "agent": selected_agent,
                    "task_type": top_report.task_type,
                    "effective_trust": self.get_agent_trust_score(selected_agent, top_report.task_type),
                    "selected_score": top_report.score,
                    "confidence_decay": {
                        "recent_weight": 0.7,
                        "historical_weight": 0.3,
                        "half_life_days": 30,
                    },
                },
                confidence=0.82,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                parent_event_id=selection_event.id,
            )

            self._emit_selection(
                selection_callback,
                request_id=request_id,
                session_id=session_id,
                selected_agent=selected_agent,
                output_agent=output_agent,
                synthesized=synthesized,
                score_gap=score_gap,
                reports=reports,
            )

            self._emit_phase(
                progress_callback,
                request_id=request_id,
                session_id=session_id,
                phase="persisting",
                agent="daise",
                message="Persisting final answer and summaries.",
            )

            execution_time_ms = (time.time() - request_started) * 1000
            response_event = self.conversation_memory.persist_agent_response(
                session_id=session_id,
                correlation_id=correlation_id,
                agent_name=output_agent,
                response_text=response_text,
                confidence=response_confidence,
                modality=modality,
                parent_event_id=query_event.id if query_event else None,
                provider=top_report.provider,
                model=top_report.model,
                execution_time_ms=execution_time_ms,
                success=True,
                extras={
                    "selected_agent": selected_agent,
                    "consulted_agents": candidate_agents,
                    "synthesized": synthesized,
                    "analysis_mode": analysis_mode,
                    "retrieval_profile": retrieval_profile,
                    "transport": context.get("transport"),
                    "trace_event_id": top_report.trace_event_id,
                    "task_type": top_report.task_type,
                    "backend_error": top_report.backend_error,
                    "backend_metadata": top_report.backend_metadata,
                    "score_gap": score_gap,
                    "quality_evaluation": {
                        "approved": quality_evaluation.get("approved"),
                        "threshold_used": quality_evaluation.get("threshold_used"),
                    },
                    "policy_decision": policy_decision,
                    "decision_state": "approve",
                    "synthesis_notes": synthesis_notes,
                },
                request_id=request_id,
            )
            self.conversation_memory.persist_workflow_event(
                source="daise",
                event_type="latency_observation",
                payload={
                    "schema_version": "1.0",
                    "request_id": request_id,
                    "selected_agent": selected_agent,
                    "output_agent": output_agent,
                    "retrieval_profile": retrieval_profile,
                    "analysis_mode": analysis_mode,
                    "transport": context.get("transport"),
                    "provider": top_report.provider,
                    "model": top_report.model,
                    "execution_time_ms": execution_time_ms,
                    "backend_error": top_report.backend_error,
                    "fallback_used": bool(top_report.backend_error),
                    "backend_metadata": top_report.backend_metadata,
                    "prompt_metrics": top_report.backend_metadata.get("prompt_metrics", {}),
                    "ollama_attempts": top_report.backend_metadata.get("ollama_attempts"),
                    "wall_time_ms": top_report.backend_metadata.get("wall_time_ms"),
                    "ollama_total_duration_ms": top_report.backend_metadata.get("ollama_total_duration_ms"),
                    "load_duration_ms": top_report.backend_metadata.get("load_duration_ms"),
                    "prompt_eval_count": top_report.backend_metadata.get("prompt_eval_count"),
                    "eval_count": top_report.backend_metadata.get("eval_count"),
                    "timeout_reason": top_report.backend_metadata.get("timeout_reason"),
                    "fallback_reason": top_report.backend_metadata.get("fallback_reason"),
                },
                confidence=response_confidence,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                parent_event_id=response_event.id,
            )
            self.conversation_memory.persist_workflow_event(
                source="daise",
                event_type="memory_retrieval_observation",
                payload={
                    "schema_version": "1.0",
                    "retrieval_profile": retrieval_profile,
                    "analysis_mode": analysis_mode,
                    "transport": context.get("transport"),
                    "selected_candidates": candidate_agents,
                    "timing_ms": {
                        item["agent"]: item.get("timing_ms", {})
                        for item in retrievals
                    },
                    "knowledge_hits": sum(item["knowledge_hits"] for item in retrievals),
                    "graph_matches": sum(item["graph_matches"] for item in retrievals),
                },
                confidence=0.9,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                parent_event_id=routing_event.id,
            )

            self.conversation_memory.persist_workflow_event(
                source="daise",
                event_type="request_outcome",
                payload={
                    "schema_version": "1.0",
                    "success": True,
                    "decision_state": "approve",
                    "selected_agent": selected_agent,
                    "output_agent": output_agent,
                    "response_event_id": response_event.id,
                    "selection_event_id": selection_event.id,
                    "trust_update_event_id": trust_update_event.id,
                    "execution_time_ms": execution_time_ms,
                    "provider": top_report.provider,
                    "model": top_report.model,
                    "backend_error": top_report.backend_error,
                    "fallback_used": bool(top_report.backend_error),
                    "retrieval_profile": retrieval_profile,
                    "analysis_mode": analysis_mode,
                    "transport": context.get("transport"),
                },
                confidence=response_confidence,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                parent_event_id=response_event.id,
            )
            summary_event = self.conversation_memory.maybe_create_session_summary(
                session_id,
                correlation_id=correlation_id,
            )

            decision_quality_controller.record_execution_result(
                correlation_id=correlation_id,
                execution_success=True,
                execution_duration=execution_time_ms / 1000.0,
            )

            operator_trace = self.conversation_memory.get_request_trace(request_id)

            self._emit_phase(
                progress_callback,
                request_id=request_id,
                session_id=session_id,
                phase="complete",
                agent="daise",
                message="Request complete.",
            )

            return {
                "response": response_text,
                "confidence": response_confidence,
                "agent": "daise",
                "output_agent": output_agent,
                "mediated_by": "daise",
                "selected_agent": selected_agent,
                "consulted_agents": candidate_agents,
                "synthesized": synthesized,
                "trace_event_id": top_report.trace_event_id,
                "provider": top_report.provider,
                "model": top_report.model,
                "backend_error": top_report.backend_error,
                "backend_metadata": top_report.backend_metadata,
                "task_type": top_report.task_type,
                "analysis_mode": analysis_mode,
                "routing_confidence": routing_confidence,
                "complexity_score": complexity_score,
                "multi_agent_mode": multi_agent_mode,
                "quality_evaluation": quality_evaluation,
                "policy_decision": policy_decision,
                "decision_state": "approve",
                "candidate_reports": [report.to_dict() for report in reports],
                "operator_trace": operator_trace,
                "session_id": session_id,
                "correlation_id": correlation_id,
                "request_id": request_id,
                "query_event_id": query_event.id if query_event else None,
                "selection_event_id": selection_event.id,
                "response_event_id": response_event.id,
                "summary_event_id": summary_event.id if summary_event else None,
                "execution_time_ms": execution_time_ms,
            }
        except Exception as exc:
            execution_time_ms = (time.time() - request_started) * 1000
            if query_event is not None:
                error_response_event = self.conversation_memory.persist_agent_response(
                    session_id=session_id,
                    correlation_id=correlation_id,
                    agent_name="daise",
                    response_text=f"Error processing request: {exc}",
                    confidence=0.0,
                    modality=modality,
                    parent_event_id=query_event.id,
                    execution_time_ms=execution_time_ms,
                    success=False,
                    extras={
                        "error": str(exc),
                        "analysis_mode": analysis_mode,
                        "retrieval_profile": retrieval_profile,
                        "transport": context.get("transport"),
                    },
                    request_id=request_id,
                )
                self.conversation_memory.persist_workflow_event(
                    source="daise",
                    event_type="request_outcome",
                    payload={
                        "schema_version": "1.0",
                        "success": False,
                        "decision_state": "unknown",
                        "selected_agent": "daise",
                        "reason": str(exc),
                        "execution_time_ms": execution_time_ms,
                        "provider": "daise",
                        "model": "exception-path",
                        "backend_error": str(exc),
                        "fallback_used": False,
                        "retrieval_profile": retrieval_profile,
                        "analysis_mode": analysis_mode,
                        "transport": context.get("transport"),
                    },
                    confidence=0.0,
                    session_id=session_id,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    parent_event_id=error_response_event.id,
                )
            self._emit_phase(
                progress_callback,
                request_id=request_id,
                session_id=session_id,
                phase="error",
                agent="daise",
                message=str(exc),
            )
            return {
                "response": f"Error processing request: {exc}",
                "confidence": 0.0,
                "agent": "daise",
                "output_agent": "daise",
                "mediated_by": "daise",
                "selected_agent": None,
                "consulted_agents": [],
                "synthesized": False,
                "trace_event_id": None,
                "provider": "",
                "model": "",
                "backend_error": str(exc),
                "backend_metadata": {},
                "task_type": "error",
                "analysis_mode": analysis_mode,
                "routing_confidence": 0.0,
                "complexity_score": 0.0,
                "multi_agent_mode": False,
                "quality_evaluation": {"approved": False, "reason": str(exc)},
                "policy_decision": policy_decision,
                "decision_state": "unknown",
                "candidate_reports": [],
                "operator_trace": self.conversation_memory.get_request_trace(request_id),
                "session_id": session_id,
                "correlation_id": correlation_id,
                "request_id": request_id,
                "query_event_id": query_event.id if query_event else None,
                "response_event_id": None,
                "summary_event_id": None,
                "execution_time_ms": execution_time_ms,
                "error": str(exc),
            }

    def _collect_agent_reports(
        self,
        *,
        user_input: str,
        context: Dict[str, Any],
        session_id: str,
        correlation_id: str,
        request_id: str,
        candidate_agents: Sequence[str],
        agents: Dict[str, Any],
        memory_bundles: Dict[str, Dict[str, Any]],
        route_scores: Dict[str, float],
        report_callback: Optional[TraceCallback],
    ) -> List[AgentReport]:
        reports: List[AgentReport] = []
        for agent_name in candidate_agents:
            agent_context = dict(context)
            agent_context.update(
                {
                    "session_id": session_id,
                    "correlation_id": correlation_id,
                    "request_id": request_id,
                    "memory_context": memory_bundles.get(agent_name, {}),
                    "daise_selected_candidates": list(candidate_agents),
                    "daise_routing_relevance": route_scores.get(agent_name, 0.0),
                }
            )
            raw_result = agents[agent_name].process(user_input, context=agent_context)
            report = self._normalize_agent_report(agent_name, raw_result)
            report.routing_relevance = round(route_scores.get(agent_name, 0.0), 3)
            report.trust_score = self.get_agent_trust_score(agent_name, report.task_type)
            reports.append(report)

            self.conversation_memory.persist_workflow_event(
                source="daise",
                event_type="agent_report",
                payload={
                    **report.to_dict(),
                    "response_preview": report.response[:280],
                },
                confidence=report.confidence,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
            )
            self._emit_report(
                report_callback,
                request_id=request_id,
                session_id=session_id,
                report=report,
            )

        if not reports:
            raise RuntimeError("No agent reports were produced")
        return reports

    def _normalize_agent_report(self, agent_name: str, result: Dict[str, Any]) -> AgentReport:
        tools_invoked = result.get("tools_invoked") or []
        tool_activity_summary: List[str] = []
        for tool in tools_invoked:
            if isinstance(tool, dict):
                tool_activity_summary.append(
                    str(tool.get("tool") or tool.get("tool_name") or tool.get("status") or "tool")
                )
            else:
                tool_activity_summary.append(str(tool))

        response = str(result.get("response") or "")
        task_type = str(result.get("task_type") or result.get("agent") or agent_name)
        evidence_summary = self._build_evidence_summary(result)
        return AgentReport(
            agent=agent_name,
            response=response,
            confidence=float(result.get("confidence", 0.0)),
            trace_event_id=result.get("trace_event_id"),
            tool_activity_summary=tool_activity_summary,
            provider=str(result.get("provider") or ""),
            model=str(result.get("model") or ""),
            evidence_summary=evidence_summary,
            task_type=task_type,
            backend_error=result.get("backend_error"),
            backend_metadata=dict(result.get("backend_metadata") or {}),
            raw_result=result,
        )

    def _build_evidence_summary(self, result: Dict[str, Any]) -> str:
        if result.get("analysis"):
            analysis = result["analysis"]
            issues_found = analysis.get("issues_found")
            summary = analysis.get("summary")
            if summary:
                return str(summary)
            if issues_found is not None:
                return f"Analysis found {issues_found} issue(s)."
        if result.get("quiz_data"):
            quiz_data = result["quiz_data"]
            if quiz_data.get("topic"):
                return f"Training context focused on {quiz_data['topic']}."
        if result.get("exercise_data"):
            exercise_data = result["exercise_data"]
            if exercise_data.get("topic"):
                return f"Learning context focused on {exercise_data['topic']}."
        if result.get("reasoning_trace"):
            trace = result["reasoning_trace"]
            steps = trace.get("steps", [])
            if steps:
                return f"Reasoning trace captured {len(steps)} step(s)."
        return "Specialist report generated."

    def _route_agent_scores(
        self,
        *,
        user_input: str,
        modality: str,
        agent_names: Sequence[str],
        routing_keywords: Dict[str, Sequence[str]],
        agent_override: Optional[str],
    ) -> Dict[str, float]:
        normalized_override = str(agent_override or "").strip().lower()
        alias_map = {
            "daise": "jarvis",
            "d.a.i.s.e": "jarvis",
            "jarvis_agent": "jarvis",
            "coding_agent": "coding",
            "voice_agent": "voice",
        }
        normalized_override = alias_map.get(normalized_override, normalized_override)
        if normalized_override and normalized_override in agent_names:
            return {agent_name: 1.0 if agent_name == normalized_override else 0.0 for agent_name in agent_names}

        lower_input = user_input.lower()
        word_count = len(user_input.split())
        coding_intent = self._is_coding_intent(lower_input)
        voice_intent = self._is_voice_intent(lower_input, modality)
        route_scores: Dict[str, float] = {}
        default_keywords = {
            "jarvis": ("help", "assist", "question", "explain", "status", "why", "what", "how", "memory"),
            "coding": ("code", "python", "bug", "debug", "review", "refactor", "test", "architecture"),
            "lilo": ("study", "exam", "quiz", "certification", "progress", "topic"),
            "stitch": ("learn", "tutorial", "exercise", "practice", "concept", "hint"),
            "voice": ("voice", "microphone", "audio", "listen", "speak"),
        }

        for agent_name in agent_names:
            keywords = tuple(routing_keywords.get(agent_name) or default_keywords.get(agent_name, ()))
            matches = sum(1 for keyword in keywords if keyword in lower_input)
            keyword_ratio = matches / max(len(keywords), 1) if keywords else 0.0
            score = min(0.95, 0.18 + (keyword_ratio * 0.82)) if matches else 0.0

            if agent_name == "jarvis":
                score = max(score, 0.74)
            if agent_name == "coding" and coding_intent:
                score = max(score, 0.86)
            if agent_name == "voice" and not voice_intent:
                score = 0.0
            if agent_name == "voice" and voice_intent:
                score = max(score, 0.84)
            if modality == "code" and agent_name == "coding":
                score = max(score, 0.94)
            if modality == "voice" and agent_name == "voice":
                score = max(score, 0.94)
            if modality == "vision" and agent_name == "jarvis":
                score = max(score, 0.7)

            if word_count < 4 and agent_name not in {"jarvis", "voice"}:
                score *= 0.82

            route_scores[agent_name] = round(score, 3)

        if route_scores.get("coding", 0.0) >= 0.78 and "jarvis" in route_scores:
            route_scores["jarvis"] = min(route_scores["jarvis"], 0.68)
        if route_scores.get("voice", 0.0) >= 0.78 and "jarvis" in route_scores:
            route_scores["jarvis"] = min(route_scores["jarvis"], 0.66)

        return route_scores

    def _is_coding_intent(self, lower_input: str) -> bool:
        clear_phrases = (
            "review this code",
            "debug this",
            "fix this bug",
            "write code",
            "implement this",
            "run tests",
            "stack trace",
            "pull request",
        )
        if any(phrase in lower_input for phrase in clear_phrases):
            return True
        coding_markers = ("code", "python", "bug", "debug", "refactor", "test", "function", "class", "api", "module")
        return sum(1 for marker in coding_markers if marker in lower_input) >= 2

    def _is_voice_intent(self, lower_input: str, modality: str) -> bool:
        if modality == "voice":
            return True
        control_phrases = (
            "voice start",
            "voice stop",
            "start voice",
            "stop voice",
            "microphone",
            "mic",
            "speak out loud",
            "read aloud",
            "tts",
            "stt",
        )
        return any(phrase in lower_input for phrase in control_phrases)

    def _select_candidate_agents(
        self,
        *,
        route_scores: Dict[str, float],
        multi_agent_mode: bool,
        agent_override: Optional[str],
    ) -> List[str]:
        normalized_override = str(agent_override or "").strip().lower()
        alias_map = {
            "daise": "jarvis",
            "d.a.i.s.e": "jarvis",
            "jarvis_agent": "jarvis",
            "coding_agent": "coding",
            "voice_agent": "voice",
        }
        normalized_override = alias_map.get(normalized_override, normalized_override)
        if normalized_override and normalized_override in route_scores:
            return [normalized_override]

        ranked_agents = sorted(route_scores.items(), key=lambda item: item[1], reverse=True)
        if not multi_agent_mode:
            return [ranked_agents[0][0]]

        candidate_agents: List[str] = []
        for agent_name, score in ranked_agents:
            if score <= 0.0:
                continue
            if score < 0.12 and candidate_agents:
                continue
            candidate_agents.append(agent_name)
            if len(candidate_agents) >= self.MAX_MULTI_AGENT_CANDIDATES:
                break

        if "jarvis" not in candidate_agents and "jarvis" in route_scores and route_scores["jarvis"] > 0:
            candidate_agents.append("jarvis")

        return candidate_agents[: self.MAX_MULTI_AGENT_CANDIDATES]

    def _calculate_complexity_score(self, user_input: str, analysis_mode: str) -> float:
        words = user_input.split()
        lower_input = user_input.lower()
        score = 0.18
        if analysis_mode == "deep":
            score += 0.28
        if len(words) >= 18:
            score += 0.18
        if any(token in lower_input for token in ("compare", "tradeoff", "architecture", "design", "analyze deeply", "multiple")):
            score += 0.18
        if any(token in lower_input for token in ("critical", "security", "delete", "danger", "production", "system-wide")):
            score += 0.18
        if user_input.count("?") > 1 or user_input.count("\n") >= 2:
            score += 0.08
        return round(min(1.0, score), 3)

    def _is_critical_request(self, user_input: str) -> bool:
        lower_input = user_input.lower()
        critical_markers = (
            "security",
            "delete",
            "remove",
            "production",
            "critical",
            "file edit",
            "architecture",
            "sensor",
        )
        return any(marker in lower_input for marker in critical_markers)

    def _should_use_multi_agent(
        self,
        *,
        analysis_mode: str,
        complexity_score: float,
        routing_confidence: float,
        critical_request: bool,
        route_scores: Dict[str, float],
        agent_override: Optional[str],
    ) -> bool:
        if agent_override:
            return False
        if analysis_mode == "deep":
            return True
        if critical_request:
            return True
        if complexity_score >= self.MULTI_AGENT_THRESHOLD:
            return True
        if self._is_ambiguous_routing(route_scores) and routing_confidence < self.LOW_CONFIDENCE_THRESHOLD:
            return True
        return False

    @staticmethod
    def _routing_gap(route_scores: Dict[str, float]) -> float:
        ranked = sorted(route_scores.values(), reverse=True)
        if len(ranked) < 2:
            return 1.0
        return round(ranked[0] - ranked[1], 3)

    def _is_ambiguous_routing(self, route_scores: Dict[str, float]) -> bool:
        ranked = sorted(route_scores.items(), key=lambda item: item[1], reverse=True)
        if len(ranked) < 2:
            return False
        top_agent, top_score = ranked[0]
        runner_agent, runner_score = ranked[1]
        if runner_score < 0.45:
            return False
        if top_agent == "jarvis" and runner_score < 0.62:
            return False
        return (top_score - runner_score) < 0.12

    def _reports_are_compatible(self, primary: AgentReport, secondary: AgentReport) -> bool:
        primary_tokens = set(self._tokenize(primary.response))
        secondary_tokens = set(self._tokenize(secondary.response))
        overlap = len(primary_tokens.intersection(secondary_tokens))
        if overlap >= 3:
            return True
        if primary.task_type == secondary.task_type:
            return True
        return not any(token in primary.response.lower() and token in secondary.response.lower() for token in ("do not", "cannot", "unsafe"))

    def _synthesize_reports(self, primary: AgentReport, secondary: AgentReport) -> str:
        primary_text = primary.response.strip()
        secondary_text = secondary.response.strip()
        if not secondary_text:
            return primary_text
        return (
            f"{primary_text}\n\n"
            f"D.A.I.S.E synthesis note: {secondary.agent} also contributed relevant context. "
            f"{secondary_text}"
        ).strip()

    def _wrap_final_response(
        self,
        response_text: str,
        policy_decision: Dict[str, Any],
        *,
        selected_agent: Optional[str] = None,
        synthesized: bool = False,
    ) -> str:
        text = response_text.strip()
        if not text:
            return "I do not know yet. I need more evidence before answering that safely."
        if policy_decision.get("decision_state") not in {"approve", None}:
            return self._policy_response(policy_decision)
        final_policy = self.policy_engine.evaluate("final_response", {"text": text}).to_dict()
        if final_policy.get("decision_state") != "approve":
            return self._policy_response(final_policy)
        text = self._strip_specialist_headers(text)
        if selected_agent and selected_agent != "daise" and not synthesized:
            text = text.replace("JARVIS", "D.A.I.S.E").replace("Jarvis", "D.A.I.S.E")
        return text

    @staticmethod
    def _strip_specialist_headers(text: str) -> str:
        normalized = text.strip()
        banners = (
            "=== CODING ANALYSIS ===",
            "=== CODING RESPONSE ===",
            "CODING ANALYSIS:",
            "CODING RESPONSE:",
            "CODINGAGENT:",
            "JARVIS:",
            "D.A.I.S.E:",
        )
        for banner in banners:
            if normalized.upper().startswith(banner.upper()):
                return normalized[len(banner) :].lstrip("- \n")
        return normalized

    def _handle_grounded_truth_request(
        self,
        *,
        user_input: str,
        modality: str,
        context: Dict[str, Any],
        policy_decision: Dict[str, Any],
        session_id: str,
        correlation_id: str,
        request_id: str,
        request_started: float,
        progress_callback: Optional[TraceCallback],
    ) -> Optional[Dict[str, Any]]:
        truth = self._grounded_truth_response(user_input, session_id=session_id)
        if truth is None:
            return None

        query_event = self.conversation_memory.persist_user_turn(
            session_id=session_id,
            correlation_id=correlation_id,
            text=user_input,
            modality=modality,
            routed_agent="daise",
            analysis_mode=str(context.get("analysis_mode") or "standard"),
            agent_override=context.get("agent_override"),
            request_id=request_id,
        )
        event_type = (
            "backend_health_observation"
            if truth["kind"] in {"status", "tinyprogrammer_status"}
            else "environment_snapshot"
        )
        trace_event = self.conversation_memory.persist_workflow_event(
            source="daise",
            event_type=event_type,
            payload={
                "schema_version": "1.0",
                "grounded_truth": {
                    "kind": truth["kind"],
                    "source": truth["source"],
                    "observation": truth["observation"],
                },
            },
            confidence=0.94,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            parent_event_id=query_event.id,
            tags=["grounded_truth", truth["kind"]],
        )
        execution_time_ms = (time.time() - request_started) * 1000
        response_event = self.conversation_memory.persist_agent_response(
            session_id=session_id,
            correlation_id=correlation_id,
            agent_name="daise",
            response_text=truth["response"],
            confidence=0.94,
            modality=modality,
            parent_event_id=query_event.id,
            provider="local_runtime_truth",
            model="grounded-truth-v1",
            execution_time_ms=execution_time_ms,
            success=True,
            extras={
                "selected_agent": "daise",
                "consulted_agents": [],
                "synthesized": False,
                "analysis_mode": context.get("analysis_mode"),
                "retrieval_profile": normalize_retrieval_profile(context.get("retrieval_profile"), context.get("analysis_mode")),
                "transport": context.get("transport"),
                "decision_state": policy_decision.get("decision_state"),
                "policy_decision": policy_decision,
                "grounded_truth": {
                    "kind": truth["kind"],
                    "source": truth["source"],
                },
            },
            request_id=request_id,
        )
        self.conversation_memory.persist_workflow_event(
            source="daise",
            event_type="request_outcome",
            payload={
                "schema_version": "1.0",
                "success": True,
                "decision_state": policy_decision.get("decision_state"),
                "selected_agent": "daise",
                "output_agent": "daise",
                "reason": "grounded_truth",
                "grounded_truth_kind": truth["kind"],
                "execution_time_ms": execution_time_ms,
                "provider": "local_runtime_truth",
                "model": "grounded-truth-v1",
                "backend_error": None,
                "fallback_used": False,
                "retrieval_profile": normalize_retrieval_profile(context.get("retrieval_profile"), context.get("analysis_mode")),
                "analysis_mode": context.get("analysis_mode"),
                "transport": context.get("transport"),
            },
            confidence=0.9,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            parent_event_id=response_event.id,
        )
        self._emit_phase(
            progress_callback,
            request_id=request_id,
            session_id=session_id,
            phase="complete",
            agent="daise",
            message="Answered from local grounded truth.",
        )
        operator_trace = self.conversation_memory.get_request_trace(request_id)
        return {
            "response": truth["response"],
            "confidence": 0.94,
            "agent": "daise",
            "output_agent": "daise",
            "mediated_by": "daise",
            "selected_agent": "daise",
            "consulted_agents": [],
            "synthesized": False,
            "trace_event_id": trace_event.id,
            "provider": "local_runtime_truth",
            "model": "grounded-truth-v1",
            "backend_error": None,
            "backend_metadata": {"grounded_truth": truth["observation"]},
            "task_type": "grounded_truth",
            "analysis_mode": context.get("analysis_mode"),
            "routing_confidence": 1.0,
            "complexity_score": 0.0,
            "multi_agent_mode": False,
            "quality_evaluation": {"approved": True, "reason": "grounded_truth"},
            "policy_decision": policy_decision,
            "decision_state": policy_decision.get("decision_state"),
            "candidate_reports": [],
            "operator_trace": operator_trace,
            "session_id": session_id,
            "correlation_id": correlation_id,
            "request_id": request_id,
            "query_event_id": query_event.id,
            "selection_event_id": None,
            "response_event_id": response_event.id,
            "summary_event_id": None,
            "execution_time_ms": execution_time_ms,
        }

    def _grounded_truth_response(self, user_input: str, *, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        normalized = " ".join(str(user_input or "").strip().lower().split())
        if not normalized:
            return None

        normalized_compact = self._compact_truth_text(normalized)
        now, clock_source = self._runtime_truth_now()
        observation = {
            "local_date": f"{now.strftime('%A, %B')} {now.day}, {now.year}",
            "local_time": now.strftime("%I:%M %p %Z").lstrip("0"),
            "day_of_week": now.strftime("%A"),
            "timezone": str(now.tzinfo or ""),
            "clock_source": clock_source,
        }
        operator_profile: Dict[str, Any] = {}
        if session_id:
            try:
                operator_profile = self.conversation_memory.operator_profile_snapshot(session_id=session_id)
            except Exception:
                operator_profile = {}
        work_update_topics = self._extract_grounded_work_update_topics(user_input)

        if self._matches_greeting_truth(normalized_compact):
            greeting = self._build_grounded_greeting(
                normalized_compact=normalized_compact,
                operator_profile=operator_profile,
            )
            return {
                "kind": "greeting",
                "source": "obeos_session_awareness",
                "response": greeting,
                "observation": {
                    "preferred_name": operator_profile.get("preferred_name"),
                    "active_work": list(operator_profile.get("active_work") or [])[:3],
                },
            }

        if self._matches_checkin_truth(normalized_compact):
            check_in = self._build_grounded_checkin(operator_profile=operator_profile)
            return {
                "kind": "check_in",
                "source": "obeos_session_awareness",
                "response": check_in,
                "observation": {
                    "preferred_name": operator_profile.get("preferred_name"),
                    "active_work": list(operator_profile.get("active_work") or [])[:3],
                },
            }

        if self._matches_memory_progress_truth(normalized_compact):
            memory_progress = self._build_grounded_memory_progress(
                normalized_compact=normalized_compact,
                operator_profile=operator_profile,
                work_topics=work_update_topics,
            )
            return {
                "kind": "memory_progress",
                "source": "obeos_memory_layer",
                "response": memory_progress,
                "observation": {
                    "preferred_name": operator_profile.get("preferred_name"),
                    "active_work": list(operator_profile.get("active_work") or [])[:3],
                    "work_update_topics": work_update_topics[:3],
                },
            }

        if self._matches_memory_system_truth(normalized_compact):
            memory_system = self._build_grounded_memory_system(
                normalized_compact=normalized_compact,
                operator_profile=operator_profile,
            )
            return {
                "kind": "memory_system",
                "source": "obeos_memory_layer",
                "response": memory_system,
                "observation": {
                    "preferred_name": operator_profile.get("preferred_name"),
                    "active_work": list(operator_profile.get("active_work") or [])[:3],
                    "memory_components": [
                        "operator_fact",
                        "active_work_item",
                        "pinned_memory",
                        "session_summary",
                    ],
                },
            }

        if self._matches_chat_rendering_truth(normalized_compact):
            rendering_truth = self._build_grounded_chat_rendering_truth(
                operator_profile=operator_profile,
            )
            return {
                "kind": "chat_rendering",
                "source": "obeos_ui_contract",
                "response": rendering_truth,
                "observation": {
                    "preferred_name": operator_profile.get("preferred_name"),
                    "active_work": list(operator_profile.get("active_work") or [])[:3],
                    "likely_causes": [
                        "surface_truncation_path",
                        "scroll_container_clipping",
                        "message_renderer_layout",
                    ],
                },
            }

        if self._matches_work_update_truth(normalized_compact, work_update_topics):
            work_update = self._build_grounded_work_update(
                normalized_compact=normalized_compact,
                operator_profile=operator_profile,
                work_topics=work_update_topics,
            )
            return {
                "kind": "work_update",
                "source": "obeos_session_awareness",
                "response": work_update,
                "observation": {
                    "preferred_name": operator_profile.get("preferred_name"),
                    "active_work": work_update_topics[:3],
                },
            }

        if self._matches_identity_truth(normalized):
            return {
                "kind": "identity",
                "source": "obeos_identity_doctrine",
                "response": "I am D.A.I.S.E., the visible assistant inside O.B.E.O.S. D.A.I.S.E. stands for Direct Artificial Intelligence System Environment.",
                "observation": {
                    "assistant": "D.A.I.S.E.",
                    "expansion": "Direct Artificial Intelligence System Environment",
                    "alias_policy": "jarvis_internal_compatibility_only",
                },
            }

        asks_date = self._matches_any(
            normalized,
            (
                "today's date",
                "todays date",
                "what date is it",
                "what is the date",
                "current date",
                "date today",
            ),
        )
        asks_day = self._matches_any(
            normalized,
            (
                "what day is it",
                "day of the week",
                "what day of the week is it",
                "what weekday is it",
                "current day",
            ),
        )
        asks_time = self._matches_any(
            normalized,
            (
                "what time is it",
                "what is the time",
                "current time",
                "local time",
                "time is it",
            ),
        )
        asks_status = self._matches_status_truth(normalized)
        asks_tiny_status = self._matches_tinyprogrammer_truth(normalized_compact)

        if asks_date and asks_time:
            return {
                "kind": "date_time",
                "source": clock_source,
                "response": f"It is {observation['local_date']}, and the local time is {observation['local_time']}.",
                "observation": observation,
            }
        if asks_date:
            return {
                "kind": "date",
                "source": clock_source,
                "response": f"Today is {observation['local_date']}.",
                "observation": observation,
            }
        if asks_day:
            return {
                "kind": "day_of_week",
                "source": clock_source,
                "response": f"Today is {observation['day_of_week']}.",
                "observation": observation,
            }
        if asks_time:
            return {
                "kind": "time",
                "source": clock_source,
                "response": f"The local time is {observation['local_time']}.",
                "observation": observation,
            }
        if asks_tiny_status:
            snapshot = self.awareness_service.build_snapshot(emit=False) if self.awareness_service is not None else {}
            tiny_truth = self._build_tinyprogrammer_truth(
                normalized_compact=normalized_compact,
                snapshot=snapshot,
            )
            if tiny_truth is not None:
                return tiny_truth
        if asks_status:
            snapshot = self.awareness_service.build_snapshot(emit=False) if self.awareness_service is not None else {}
            observed = snapshot.get("observed", {}) if snapshot else {}
            backend = observed.get("backend", {}) if isinstance(observed.get("backend"), dict) else {}
            services = observed.get("services", {}) if isinstance(observed.get("services"), dict) else {}
            gps = observed.get("sensors", {}).get("gps", {}) if isinstance(observed.get("sensors"), dict) else {}
            health = snapshot.get("health", {}) if isinstance(snapshot.get("health"), dict) else {}
            backend_label = backend.get("status", "unknown")
            provider = backend.get("provider", "backend")
            model = backend.get("configured_model", "unknown model")
            telegram = services.get("telegram", {}) if isinstance(services.get("telegram"), dict) else {}
            gps_status = gps.get("status", "offline") if isinstance(gps, dict) else "offline"
            status_text = (
                f"I am online. Backend status is {backend_label} on {provider} using {model}. "
                f"Telegram is {telegram.get('status', 'unknown')}. GPS is {gps_status}."
            )
            return {
                "kind": "status",
                "source": "local_awareness_snapshot",
                "response": status_text,
                "observation": {
                    "health": health,
                    "backend": backend,
                    "telegram": telegram,
                    "gps": gps,
                },
            }
        return None

    @staticmethod
    def _runtime_truth_now() -> tuple[datetime, str]:
        override = str(os.getenv("BLACKFONG_RUNTIME_TRUTH_NOW", "") or "").strip()
        if override:
            try:
                parsed = datetime.fromisoformat(override.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    local_tz = datetime.now().astimezone().tzinfo
                    parsed = parsed.replace(tzinfo=local_tz)
                return parsed.astimezone(), "runtime_truth_override"
            except ValueError:
                pass
        return datetime.now().astimezone(), "local_system_clock"

    @staticmethod
    def _matches_any(normalized: str, phrases: Sequence[str]) -> bool:
        return any(phrase in normalized for phrase in phrases)

    @staticmethod
    def _compact_truth_text(normalized: str) -> str:
        compact = re.sub(r"[^a-z0-9'\s]+", " ", str(normalized or "").lower())
        return " ".join(compact.split())

    def _matches_greeting_truth(self, normalized_compact: str) -> bool:
        if not normalized_compact:
            return False
        greeting_phrases = {
            "hello",
            "hello there",
            "hi",
            "hi there",
            "hey",
            "hey there",
            "good morning",
            "good afternoon",
            "good evening",
            "im back",
            "i'm back",
            "we are back",
            "were back",
            "we're back",
        }
        token_count = len(normalized_compact.split())
        if token_count <= 5 and normalized_compact in greeting_phrases:
            return True
        greeting_prefixes = ("hello ", "hi ", "hey ", "good morning ", "good afternoon ", "good evening ")
        if token_count <= 6 and "back" in normalized_compact and normalized_compact.startswith(greeting_prefixes):
            return True
        return False

    def _matches_checkin_truth(self, normalized_compact: str) -> bool:
        if not normalized_compact:
            return False
        check_in_phrases = {
            "how are you",
            "how are you doing",
            "how are you doing tonight",
            "how are you tonight",
            "hows it going",
            "how's it going",
            "are you there",
            "you there",
            "are you awake",
            "you awake",
            "are you doing okay",
            "you doing okay",
        }
        token_count = len(normalized_compact.split())
        return token_count <= 7 and normalized_compact in check_in_phrases

    def _matches_work_update_truth(self, normalized_compact: str, work_topics: Sequence[str]) -> bool:
        if not normalized_compact or not work_topics:
            return False
        if len(normalized_compact.split()) > 32:
            return False
        if any(token in normalized_compact for token in ("?", " why ", " status", " online", " offline")):
            return False
        update_markers = (
            "i am working on",
            "im working on",
            "i'm working on",
            "i am still working on",
            "im still working on",
            "i'm still working on",
            "i have been working on",
            "i've been working on",
            "we are working on",
            "we're working on",
            "updates to your system",
            "updates to the system",
        )
        if any(marker in normalized_compact for marker in update_markers):
            return True
        greeting_update_prefixes = (
            "good morning i am ",
            "good morning im ",
            "good morning i'm ",
            "good afternoon i am ",
            "good afternoon im ",
            "good afternoon i'm ",
            "good evening i am ",
            "good evening im ",
            "good evening i'm ",
        )
        return normalized_compact.startswith(greeting_update_prefixes)

    def _matches_memory_progress_truth(self, normalized_compact: str) -> bool:
        if not normalized_compact or len(normalized_compact.split()) > 96:
            return False
        phrases = (
            "made a good amount of changes",
            "made a lot of changes",
            "made alot of changes",
            "made changes and they should reflect",
            "noticed when i ask you what your name is you remembered it",
            "congratulate you on that milestone",
            "shows you have some kind of memory",
            "have you noticed anything new",
            "noticed anything new",
            "improved the ecosystem alot",
            "improved the ecosystem a lot",
            "improved the system a lot",
        )
        if any(phrase in normalized_compact for phrase in phrases):
            return True
        memory_markers = (
            "your name",
            "remembered it",
            "some kind of memory",
            "milestone",
            "remember your name",
        )
        change_markers = (
            "what changed",
            "changed",
            "improved the ecosystem",
            "improved the system",
            "noticed anything new",
            "notice anything new",
        )
        return any(marker in normalized_compact for marker in memory_markers) and any(
            marker in normalized_compact for marker in change_markers
        )

    def _matches_memory_system_truth(self, normalized_compact: str) -> bool:
        if not normalized_compact or len(normalized_compact.split()) > 40:
            return False
        phrases = (
            "how did you add a note to yourself",
            "how do you add a note to yourself",
            "how does that work",
            "how does memory work",
            "will you remember to do that in the future",
            "will you remember that in the future",
            "will you remember to do that",
            "will you remember that",
            "did you save that",
            "did you store that",
            "keep that in mind",
        )
        return any(phrase in normalized_compact for phrase in phrases)

    def _matches_chat_rendering_truth(self, normalized_compact: str) -> bool:
        if not normalized_compact or len(normalized_compact.split()) > 36:
            return False
        phrases = (
            "text is being cut off",
            "text being cut off",
            "reply is being cut off",
            "reply being cut off",
            "messages are cut off",
            "message is cut off",
            "message being cut off",
            "message gets cut off",
            "chat is cut off",
            "chat gets cut off",
            "not all of the text is visible",
            "not all text is visible",
            "cant see the whole reply",
            "can't see the whole reply",
            "reply tail is missing",
            "response is clipped",
            "message is clipped",
            "reply is clipped",
            "reply is truncated",
            "message is truncated",
        )
        return any(phrase in normalized_compact for phrase in phrases)

    def _build_grounded_greeting(
        self,
        *,
        normalized_compact: str,
        operator_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        lowered = normalized_compact.lower()
        salutation = "Hello"
        if "good morning" in lowered:
            salutation = "Good morning"
        elif "good afternoon" in lowered:
            salutation = "Good afternoon"
        elif "good evening" in lowered:
            salutation = "Good evening"
        elif "back" in lowered and any(
            token in lowered
            for token in ("im back", "i'm back", "we are back", "were back", "we're back")
        ):
            salutation = "Welcome back"
        profile = dict(operator_profile or {})
        preferred_name = str(profile.get("preferred_name") or "").strip()
        active_work = [
            str(item).strip()
            for item in list(profile.get("active_work") or [])
            if str(item).strip()
        ]
        response = f"{salutation}{', ' + preferred_name if preferred_name else ''}. I'm here in O.B.E.O.S and ready."
        if active_work:
            response += f" I still have our recent work around {self._join_grounded_items(active_work[:3])}."
        return response

    def _build_grounded_chat_rendering_truth(
        self,
        *,
        operator_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        profile = dict(operator_profile or {})
        preferred_name = str(profile.get("preferred_name") or "").strip()
        active_work = [
            str(item).strip()
            for item in list(profile.get("active_work") or [])
            if str(item).strip()
        ]
        response = (
            f"{preferred_name + ', ' if preferred_name else ''}"
            "that usually points to the active chat surface rather than the memory layer. "
            "When replies get cut off in O.B.E.O.S, the most likely cause is a truncation or layout problem "
            "in the current HUD or desktop message renderer, scroll container, or panel sizing."
        )
        response += " The underlying request can still complete even when the visible message tail is clipped."
        if active_work:
            response += f" I still have our recent work around {self._join_grounded_items(active_work[:3])} in focus."
        return response

    def _build_grounded_checkin(self, *, operator_profile: Optional[Dict[str, Any]] = None) -> str:
        profile = dict(operator_profile or {})
        preferred_name = str(profile.get("preferred_name") or "").strip()
        active_work = [
            str(item).strip()
            for item in list(profile.get("active_work") or [])
            if str(item).strip()
        ]
        response = f"I'm here{', ' + preferred_name if preferred_name else ''}, and operating normally inside O.B.E.O.S."
        if active_work:
            response += f" I still have our recent work around {self._join_grounded_items(active_work[:3])} in focus."
        return response

    def _build_grounded_work_update(
        self,
        *,
        normalized_compact: str,
        operator_profile: Optional[Dict[str, Any]] = None,
        work_topics: Sequence[str],
    ) -> str:
        profile = dict(operator_profile or {})
        preferred_name = str(profile.get("preferred_name") or "").strip()
        salutation = "I've noted that"
        if normalized_compact.startswith("good morning"):
            salutation = f"Good morning{', ' + preferred_name if preferred_name else ''}. I've noted that"
        elif normalized_compact.startswith("good afternoon"):
            salutation = f"Good afternoon{', ' + preferred_name if preferred_name else ''}. I've noted that"
        elif normalized_compact.startswith("good evening"):
            salutation = f"Good evening{', ' + preferred_name if preferred_name else ''}. I've noted that"
        elif preferred_name:
            salutation = f"{preferred_name}, I've noted that"
        response = f"{salutation} you're still working on {self._join_grounded_items(list(work_topics)[:3])}."
        response += " I'm here if you want to drill into the next step or sanity-check the current direction."
        return response

    def _build_grounded_memory_progress(
        self,
        *,
        normalized_compact: str = "",
        operator_profile: Optional[Dict[str, Any]] = None,
        work_topics: Sequence[str],
    ) -> str:
        profile = dict(operator_profile or {})
        preferred_name = str(profile.get("preferred_name") or "").strip()
        current_topics = [
            str(item).strip()
            for item in list(work_topics or [])
            if str(item).strip()
        ]
        active_work = [
            str(item).strip()
            for item in (current_topics + list(profile.get("active_work") or []))
            if str(item).strip()
        ]
        active_work = list(dict.fromkeys(active_work))
        parts: List[str] = ["Yes, I can see the difference."]
        if "your name" in normalized_compact or "remember your name" in normalized_compact or "remembered it" in normalized_compact:
            parts.append("I can also carry forward that I am D.A.I.S.E. inside O.B.E.O.S.")
        if preferred_name:
            parts.append(f'I can now reliably carry forward that you prefer I call you "{preferred_name}".')
        if active_work:
            parts.append(f"I also still have our recent work around {self._join_grounded_items(active_work[:3])} in focus.")
        parts.append(
            "That tells me the shared O.B.E.O.S memory layer is carrying more of our history forward instead of losing it between turns."
        )
        return " ".join(parts)

    def _build_grounded_memory_system(
        self,
        *,
        normalized_compact: str = "",
        operator_profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        profile = dict(operator_profile or {})
        preferred_name = str(profile.get("preferred_name") or "").strip()
        active_work = [
            str(item).strip()
            for item in list(profile.get("active_work") or [])
            if str(item).strip()
        ]
        remembers_future = "will you remember" in normalized_compact or "keep that in mind" in normalized_compact
        if remembers_future:
            if preferred_name:
                opening = f"Yes, {preferred_name}, if we keep it in the shared O.B.E.O.S memory layer."
            else:
                opening = "Yes, if we keep it in the shared O.B.E.O.S memory layer."
            response = (
                f"{opening} Important context is written into conversation memory backed by events.db, "
                "then derived into operator facts, active work items, pinned memory, and session summaries."
            )
        else:
            opening = "I do not keep a separate hidden notebook."
            if preferred_name:
                opening = f"{preferred_name}, I do not keep a separate hidden notebook."
            response = (
                f"{opening} O.B.E.O.S writes important context into the shared conversation memory layer backed by events.db, "
                "then derives operator facts, active work items, pinned memory, and session summaries from it."
            )
        if active_work:
            response += f" That is how I can carry forward things like {self._join_grounded_items(active_work[:3])}."
        else:
            response += " That is how I can carry forward your preferences and recent work."
        return response

    def _extract_grounded_work_update_topics(self, user_input: str) -> List[str]:
        try:
            extractor = getattr(self.conversation_memory, "_extract_work_topics", None)
            if callable(extractor):
                return [
                    str(item).strip()
                    for item in list(extractor(user_input) or [])
                    if str(item).strip()
                ]
        except Exception:
            return []
        return []

    def _matches_identity_truth(self, normalized: str) -> bool:
        if self._matches_any(
            normalized,
            (
                "what does daise stand for",
                "what does d.a.i.s.e stand for",
                "what is daise",
                "what's your name",
                "who are you",
                "what is your name",
                "do you remember your name",
                "remember your name",
            ),
        ):
            return True
        return "direct artificial intelligence system environment" in normalized

    def _matches_status_truth(self, normalized: str) -> bool:
        short_tokens = len(normalized.split()) <= 10
        if normalized in {"status", "/status"}:
            return True
        if not short_tokens:
            return False
        status_phrases = (
            "what is your status",
            "what's your status",
            "are you online",
            "are you awake",
            "system status",
            "backend status",
            "runtime status",
        )
        return self._matches_any(normalized, status_phrases)

    def _matches_tinyprogrammer_truth(self, normalized_compact: str) -> bool:
        if "tinyprogrammer" not in normalized_compact and "tiny programmer" not in normalized_compact:
            return False
        if len(normalized_compact.split()) > 18:
            return False
        if normalized_compact.startswith(
            (
                "i am ",
                "im ",
                "i'm ",
                "we are ",
                "were ",
                "we're ",
                "good morning i am ",
                "good afternoon i am ",
                "good evening i am ",
                "good evening im ",
                "good evening i'm ",
            )
        ):
            return False
        explicit_markers = (
            "status",
            "online",
            "offline",
            "why",
            "available",
            "availability",
            "up and running",
            "not working",
            "isnt",
            "isn't",
            "failed",
            "failure",
            "error",
        )
        if any(marker in normalized_compact for marker in explicit_markers):
            return True
        explicit_phrases = (
            "is tinyprogrammer working",
            "is tiny programmer working",
            "is tinyprogrammer up",
            "is tiny programmer up",
            "did tinyprogrammer start",
            "did tiny programmer start",
            "can tinyprogrammer start",
            "can tiny programmer start",
            "tinyprogrammer startup status",
            "tiny programmer startup status",
        )
        return any(phrase in normalized_compact for phrase in explicit_phrases)

    def _build_tinyprogrammer_truth(
        self,
        *,
        normalized_compact: str,
        snapshot: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        observed = snapshot.get("observed", {}) if isinstance(snapshot, dict) else {}
        services = observed.get("services", {}) if isinstance(observed.get("services"), dict) else {}
        tiny = services.get("tinyprogrammer", {}) if isinstance(services.get("tinyprogrammer"), dict) else {}
        if not tiny:
            return None
        online = bool(tiny.get("online"))
        state = str(tiny.get("state") or tiny.get("mode") or "unknown")
        reason_candidates = (
            tiny.get("last_error_summary"),
            tiny.get("stream_human_reason"),
            tiny.get("live_control_human_reason"),
            tiny.get("native_window_human_reason"),
            tiny.get("human_reason"),
            tiny.get("error"),
        )
        reason = ""
        for candidate in reason_candidates:
            text = str(candidate or "").strip()
            if not text:
                continue
            if text.lower() in {
                "tinyprogrammer is offline or unreachable from o.b.e.o.s.",
                "tinyprogrammer live controls are available.",
            }:
                continue
            reason = text
            break

        if online:
            response = f"TinyProgrammer is online right now. State is {state}."
            if reason:
                response += f" {reason}"
        else:
            if any(token in normalized_compact for token in ("why", "not working", "working online", "isnt", "isn't")):
                response = f"TinyProgrammer is not online right now. State is {state}."
                if reason:
                    response += f" {reason}"
                else:
                    response += " O.B.E.O.S currently sees it as offline or unreachable."
            else:
                response = f"TinyProgrammer is offline right now. State is {state}."
                if reason:
                    response += f" {reason}"

        return {
            "kind": "tinyprogrammer_status",
            "source": "local_awareness_snapshot",
            "response": response,
            "observation": {
                "tinyprogrammer": tiny,
                "health": snapshot.get("health", {}) if isinstance(snapshot, dict) else {},
            },
        }

    @staticmethod
    def _join_grounded_items(items: Sequence[str]) -> str:
        cleaned = [str(item).strip() for item in items if str(item).strip()]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        if len(cleaned) == 2:
            return f"{cleaned[0]} and {cleaned[1]}"
        return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"

    def _handle_file_workflow_request(
        self,
        *,
        intent: FileChatIntent,
        user_input: str,
        modality: str,
        context: Dict[str, Any],
        policy_decision: Dict[str, Any],
        progress_callback: Optional[TraceCallback],
        selection_callback: Optional[TraceCallback],
        session_id: str,
        correlation_id: str,
        request_id: str,
        query_event: Optional[Event],
        request_started: float,
    ) -> Dict[str, Any]:
        analysis_mode = str(context.get("analysis_mode") or "quick")
        self._emit_phase(
            progress_callback,
            request_id=request_id,
            session_id=session_id,
            phase="routing",
            agent="daise",
            message=f"Routing through D.A.I.S.E file and scope workflow: {intent.action}.",
            candidates=["daise"],
            routing_confidence=0.96,
            complexity_score=0.45,
            multi_agent_mode=False,
            routing_gap=1.0,
            ambiguous_routing=False,
        )
        routing_event = self.conversation_memory.persist_workflow_event(
            source="daise",
            event_type="routing_decision",
            payload={
                "query": user_input,
                "modality": modality,
                "analysis_mode": analysis_mode,
                "agent_override": context.get("agent_override"),
                "intent": {"type": "file_workflow", "action": intent.action, "path": intent.path, "proposal_id": intent.proposal_id},
                "identity_doctrine": {
                    "blackfong": "Blackfong adjudicates truth.",
                    "daise": "D.A.I.S.E communicates truth.",
                    "jarvis": "legacy_alias_for_daise",
                },
                "suggested_agent": "daise",
                "candidates": [{"agent": "daise", "routing_relevance": 0.96}],
                "routing_confidence": 0.96,
                "routing_gap": 1.0,
                "ambiguous_routing": False,
                "complexity_score": 0.45,
                "critical_request": False,
                "multi_agent_mode": False,
            },
            confidence=0.96,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
        )
        query_event = query_event or self.conversation_memory.persist_user_turn(
            session_id=session_id,
            correlation_id=correlation_id,
            text=user_input,
            modality=modality,
            routed_agent="daise",
            analysis_mode=analysis_mode,
            agent_override=context.get("agent_override"),
            request_id=request_id,
        )
        self._emit_phase(
            progress_callback,
            request_id=request_id,
            session_id=session_id,
            phase="memory",
            agent="daise",
            message="Skipping large memory retrieval for direct governed file workflow.",
        )
        self.conversation_memory.persist_workflow_event(
            source="daise",
            event_type="memory_retrieval",
            payload={
                "query": user_input,
                "retrieval_profile": normalize_retrieval_profile(context.get("retrieval_profile"), analysis_mode),
                "selected_candidates": ["daise"],
                "retrievals": [],
                "session_messages": 0,
                "session_summaries": 0,
                "global_memories": 0,
                "recent_events": 0,
                "knowledge_hits": 0,
                "knowledge_citations": 0,
                "prompt_metrics": {},
                "timing_ms": {},
                "skipped": True,
                "reason": "direct_file_workflow",
            },
            confidence=1.0,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            parent_event_id=routing_event.id,
        )
        self._emit_phase(
            progress_callback,
            request_id=request_id,
            session_id=session_id,
            phase="agent",
            agent="daise",
            message="Executing D.A.I.S.E file workflow with governed edits and approved read-map scopes.",
            candidates=["daise"],
        )

        workflow_result = self._execute_file_workflow_intent(
            intent,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
        )
        response_confidence = 0.95 if workflow_result.get("success", False) else 0.72
        response_text = self._wrap_final_response(
            self._format_file_workflow_response(intent, workflow_result),
            policy_decision,
            selected_agent="daise",
            synthesized=False,
        )

        selection_event = self.conversation_memory.persist_workflow_event(
            source="daise",
            event_type="agent_selection",
            payload={
                "selected_agent": "daise",
                "output_agent": "daise",
                "synthesized": False,
                "score_gap": 1.0,
                "consulted_agents": [],
                "selected_score": response_confidence,
                "routing_confidence": 0.96,
                "complexity_score": 0.45,
                "task_type": "file_workflow",
                "provider": "blackfong",
                "model": "file-workflow-v2",
                "outcome_success": bool(workflow_result.get("success", False)),
                "file_action": intent.action,
                "target_path": intent.path,
                "proposal_id": intent.proposal_id,
            },
            confidence=response_confidence,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            parent_event_id=routing_event.id,
        )
        self._emit_selection(
            selection_callback,
            request_id=request_id,
            session_id=session_id,
            selected_agent="daise",
            output_agent="daise",
            synthesized=False,
            score_gap=1.0,
            reports=[],
        )

        self._emit_phase(
            progress_callback,
            request_id=request_id,
            session_id=session_id,
            phase="persisting",
            agent="daise",
            message="Persisting governed file workflow result.",
        )
        execution_time_ms = (time.time() - request_started) * 1000
        response_event = self.conversation_memory.persist_agent_response(
            session_id=session_id,
            correlation_id=correlation_id,
            agent_name="daise",
            response_text=response_text,
            confidence=response_confidence,
            modality=modality,
            parent_event_id=query_event.id if query_event else None,
            provider="blackfong",
            model="file-workflow-v2",
            execution_time_ms=execution_time_ms,
            success=bool(workflow_result.get("success", False)),
            extras={
                "selected_agent": "daise",
                "consulted_agents": [],
                "synthesized": False,
                "analysis_mode": analysis_mode,
                "retrieval_profile": normalize_retrieval_profile(context.get("retrieval_profile"), analysis_mode),
                "transport": context.get("transport"),
                "task_type": "file_workflow",
                "backend_error": workflow_result.get("error"),
                "backend_metadata": {"file_action": intent.action},
                "policy_decision": policy_decision,
                "decision_state": "approve",
                "file_workflow": workflow_result,
            },
            request_id=request_id,
        )
        self.conversation_memory.persist_workflow_event(
            source="daise",
            event_type="latency_observation",
            payload={
                "schema_version": "1.0",
                "request_id": request_id,
                "selected_agent": "daise",
                "output_agent": "daise",
                "retrieval_profile": normalize_retrieval_profile(context.get("retrieval_profile"), analysis_mode),
                "analysis_mode": analysis_mode,
                "transport": context.get("transport"),
                "provider": "blackfong",
                "model": "file-workflow-v2",
                "execution_time_ms": execution_time_ms,
                "backend_error": workflow_result.get("error"),
                "fallback_used": False,
                "backend_metadata": {"file_action": intent.action},
            },
            confidence=response_confidence,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            parent_event_id=response_event.id,
        )
        self.conversation_memory.persist_workflow_event(
            source="daise",
            event_type="request_outcome",
            payload={
                "schema_version": "1.0",
                "success": bool(workflow_result.get("success", False)),
                "decision_state": "approve",
                "selected_agent": "daise",
                "output_agent": "daise",
                "response_event_id": response_event.id,
                "selection_event_id": selection_event.id,
                "execution_time_ms": execution_time_ms,
                "file_action": intent.action,
                "provider": "blackfong",
                "model": "file-workflow-v2",
                "backend_error": workflow_result.get("error"),
                "fallback_used": False,
                "retrieval_profile": normalize_retrieval_profile(context.get("retrieval_profile"), analysis_mode),
                "analysis_mode": analysis_mode,
                "transport": context.get("transport"),
            },
            confidence=response_confidence,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            parent_event_id=response_event.id,
        )
        summary_event = self.conversation_memory.maybe_create_session_summary(
            session_id,
            correlation_id=correlation_id,
        )
        decision_quality_controller.record_execution_result(
            correlation_id=correlation_id,
            execution_success=bool(workflow_result.get("success", False)),
            execution_duration=execution_time_ms / 1000.0,
        )
        operator_trace = self.conversation_memory.get_request_trace(request_id)
        self._emit_phase(
            progress_callback,
            request_id=request_id,
            session_id=session_id,
            phase="complete",
            agent="daise",
            message="D.A.I.S.E file and scope workflow complete.",
        )
        return {
            "response": response_text,
            "confidence": response_confidence,
            "agent": "daise",
            "output_agent": "daise",
            "mediated_by": "daise",
            "selected_agent": "daise",
            "consulted_agents": [],
            "synthesized": False,
            "trace_event_id": selection_event.id,
            "provider": "blackfong",
            "model": "file-workflow-v2",
            "backend_error": workflow_result.get("error"),
            "backend_metadata": {"file_action": intent.action},
            "task_type": "file_workflow",
            "analysis_mode": analysis_mode,
            "routing_confidence": 0.96,
            "complexity_score": 0.45,
            "multi_agent_mode": False,
            "quality_evaluation": {"approved": True, "reason": "direct_file_workflow"},
            "policy_decision": policy_decision,
            "decision_state": "approve",
            "candidate_reports": [],
            "operator_trace": operator_trace,
            "session_id": session_id,
            "correlation_id": correlation_id,
            "request_id": request_id,
            "query_event_id": query_event.id if query_event else None,
            "selection_event_id": selection_event.id,
            "response_event_id": response_event.id,
            "summary_event_id": summary_event.id if summary_event else None,
            "execution_time_ms": execution_time_ms,
            "file_workflow": workflow_result,
        }

    def _execute_file_workflow_intent(
        self,
        intent: FileChatIntent,
        *,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.file_workflow is None:
            return {"success": False, "error": "Governed file workflow is unavailable."}
        if intent.action == "policy":
            return {"success": True, "action": "policy", "workspace_policy": self.file_workflow.workspace_policy()}
        if intent.action == "proposals":
            return {"success": True, "action": "proposals", **self.file_workflow.list_proposals()}
        if intent.action == "roots":
            if self.file_scope_service is None:
                return {"success": False, "action": "roots", "error": "Read-map scope service is unavailable."}
            return {
                "success": True,
                "action": "roots",
                "scope_roots": self.file_scope_service.list_roots(
                    session_id=session_id,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    emit=True,
                ),
            }
        if intent.action == "approve_root":
            if self.file_scope_service is None:
                return {"success": False, "action": "approve_root", "error": "Read-map scope service is unavailable."}
            result = self.file_scope_service.approve_root(
                intent.path or "",
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
            )
            result["action"] = "approve_root"
            return result
        if intent.action == "map":
            if self.file_scope_service is None:
                return {"success": False, "action": "map", "error": "Read-map scope service is unavailable."}
            result = self.file_scope_service.map_path(
                intent.path or "",
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
            )
            result["action"] = "map"
            return result
        if intent.action == "scan":
            result = self.file_workflow.scan(intent.path or ".")
            result["action"] = "scan"
            return result
        if intent.action == "read":
            if self.file_scope_service is not None:
                result = self.file_scope_service.read_path(
                    intent.path or "",
                    session_id=session_id,
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
            else:
                result = self.file_workflow.read(intent.path or "")
            result["action"] = "read"
            return result
        if intent.action == "propose":
            if not intent.proposed_content:
                return {
                    "success": False,
                    "action": "propose",
                    "path": intent.path,
                    "error": "Include a fenced code block or a 'with content:' section so I can stage a governed proposal.",
                    "workspace_policy": self.file_workflow.workspace_policy(),
                }
            result = self.file_workflow.propose(
                path=intent.path or "",
                proposed_content=intent.proposed_content,
                summary=intent.summary,
            )
            result["action"] = "propose"
            return result
        if intent.action == "approve":
            result = self.file_workflow.approve(
                intent.proposal_id or "",
                consent={"approved": True, "level": 2, "source": "chat"},
            )
            result["action"] = "approve"
            return result
        if intent.action == "validate":
            result = self.file_workflow.pre_apply_validation(intent.proposal_id or "")
            result["action"] = "validate"
            return result
        if intent.action == "apply":
            result = self.file_workflow.apply(intent.proposal_id or "")
            result["action"] = "apply"
            return result
        if intent.action == "rollback":
            expected_phrase = f"ROLLBACK {intent.proposal_id or ''}".strip()
            result = self.file_workflow.rollback(
                intent.proposal_id or "",
                consent={
                    "approved": True,
                    "level": 3,
                    "source": "chat",
                    "expected_phrase": expected_phrase,
                    "typed_confirmation": intent.typed_confirmation or "",
                },
            )
            result["action"] = "rollback"
            return result
        return {"success": False, "error": f"Unsupported D.A.I.S.E file action: {intent.action}", "action": intent.action}

    def _format_file_workflow_response(self, intent: FileChatIntent, result: Dict[str, Any]) -> str:
        action = intent.action
        if action == "policy":
            policy = result.get("workspace_policy", {})
            allowed = policy.get("allowed_roots", [])
            denied = policy.get("denied_roots", [])
            allowed_text = "\n".join(f"- {entry}" for entry in allowed[:5]) or "- none configured"
            denied_text = "\n".join(f"- {entry}" for entry in denied[:4]) or "- none configured"
            return (
                "I can work through the governed file workflow inside these project roots.\n\n"
                f"Allowed roots:\n{allowed_text}\n\n"
                f"Denied roots:\n{denied_text}"
            )

        if action == "roots":
            roots = result.get("scope_roots", {})
            governed = roots.get("governed_roots", [])
            approved = roots.get("approved_roots", [])
            candidates = [
                entry.get("path")
                for entry in roots.get("discovered_roots", [])
                if entry.get("state") == "candidate"
            ]
            governed_text = "\n".join(f"- {entry}" for entry in governed[:4]) or "- none configured"
            approved_text = "\n".join(f"- {entry}" for entry in approved[:4]) or "- none approved yet"
            candidate_text = "\n".join(f"- {entry}" for entry in candidates[:6]) or "- no candidate roots discovered"
            return (
                "These are the current D.A.I.S.E file scopes.\n\n"
                f"Governed edit roots:\n{governed_text}\n\n"
                f"Approved read-map roots:\n{approved_text}\n\n"
                f"Candidate roots you can approve next:\n{candidate_text}"
            )

        if action == "proposals":
            proposals = result.get("proposals", [])
            if not proposals:
                return "There are no active governed file proposals right now."
            lines = []
            for proposal in proposals[:6]:
                lines.append(
                    f"- {proposal.get('proposal_id')}: {proposal.get('state')} | {proposal.get('path')}"
                )
            return "Here are the current governed file proposals:\n" + "\n".join(lines)

        if action == "approve_root":
            if not result.get("success"):
                return f"I could not approve that read-map root yet: {result.get('human_reason') or result.get('error') or 'unknown reason'}."
            return (
                f"I approved {result.get('path')} for D.A.I.S.E read and map access.\n"
                "Write authority still stays inside governed project roots in this phase."
            )

        if action == "map":
            if not result.get("success"):
                return f"I could not map that path yet: {result.get('human_reason') or result.get('error') or 'unknown reason'}."
            entry = result.get("entry") or {}
            children = entry.get("children") or []
            preview = "\n".join(
                f"- {child.get('kind', 'entry')}: {child.get('name')} [{child.get('state', 'candidate')}]"
                for child in children[:12]
            ) or "- no visible children"
            return (
                f"I mapped {result.get('path')} through the approved read-map scope.\n"
                f"Visible entries:\n{preview}"
            )

        if action == "scan":
            if not result.get("success"):
                return f"I could not scan that path safely: {result.get('error') or (result.get('policy_decision') or {}).get('reason', 'unknown error')}."
            entries = result.get("entries", [])
            preview = "\n".join(
                f"- {entry.get('kind', 'entry')}: {entry.get('name')}"
                for entry in entries[:12]
            ) or "- no visible entries"
            return (
                f"I scanned {result.get('path')} through the governed file workflow.\n"
                f"Visible entries:\n{preview}"
            )

        if action == "read":
            if not result.get("success"):
                return f"I could not read that file safely: {result.get('human_reason') or result.get('error') or (result.get('policy_decision') or {}).get('reason', 'unknown error')}."
            excerpt = self._format_file_excerpt(str(result.get("content") or ""))
            return (
                f"I read {result.get('path')} through D.A.I.S.E's current approved file scope.\n\n"
                f"```text\n{excerpt}\n```"
            )

        proposal = result.get("proposal") or {}
        proposal_id = proposal.get("proposal_id") or intent.proposal_id or "unknown"
        if action == "propose":
            if not result.get("success"):
                return (
                    f"I am ready to stage a governed proposal for {intent.path}, but I still need the file content. "
                    "Include a fenced code block or a 'with content:' section in the message."
                )
            return (
                f"I staged a governed file proposal for {proposal.get('path')}.\n"
                f"Proposal ID: {proposal_id}\n"
                f"State: {proposal.get('state')}\n"
                f"Consent level required to approve: {proposal.get('required_consent_level')}"
            )
        if action == "approve":
            if result.get("success"):
                return f"Governed file proposal {proposal_id} is approved and ready for pre-apply validation."
            policy_reason = (result.get("policy_decision") or {}).get("reason") or result.get("error") or "approval was not granted"
            return f"I could not approve proposal {proposal_id}: {policy_reason}."
        if action == "validate":
            validation = result.get("validation") or {}
            if result.get("success"):
                return f"Pre-apply validation passed for proposal {proposal_id}. The file is ready to apply."
            return f"Pre-apply validation failed for proposal {proposal_id}: {validation.get('reason') or result.get('error') or 'unknown reason'}."
        if action == "apply":
            validation = result.get("validation") or {}
            if result.get("success"):
                return f"Proposal {proposal_id} was applied and post-apply validation passed."
            return f"I could not apply proposal {proposal_id}: {validation.get('reason') or result.get('error') or 'unknown reason'}."
        if action == "rollback":
            if result.get("success"):
                return f"Proposal {proposal_id} was rolled back through the governed file workflow."
            expected_phrase = result.get("expected_phrase") or f"ROLLBACK {proposal_id}"
            return (
                f"I could not roll back proposal {proposal_id} yet. "
                f"Reply with the exact confirmation phrase `{expected_phrase}` to authorize rollback."
            )
        return result.get("error") or "The governed file workflow completed."

    @staticmethod
    def _format_file_excerpt(content: str, *, limit: int = 2200) -> str:
        normalized = str(content or "").replace("\r\n", "\n").strip("\n")
        if len(normalized) <= limit:
            return normalized or "[empty file]"
        return normalized[:limit].rstrip() + "\n... [truncated]"

    def _policy_response(self, policy_decision: Dict[str, Any]) -> str:
        state = str(policy_decision.get("decision_state") or "unknown")
        reasons = policy_decision.get("reasons") or []
        reason = str(reasons[0]) if reasons else "I do not have enough safe evidence to continue."
        if state == "clarify":
            return f"I need one clarification before I do that: {reason}"
        if state == "reject":
            return f"I cannot do that safely: {reason}"
        if state == "defer":
            return f"I should defer that action until the missing approval or workflow is in place: {reason}"
        return f"I do not know enough to answer safely yet: {reason}"

    @staticmethod
    def _requests_active_capture(user_input: str) -> bool:
        lower_input = user_input.lower()
        capture_verbs = ("turn on", "activate", "start", "record", "listen", "watch", "capture")
        capture_targets = ("camera", "webcam", "microphone", "mic")
        return any(verb in lower_input for verb in capture_verbs) and any(target in lower_input for target in capture_targets)

    def get_agent_trust_score(self, agent_name: str, task_type: str) -> float:
        selection_events = self.event_store.list_events_by_type("agent_selection", limit=2000)
        exact_matches = []
        agent_matches = []
        for event in selection_events:
            payload = event.payload or {}
            if payload.get("selected_agent") != agent_name:
                continue
            outcome = payload.get("outcome_success")
            if outcome is None:
                continue
            agent_matches.append(bool(outcome))
            if payload.get("task_type") == task_type:
                exact_matches.append(event)

        event_history = exact_matches if len(exact_matches) >= self.TRUST_HISTORY_MIN else [
            event
            for event in selection_events
            if (event.payload or {}).get("selected_agent") == agent_name
            and (event.payload or {}).get("outcome_success") is not None
        ]
        if len(event_history) >= self.TRUST_HISTORY_MIN:
            recent_accuracy = self._decayed_outcome_accuracy(event_history[:30])
            historical_accuracy = self._decayed_outcome_accuracy(event_history[: self.TRUST_HISTORY_WINDOW])
            return round((recent_accuracy * 0.7) + (historical_accuracy * 0.3), 3)

        history = agent_matches
        if len(history) < self.TRUST_HISTORY_MIN:
            return self.DEFAULT_TRUST_SCORE
        trimmed = history[: self.TRUST_HISTORY_WINDOW]
        return round(sum(1 for outcome in trimmed if outcome) / len(trimmed), 3)

    def _decayed_outcome_accuracy(self, events: Sequence[Event]) -> float:
        if not events:
            return self.DEFAULT_TRUST_SCORE
        now_ms = time.time() * 1000
        weighted_success = 0.0
        total_weight = 0.0
        half_life_days = 30.0
        for event in events:
            outcome = bool((event.payload or {}).get("outcome_success"))
            age_days = max(0.0, (now_ms - event.timestamp) / (24 * 60 * 60 * 1000))
            weight = 0.5 ** (age_days / half_life_days)
            total_weight += weight
            if outcome:
                weighted_success += weight
        if total_weight <= 0:
            return self.DEFAULT_TRUST_SCORE
        return max(0.0, min(1.0, weighted_success / total_weight))

    def _tokenize(self, text: str) -> List[str]:
        return [token for token in text.lower().replace("\n", " ").split(" ") if token]

    def _emit_phase(
        self,
        callback: Optional[TraceCallback],
        **payload: Any,
    ) -> None:
        if callback is None:
            return
        try:
            callback(
                {
                    "timestamp": time.time(),
                    **payload,
                }
            )
        except Exception:
            return

    def _emit_report(
        self,
        callback: Optional[TraceCallback],
        *,
        request_id: str,
        session_id: str,
        report: AgentReport,
    ) -> None:
        if callback is None:
            return
        try:
            callback(
                {
                    "timestamp": time.time(),
                    "request_id": request_id,
                    "session_id": session_id,
                    "report": report.to_dict(),
                }
            )
        except Exception:
            return

    def _emit_selection(
        self,
        callback: Optional[TraceCallback],
        *,
        request_id: str,
        session_id: str,
        selected_agent: str,
        output_agent: str,
        synthesized: bool,
        score_gap: float,
        reports: Sequence[AgentReport],
    ) -> None:
        if callback is None:
            return
        try:
            callback(
                {
                    "timestamp": time.time(),
                    "request_id": request_id,
                    "session_id": session_id,
                    "selected_agent": selected_agent,
                    "output_agent": output_agent,
                    "synthesized": synthesized,
                    "score_gap": score_gap,
                    "reports": [report.to_dict() for report in reports],
                }
            )
        except Exception:
            return
