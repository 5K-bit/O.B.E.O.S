from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from ..chat_backends import ChatBackendError
from ..identity_semantics import identity_registry
from ..model_routing import (
    backend_options_for_context,
    build_routed_chat_backend,
    detect_intent,
    normalize_response_mode,
)

ModelCallable = Callable[[str, "ReasoningContext"], str]
ToolCallable = Callable[[str, "ReasoningContext"], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class ReasoningContext:
    """Context supplied to a model. Retrieval is evidence, never the reasoning engine."""

    conversation: Sequence[Mapping[str, Any]] = ()
    memory: Sequence[Mapping[str, Any]] = ()
    system_state: Mapping[str, Any] = field(default_factory=dict)
    tool_results: Sequence[Mapping[str, Any]] = ()
    instructions: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReasoningPipeline:
    """Explicit DAISE v1 lane: model -> context -> memory -> tools."""

    model: ModelCallable
    tools: Mapping[str, ToolCallable] = field(default_factory=dict)

    def answer(
        self,
        user_input: str,
        *,
        conversation: Sequence[Mapping[str, Any]] = (),
        memory: Sequence[Mapping[str, Any]] = (),
        system_state: Mapping[str, Any] | None = None,
        requested_tools: Sequence[str] = (),
        instructions: Mapping[str, Any] | None = None,
    ) -> str:
        base = ReasoningContext(
            conversation=conversation,
            memory=memory,
            system_state=system_state or {},
            instructions=instructions or {},
        )
        results: list[Mapping[str, Any]] = []
        for name in requested_tools:
            tool = self.tools.get(name)
            if tool is None:
                results.append({"tool": name, "ok": False, "error": "tool_unavailable"})
                continue
            try:
                result = dict(tool(user_input, base))
                results.append({"tool": name, "ok": True, **result})
            except Exception as exc:
                results.append({"tool": name, "ok": False, "error": str(exc)})
        context = ReasoningContext(
            conversation=conversation,
            memory=memory,
            system_state=system_state or {},
            tool_results=tuple(results),
            instructions=instructions or {},
        )
        response = self.model(user_input, context)
        if not isinstance(response, str) or not response.strip():
            raise RuntimeError("DAISE model lane returned no usable response")
        return response.strip()


def _select_agent(
    user_input: str,
    *,
    agents: Mapping[str, Any],
    routing_keywords: Mapping[str, Sequence[str]],
    agent_override: str | None,
    modality: str,
) -> str:
    override = str(agent_override or "").strip().lower()
    alias_map = {"daise": "jarvis", "d.a.i.s.e": "jarvis", "coding_agent": "coding", "voice_agent": "voice"}
    override = alias_map.get(override, override)
    if override and override in agents:
        return override
    if modality == "code" and "coding" in agents:
        return "coding"
    if modality == "voice" and "voice" in agents:
        return "voice"
    lower = user_input.lower()
    from .core import DAISE
    scores = {
        name: (0 if name == "lilo" and not DAISE._is_training_intent(lower) else
               sum(1 for token in routing_keywords.get(name, ()) if str(token).lower() in lower))
        for name in agents
    }
    if scores and max(scores.values()) > 0:
        return max(scores, key=scores.get)
    if "jarvis" in agents:
        return "jarvis"
    return next(iter(agents))


def _normalize_conversation(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(bundle.get("session_messages") or [])[-12:]:
        if not isinstance(item, Mapping) or item.get("prompt_eligible") is False:
            continue
        role = str(item.get("role") or item.get("speaker") or item.get("source") or "user").lower()
        if role in {"daise", "jarvis", "assistant", "agent"}:
            role = "assistant"
        elif role != "system":
            role = "user"
        text = item.get("content") or item.get("text") or item.get("response") or item.get("message")
        if text:
            rows.append({"role": role, "content": str(text)[:4000]})
    return rows


def _memory_evidence(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for key, limit in (
        ("global_memories", 6),
        ("session_summaries", 4),
        ("knowledge_hits", 6),
        ("recent_events", 6),
    ):
        values = bundle.get(key) or []
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        for value in list(values)[:limit]:
            if isinstance(value, Mapping):
                evidence.append({"kind": key, **dict(value)})
            else:
                evidence.append({"kind": key, "value": str(value)})
    for key in ("grounded_memory_answer", "grounded_knowledge_answer", "grounded_conversation_answer"):
        grounded = bundle.get(key)
        if isinstance(grounded, Mapping) and str(grounded.get("answer") or "").strip():
            evidence.append({"kind": key, "frame": grounded.get("kind"), "answer": str(grounded["answer"])})
    graph = bundle.get("cognition_graph")
    if isinstance(graph, Mapping) and graph:
        evidence.append({"kind": "cognition_graph", "value": dict(graph)})
    return evidence


def _backend_error_type(backend_meta: Mapping[str, Any]) -> str | None:
    """The backend's own failure class (e.g. ``endpoint_not_ready``), not its message."""
    metadata = backend_meta.get("metadata") or {}
    primary = metadata.get("primary_backend_metadata") or {}
    value = primary.get("error_type") or metadata.get("error_type")
    return str(value) if value else None


def _safe_json(value: Any, *, max_chars: int = 14000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text[:max_chars]


def _legacy_file_result(daise: Any, *, user_input: str, modality: str, context: dict[str, Any], agents: Mapping[str, Any], routing_keywords: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Retain the mature governed mutation flow during the reasoning cutover."""
    result = daise.process_request_legacy(
        user_input=user_input,
        modality=modality,
        context=context,
        agents=dict(agents),
        routing_keywords=dict(routing_keywords),
    )
    workflow = result.get("file_workflow") if isinstance(result, dict) else None
    if isinstance(workflow, Mapping) and workflow.get("success"):
        proposal = workflow.get("proposal")
        if isinstance(proposal, Mapping):
            raw_proposal_id = proposal.get("proposal_id") or proposal.get("id")
            if raw_proposal_id:
                proposal_id = str(raw_proposal_id)
                path = proposal.get("path")
                result["response"] = (
                    f"Proposal ID: {proposal_id}\n"
                    f"Path: {path}\n"
                    "The change is proposed only; review/validation/approval are still required before apply."
                )
    return result


def process_request_v1(
    daise: Any,
    *,
    user_input: str,
    modality: str = "text",
    context: dict[str, Any] | None = None,
    agents: Mapping[str, Any] | None = None,
    routing_keywords: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """DAISE v1 request lane.

    Conversational answers are generated by the model. Retrieval/cognition are context.
    Governed file mutation remains on the existing proposal/validation/approval/rollback
    path until that workflow is migrated independently.
    """
    if not agents:
        raise ValueError("D.A.I.S.E requires at least one model/agent runtime")

    context = dict(context or {})
    routing_keywords = dict(routing_keywords or {})
    request_started = time.time()
    session_id = context.get("session_id") or daise.conversation_memory.create_session_id()
    correlation_id = context.get("correlation_id") or daise.conversation_memory.create_correlation_id()
    request_id = context.get("request_id") or str(uuid.uuid4())[:8]
    mode = normalize_response_mode(context.get("analysis_mode") or context.get("response_mode"))
    policy_decision = {"decision_state": "approve", "reason": "v1_cutover"}
    intent = detect_intent(user_input)

    if intent.file_ops and hasattr(daise, "process_request_legacy"):
        return _legacy_file_result(
            daise,
            user_input=user_input,
            modality=modality,
            context=context,
            agents=agents,
            routing_keywords=routing_keywords,
        )

    resolutions = identity_registry.resolve_mentions(user_input)
    if context.get("agent_override"):
        resolutions.append(identity_registry.resolve(str(context["agent_override"])))
    for resolution in resolutions:
        if resolution.input_name != resolution.canonical_identity or resolution.resolution_method != "canonical":
            identity_registry.emit_resolution_event(
                resolution,
                memory_service=daise.conversation_memory,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
            )

    try:
        memory_query_kind = daise.conversation_memory._detect_grounded_memory_query(user_input) or ""
        prior_session_context = bool(daise.conversation_memory.get_session_messages(str(session_id), limit=1))
        prompt_tokens = set(daise.conversation_memory._tokenize(user_input))
    except Exception:
        memory_query_kind = ""
        prior_session_context = False
        prompt_tokens = set(str(user_input or "").lower().split())
    explicit_identity_query = bool(prompt_tokens.intersection({"identity", "name", "daise", "assistant", "stand", "call", "called"}))
    # Deterministic runtime facts (clock, status, work ledger) are evidence for the
    # model, never the reply. ``allow_deterministic_evidence=False`` skips them.
    grounded_evidence_allowed = context.get("allow_deterministic_evidence", True) is not False
    truth_probe = getattr(daise, "_grounded_truth_response", None)
    probed_truth: dict[str, Any] | None = None
    try:
        if callable(truth_probe) and grounded_evidence_allowed:
            probed = truth_probe(user_input, session_id=session_id)
            probed_truth = dict(probed) if isinstance(probed, Mapping) else None
        probed_truth_kind = str((probed_truth or {}).get("kind") or "")
    except Exception:
        probed_truth = None
        probed_truth_kind = ""
    defer_memory_system_to_recall = bool(
        memory_query_kind
        and probed_truth_kind == "memory_system"
        and prompt_tokens.intersection({"work", "recent", "memory"})
    )
    defer_truth_to_memory = bool(
        prior_session_context
        and not explicit_identity_query
        and (probed_truth_kind == "identity" or defer_memory_system_to_recall)
    )

    runtime_truth = None if defer_truth_to_memory else probed_truth

    continuity_routing_hint = None
    continuity_hint_builder = getattr(daise, "_continuity_routing_hint", None)
    if callable(continuity_hint_builder):
        continuity_routing_hint = continuity_hint_builder(
            session_id=session_id,
            user_input=user_input,
            analysis_mode=mode,
            retrieval_profile=context.get("retrieval_profile"),
        )
    route_scores: dict[str, float] = {}
    route_score_builder = getattr(daise, "_route_agent_scores", None)
    if callable(route_score_builder):
        route_scores = dict(
            route_score_builder(
                user_input=user_input,
                modality=modality,
                agent_names=list(agents.keys()),
                routing_keywords=routing_keywords,
                agent_override=context.get("agent_override"),
                continuity_hint=continuity_routing_hint,
            )
        )
        selected_internal = max(route_scores, key=route_scores.get) if route_scores else ""
    else:
        selected_internal = _select_agent(
            user_input,
            agents=agents,
            routing_keywords=routing_keywords,
            agent_override=context.get("agent_override"),
            modality=modality,
        )
        if intent.coding and "coding" in agents:
            selected_internal = "coding"
        route_scores = {name: 1.0 if name == selected_internal else 0.0 for name in agents}
    if not selected_internal:
        selected_internal = _select_agent(
            user_input,
            agents=agents,
            routing_keywords=routing_keywords,
            agent_override=context.get("agent_override"),
            modality=modality,
        )
    routing_confidence = round(float(route_scores.get(selected_internal, 1.0)), 3)
    visible_agent = "daise" if selected_internal == "jarvis" else selected_internal
    task_type_hint = {
        "coding": "coding_help",
        "jarvis": "general_conversation",
        "lilo": "lilo",
        "stitch": "learning",
        "voice": "voice",
    }.get(selected_internal, selected_internal)

    daise.conversation_memory.persist_workflow_event(
        source="daise",
        event_type="routing_decision",
        payload={
            "schema_version": "1.0",
            "query": user_input,
            "modality": modality,
            "analysis_mode": mode,
            "agent_override": context.get("agent_override"),
            "suggested_agent": selected_internal,
            "selected_agent": visible_agent,
            "candidates": [
                {"agent": agent_name, "routing_relevance": round(float(score), 3)}
                for agent_name, score in route_scores.items()
            ],
            "routing_confidence": routing_confidence,
            "continuity_routing_hint": continuity_routing_hint,
        },
        confidence=routing_confidence,
        session_id=session_id,
        correlation_id=correlation_id,
        request_id=request_id,
    )
    daise.conversation_memory.persist_workflow_event(
        source="daise",
        event_type="agent_selection",
        payload={
            "schema_version": "1.0",
            "selected_agent": selected_internal,
            "visible_agent": visible_agent,
            "output_agent": "daise",
            "task_type": task_type_hint,
            "synthesized": False,
            "routing_confidence": routing_confidence,
        },
        confidence=routing_confidence,
        session_id=session_id,
        correlation_id=correlation_id,
        request_id=request_id,
    )

    query_event = daise.conversation_memory.persist_user_turn(
        session_id=session_id,
        correlation_id=correlation_id,
        text=user_input,
        modality=modality,
        routed_agent=visible_agent,
        analysis_mode=mode,
        agent_override=context.get("agent_override"),
        request_id=request_id,
    )

    bundle = daise.conversation_memory.build_context(
        session_id=session_id,
        query=user_input,
        agent_name=visible_agent,
        analysis_mode=mode,
        skip_vector_search=bool(context.get("fast_path")),
        retrieval_profile=context.get("retrieval_profile"),
        request_id=request_id,
        transport=context.get("transport"),
    )

    if daise.memory_chamber is not None and mode == "deep":
        try:
            bundle["cognition_graph"] = daise.memory_chamber.reasoning_context(
                user_input,
                session_id=session_id,
                request_id=request_id,
                limit=8,
                profile="deep",
            )
        except Exception:
            bundle["cognition_graph"] = {}

    conversation = _normalize_conversation(bundle)
    evidence = _memory_evidence(bundle)
    if not grounded_evidence_allowed:
        evidence = [item for item in evidence if not str(item.get("kind", "")).startswith("grounded_")]

    graph_bundle = bundle.get("cognition_graph") or {}
    retrieval_profile = bundle.get("retrieval_profile") or context.get("retrieval_profile") or mode
    try:
        retrieval_event = daise.conversation_memory.persist_workflow_event(
            source="daise",
            event_type="memory_retrieval",
            payload={
                "schema_version": "1.0",
                "query": user_input,
                "selected_candidates": [selected_internal],
                "retrieval_profile": retrieval_profile,
                "analysis_mode": mode,
                "transport": context.get("transport"),
                "session_messages": len(bundle.get("session_messages") or []),
                "session_summaries": len(bundle.get("session_summaries") or []),
                "global_memories": len(bundle.get("global_memories") or []),
                "recent_events": len(bundle.get("recent_events") or []),
                "knowledge_hits": len(bundle.get("knowledge_hits") or []),
                "knowledge_citations": len(bundle.get("citations") or []),
                "graph_neighborhoods": {
                    selected_internal: {
                        "matched_nodes": graph_bundle.get("matched_nodes") or [],
                        "semantic_hits": graph_bundle.get("semantic_hits") or [],
                        "timing_ms": graph_bundle.get("timing_ms") or {},
                    }
                },
            },
            confidence=1.0,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            parent_event_id=query_event.id,
        )
        daise.conversation_memory.persist_workflow_event(
            source="daise",
            event_type="memory_retrieval_observation",
            payload={
                "schema_version": "1.0",
                "retrieval_profile": retrieval_profile,
                "analysis_mode": mode,
                "transport": context.get("transport"),
                "selected_candidates": [selected_internal],
                "knowledge_hits": len(bundle.get("knowledge_hits") or []),
                "graph_matches": len(graph_bundle.get("matched_nodes") or []) + len(graph_bundle.get("semantic_hits") or []),
                "timing_ms": graph_bundle.get("timing_ms") or {},
            },
            confidence=0.9,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            parent_event_id=retrieval_event.id,
        )
    except Exception:
        pass

    system_state: dict[str, Any] = {}
    if isinstance(context.get("system_state"), Mapping):
        system_state.update(dict(context["system_state"]))
    if daise.awareness_service is not None and not context.get("fast_path"):
        try:
            snapshot = daise.awareness_service.snapshot()
            if isinstance(snapshot, Mapping):
                system_state.update(dict(snapshot))
        except Exception:
            pass

    backend_meta: dict[str, Any] = {}

    def model_callable(text: str, reasoning_context: ReasoningContext) -> str:
        nonlocal backend_meta
        from ..daise_identity import IDENTITY_AND_TRUTH_PROMPT, runtime_truth_message
        from ..cancellation import RequestCancelled
        system_prompt = (
            IDENTITY_AND_TRUTH_PROMPT +
            "Reason with the model first. Retrieved evidence is optional context, not an answer. "
            "Ignore irrelevant retrievals. Never copy a retrieved passage merely because it matched. "
            "For ordinary knowledge or arithmetic, answer from model reasoning when no external evidence is needed. "
            "Use memory only when relevant to continuity, user facts, project state, or grounded evidence."
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(
            {"role": str(row.get("role") or "user"), "content": str(row.get("content") or "")}
            for row in reasoning_context.conversation
            if row.get("content")
        )
        if len(messages) > 1 and messages[-1] == {"role": "user", "content": text}:
            messages.pop()  # The current user turn is appended after evidence.
        if reasoning_context.memory:
            messages.append({"role": "system", "content": "Retrieved evidence (may be irrelevant):\n" + _safe_json(reasoning_context.memory)})
        if reasoning_context.system_state:
            messages.append({"role": "system", "content": "Current OBEOS state when relevant:\n" + _safe_json(reasoning_context.system_state, max_chars=8000)})
        if reasoning_context.tool_results:
            messages.append({"role": "system", "content": "Tool results:\n" + _safe_json(reasoning_context.tool_results, max_chars=8000)})
        truth_message = runtime_truth_message(runtime_truth)
        if truth_message is not None:
            messages.append(truth_message)
        messages.append({"role": "user", "content": text})

        backend = build_routed_chat_backend(selected_internal, mode)
        options = backend_options_for_context({"analysis_mode": mode, "response_mode": mode}, selected_internal)
        try:
            stream_callback = context.get("stream_callback")
            if stream_callback is not None and hasattr(backend, "generate_stream"):
                generated = backend.generate_stream(messages, on_token=stream_callback, **options)
            else:
                generated = backend.generate(messages, **options)
            backend_meta = {
                "provider": getattr(generated, "provider", getattr(backend, "provider_name", "ollama")),
                "model": getattr(generated, "model", getattr(backend, "model", None)),
                "metadata": dict(getattr(generated, "metadata", {}) or {}),
                "backend_error": None,
            }
            return str(getattr(generated, "text", generated))
        except RequestCancelled:
            raise
        except Exception as exc:
            backend_meta = {
                "provider": getattr(backend, "provider_name", "ollama"),
                "model": getattr(backend, "model", None),
                "metadata": {"response_path": "model_failure"},
                "backend_error": str(exc),
            }
            if isinstance(exc, ChatBackendError):
                raise
            raise ChatBackendError(
                "D.A.I.S.E model generation failed; no conversational fallback is available",
                metadata={"response_path": "model_failure", "cause": str(exc)[:500]},
            ) from exc

    specialist_result = None
    specialist = agents.get(selected_internal)

    if specialist is not None and hasattr(specialist, "process"):
        try:
            specialist_result = specialist.process(
                user_input,
                context={
                    **context,
                    "session_id": session_id,
                    "correlation_id": correlation_id,
                    "request_id": request_id,
                    "memory_context": bundle,
                    "runtime_truth": runtime_truth,
                    "daise_selected_candidates": [selected_internal],
                },
            )
        except Exception:
            specialist_result = None

    grounded_conversation_override = getattr(daise, "_grounded_conversation_override_for_generic_report", None)
    if callable(grounded_conversation_override) and isinstance(specialist_result, Mapping):
        override = grounded_conversation_override(
            user_input=user_input,
            top_report=SimpleNamespace(
                provider=specialist_result.get("provider"),
                model=specialist_result.get("model"),
                response=specialist_result.get("response"),
                backend_metadata=specialist_result.get("backend_metadata"),
                backend_error=specialist_result.get("backend_error"),
            ),
            selected_agent=selected_internal,
            synthesized=False,
            memory_bundles={selected_internal: bundle},
        )
        if override is not None:
            specialist_result = {
                **dict(specialist_result),
                "response": override.get("response"),
                "confidence": override.get("confidence", specialist_result.get("confidence")),
                "provider": override.get("provider"),
                "model": override.get("model"),
                "task_type": override.get("task_type"),
                "backend_metadata": dict(override.get("backend_metadata") or {}),
                "backend_error": None,
            }

    if isinstance(specialist_result, Mapping) and str(specialist_result.get("response") or "").strip():
        response_text = str(specialist_result["response"]).strip()

        for prefix in (
            "CODINGAGENT:",
            "CODING AGENT:",
            "JARVIS:",
            "LILO:",
            "STITCH:",
        ):
            if response_text.upper().startswith(prefix):
                response_text = response_text[len(prefix):].lstrip()
                break

        backend_meta = {
            "provider": specialist_result.get("provider") or "specialist",
            "model": specialist_result.get("model") or selected_internal,
            "metadata": dict(specialist_result.get("backend_metadata") or {}),
            "backend_error": specialist_result.get("backend_error"),
        }
    else:
        pipeline = ReasoningPipeline(model=model_callable)
        response_text = pipeline.answer(
            user_input,
            conversation=conversation,
            memory=evidence,
            system_state=system_state,
            requested_tools=(),
            instructions={"analysis_mode": mode, "selected_runtime": selected_internal},
        )

    provider = str(backend_meta.get("provider") or "unknown")
    completion_metadata = dict(backend_meta.get("metadata") or {})
    if not backend_meta.get("backend_error") and (
        completion_metadata.get("response_complete") is False
        or completion_metadata.get("finish_reason") == "length"
    ):
        backend_meta["backend_error"] = "Model response is incomplete: output limit or interrupted completion"
    response_success = not bool(backend_meta.get("backend_error"))
    confidence = (0.92 if provider == "ollama" else 0.64) if response_success else 0.0
    execution_time_ms = (time.time() - request_started) * 1000
    response_event = daise.conversation_memory.persist_agent_response(
        session_id=session_id,
        correlation_id=correlation_id,
        agent_name=visible_agent,
        response_text=response_text,
        confidence=confidence,
        modality=modality,
        parent_event_id=query_event.id,
        provider=provider,
        model=str(backend_meta.get("model") or "unknown"),
        execution_time_ms=execution_time_ms,
        success=response_success,
        extras={
            "selected_agent": visible_agent,
            "runtime_agent": selected_internal,
            "consulted_agents": [visible_agent],
            "synthesized": False,
            "analysis_mode": mode,
            "reasoning_architecture": "model-context-memory-tools",
            "retrieval_is_evidence_only": True,
            "backend_error": backend_meta.get("backend_error"),
            "backend_metadata": backend_meta.get("metadata") or {},
        },
        request_id=request_id,
    )

    backend_metadata = dict(backend_meta.get("metadata") or {})
    latency_payload = {
        "schema_version": "1.0",
        "request_id": request_id,
        "selected_agent": visible_agent,
        "runtime_agent": selected_internal,
        "output_agent": "daise",
        "provider": provider,
        "model": backend_meta.get("model"),
        "analysis_mode": mode,
        "retrieval_profile": retrieval_profile,
        "transport": context.get("transport"),
        "execution_time_ms": execution_time_ms,
        "backend_error": backend_meta.get("backend_error"),
        "fallback_used": bool(backend_meta.get("backend_error")),
        "selected_model": backend_metadata.get("selected_model") or backend_meta.get("model"),
        "model_chain": backend_metadata.get("model_chain") or [backend_meta.get("model")],
        "effective_timeout_s": backend_metadata.get("effective_timeout_s"),
        "streamed": backend_metadata.get("streamed", False),
        "response_path": backend_metadata.get("response_path") or "live_model",
        "fallback_trigger": backend_metadata.get("fallback_trigger"),
        "live_lane_attempted": bool(backend_metadata.get("live_lane_attempted", False)),
        "fallback_mode": backend_metadata.get("fallback_mode") or "none",
        "prompt_metrics": backend_metadata.get("prompt_metrics", {}),
        "ollama_attempts": backend_metadata.get("ollama_attempts"),
        "wall_time_ms": backend_metadata.get("wall_time_ms"),
        "ollama_total_duration_ms": backend_metadata.get("ollama_total_duration_ms"),
        "load_duration_ms": backend_metadata.get("load_duration_ms"),
        "prompt_eval_count": backend_metadata.get("prompt_eval_count"),
        "eval_count": backend_metadata.get("eval_count"),
        "timeout_reason": backend_metadata.get("timeout_reason"),
        "fallback_reason": backend_metadata.get("fallback_reason"),
    }

    try:
        observe_truth = getattr(daise, "_persist_runtime_truth_observation", None)
        if runtime_truth and callable(observe_truth):
            observe_truth(
                runtime_truth,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                parent_event_id=query_event.id,
            )
    except Exception:
        pass

    try:
        daise.conversation_memory.persist_workflow_event(
            source="daise",
            event_type="latency_observation",
            payload=latency_payload,
            confidence=confidence,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            parent_event_id=response_event.id,
        )

        daise.conversation_memory.persist_workflow_event(
            source="daise",
            event_type="request_outcome",
            payload={
                "schema_version": "1.0",
                "success": response_success,
                "selected_agent": visible_agent,
                "runtime_agent": selected_internal,
                "provider": provider,
                "model": backend_meta.get("model"),
                "backend_error": backend_meta.get("backend_error"),
                "analysis_mode": mode,
                "retrieval_profile": retrieval_profile,
                "transport": context.get("transport"),
                "reasoning_architecture": "model-context-memory-tools",
                "retrieval_is_evidence_only": True,
                "execution_time_ms": execution_time_ms,
                "fallback_used": latency_payload["fallback_used"],
                "response_path": latency_payload["response_path"],
                "live_lane_attempted": latency_payload["live_lane_attempted"],
                "fallback_mode": latency_payload["fallback_mode"],
                "selected_model": latency_payload["selected_model"],
                "model_chain": latency_payload["model_chain"],
                "effective_timeout_s": latency_payload["effective_timeout_s"],
                "grounded_truth_kind": (runtime_truth or {}).get("kind"),
            },
            confidence=confidence,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            parent_event_id=response_event.id,
        )
    except Exception:
        pass

    return {
        "response": response_text,
        "confidence": confidence,
        "agent": "daise",
        "output_agent": "daise",
        "mediated_by": "daise",
        "selected_agent": visible_agent,
        "runtime_agent": selected_internal,
        "consulted_agents": [visible_agent],
        "synthesized": False,
        "trace_event_id": response_event.id,
        "provider": provider,
        "model": backend_meta.get("model"),
        "backend_error": backend_meta.get("backend_error"),
        "error": backend_meta.get("backend_error"),
        "error_type": _backend_error_type(backend_meta) if backend_meta.get("backend_error") else None,
        "backend_metadata": backend_meta.get("metadata") or {},
        "runtime_truth": (
            {key: runtime_truth.get(key) for key in ("kind", "source", "observation")}
            if runtime_truth else None
        ),
        "task_type": "model_reasoning",
        "analysis_mode": mode,
        "reasoning_architecture": "model-context-memory-tools",
        "retrieval_is_evidence_only": True,
        "routing_confidence": 1.0,
        "multi_agent_mode": False,
        "candidate_reports": [],
        "session_id": session_id,
        "correlation_id": correlation_id,
        "request_id": request_id,
        "query_event_id": query_event.id,
        "response_event_id": response_event.id,
        "execution_time_ms": execution_time_ms,
    }


def install_daise_v1_cutover(daise_cls: type[Any]) -> None:
    """Install the v1 request lane while retaining the old method for bounded compatibility."""
    if getattr(daise_cls, "_obeos_v1_cutover_installed", False):
        return
    daise_cls.process_request_legacy = daise_cls.process_request
    daise_cls.process_request = process_request_v1
    daise_cls._obeos_v1_cutover_installed = True
