from __future__ import annotations

from typing import Any, Dict, List, Optional

from .conversation_memory import ConversationMemoryService, conversation_memory_service
from .operator_session import get_shared_session_id, link_surface
from .model_routing import normalize_response_mode, resolve_response_mode
from .orchestrator_loader import OrchestratorUnavailable, load_unified_orchestrator


class OperatorChatService:
    """Shared local chat runner for desktop-native surfaces."""

    def __init__(self, *, memory_service: Optional[ConversationMemoryService] = None, orchestrator: Any = None) -> None:
        self.memory_service = memory_service or conversation_memory_service
        self.orchestrator = orchestrator

    def send_message(
        self,
        *,
        message: str,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        agent_override: Optional[str] = None,
        analysis_mode: str = "fast",
        stream_callback: Optional[Any] = None,
        include_memory: bool = False,
        include_messages: bool = True,
        include_trace: bool = True,
        modality: str = "text",
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text = str(message or "").strip()
        if not text:
            return {"status": "error", "error": "Message is required"}
        session_id = session_id or get_shared_session_id(surface="operator_chat")
        link_surface("operator_chat")
        request_id = request_id or self.memory_service.create_correlation_id()[:8]
        analysis_mode = normalize_response_mode(
            resolve_response_mode(text, analysis_mode, agent_override=agent_override)
        )
        if self.orchestrator is not None:
            orchestrator = self.orchestrator
            input_modality = self._resolve_input_modality(modality, None)
            try:
                from blackfong_ecosystem.unified_orchestrator import InputModality

                input_modality = self._resolve_input_modality(modality, InputModality)
            except ImportError:
                pass
        else:
            try:
                loaded = load_unified_orchestrator(required=True, instance=True)
            except OrchestratorUnavailable as exc:
                return {"status": "error", "error": str(exc), "session_id": session_id, "request_id": request_id}
            orchestrator = loaded.orchestrator
            input_modality = self._resolve_input_modality(modality, loaded.input_modality)
        progress_events: List[Dict[str, Any]] = []
        reports: List[Dict[str, Any]] = []
        selections: List[Dict[str, Any]] = []

        orchestrator_context = {
            "session_id": session_id,
            "request_id": request_id,
            "agent_override": agent_override,
            "analysis_mode": analysis_mode,
            "response_mode": analysis_mode,
            "progress_callback": lambda event: progress_events.append(dict(event)),
            "agent_report_callback": lambda event: reports.append(dict(event)),
            "agent_selection_callback": lambda event: selections.append(dict(event)),
        }
        orchestrator_context.update(dict(context or {}))
        orchestrator_context["session_id"] = session_id
        orchestrator_context["request_id"] = request_id
        orchestrator_context["analysis_mode"] = analysis_mode
        orchestrator_context["response_mode"] = analysis_mode
        orchestrator_context["input_origin"] = orchestrator_context.get("input_origin") or "operator_chat"
        orchestrator_context["input_modality"] = str(modality or "text").strip().lower() or "text"
        if stream_callback is not None:
            orchestrator_context["stream_callback"] = stream_callback
        result = orchestrator.process_input(
            text,
            input_modality,
            context=orchestrator_context,
        )
        selected_agent = str(result.get("selected_agent") or result.get("agent") or agent_override or "daise")
        payload: Dict[str, Any] = {
            "status": "error" if result.get("error") else "complete",
            "session_id": session_id,
            "request_id": request_id,
            "result": result,
            "analysis_mode": analysis_mode,
            "progress_events": progress_events,
            "reports": reports,
            "selection": selections[-1] if selections else None,
        }
        if include_messages:
            payload["messages"] = self.memory_service.get_session_messages(session_id, limit=80)
        if include_trace:
            payload["trace"] = self.memory_service.get_request_trace(request_id)
        if include_memory:
            payload["memory"] = self.memory_service.build_context(
                session_id=session_id,
                query=text,
                agent_name=selected_agent,
                analysis_mode=analysis_mode,
            )
        return payload

    @staticmethod
    def _resolve_input_modality(modality: str, input_modality_enum: Any = None) -> Any:
        normalized = str(modality or "text").strip().lower() or "text"
        if input_modality_enum is None:
            return normalized
        attr = {
            "text": "TEXT",
            "voice": "VOICE",
            "audio": "VOICE",
            "speech": "VOICE",
            "vision": "VISION",
            "image": "VISION",
            "photo": "VISION",
            "code": "CODE",
        }.get(normalized, "TEXT")
        return getattr(input_modality_enum, attr, input_modality_enum.TEXT)
