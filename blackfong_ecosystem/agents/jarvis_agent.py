"""
JARVIS Agent Wrapper

Wraps the local JARVIS assistant to conform to the Agent interface.
Enables JARVIS to participate in the Blackfong multi-agent system.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from blackfong_ecosystem.agent import Agent, AgentType, ReasoningStep, ReasoningTrace, ToolRequest, ToolStatus
from blackfong_ecosystem.chat_backends import (
    BaseChatBackend,
    ChatBackendError,
    HeuristicChatBackend,
    build_agent_chat_backend,
)
from blackfong_ecosystem.config import chat_settings
from blackfong_ecosystem.model_routing import (
    backend_options_for_context,
    build_routed_chat_backend,
    detect_intent,
    normalize_response_mode,
)
from blackfong_ecosystem.event_client import EventClient
from blackfong_ecosystem.tool_dispatcher import ToolDispatcher


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
                confidence=0.92 if backend_info.get("provider") == "ollama" else 0.68,
            )
            trace.steps.append(step_3)
            
            trace.final_decision = response_text
            trace.overall_confidence = 0.92 if backend_info.get("provider") == "ollama" else 0.68
            
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
                "backend_metadata": backend_info.get("metadata"),
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
    
    def _generate_response(self, user_input: str, context: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """Generate a response with the configured local backend and safe fallback."""
        grounded = self._grounded_memory_response(context) or self._grounded_knowledge_response(context)
        if grounded is not None:
            prompt_metrics = dict(((context.get("memory_context") or {}).get("prompt_metrics") or {}))
            metadata = dict(grounded["metadata"])
            metadata["prompt_metrics"] = prompt_metrics
            metadata.setdefault("prompt_message_count", 0)
            metadata.setdefault("prompt_chars", 0)
            metadata.setdefault("approx_prompt_tokens", 0)
            return grounded["answer"], {
                "provider": grounded["provider"],
                "model": grounded["model"],
                "metadata": metadata,
                "backend_error": None,
            }
        messages, prompt_metrics = self._build_chat_messages(user_input, context)
        generation_mode = self._effective_generation_mode(user_input, context)
        backend_context = dict(context)
        backend_context["analysis_mode"] = generation_mode
        backend_context["response_mode"] = generation_mode
        backend_options = self._backend_options(backend_context)
        requested_mode = normalize_response_mode(context.get("analysis_mode") or context.get("response_mode"))
        chat_backend = self.chat_backend if self._chat_backend_explicit else build_routed_chat_backend("jarvis", generation_mode)
        backend_health = self._resolve_backend_health(context)
        backend_options = self._apply_backend_health_overrides(backend_options, backend_health)
        if self._should_force_fallback(backend_health):
            backend_result = self.fallback_backend.generate(
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
                }
            )
            metadata["prompt_metrics"] = prompt_metrics
            metadata.setdefault("prompt_message_count", prompt_metrics.get("message_count"))
            metadata.setdefault("prompt_chars", prompt_metrics.get("prompt_chars"))
            metadata.setdefault("approx_prompt_tokens", prompt_metrics.get("approx_prompt_tokens"))
            metadata["requested_mode"] = requested_mode
            metadata["generation_mode"] = generation_mode
            return backend_result.text, {
                "provider": backend_result.provider,
                "model": backend_result.model,
                "metadata": metadata,
                "backend_error": self._backend_health_reason(backend_health),
            }
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
        except ChatBackendError as exc:
            backend_result = self.fallback_backend.generate(
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

        return backend_result.text, {
            "provider": backend_result.provider,
            "model": backend_result.model,
            "metadata": metadata,
            "backend_error": backend_error,
        }

    def _effective_generation_mode(self, user_input: str, context: Dict[str, Any]) -> str:
        """Keep standard retrieval while letting lightweight chat use the faster generation lane."""
        requested_mode = normalize_response_mode(context.get("analysis_mode") or context.get("response_mode"))
        if requested_mode != "standard":
            return requested_mode
        transport = str(context.get("transport") or context.get("input_origin") or "").strip().lower()
        if transport in {"telegram", "hud", "desktop", "legion"}:
            return requested_mode
        detected = context.get("detected_intent")
        if isinstance(detected, dict):
            is_casual = bool(detected.get("casual"))
            is_heavy = any(
                bool(detected.get(key))
                for key in ("coding", "deep_reasoning", "file_ops", "diagnostics", "tinyprogrammer")
            )
        else:
            inferred = detect_intent(user_input)
            is_casual = inferred.casual
            is_heavy = any(
                (
                    inferred.coding,
                    inferred.deep_reasoning,
                    inferred.file_ops,
                    inferred.diagnostics,
                    inferred.tinyprogrammer,
                )
            )
        text = str(user_input or "").strip()
        if is_casual and not is_heavy and len(text) <= 220:
            return "fast"
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
            "standard": 18.0,
            "deep": 45.0,
        }
        try:
            current_timeout = float(adjusted.get("timeout") or 0.0)
        except (TypeError, ValueError):
            current_timeout = 0.0
        adjusted["timeout"] = min(current_timeout, degraded_timeout_caps.get(mode, 18.0)) if current_timeout > 0 else degraded_timeout_caps.get(mode, 18.0)
        adjusted["max_attempts"] = 1
        adjusted["retry_read_timeout"] = False
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

    def _should_force_fallback(self, backend_health: Dict[str, Any]) -> bool:
        state = str(backend_health.get("state") or "").strip().lower()
        if state == "unavailable":
            return True
        if state != "degraded":
            return False
        return int(backend_health.get("recent_timeout_count") or 0) >= 2

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
            },
        }

    def _grounded_knowledge_response(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        memory_context = context.get("memory_context") or {}
        grounded = memory_context.get("grounded_knowledge_answer")
        if not isinstance(grounded, dict):
            return None
        answer_text = str(grounded.get("answer") or "").strip()
        if not answer_text:
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
            },
        }

    def _backend_options(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Choose bounded backend behavior for fast/standard/deep response modes."""
        return backend_options_for_context(context, "jarvis")

    def _build_chat_messages(self, user_input: str, context: Dict[str, Any]) -> tuple[List[Dict[str, str]], Dict[str, Any]]:
        """Build a chat transcript for the local model backend."""
        memory_context = context.get("memory_context", {})
        session_messages = list(memory_context.get("session_messages", []))
        prompt_context = memory_context.get("prompt_context", "")
        prompt_mode = str(memory_context.get("prompt_mode") or memory_context.get("memory_profile", {}).get("prompt_mode") or "standard")
        agent_override = context.get("agent_override")

        system_prompt = (
            "You are D.A.I.S.E inside O.B.E.O.S, a local offline-first ecosystem assistant. "
            "Your visible operator-facing name is D.A.I.S.E. "
            "Jarvis is an internal compatibility alias only and should not be volunteered unless the operator explicitly asks about naming compatibility. "
            "If the operator asks what D.A.I.S.E stands for, the expansion is Direct Artificial Intelligence System Environment. "
            "Blackfong adjudicates truth and governance; you communicate truthful, grounded answers to the operator. "
            "Use the supplied session transcript and global memory when it is relevant. "
            "When bounded ecosystem context includes knowledge citations or memory evidence, treat that supplied context as authoritative for this turn instead of substituting unrelated prior model knowledge. "
            "Answer naturally, prefer concise responses by default, and be explicit when you are unsure. "
            "If the user asks about system abilities or commands, mention the HUD, TUI, slash commands, admitted agents, or routing when helpful."
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

        if not str(prompt_mode).startswith("quick"):
            for message in session_messages[-chat_settings.transcript_window :]:
                role = message.get("role", "user")
                content = str(message.get("text", "")).strip()
                if not content:
                    continue
                if role not in {"user", "assistant", "system"}:
                    role = "user"
                messages.append({"role": role, "content": content})

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
            "raw_session_messages_included": 0 if prompt_mode.startswith("quick") else min(
                len(session_messages),
                chat_settings.transcript_window,
            ),
        }
        return messages, prompt_metrics
    
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
