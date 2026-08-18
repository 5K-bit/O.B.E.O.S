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

    def process_request(self, *, user_input: str, modality: str = "text", context: Optional[Dict[str, Any]] = None, agents: Optional[Dict[str, Any]] = None, routing_keywords: Optional[Dict[str, Sequence[str]]] = None) -> Dict[str, Any]:
        if not agents:
            raise ValueError("D.A.I.S.E requires at least one specialist agent")
        request_started = time.time()
        context = dict(context or {})
        session_id = context.get("session_id") or self.conversation_memory.create_session_id()
        correlation_id = context.get("correlation_id") or self.conversation_memory.create_correlation_id()
        request_id = context.get("request_id") or str(uuid.uuid4())[:8]
        analysis_mode = normalize_response_mode(resolve_response_mode(user_input, context.get("analysis_mode") or context.get("response_mode"), agent_override=context.get("agent_override")))
        retrieval_profile = normalize_retrieval_profile(context.get("retrieval_profile"), analysis_mode)
        retrieval_settings = retrieval_profile_settings(retrieval_profile)
        context["analysis_mode"] = analysis_mode
        context["response_mode"] = analysis_mode
        context["retrieval_profile"] = retrieval_profile
        detected_intent = detect_intent(user_input)
        context["detected_intent"] = {"casual": detected_intent.casual, "coding": detected_intent.coding, "deep_reasoning": detected_intent.deep_reasoning, "file_ops": detected_intent.file_ops, "diagnostics": detected_intent.diagnostics, "tinyprogrammer": detected_intent.tinyprogrammer}
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
            self._emit_phase(progress_callback, request_id=request_id, session_id=session_id, phase="accepted", agent="daise", message="Request accepted by D.A.I.S.E.", identity_resolutions=[resolution.to_dict() for resolution in identity_resolutions])
            for resolution in identity_resolutions:
                if resolution.input_name != resolution.canonical_identity or resolution.resolution_method != "canonical":
                    identity_registry.emit_resolution_event(resolution, memory_service=self.conversation_memory, session_id=session_id, correlation_id=correlation_id, request_id=request_id)
            policy_decision = self.policy_engine.evaluate("request", {"text": user_input, "transport": context.get("transport"), "capabilities": context.get("capabilities"), "explicit_capture_mode": bool(context.get("explicit_capture_mode") or context.get("sensor_mode_enabled")), "active_capture_requested": self._requests_active_capture(user_input), "file_mutation_requested": detected_intent.file_ops, "insufficient_evidence": not user_input.strip()}).to_dict()
            policy_event = self.conversation_memory.persist_workflow_event(source="daise", event_type="policy_decision", payload={"schema_version": "1.0", "action": "request", **policy_decision}, confidence=1.0 if policy_decision.get("approved") else 0.82, session_id=session_id, correlation_id=correlation_id, request_id=request_id)
            self._emit_phase(progress_callback, request_id=request_id, session_id=session_id, phase="policy", agent="daise", message=f"Policy decision: {policy_decision.get('decision_state', 'unknown')}.", policy_decision=policy_decision)
            if policy_decision.get("decision_state") in {"reject", "clarify", "unknown"}:
                query_event = self.conversation_memory.persist_user_turn(session_id=session_id, correlation_id=correlation_id, text=user_input, modality=modality, routed_agent="daise", analysis_mode=analysis_mode, agent_override=agent_override, request_id=request_id)
                response_text = self._policy_response(policy_decision)
                execution_time_ms = (time.time() - request_started) * 1000
                response_event = self.conversation_memory.persist_agent_response(session_id=session_id, correlation_id=correlation_id, agent_name="daise", response_text=response_text, confidence=0.72, modality=modality, parent_event_id=query_event.id, provider="policy_engine", model="policy-v1", execution_time_ms=execution_time_ms, success=True, extras={"selected_agent": "daise", "consulted_agents": [], "synthesized": False, "analysis_mode": analysis_mode, "retrieval_profile": retrieval_profile, "transport": context.get("transport"), "decision_state": policy_decision.get("decision_state"), "policy_decision": policy_decision}, request_id=request_id)
                self.conversation_memory.persist_workflow_event(source="daise", event_type="request_outcome", payload={"schema_version": "1.0", "success": True, "decision_state": policy_decision.get("decision_state"), "selected_agent": "daise", "output_agent": "daise", "reason": "policy_gate", "execution_time_ms": execution_time_ms, "provider": "policy_engine", "model": "policy-v1", "backend_error": None, "fallback_used": False, "retrieval_profile": retrieval_profile, "analysis_mode": analysis_mode, "transport": context.get("transport")}, confidence=0.72, session_id=session_id, correlation_id=correlation_id, request_id=request_id, parent_event_id=response_event.id)
                operator_trace = self.conversation_memory.get_request_trace(request_id)
                self._emit_phase(progress_callback, request_id=request_id, session_id=session_id, phase="complete", agent="daise", message="Request completed by policy gate.")
                return {"response": response_text, "confidence": 0.72, "agent": "daise", "output_agent": "daise", "mediated_by": "daise", "selected_agent": "daise", "consulted_agents": [], "synthesized": False, "trace_event_id": policy_event.id, "provider": "policy_engine", "model": "policy-v1", "backend_error": None, "backend_metadata": {}, "task_type": "policy_gate", "analysis_mode": analysis_mode, "routing_confidence": 0.0, "complexity_score": 0.0, "multi_agent_mode": False, "quality_evaluation": {"approved": False, "reason": "policy_gate"}, "policy_decision": policy_decision, "decision_state": policy_decision.get("decision_state"), "candidate_reports": [], "operator_trace": operator_trace, "session_id": session_id, "correlation_id": correlation_id, "request_id": request_id, "query_event_id": query_event.id, "selection_event_id": None, "response_event_id": response_event.id, "summary_event_id": None, "execution_time_ms": execution_time_ms}
            grounded_result = self._handle_grounded_truth_request(user_input=user_input, modality=modality, context=context, policy_decision=policy_decision, session_id=session_id, correlation_id=correlation_id, request_id=request_id, request_started=request_started, progress_callback=progress_callback)
            if grounded_result is not None:
                return grounded_result
            file_intent = parse_file_chat_intent(user_input) if self.file_workflow is not None else None
            if file_intent is not None and self.file_workflow is not None:
                return self._handle_file_workflow_request(intent=file_intent, user_input=user_input, modality=modality, context=context, policy_decision=policy_decision, progress_callback=progress_callback, selection_callback=selection_callback, session_id=session_id, correlation_id=correlation_id, request_id=request_id, query_event=query_event, request_started=request_started)
            continuity_routing_hint = self._continuity_routing_hint(session_id=session_id, user_input=user_input, analysis_mode=analysis_mode, retrieval_profile=retrieval_profile)
            route_scores = self._route_agent_scores(user_input=user_input, modality=modality, agent_names=list(agents.keys()), routing_keywords=routing_keywords or {}, agent_override=agent_override, continuity_hint=continuity_routing_hint)
            suggested_agent = max(route_scores, key=route_scores.get)
            routing_confidence = round(route_scores[suggested_agent], 3)
            routing_gap = self._routing_gap(route_scores)
            ambiguous_routing = self._is_ambiguous_routing(route_scores)
            complexity_score = self._calculate_complexity_score(user_input, analysis_mode)
            critical_request = self._is_critical_request(user_input)
            multi_agent_mode = False
            if not context.get("fast_path") and allows_multi_agent(context, detected_intent):
                multi_agent_mode = self._should_use_multi_agent(analysis_mode=analysis_mode, complexity_score=complexity_score, routing_confidence=routing_confidence, critical_request=critical_request, route_scores=route_scores, agent_override=agent_override)
            candidate_agents = self._select_candidate_agents(route_scores=route_scores, multi_agent_mode=multi_agent_mode, agent_override=agent_override)
            decision_type = "critical_action" if critical_request else "complex_query" if multi_agent_mode else "standard_routing"
            quality_evaluation = decision_quality_controller.evaluate_decision_quality(correlation_id=correlation_id, user_input=user_input, selected_agent=suggested_agent, confidence_score=routing_confidence, decision_type=decision_type)
            self._emit_phase(progress_callback, request_id=request_id, session_id=session_id, phase="routing", agent=suggested_agent, message=f"Routing through {len(candidate_agents)} candidate agent(s).", candidates=candidate_agents, routing_confidence=routing_confidence, complexity_score=complexity_score, multi_agent_mode=multi_agent_mode, routing_gap=routing_gap, ambiguous_routing=ambiguous_routing)
            routing_event = self.conversation_memory.persist_workflow_event(source="daise", event_type="routing_decision", payload={"query": user_input, "modality": modality, "analysis_mode": analysis_mode, "agent_override": agent_override, "identity_doctrine": {"blackfong": "Blackfong adjudicates truth.", "daise": "D.A.I.S.E communicates truth.", "jarvis": "legacy_alias_for_daise"}, "identity_resolutions": [resolution.to_dict() for resolution in identity_resolutions], "suggested_agent": suggested_agent, "candidates": [{"agent": agent_name, "routing_relevance": round(route_scores.get(agent_name, 0.0), 3)} for agent_name in candidate_agents], "routing_confidence": routing_confidence, "routing_gap": routing_gap, "ambiguous_routing": ambiguous_routing, "complexity_score": complexity_score, "critical_request": critical_request, "multi_agent_mode": multi_agent_mode, "quality_evaluation": {"approved": quality_evaluation.get("approved"), "requires_secondary_evaluation": quality_evaluation.get("requires_secondary_evaluation"), "requires_multi_agent_consensus": quality_evaluation.get("requires_multi_agent_consensus"), "threshold_used": quality_evaluation.get("threshold_used")}, "continuity_routing_hint": continuity_routing_hint}, confidence=routing_confidence, session_id=session_id, correlation_id=correlation_id, request_id=request_id)
            query_event = self.conversation_memory.persist_user_turn(session_id=session_id, correlation_id=correlation_id, text=user_input, modality=modality, routed_agent=suggested_agent, analysis_mode=analysis_mode, agent_override=agent_override, request_id=request_id)
            self._emit_phase(progress_callback, request_id=request_id, session_id=session_id, phase="memory", agent="daise", message="Retrieving session memory and ecosystem context.")
            memory_bundles: Dict[str, Dict[str, Any]] = {}
            retrievals = []
            use_chamber = self.memory_chamber is not None and bool(retrieval_settings.get("include_graph")) and (requires_memory_chamber(context, detected_intent) or retrieval_profile == "architecture")
            skip_vector = not bool(retrieval_settings.get("include_semantic")) or bool(context.get("fast_path"))
            agents_for_memory = candidate_agents[:1] if context.get("fast_path") else candidate_agents
            for agent_name in agents_for_memory:
                bundle = self.conversation_memory.build_context(session_id=session_id, query=user_input, agent_name=agent_name, analysis_mode=analysis_mode, skip_vector_search=skip_vector, retrieval_profile=retrieval_profile, request_id=request_id, transport=context.get("transport"))
                if use_chamber:
                    bundle["cognition_graph"] = self.memory_chamber.reasoning_context(user_input, session_id=session_id, request_id=request_id, limit=int(retrieval_settings.get("graph_limit") or 8), profile=retrieval_profile)
                memory_bundles[agent_name] = bundle
            for agent_name in candidate_agents:
                if agent_name not in memory_bundles:
                    memory_bundles[agent_name] = memory_bundles.get(agents_for_memory[0], {})
                agent_bundle = memory_bundles.get(agent_name, {})
                graph_bundle = agent_bundle.get("cognition_graph") or {}
                retrievals.append({"agent": agent_name, "retrieval_profile": agent_bundle.get("retrieval_profile", retrieval_profile), "session_messages": len(agent_bundle.get("session_messages", [])), "session_summaries": len(agent_bundle.get("session_summaries", [])), "global_memories": len(agent_bundle.get("global_memories", [])), "recent_events": len(agent_bundle.get("recent_events", [])), "prompt_metrics": agent_bundle.get("prompt_metrics", {}), "knowledge_hits": len(agent_bundle.get("knowledge_hits", [])), "knowledge_citations": len(agent_bundle.get("citations", [])), "graph_matches": len(graph_bundle.get("matched_nodes", [])), "semantic_hits": len(graph_bundle.get("semantic_hits", [])), "timing_ms": {"memory": agent_bundle.get("timing_ms", {}), "graph": graph_bundle.get("timing_ms", {})}})
            self.conversation_memory.persist_workflow_event(source="daise", event_type="memory_retrieval", payload={"schema_version": "1.0", "query": user_input, "selected_candidates": candidate_agents, "retrieval_profile": retrieval_profile, "analysis_mode": analysis_mode, "transport": context.get("transport"), "retrievals": retrievals, "session_messages": sum(item["session_messages"] for item in retrievals), "session_summaries": sum(item["session_summaries"] for item in retrievals), "global_memories": sum(item["global_memories"] for item in retrievals), "recent_events": sum(item["recent_events"] for item in retrievals), "knowledge_hits": sum(item["knowledge_hits"] for item in retrievals), "knowledge_citations": sum(item["knowledge_citations"] for item in retrievals), "prompt_metrics": {item["agent"]: item.get("prompt_metrics", {}) for item in retrievals}, "timing_ms": {item["agent"]: item.get("timing_ms", {}) for item in retrievals}, "graph_neighborhoods": {item["agent"]: memory_bundles.get(item["agent"], {}).get("cognition_graph", {}) for item in retrievals}}, confidence=1.0, session_id=session_id, correlation_id=correlation_id, request_id=request_id, parent_event_id=routing_event.id)
            grounded_memory_result = self._handle_grounded_memory_bundle(user_input=user_input, modality=modality, context=context, policy_decision=policy_decision, session_id=session_id, correlation_id=correlation_id, request_id=request_id, request_started=request_started, progress_callback=progress_callback, query_event=query_event, retrieval_profile=retrieval_profile, analysis_mode=analysis_mode, memory_bundles=memory_bundles, suggested_agent=suggested_agent, candidate_agents=candidate_agents)
            if grounded_memory_result is not None:
                return grounded_memory_result
            self._emit_phase(progress_callback, request_id=request_id, session_id=session_id, phase="agent", agent=suggested_agent, message="Consulting specialist agents.", candidates=candidate_agents)
            reports = self._collect_agent_reports(user_input=user_input, context=context, session_id=session_id, correlation_id=correlation_id, request_id=request_id, candidate_agents=candidate_agents, agents=agents, memory_bundles=memory_bundles, route_scores=route_scores, report_callback=report_callback)
            self._emit_phase(progress_callback, request_id=request_id, session_id=session_id, phase="evaluation", agent="daise", message="Evaluating specialist reports.")
            for report in reports:
                report.score = round((0.45 * report.confidence) + (0.35 * report.routing_relevance) + (0.20 * report.trust_score), 3)
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
            grounded_conversation_override = self._grounded_conversation_override_for_generic_report(user_input=user_input, top_report=top_report, selected_agent=selected_agent, synthesized=synthesized, memory_bundles=memory_bundles)
            if grounded_conversation_override is not None:
                response_text = grounded_conversation_override["response"]
                response_confidence = round(max(response_confidence, grounded_conversation_override["confidence"]), 3)
                # Preserve the specialist routing decision for trace/provenance. D.A.I.S.E may
                # mediate or rewrite the final response without retroactively changing which
                # specialist was selected.
                output_agent = grounded_conversation_override["output_agent"]
                top_report.provider = grounded_conversation_override["provider"]
                top_report.model = grounded_conversation_override["model"]
                top_report.task_type = grounded_conversation_override["task_type"]
                top_report.backend_error = None
                top_report.backend_metadata = dict(grounded_conversation_override["backend_metadata"])
                top_report.raw_result = {**dict(top_report.raw_result or {}), "response": response_text, "provider": top_report.provider, "model": top_report.model, "task_type": top_report.task_type, "backend_error": None, "backend_metadata": top_report.backend_metadata}
                synthesis_notes = (f"{synthesis_notes} " if synthesis_notes else "") + "Replaced low-information generic specialist reply with grounded conversation context."
            response_text = self._wrap_final_response(response_text, policy_decision, selected_agent=selected_agent, synthesized=synthesized)
            evaluation_event = self.conversation_memory.persist_workflow_event(source="daise", event_type="agent_evaluation", payload={"selected_agent": selected_agent, "output_agent": output_agent, "synthesized": synthesized, "score_gap": score_gap, "reports": [report.to_dict() for report in reports], "synthesis_notes": synthesis_notes}, confidence=response_confidence, session_id=session_id, correlation_id=correlation_id, request_id=request_id)
            response_event = self.conversation_memory.persist_agent_response(session_id=session_id, correlation_id=correlation_id, agent_name=output_agent, response_text=response_text, confidence=response_confidence, modality=modality, parent_event_id=query_event.id if query_event else None, provider=top_report.provider, model=top_report.model, execution_time_ms=(time.time() - request_started) * 1000, success=True, extras={"selected_agent": selected_agent, "consulted_agents": candidate_agents, "synthesized": synthesized, "analysis_mode": analysis_mode, "retrieval_profile": retrieval_profile, "transport": context.get("transport")}, request_id=request_id)
            execution_time_ms = (time.time() - request_started) * 1000
            self.conversation_memory.persist_workflow_event(source="daise", event_type="request_outcome", payload={"schema_version": "1.0", "success": True, "selected_agent": selected_agent, "output_agent": output_agent, "execution_time_ms": execution_time_ms, "provider": top_report.provider, "model": top_report.model, "backend_error": top_report.backend_error, "fallback_used": False, "retrieval_profile": retrieval_profile, "analysis_mode": analysis_mode, "transport": context.get("transport"), "response_path": (top_report.backend_metadata or {}).get("response_path") or "live_model"}, confidence=response_confidence, session_id=session_id, correlation_id=correlation_id, request_id=request_id, parent_event_id=response_event.id)
            self._persist_latency_observation(request_id=request_id, session_id=session_id, correlation_id=correlation_id, selected_agent=selected_agent, output_agent=output_agent, report=top_report, execution_time_ms=execution_time_ms, context=context)
            operator_trace = self.conversation_memory.get_request_trace(request_id)
            self._emit_phase(progress_callback, request_id=request_id, session_id=session_id, phase="complete", agent=output_agent, message="Request completed.", selected_agent=selected_agent, output_agent=output_agent)
            return {"response": response_text, "confidence": response_confidence, "agent": output_agent, "output_agent": output_agent, "mediated_by": "daise", "selected_agent": selected_agent, "consulted_agents": candidate_agents, "synthesized": synthesized, "trace_event_id": evaluation_event.id, "provider": top_report.provider, "model": top_report.model, "backend_error": top_report.backend_error, "backend_metadata": top_report.backend_metadata, "task_type": top_report.task_type, "analysis_mode": analysis_mode, "routing_confidence": routing_confidence, "complexity_score": complexity_score, "multi_agent_mode": multi_agent_mode, "quality_evaluation": quality_evaluation, "policy_decision": policy_decision, "decision_state": policy_decision.get("decision_state"), "candidate_reports": [report.to_dict() for report in reports], "operator_trace": operator_trace, "session_id": session_id, "correlation_id": correlation_id, "request_id": request_id, "query_event_id": query_event.id if query_event else None, "selection_event_id": evaluation_event.id, "response_event_id": response_event.id, "summary_event_id": None, "execution_time_ms": execution_time_ms}
        except Exception:
            raise

    def _normalize_agent_report(self, agent_name: str, result: Dict[str, Any]) -> AgentReport:
        tools_invoked = result.get("tools_invoked") or []
        tool_activity_summary: List[str] = []
        for tool in tools_invoked:
            if isinstance(tool, dict):
                tool_activity_summary.append(str(tool.get("tool") or tool.get("tool_name") or tool.get("status") or "tool"))
            else:
                tool_activity_summary.append(str(tool))
        response = str(result.get("response") or "")
        task_type = str(result.get("task_type") or result.get("agent") or agent_name)
        evidence_summary = self._build_evidence_summary(result)
        return AgentReport(agent=agent_name, response=response, confidence=float(result.get("confidence", 0.0)), trace_event_id=result.get("trace_event_id"), tool_activity_summary=tool_activity_summary, provider=str(result.get("provider") or ""), model=str(result.get("model") or ""), evidence_summary=evidence_summary, task_type=task_type, backend_error=result.get("backend_error"), backend_metadata=dict(result.get("backend_metadata") or {}), raw_result=result)

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

    def _grounded_conversation_override_for_generic_report(self, *, user_input: str, top_report: AgentReport, selected_agent: str, synthesized: bool, memory_bundles: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if synthesized or selected_agent != "jarvis":
            return None
        normalized_provider = " ".join(str(top_report.provider or "").strip().lower().split())
        normalized_model = " ".join(str(top_report.model or "").strip().lower().split())
        normalized_response = " ".join(str(top_report.response or "").strip().lower().split())
        if normalized_provider not in {"heuristic", ""}:
            return None
        if normalized_model and normalized_model not in {"fake-jarvis", "heuristic", "fallback-heuristic"}:
            return None
        if not self._looks_like_low_information_agent_reply(normalized_response):
            return None
        primary_bundle = memory_bundles.get(selected_agent) or next(iter(memory_bundles.values()), {})
        if not isinstance(primary_bundle, dict):
            return None
        grounded = primary_bundle.get("grounded_conversation_answer")
        if not isinstance(grounded, dict):
            return None
        grounded_kind = " ".join(str(grounded.get("kind") or "").strip().lower().split())
        compact_query = " ".join(str(user_input or "").strip().lower().split())
        compact_query_tokens = set(self.conversation_memory._tokenize(compact_query))
        followup_flags = self.conversation_memory._conversational_followup_flags(compact_query)
        carry_over_phrase = (("carry" in compact_query or "carries" in compact_query) and "over" in compact_query)
        explanatory_followup = (grounded_kind == "followup" and self.conversation_memory._looks_like_structural_explanatory_followup(compact_query, compact_query_tokens)) or (grounded_kind == "followup" and any(phrase in compact_query for phrase in ("tell me more", "say more", "go on", "what do you mean", "what did you mean", "why do you say that", "why do you think that", "what makes you say that", "how so", "why is that", "why though", "expand on that", "elaborate", "continue that", "what else", "and then")))
        explicit_surface_followup = grounded_kind == "followup" and (bool(followup_flags.get("environment_followup")) or (carry_over_phrase and any(token in compact_query for token in ("there", "here", "across")))) and (carry_over_phrase or "across" in compact_query)
        if grounded_kind == "followup" and not (explicit_surface_followup or explanatory_followup):
            return None
        grounded_answer = str(grounded.get("answer") or "").strip()
        if not grounded_answer:
            return None
        return {"response": grounded_answer, "confidence": 0.9, "selected_agent": selected_agent, "output_agent": "daise", "provider": "conversation_memory", "model": "deterministic-conversation-grounded", "task_type": "grounded_conversation", "backend_metadata": {"grounded_conversation": True, "kind": grounded.get("kind"), "timing_ms": grounded.get("timing_ms", {}), "evidence_counts": grounded.get("evidence_counts", {}), "provenance": list(grounded.get("provenance") or []), "response_path": "grounded_conversation"}}
