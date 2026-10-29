"""
JARVIS Agent Wrapper

Wraps the local JARVIS assistant to conform to the Agent interface.
Enables JARVIS to participate in the Blackfong multi-agent system.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Dict, List, Optional

from blackfong_ecosystem.agent import Agent, AgentType, ReasoningStep, ReasoningTrace, ToolRequest, ToolStatus
from blackfong_ecosystem.chat_backends import (
    BaseChatBackend,
    ChatBackendError,
    ChatBackendResult,
    HeuristicChatBackend,
    build_agent_chat_backend,
)
from blackfong_ecosystem.config import chat_settings
from blackfong_ecosystem.daise_identity import (
    IDENTITY_AND_TRUTH_PROMPT,
    grounded_answer_message,
    runtime_truth_message,
)
from blackfong_ecosystem.history_budget import estimated_tokens, recent_history
from blackfong_ecosystem.conversation_memory import conversation_memory_service
from blackfong_ecosystem.model_routing import (
    backend_options_for_context,
    build_routed_chat_backend,
    model_chain_for_agent,
    normalize_response_mode,
)
from blackfong_ecosystem.event_client import EventClient
from blackfong_ecosystem.tool_dispatcher import ToolDispatcher


# Raw transcript messages kept for quick-mode turns (still token-budgeted).
QUICK_TRANSCRIPT_FLOOR = 6


class JARVISAgent(Agent):
    """
    JARVIS as a standardized Agent.
    
    Implements the Agent interface to allow JARVIS to:
    - Process user queries
    - Invoke tools via the event system
    - Emit reasoning traces
    - Participate in multi-agent coordination
    """
    
    def __init__(
        self,
        event_client: Optional[EventClient] = None,
        event_api_url: str = "http://localhost:8000",
        chat_backend: Optional[BaseChatBackend] = None,
        fallback_backend: Optional[BaseChatBackend] = None,
    ):
        super().__init__(
            name="jarvis",
            agent_type=AgentType.REASONING,
            event_client=event_client,
            event_api_url=event_api_url,
        )
        self.dispatcher = ToolDispatcher(event_client=event_client)
        self.session_id = str(uuid.uuid4())
        self._chat_backend_explicit = chat_backend is not None
        self.chat_backend = chat_backend or build_agent_chat_backend("jarvis")
        self.fallback_backend = fallback_backend or HeuristicChatBackend()
    
    def process(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process user input and return a response with reasoning trace.
        
        Args:
            user_input: User's query
            context: Optional context dict
        
        Returns:
            {
                "response": str,
                "reasoning_trace": {...},
                "confidence": float,
                "tools_invoked": [...],
                "trace_event_id": str or None,
            }
        """
        timestamp_ms = int(time.time() * 1000)
        trace = ReasoningTrace(
            agent_name=self.name,
            user_input=user_input,
            timestamp=timestamp_ms,
        )
        
        tools_invoked: List[str] = []
        active_session_id = (context or {}).get("session_id") or self.session_id
        memory_context = (context or {}).get("memory_context", {})
        
        try:
            # Step 1: Parse user input
            step_1 = ReasoningStep(
                step_id="parse",
                action="parse_input",
                input=user_input,
                output={"parsed": True, "intent": "general_query"},
                confidence=0.95,
            )
            trace.steps.append(step_1)

            # Step 2: Record available conversation memory
            step_2 = ReasoningStep(
                step_id="memory_context",
                action="hydrate_conversation_memory",
                input={"session_id": active_session_id},
                output={
                    "session_messages": len(memory_context.get("session_messages", [])),
                    "global_memories": len(memory_context.get("global_memories", [])),
                    "session_summaries": len(memory_context.get("session_summaries", [])),
                    "prompt_metrics": memory_context.get("prompt_metrics", {}),
                },
                confidence=0.92,
            )
            trace.steps.append(step_2)

            # Step 3: Generate response with the local chat backend
            response_text, backend_info = self._generate_response(user_input, context or {})
            response_text, sanitization = self._sanitize_visible_response(response_text, user_input)
            backend_info = dict(backend_info)
            backend_metadata = dict(backend_info.get("metadata") or {})
            backend_metadata.update(sanitization)
            backend_info["metadata"] = backend_metadata
            
            step_3 = ReasoningStep(
                step_id="generate_response",
                action="generate_text_response",
                input={"user_input": user_input},
                output={
                    "response": response_text,
                    "provider": backend_info.get("provider"),
                    "model": backend_info.get("model"),
                    "backend_error": backend_info.get("backend_error"),
                    "metadata": backend_info.get("metadata"),
                },
                confidence=0.0 if backend_info.get("backend_error") else (0.92 if backend_info.get("provider") == "ollama" else 0.68),
            )
            trace.steps.append(step_3)

            trace.final_decision = response_text
            trace.overall_confidence = 0.0 if backend_info.get("backend_error") else (0.92 if backend_info.get("provider") == "ollama" else 0.68)
            
            # Emit reasoning trace as an event
            correlation_id = context.get("correlation_id") if context else None
            trace_event_id = self.emit_reasoning_trace(
                trace=trace,
                session_id=active_session_id,
                correlation_id=correlation_id,
                request_id=context.get("request_id") if context else None,
            )
            
            return {
                "response": response_text,
                "reasoning_trace": trace.to_dict(),
                "confidence": trace.overall_confidence,
                "tools_invoked": tools_invoked,
                "trace_event_id": trace_event_id,
                "task_type": "general_conversation",
                "provider": backend_info.get("provider"),
                "model": backend_info.get("model"),
                "backend_error": backend_info.get("backend_error"),
                "error": backend_info.get("backend_error"),
                "backend_metadata": backend_info.get("metadata"),
                "response_path": (backend_info.get("metadata") or {}).get("response_path") or "unknown",
                "agent": self.name,
            }
        
        except Exception as e:
            return {
                "response": f"Error processing request: {str(e)}",
                "reasoning_trace": trace.to_dict(),
                "confidence": 0.0,
                "tools_invoked": [],
                "trace_event_id": None,
                "error": str(e),
                "agent": self.name,
            }

    def _sanitize_visible_response(self, response_text: str, user_input: str) -> tuple[str, Dict[str, Any]]:
        """Keep internal fallback/source artifacts out of the operator transcript."""
        text = str(response_text or "").strip()
        lowered = text.lower()
        raw_markers = ("[source ", "kind=knowledge_collection", "[page 1]", "raw ocr")
        if any(marker in lowered for marker in raw_markers):
            return (
                "I found local material related to this question, but the retrieved extract was not readable enough to present reliably. Please rephrase the topic or ask me to inspect that collection directly.",
                {"response_sanitized": True, "sanitization_reason": "raw_retrieval_artifact"},
            )
        # These phrases are implementation diagnostics and should not become
        # D.A.I.S.E.'s conversational voice when a backend answer is usable.
        if not any(token in str(user_input or "").lower() for token in ("trace", "diagnos", "fallback", "backend")):
            text = re.sub(
                r"\s*The live model lane missed this turn, so I am answering from bounded local O\.B\.E\.O\.S\. memory and knowledge instead\.?\s*$",
                "",
                text,
                flags=re.IGNORECASE,
            ).strip()
            text = re.sub(r"\s*I still have our recent work around .*?(?=\.|$)", "", text, flags=re.IGNORECASE).strip()
        identity_replacements = (
            "Digital Assistant for Intelligent Systems and Expertise",
            "Distributed Assistant for Innovative Solutions and Expertise",
        )
        if any(phrase.lower() in lowered for phrase in identity_replacements):
            text = re.sub(
                r"Digital Assistant for Intelligent Systems and Expertise|Distributed Assistant for Innovative Solutions and Expertise",
                "Direct Artificial Intelligence System Environment",
                text,
                flags=re.IGNORECASE,
            )
        return text or "I'm here and ready to help. What would you like to work through?", {
            "response_sanitized": text != str(response_text or "").strip(),
            "sanitization_reason": "internal_phrase_cleanup" if text != str(response_text or "").strip() else None,
        }
    
    def _generate_response(self, user_input: str, context: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """Generate a response with the configured local backend and safe fallback."""
        messages, prompt_metrics = self._build_chat_messages(user_input, context)
        generation_mode = self._effective_generation_mode(user_input, context)
        backend_context = dict(context)
        backend_context["analysis_mode"] = generation_mode
        backend_context["response_mode"] = generation_mode
        backend_options = self._backend_options(backend_context)
        requested_mode = normalize_response_mode(context.get("analysis_mode") or context.get("response_mode"))
        chat_backend = self.chat_backend if self._chat_backend_explicit else build_routed_chat_backend("jarvis", generation_mode)
        backend_health = self._resolve_backend_health(context)
        recovery_probe_attempted = self._allow_conversational_recovery_probe(
            backend_health,
            user_input=user_input,
            context=context,
            prompt_metrics=prompt_metrics,
            generation_mode=generation_mode,
        ) or (
            str(backend_health.get("state") or "").strip().lower() == "degraded"
            and bool(backend_health.get("allow_recovery_probe"))
        )
        backend_options = self._apply_backend_health_overrides(backend_options, backend_health)
        backend_chain = self._backend_chain_preview(chat_backend, generation_mode, backend_options)
        if self._should_force_fallback(
            backend_health,
            user_input=user_input,
            context=context,
            prompt_metrics=prompt_metrics,
            generation_mode=generation_mode,
        ):
            bounded_fallback = self._bounded_context_fallback_response(
                user_input=user_input,
                context=context,
                backend_error=self._backend_health_reason(backend_health),
                primary_provider=getattr(chat_backend, "provider_name", None),
            )
            generic_fallback_used = bounded_fallback is None
            backend_result = bounded_fallback or self.fallback_backend.generate(
                messages,
                backend_error=self._backend_health_reason(backend_health),
                fallback_reason=self._backend_health_reason(backend_health),
                provider=getattr(chat_backend, "provider_name", "ollama"),
                model=getattr(chat_backend, "model", None),
                backend_health=backend_health,
            )
            metadata = dict(backend_result.metadata or {})
            metadata.update(
                {
                    "fallback_reason": self._backend_health_reason(backend_health),
                    "backend_health": backend_health,
                    "preemptive_fallback": True,
                    "mode": backend_options.get("mode"),
                    "response_path": metadata.get("response_path") or "preemptive_fallback",
                    "health_forced_fallback": True,
                    "fallback_trigger": "backend_health",
                    "model_chain": backend_chain,
                    "selected_model": backend_chain[0] if backend_chain else getattr(chat_backend, "model", None),
                    "effective_timeout_s": backend_options.get("timeout"),
                    "streamed": False,
                    "backend_health_state_at_request": backend_health.get("state"),
                    "recovery_probe_attempted": recovery_probe_attempted,
                    "recovery_probe_succeeded": False,
                    "degraded_reason": backend_health.get("degraded_reason"),
                    "recovery_reason": backend_health.get("recovery_reason"),
                    "timeout_window_seconds": backend_health.get("timeout_window_seconds"),
                    "live_lane_attempted": False,
                    "fallback_mode": "preemptive",
                }
            )
            metadata["prompt_metrics"] = prompt_metrics
            metadata.setdefault("prompt_message_count", prompt_metrics.get("message_count"))
            metadata.setdefault("prompt_chars", prompt_metrics.get("prompt_chars"))
            metadata.setdefault("approx_prompt_tokens", prompt_metrics.get("approx_prompt_tokens"))
            metadata["requested_mode"] = requested_mode
            metadata["generation_mode"] = generation_mode
            return backend_result.text, {
                "provider": (
                    getattr(chat_backend, "provider_name", None) or backend_result.provider
                ) if generic_fallback_used else backend_result.provider,
                "model": backend_result.model,
                "metadata": metadata,
                "backend_error": self._backend_health_reason(backend_health),
            }
        generic_fallback_used = False
        try:
            stream_callback = context.get("stream_callback")
            if stream_callback is not None and hasattr(chat_backend, "generate_stream"):
                backend_result = chat_backend.generate_stream(
                    messages,
                    on_token=stream_callback,
                    **backend_options,
                )
            else:
                backend_result = chat_backend.generate(messages, **backend_options)
            backend_error = None
            metadata = dict(backend_result.metadata or {})
            if metadata.get("response_complete") is False or metadata.get("finish_reason") == "length":
                backend_error = "Model response is incomplete: " + str(
                    metadata.get("termination") or metadata.get("finish_reason") or "unknown termination")
                metadata["response_complete"] = False
            metadata.setdefault("response_path", "live_model")
            metadata.setdefault("health_forced_fallback", False)
            metadata.setdefault("fallback_trigger", None)
            metadata.setdefault("streamed", bool(stream_callback is not None and metadata.get("streamed") is True))
            metadata.setdefault("backend_health_state_at_request", backend_health.get("state"))
            metadata.setdefault("recovery_probe_attempted", recovery_probe_attempted)
            metadata.setdefault("recovery_probe_succeeded", recovery_probe_attempted and backend_error is None)
            metadata.setdefault("degraded_reason", backend_health.get("degraded_reason"))
            metadata.setdefault("recovery_reason", "live_model_response" if recovery_probe_attempted and backend_error is None else backend_health.get("recovery_reason"))
            metadata.setdefault("timeout_window_seconds", backend_health.get("timeout_window_seconds"))
            metadata.setdefault("live_lane_attempted", True)
            metadata.setdefault("fallback_mode", "none")
        except ChatBackendError as exc:
            bounded_fallback = self._bounded_context_fallback_response(
                user_input=user_input,
                context=context,
                backend_error=str(exc),
                primary_provider=getattr(chat_backend, "provider_name", None),
            )
            generic_fallback_used = bounded_fallback is None
            backend_result = bounded_fallback or self.fallback_backend.generate(
                messages,
                backend_error=str(exc),
                fallback_reason=str(exc),
                provider=getattr(chat_backend, "provider_name", "ollama"),
                model=getattr(chat_backend, "model", None),
                backend_health=backend_health,
                primary_backend_metadata=getattr(exc, "metadata", {}),
            )
            backend_error = str(exc)
            metadata = dict(backend_result.metadata or {})
            metadata.update(
                {
                    "fallback_reason": str(exc),
                    "primary_backend_metadata": getattr(exc, "metadata", {}),
                    "timeout_reason": getattr(exc, "metadata", {}).get("timeout_reason"),
                    "ollama_attempts": getattr(exc, "metadata", {}).get("ollama_attempts", 0),
                    "attempt_log": getattr(exc, "metadata", {}).get("attempt_log", []),
                    "mode": backend_options.get("mode"),
                    "backend_health": backend_health,
                    "response_path": metadata.get("response_path") or "fallback_after_backend_error",
                    "health_forced_fallback": False,
                    "fallback_trigger": (
                        getattr(exc, "metadata", {}).get("timeout_reason")
                        or getattr(exc, "metadata", {}).get("error_type")
                        or "backend_error"
                    ),
                    "model_chain": getattr(exc, "metadata", {}).get("model_chain") or backend_chain,
                    "selected_model": getattr(exc, "metadata", {}).get("selected_model") or getattr(exc, "metadata", {}).get("model"),
                    "effective_timeout_s": getattr(exc, "metadata", {}).get("effective_timeout_s") or getattr(exc, "metadata", {}).get("timeout_s") or backend_options.get("timeout"),
                    "streamed": bool(stream_callback is not None),
                    "backend_health_state_at_request": backend_health.get("state"),
                    "recovery_probe_attempted": (
                        str(backend_health.get("state") or "").strip().lower() == "degraded"
                        and bool(backend_health.get("allow_recovery_probe"))
                    ),
                    "recovery_probe_succeeded": False,
                    "degraded_reason": backend_health.get("degraded_reason"),
                    "recovery_reason": backend_health.get("recovery_reason"),
                    "timeout_window_seconds": backend_health.get("timeout_window_seconds"),
                    "live_lane_attempted": True,
                    "fallback_mode": "reactive",
                }
            )

        metadata["prompt_metrics"] = prompt_metrics
        metadata.setdefault("prompt_message_count", prompt_metrics.get("message_count"))
        metadata.setdefault("prompt_chars", prompt_metrics.get("prompt_chars"))
        metadata.setdefault("approx_prompt_tokens", prompt_metrics.get("approx_prompt_tokens"))
        metadata["requested_mode"] = requested_mode
        metadata["generation_mode"] = generation_mode
        if backend_health:
            metadata.setdefault("backend_health", backend_health)
        metadata.setdefault("model_chain", backend_chain)
        metadata.setdefault("selected_model", getattr(backend_result, "model", None))
        metadata.setdefault("effective_timeout_s", backend_options.get("timeout"))
        metadata.setdefault("backend_health_state_at_request", backend_health.get("state"))
        metadata.setdefault("recovery_probe_attempted", False)
        metadata.setdefault("recovery_probe_succeeded", False)
        metadata.setdefault("degraded_reason", backend_health.get("degraded_reason"))
        metadata.setdefault("recovery_reason", backend_health.get("recovery_reason"))
        metadata.setdefault("timeout_window_seconds", backend_health.get("timeout_window_seconds"))
        metadata.setdefault("live_lane_attempted", False)
        metadata.setdefault("fallback_mode", "none")

        return backend_result.text, {
            "provider": (
                getattr(chat_backend, "provider_name", None) or backend_result.provider
            ) if generic_fallback_used else backend_result.provider,
            "model": backend_result.model,
            "metadata": metadata,
            "backend_error": backend_error,
        }

    def _effective_generation_mode(self, user_input: str, context: Dict[str, Any]) -> str:
        """Honor the selected generation lane instead of silently downgrading it."""
        requested_mode = normalize_response_mode(context.get("analysis_mode") or context.get("response_mode"))
        return requested_mode

    @staticmethod
    def _resolve_backend_health(context: Dict[str, Any]) -> Dict[str, Any]:
        memory_context = context.get("memory_context") or {}
        candidate = memory_context.get("backend_health") or context.get("backend_health") or {}
        return dict(candidate) if isinstance(candidate, dict) else {}

    def _apply_backend_health_overrides(self, options: Dict[str, Any], backend_health: Dict[str, Any]) -> Dict[str, Any]:
        state = str(backend_health.get("state") or "").strip().lower()
        if state not in {"degraded", "unavailable"}:
            return dict(options)
        adjusted = dict(options)
        mode = normalize_response_mode(adjusted.get("mode"))
        if state == "unavailable":
            adjusted["timeout"] = 0.0
            adjusted["max_attempts"] = 0
            adjusted["retry_read_timeout"] = False
            return adjusted
        degraded_timeout_caps = {
            "fast": 8.0,
            "standard": 30.0,
            "deep": 60.0,
        }
        try:
            current_timeout = float(adjusted.get("timeout") or 0.0)
        except (TypeError, ValueError):
            current_timeout = 0.0
        adjusted["timeout"] = min(current_timeout, degraded_timeout_caps.get(mode, 18.0)) if current_timeout > 0 else degraded_timeout_caps.get(mode, 18.0)
        adjusted["max_attempts"] = 1
        adjusted["retry_read_timeout"] = False
        adjusted["disable_chain_fallbacks"] = True
        return adjusted

    @staticmethod
    def _backend_health_reason(backend_health: Dict[str, Any]) -> str:
        state = str(backend_health.get("state") or "").strip().lower()
        error = str(backend_health.get("last_backend_error") or "").strip()
        if state == "unavailable":
            return error or "The local model backend is currently unavailable."
        if state == "degraded":
            return error or "The local model backend is currently degraded."
        return error or "The local model backend reported a degraded state."

    def _should_force_fallback(
        self,
        backend_health: Dict[str, Any],
        *,
        user_input: str = "",
        context: Optional[Dict[str, Any]] = None,
        prompt_metrics: Optional[Dict[str, Any]] = None,
        generation_mode: Optional[str] = None,
    ) -> bool:
        state = str(backend_health.get("state") or "").strip().lower()
        if state == "unavailable":
            return True
        if state != "degraded":
            return False
        if self._allow_conversational_recovery_probe(
            backend_health,
            user_input=user_input,
            context=context,
            prompt_metrics=prompt_metrics,
            generation_mode=generation_mode,
        ):
            return False
        if backend_health.get("repeated_recent_failures"):
            return True
        return not bool(backend_health.get("allow_recovery_probe"))

    @staticmethod
    def _allow_conversational_recovery_probe(
        backend_health: Dict[str, Any],
        *,
        user_input: str = "",
        context: Optional[Dict[str, Any]] = None,
        prompt_metrics: Optional[Dict[str, Any]] = None,
        generation_mode: Optional[str] = None,
    ) -> bool:
        if str(backend_health.get("state") or "").strip().lower() != "degraded":
            return False
        if bool(backend_health.get("allow_recovery_probe")):
            return True
        if not bool(backend_health.get("repeated_recent_failures")):
            return False
        metrics = dict(prompt_metrics or {})
        memory_context = dict(((context or {}).get("memory_context") or {}))
        if str(generation_mode or "").strip().lower() not in {"standard", "fast"}:
            return False
        grounded_conversation = bool(metrics.get("grounded_conversation")) or bool(
            str((memory_context.get("grounded_conversation_answer") or {}).get("answer") or "").strip()
        )
        if not grounded_conversation:
            return False
        followup_continuity = bool(metrics.get("followup_continuity_guidance"))
        if not followup_continuity:
            lowered = str(user_input or "").strip().lower()
            continuity_flags = conversation_memory_service._conversational_followup_flags(lowered)
            followup_continuity = bool(
                continuity_flags.get("ultra_short_followup")
                or continuity_flags.get("short_scope_followup")
                or continuity_flags.get("environment_followup")
                or continuity_flags.get("referential_followup")
            )
        if not followup_continuity:
            return False
        approx_prompt_tokens = int(metrics.get("approx_prompt_tokens") or 0)
        if approx_prompt_tokens and approx_prompt_tokens > 1800:
            return False
        semantic_hits = int(metrics.get("semantic_hits") or 0)
        if semantic_hits > 6:
            return False
        return True

    def _grounded_memory_response(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        memory_context = context.get("memory_context") or {}
        grounded = memory_context.get("grounded_memory_answer")
        if not isinstance(grounded, dict):
            return None
        answer_text = str(grounded.get("answer") or "").strip()
        if not answer_text:
            return None
        return {
            "answer": answer_text,
            "provider": "conversation_memory",
            "model": "deterministic-memory-grounded",
            "metadata": {
                "grounded_memory": True,
                "kind": grounded.get("kind"),
                "retrieval_profile": grounded.get("retrieval_profile"),
                "timing_ms": grounded.get("timing_ms", {}),
                "evidence_counts": grounded.get("evidence_counts", {}),
                "provenance": list(grounded.get("provenance") or []),
                "response_path": "grounded_memory",
                "model_chain": ["deterministic-memory-grounded"],
                "selected_model": "deterministic-memory-grounded",
                "effective_timeout_s": 0.0,
            },
        }

    def _grounded_knowledge_response(self, context: Dict[str, Any], *, user_input: str) -> Optional[Dict[str, Any]]:
        memory_context = context.get("memory_context") or {}
        grounded = memory_context.get("grounded_knowledge_answer")
        if not isinstance(grounded, dict):
            return None
        answer_text = str(grounded.get("answer") or "").strip()
        if not answer_text:
            return None
        citations = list(memory_context.get("citations") or [])
        lowered = str(user_input or "").strip().lower()
        if (
            self._looks_like_raw_knowledge_dump(answer_text)
            or not self._should_use_grounded_knowledge(lowered, citations)
        ):
            return None
        return {
            "answer": answer_text,
            "provider": "knowledge_librarian",
            "model": "deterministic-collection-grounded",
            "metadata": {
                "grounded_answer": True,
                "collection": grounded.get("collection"),
                "retrieval_profile": grounded.get("retrieval_profile"),
                "compute_used": grounded.get("compute_used"),
                "retrieved_count": grounded.get("retrieved_count"),
                "timing_ms": grounded.get("timing_ms", {}),
                "provenance": list(grounded.get("provenance") or []),
                "response_path": "grounded_knowledge",
                "model_chain": ["deterministic-collection-grounded"],
                "selected_model": "deterministic-collection-grounded",
                "effective_timeout_s": 0.0,
            },
        }

    def _bounded_context_fallback_response(
        self,
        *,
        user_input: str,
        context: Dict[str, Any],
        backend_error: str,
        primary_provider: Optional[str] = None,
    ) -> Optional[ChatBackendResult]:
        memory_context = context.get("memory_context") or {}
        operator_profile = dict(memory_context.get("operator_profile") or {})
        active_work = [
            str(item).strip()
            for item in list(operator_profile.get("active_work") or [])
            if str(item).strip()
        ]
        grounded_knowledge = memory_context.get("grounded_knowledge_answer") or {}
        grounded_conversation = memory_context.get("grounded_conversation_answer") or {}
        knowledge_context = str(memory_context.get("knowledge_context") or "").strip()
        citations = list(memory_context.get("citations") or [])
        backend_health = dict(memory_context.get("backend_health") or {})
        lowered = str(user_input or "").strip().lower()

        answer = ""
        if self._is_social_checkin_prompt(lowered):
            answer = self._compose_social_checkin_fallback(active_work, operator_profile)
        elif self._is_runtime_status_prompt(lowered):
            answer = self._compose_runtime_status_fallback(
                active_work=active_work,
                operator_profile=operator_profile,
                backend_health=backend_health,
                transport=str(context.get("transport") or memory_context.get("transport") or "").strip(),
                user_input=user_input,
            )
        elif active_work and any(
            phrase in lowered
            for phrase in (
                "improve obeos",
                "improve your system",
                "improve the ecosystem",
                "improve response time",
                "response time",
                "faster replies",
                "faster responses",
                "latency",
                "what should we work on",
                "what should we improve",
            )
        ):
            answer = self._compose_active_work_fallback(active_work)
        elif active_work and any(
            phrase in lowered
            for phrase in (
                "everything going on",
                "what is happening with us right now",
                "what should i know about the project",
                "overall situation right now",
                "what should i be thinking about right now",
                "what should i be thinking about",
            )
        ):
            answer = self._compose_state_summary_fallback(active_work, operator_profile)
        elif active_work and any(
            phrase in lowered
            for phrase in (
                "our work",
                "all this",
                "everything we are doing",
                "everything we're doing",
                "what do you think this is becoming",
                "what this is becoming",
                "about us and this project",
                "where do you think all of this is headed",
                "where is this headed",
                "sum up everything",
            )
        ):
            answer = self._compose_project_direction_fallback(active_work, operator_profile)
        elif (
            grounded_knowledge
            and str(grounded_knowledge.get("answer") or "").strip()
            and not self._looks_like_raw_knowledge_dump(str(grounded_knowledge.get("answer") or ""))
            and self._should_use_grounded_knowledge(lowered, citations)
        ):
            answer = str(grounded_knowledge.get("answer") or "").strip()
        elif grounded_conversation and str(grounded_conversation.get("answer") or "").strip():
            answer = str(grounded_conversation.get("answer") or "").strip()
        elif knowledge_context and self._should_use_grounded_knowledge(lowered, citations):
            answer = self._compose_knowledge_context_fallback(
                user_input=user_input,
                knowledge_context=knowledge_context,
                citations=citations,
            )
        elif active_work and any(
            phrase in lowered
            for phrase in (
                "recent updates",
                "what changed",
                "where did we leave off",
                "what are we working on",
                "what have we been working on",
            )
        ):
            answer = (
                "From the local O.B.E.O.S. context I still have in focus, "
                f"we have been working on {self._join_items(active_work[:3])}."
            )

        if not answer:
            return None

        if "live model lane" not in answer.lower():
            answer = (
                f"{answer.rstrip()} The live model lane missed this turn, "
                "so I am answering from bounded local O.B.E.O.S. memory and knowledge instead."
            )
        return ChatBackendResult(
            text=answer,
            provider=(
                primary_provider
                or getattr(self.chat_backend, "provider_name", None)
                or "ollama"
            ),
            model="bounded-context-grounded",
            metadata={
                "fallback_reason": backend_error,
                "bounded_context_fallback": True,
                "response_path": "bounded_context_fallback",
                "fallback_provider": "heuristic",
                "fallback_model": "bounded-context-grounded",
            },
        )

    def _compose_active_work_fallback(self, active_work: List[str]) -> str:
        focused = active_work[:3]
        if not focused:
            return ""
        return (
            "Based on the strongest current O.B.E.O.S. work tracks I still have in memory, "
            f"the highest-value improvements right now are {self._join_items(focused)}."
        )

    def _compose_project_direction_fallback(
        self,
        active_work: List[str],
        operator_profile: Dict[str, Any],
    ) -> str:
        focused = active_work[:3]
        preferred_name = str(operator_profile.get("preferred_name") or "").strip()
        opening = "From the strongest grounded O.B.E.O.S. context I still have"
        if preferred_name:
            opening = f"{preferred_name}, from the strongest grounded O.B.E.O.S. context I still have"
        return (
            f"{opening}, this work is becoming a more coherent living system instead of a fragile chat shell. "
            f"The clearest current threads are {self._join_items(focused)}, and they all point toward something more grounded, observable, and dependable."
        )

    def _compose_state_summary_fallback(
        self,
        active_work: List[str],
        operator_profile: Dict[str, Any],
    ) -> str:
        focused = active_work[:3]
        preferred_name = str(operator_profile.get("preferred_name") or "").strip()
        opening = "From the strongest grounded O.B.E.O.S. state I still have"
        if preferred_name:
            opening = f"{preferred_name}, from the strongest grounded O.B.E.O.S. state I still have"
        return (
            f"{opening}, the overall situation right now centers on {self._join_items(focused)}. "
            "Those are the tracks most likely to improve whether O.B.E.O.S. feels grounded, coherent, and dependable across its surfaces."
        )

    def _compose_social_checkin_fallback(
        self,
        active_work: List[str],
        operator_profile: Dict[str, Any],
    ) -> str:
        preferred_name = str(operator_profile.get("preferred_name") or "").strip()
        salutation = f"{preferred_name}, " if preferred_name else ""
        if active_work:
            return (
                f"{salutation}I'm here, and still tracking {self._join_items(active_work[:2])} inside O.B.E.O.S."
            )
        return f"{salutation}I'm here and still present inside O.B.E.O.S."

    def _compose_runtime_status_fallback(
        self,
        *,
        active_work: List[str],
        operator_profile: Dict[str, Any],
        backend_health: Dict[str, Any],
        transport: str,
        user_input: str,
    ) -> str:
        preferred_name = str(operator_profile.get("preferred_name") or "").strip()
        salutation = f"{preferred_name}, " if preferred_name else ""
        parts: List[str] = []
        lowered = str(user_input or "").strip().lower()
        surface = transport.replace("_", " ").strip()
        if any(token in lowered for token in ("which surface", "came from", "trace test")):
            if surface:
                parts.append(f"{salutation}this turn came through {surface}.")
            else:
                parts.append(f"{salutation}this turn came through the current O.B.E.O.S. surface.")
        state = str(backend_health.get("state") or "").strip().lower()
        if state == "degraded":
            reason = str(backend_health.get("degraded_reason") or "recent backend issues").replace("_", " ").strip()
            parts.append(f"The last runtime health I have is degraded because of {reason}.")
            if backend_health.get("allow_recovery_probe"):
                parts.append("A bounded live-model recovery probe is still allowed on the next turn.")
        elif state:
            parts.append(f"The last runtime health I have is {state}.")
        else:
            parts.append("I do not have a fresh runtime health snapshot in this fallback lane, but I am still present.")
        if active_work:
            parts.append(f"I still have {self._join_items(active_work[:2])} in focus.")
        return " ".join(part.strip() for part in parts if part.strip()).strip()

    def _compose_knowledge_context_fallback(
        self,
        *,
        user_input: str,
        knowledge_context: str,
        citations: List[Dict[str, Any]],
    ) -> str:
        cleaned = re.sub(r"\s+", " ", knowledge_context).strip()
        if not cleaned:
            return ""
        if self._looks_like_raw_knowledge_dump(cleaned):
            return ""
        cleaned = re.sub(r"^Bounded retrieval context:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.replace("[truncated for standard chat latency]", "").strip()
        cleaned = cleaned[:320].rstrip(" ,.;:-")
        citation = citations[0] if citations else {}
        collection = str(citation.get("collection") or "").strip()
        title = str(citation.get("title") or citation.get("source_file") or "").strip()
        lowered = str(user_input or "").strip().lower()
        if any(token in lowered for token in ("how", "what", "explain", "resources", "architecture", "routing")):
            prefix = "From bounded local O.B.E.O.S. knowledge"
            if collection:
                prefix += f" in {collection}"
            if title:
                prefix += f" ({title})"
            return f"{prefix}, {cleaned}."
        return cleaned + "."

    @staticmethod
    def _join_items(items: List[str]) -> str:
        cleaned = [str(item).strip() for item in items if str(item).strip()]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            return cleaned[0]
        if len(cleaned) == 2:
            return f"{cleaned[0]} and {cleaned[1]}"
        return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"

    @staticmethod
    def _is_social_checkin_prompt(lowered: str) -> bool:
        return any(
            token in lowered
            for token in (
                "good evening",
                "good morning",
                "good afternoon",
                "how are you",
                "howre you",
                "how's it going",
                "hows it going",
                "are you still there",
                "still there",
                "are you there",
                "you still there",
                "are you hungry",
            )
        )

    @staticmethod
    def _is_runtime_status_prompt(lowered: str) -> bool:
        return any(
            token in lowered
            for token in (
                "runtime status",
                "system status",
                "backend status",
                "which surface",
                "came from",
                "trace test",
                "runtime looking",
                "backend alive",
            )
        )

    @staticmethod
    def _should_use_grounded_knowledge(lowered: str, citations: List[Dict[str, Any]]) -> bool:
        if any(
            token in lowered
            for token in (
                "good evening",
                "good morning",
                "good afternoon",
                "how are you",
                "howre you",
                "are you hungry",
                "are you still there",
                "still there",
                "trace test",
                "runtime status",
                "system status",
                "backend status",
                "which surface",
                "came from",
            )
        ):
            return False
        query_tokens = {
            token
            for token in re.findall(r"[a-z0-9][a-z0-9_.-]+", lowered)
            if token not in {
                "about", "and", "are", "can", "do", "does", "explain", "have", "how",
                "i", "is", "me", "of", "please", "remember", "recall", "tell", "the",
                "this", "to", "what", "we", "who", "with", "you", "your",
            }
        }
        evidence_tokens: set[str] = set()
        for item in citations:
            for field in ("collection", "title", "source_file", "path"):
                value = str(item.get(field) or "").strip().lower()
                evidence_tokens.update(
                    token
                    for token in re.findall(r"[a-z0-9][a-z0-9_.-]+", value)
                    if len(token) >= 3
                )
        # Retrieval is supplementary. A relevant topic must be named in the
        # question before a citation can become the answer.
        return bool(query_tokens & evidence_tokens)

    @staticmethod
    def _looks_like_raw_knowledge_dump(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        return any(
            marker in normalized
            for marker in (
                "kind=knowledge_collection",
                "raw ocr",
                "[page 1]",
                "ufg ",
            )
        )

    @staticmethod
    def _backend_chain_preview(chat_backend: BaseChatBackend, response_mode: str, options: Dict[str, Any]) -> List[str]:
        override = options.get("model")
        if override:
            return [str(override)]
        primary = str(getattr(chat_backend, "model", "") or "").strip()
        fallback_models = [
            str(item).strip()
            for item in getattr(chat_backend, "fallback_models", [])
            if str(item).strip()
        ]
        if options.get("disable_chain_fallbacks"):
            return [primary] if primary else []
        if primary:
            return [primary, *[item for item in fallback_models if item != primary]]
        return model_chain_for_agent("jarvis", response_mode)

    def _backend_options(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Choose bounded backend behavior for fast/standard/deep response modes."""
        return backend_options_for_context(context, "jarvis")

    def _build_chat_messages(self, user_input: str, context: Dict[str, Any]) -> tuple[List[Dict[str, str]], Dict[str, Any]]:
        """Build a chat transcript for the local model backend."""
        memory_context = context.get("memory_context", {})
        session_messages = [message for message in memory_context.get("session_messages", [])
                            if message.get("prompt_eligible", True)]
        prompt_context = memory_context.get("prompt_context", "")
        grounded_conversation = memory_context.get("grounded_conversation_answer") or {}
        prompt_mode = str(memory_context.get("prompt_mode") or memory_context.get("memory_profile", {}).get("prompt_mode") or "standard")
        agent_override = context.get("agent_override")
        # Quick turns still need the last few exchanges to resolve terse follow-ups.
        transcript_messages = self._sanitize_session_messages(
            session_messages[-QUICK_TRANSCRIPT_FLOOR:] if prompt_mode.startswith("quick") else session_messages,
            current_input=user_input,
        )

        system_prompt = (
            IDENTITY_AND_TRUTH_PROMPT +
            "Use relevant memory and cite sources when useful. Resolve follow-ups from the immediate conversation "
            "before older memories. Match reply length to the question; do not narrate internal prompt scaffolding."
        )
        if agent_override:
            visible_name = "D.A.I.S.E" if str(agent_override).strip().lower() in {"daise", "jarvis"} else str(agent_override)
            system_prompt += f" The operator explicitly selected {visible_name} for this turn."

        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if prompt_context:
            messages.append(
                {
                    "role": "system",
                    "content": f"Conversation memory and ecosystem context:\n{prompt_context}",
                }
            )
        if isinstance(grounded_conversation, dict):
            guidance_text = str(grounded_conversation.get("answer") or "").strip()
            guidance_kind = str(grounded_conversation.get("kind") or "").strip()
            if guidance_text:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Broad conversational grounding for this turn:\n"
                            f"{guidance_text}\n"
                            "Use this as a memory-backed backbone for the reply, but answer naturally instead of copying it verbatim."
                            " Do not mention grounding, bounded context, retrieval, or prompt scaffolding unless the operator explicitly asks."
                            + (f" Frame={guidance_kind}." if guidance_kind else "")
                        ),
                    }
                )
            if self._should_add_followup_continuity_guidance(
                user_input=user_input,
                prompt_context=prompt_context,
                guidance_kind=guidance_kind,
            ):
                continuity_match = re.search(
                    r"Conversation continuity:\n(?P<body>.+?)(?:\n\n|\Z)",
                    prompt_context,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                continuity_body = " ".join(str((continuity_match.group("body") if continuity_match else "") or "").split()).strip()
                if continuity_body:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "Follow-up continuity for this turn:\n"
                                f"{continuity_body}\n"
                                "This user message is a continuation of the current thread. Carry the prior meaning forward,"
                                " answer the follow-up directly, and avoid restarting from a generic high-level summary."
                                " Prefer the concrete topics explicitly named in the continuity and grounding context."
                                " Do not introduce adjacent older topics unless the user asks for them or they are necessary to answer faithfully."
                                " For terse follow-ups like 'same there?' or 'what about telegram?', start by resolving what 'same' or 'there' refers to in the current thread."
                                " Do not turn the reply into a generic motivational check-in, status poll, or broad recap unless the user explicitly asks for one."
                            ),
                        }
                    )
                specialist_match = re.search(
                    r"Specialist continuity:\n(?P<body>.+?)(?:\n\n|\Z)",
                    prompt_context,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                specialist_body = " ".join(str((specialist_match.group("body") if specialist_match else "") or "").split()).strip()
                if specialist_body:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "Active specialist lane for this turn:\n"
                                f"{specialist_body}\n"
                                "Keep continuity with that working lane when answering this follow-up unless the user clearly changes direction."
                            ),
                        }
                    )

        # Deterministic answers and runtime facts are evidence for the model's own reply.
        if context.get("allow_deterministic_evidence", True) is not False:
            for label, grounded in (
                ("Memory-backed answer", self._grounded_memory_response(context)),
                ("Knowledge-collection answer", self._grounded_knowledge_response(context, user_input=user_input)),
            ):
                evidence_message = grounded_answer_message(label, grounded)
                if evidence_message is not None:
                    messages.append(evidence_message)
            truth_message = runtime_truth_message(context.get("runtime_truth"))
            if truth_message is not None:
                messages.append(truth_message)

        context_capacity = int(getattr(chat_settings, "daise_num_ctx", 8192) or 8192)
        output_reserve = int(getattr(chat_settings, "daise_num_predict", 2048) or 2048)
        reserved = sum(estimated_tokens(item["content"]) for item in messages)
        history_budget = max(0, min(
            context_capacity // 3,
            context_capacity - output_reserve - reserved - estimated_tokens(user_input) - 256,
        ))
        if transcript_messages and str(transcript_messages[-1].get("text", "")).strip() == user_input and transcript_messages[-1].get("role") == "user":
            transcript_messages = transcript_messages[:-1]
        transcript_messages = recent_history(transcript_messages, history_budget)
        history = []
        for message in transcript_messages:
            role = message.get("role", "user")
            content = str(message.get("text", "")).strip()
            if not content:
                continue
            if role not in {"user", "assistant", "system"}:
                role = "user"
            history.append({"role": role, "content": content})
        if history and history[-1] == {"role": "user", "content": user_input}:
            history.pop()
        # Keep stable history ahead of changing retrieval evidence so local
        # providers can reuse the conversation prefix between turns.
        messages[1:1] = history

        if not messages or messages[-1].get("role") != "user" or messages[-1].get("content") != user_input:
            messages.append({"role": "user", "content": user_input})

        prompt_chars = sum(len(message.get("content", "")) for message in messages)
        memory_prompt_metrics = dict(memory_context.get("prompt_metrics") or {})
        prompt_metrics = {
            **memory_prompt_metrics,
            "prompt_mode": prompt_mode,
            "message_count": len(messages),
            "prompt_chars": prompt_chars,
            "approx_prompt_tokens": max(1, prompt_chars // 4) if prompt_chars else 0,
            "system_prompt_chars": len(system_prompt),
            "memory_prompt_chars": len(prompt_context),
            "grounded_conversation_guidance": bool(str((grounded_conversation or {}).get("answer") or "").strip()),
            "followup_continuity_guidance": bool(
                self._should_add_followup_continuity_guidance(
                    user_input=user_input,
                    prompt_context=prompt_context,
                    guidance_kind=str((grounded_conversation or {}).get("kind") or "").strip(),
                )
                and "Conversation continuity:" in str(prompt_context or "")
            ),
            "specialist_continuity_guidance": bool(
                self._should_add_followup_continuity_guidance(
                    user_input=user_input,
                    prompt_context=prompt_context,
                    guidance_kind=str((grounded_conversation or {}).get("kind") or "").strip(),
                )
                and "Specialist continuity:" in str(prompt_context or "")
            ),
            "raw_session_messages_included": len(transcript_messages),
        }
        return messages, prompt_metrics

    def _should_add_followup_continuity_guidance(
        self,
        *,
        user_input: str,
        prompt_context: str,
        guidance_kind: str,
    ) -> bool:
        if not prompt_context:
            return False
        if str(guidance_kind or "").strip().lower() == "followup":
            return True
        continuity_flags = conversation_memory_service._conversational_followup_flags(str(user_input or ""))
        compact_tokens = set(continuity_flags.get("tokens") or set())
        if not compact_tokens:
            return False
        return bool(
            (
                continuity_flags.get("short_scope_followup")
                or continuity_flags.get("environment_followup")
                or continuity_flags.get("ultra_short_followup")
            )
            and (
                "Conversation continuity:" in prompt_context
                or "Specialist continuity:" in prompt_context
            )
        )

    def _sanitize_session_messages(self, messages: List[Dict[str, Any]], *, current_input: str) -> List[Dict[str, Any]]:
        sanitized: List[Dict[str, Any]] = []
        lowered_current = str(current_input or "").strip().lower()
        skip_user_texts: set[str] = set()
        for message in messages:
            role = str(message.get("role") or "user").strip().lower()
            text = str(message.get("text") or "").strip()
            if not text:
                continue
            lowered = text.lower()
            if role == "assistant":
                harmful_patterns = (
                    "error processing request:",
                    "primary ollama response did not complete",
                    "using the local fallback path",
                    "local o.b.e.o.s. memory and stored knowledge while the live model lane recovers",
                    "digital assistant for intelligent systems and expertise",
                    "legacy alias",
                    "compatibility reasons",
                )
                if any(pattern in lowered for pattern in harmful_patterns):
                    if sanitized and str(sanitized[-1].get("role") or "").strip().lower() == "user":
                        previous_user = str(sanitized[-1].get("text") or "").strip().lower()
                        if previous_user != lowered_current:
                            skip_user_texts.add(previous_user)
                            sanitized.pop()
                    continue
            if role == "user" and lowered in skip_user_texts and lowered != lowered_current:
                continue
            sanitized.append(message)
        return sanitized
    
    def invoke_tool_explicit(
        self,
        tool_name: str,
        action: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Explicitly invoke a tool and return the result.
        
        Args:
            tool_name: Service name
            action: Action on that service
            params: Parameters for the action
        
        Returns:
            Result dict or error
        """
        request = ToolRequest(
            request_id=str(uuid.uuid4()),
            tool_name=tool_name,
            action=action,
            params=params,
            context={"agent": self.name},
        )
        
        response = self.dispatcher.dispatch(request)
        
        return {
            "success": response.success,
            "result": response.result,
            "error": response.error,
            "status": response.status.value,
        }
