from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..chat_backends import HeuristicChatBackend
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
    scores = {
        name: sum(1 for token in routing_keywords.get(name, ()) if str(token).lower() in lower)
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
        if not isinstance(item, Mapping):
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
    graph = bundle.get("cognition_graph")
    if isinstance(graph, Mapping) and graph:
        evidence.append({"kind": "cognition_graph", "value": dict(graph)})
    return evidence


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
        if isinstance(proposal, Mapping) and proposal.get("id"):
            proposal_id = str(proposal["id"])
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

    # Preserve identity semantics across the cutover. Compatibility aliases are
    # normalized internally but never become the visible assistant identity.
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

    selected_internal = _select_agent(
        user_input,
        agents=agents,
        routing_keywords=routing_keywords,
        agent_override=context.get("agent_override"),
        modality=modality,
    )
    visible_agent = "daise" if selected_internal == "jarvis" else selected_internal

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
    conversation = _normalize_conversation(bundle)
    evidence = _memory_evidence(bundle)

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
        system_prompt = (
            "You are D.A.I.S.E., the visible intelligence layer for O.B.E.O.S. "
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
        if reasoning_context.memory:
            messages.append({"role": "system", "content": "Retrieved evidence (may be irrelevant):\n" + _safe_json(reasoning_context.memory)})
        if reasoning_context.system_state:
            messages.append({"role": "system", "content": "Current OBEOS state when relevant:\n" + _safe_json(reasoning_context.system_state, max_chars=8000)})
        if reasoning_context.tool_results:
            messages.append({"role": "system", "content": "Tool results:\n" + _safe_json(reasoning_context.tool_results, max_chars=8000)})
        messages.append({"role": "user", "content": text})

        backend = build_routed_chat_backend(selected_internal, mode)
        options = backend_options_for_context({"analysis_mode": mode, "response_mode": mode}, selected_internal)
        try:
            generated = backend.generate(messages, **options)
            backend_meta = {
                "provider": getattr(generated, "provider", getattr(backend, "provider_name", "ollama")),
                "model": getattr(generated, "model", getattr(backend, "model", None)),
                "metadata": dict(getattr(generated, "metadata", {}) or {}),
                "backend_error": None,
            }
            return str(getattr(generated, "text", generated))
        except Exception as exc:
            fallback = HeuristicChatBackend(model="daise-model-unavailable")
            generated = fallback.generate(
                messages,
                backend_error=str(exc),
                fallback_reason="model_unavailable",
                provider=getattr(backend, "provider_name", "ollama"),
                model=getattr(backend, "model", None),
            )
            backend_meta = {
                "provider": getattr(generated, "provider", "heuristic"),
                "model": getattr(generated, "model", "daise-model-unavailable"),
                "metadata": dict(getattr(generated, "metadata", {}) or {}),
                "backend_error": str(exc),
            }
            return str(getattr(generated, "text", generated))

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
    confidence = 0.92 if provider == "ollama" else 0.64
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
        success=True,
        extras={
            "selected_agent": visible_agent,
            "runtime_agent": selected_internal,
            "consulted_agents": [visible_agent],
            "synthesized": False,
            "analysis_mode": mode,
            "reasoning_architecture": "model-context-memory-tools",
            "retrieval_is_evidence_only": True,
            "backend_metadata": backend_meta.get("metadata") or {},
        },
        request_id=request_id,
    )

    try:
        daise.conversation_memory.persist_workflow_event(
            source="daise",
            event_type="request_outcome",
            payload={
                "schema_version": "1.0",
                "success": True,
                "selected_agent": visible_agent,
                "runtime_agent": selected_internal,
                "provider": provider,
                "model": backend_meta.get("model"),
                "backend_error": backend_meta.get("backend_error"),
                "analysis_mode": mode,
                "reasoning_architecture": "model-context-memory-tools",
                "retrieval_is_evidence_only": True,
                "execution_time_ms": execution_time_ms,
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
        "agent": visible_agent,
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
        "backend_metadata": backend_meta.get("metadata") or {},
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
