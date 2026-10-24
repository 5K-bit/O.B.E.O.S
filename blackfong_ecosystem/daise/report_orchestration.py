"""Specialist execution and report normalization, separated from request routing."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence
from ..cancellation import raise_if_cancelled

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


class ReportOrchestration:
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
            raise_if_cancelled()
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
            report.trust_score = self.get_agent_trust_score(
                agent_name, report.task_type
            )
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

    def _normalize_agent_report(
        self, agent_name: str, result: Dict[str, Any]
    ) -> AgentReport:
        tools_invoked = result.get("tools_invoked") or []
        tool_activity_summary: List[str] = []
        for tool in tools_invoked:
            if isinstance(tool, dict):
                tool_activity_summary.append(
                    str(
                        tool.get("tool")
                        or tool.get("tool_name")
                        or tool.get("status")
                        or "tool"
                    )
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
