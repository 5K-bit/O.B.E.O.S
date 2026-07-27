from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .conversation_memory import ConversationMemoryService, conversation_memory_service
from .external_integrations import ExternalIntegrationRegistry
from .operator_actions import OperatorActionService
from .policy_engine import PolicyEngine


class RedAmonWorkflowService:
    """Operator-approved security workflow wrapper for RedAmon."""

    def __init__(
        self,
        *,
        memory_service: Optional[ConversationMemoryService] = None,
        operator_actions: Optional[OperatorActionService] = None,
        integration_registry: Optional[ExternalIntegrationRegistry] = None,
        policy_engine: Optional[PolicyEngine] = None,
    ) -> None:
        self.memory_service = memory_service or conversation_memory_service
        self.operator_actions = operator_actions or OperatorActionService(memory_service=self.memory_service)
        self.integration_registry = integration_registry or ExternalIntegrationRegistry()
        self.policy_engine = policy_engine or PolicyEngine()

    def status(self, *, session_id: Optional[str] = None) -> Dict[str, Any]:
        integration = self.integration_registry.describe("redamon_mode")
        actions = self.operator_actions.list_actions(limit=30, session_id=session_id)
        related = [
            item
            for item in actions.get("actions", [])
            if str(item.get("action") or "").startswith("redamon_")
        ]
        active = next((item for item in related if item.get("status") in {"pending", "executing", "blocked", "waiting_for_consent"}), None)
        return {
            "schema_version": "1.0",
            "mode_id": "redamon_mode",
            "label": "RedAmon Mode",
            "integration": integration,
            "active_mode": bool(active),
            "current_action": active,
            "history": related[:10],
            "consent_rule": self.policy_engine.required_consent_level("launch_redamon_mode"),
            "banner": {
                "title": "RedAmon mode is separate and operator-approved.",
                "summary": "Use RedAmon for reviewed security workflow steps, not ambient assistant authority.",
            },
        }

    def enter_mode(
        self,
        *,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        objective: str = "",
    ) -> Dict[str, Any]:
        note = objective.strip() or "Prepare RedAmon review mode."
        event = self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="operator_action_result",
            payload={
                "schema_version": "1.0",
                "intent_id": f"redamon-mode-{int(time.time())}",
                "action": "redamon_enter_mode",
                "status": "completed",
                "text": note,
                "human_reason": "Operator entered RedAmon review mode.",
                "reason_code": "mode_entered",
                "metadata": {"workflow": "redamon_mode"},
            },
            confidence=0.92,
            session_id=session_id,
            request_id=request_id,
            tags=["redamon_mode", "security_workflow"],
        )
        return {
            "success": True,
            "status": "mode_ready",
            "session_id": session_id,
            "request_id": request_id,
            "event_id": event.id,
            "objective": note,
            "mode": self.status(session_id=session_id),
        }

    def propose_launch(
        self,
        *,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        objective: str = "",
        target: str = "",
    ) -> Dict[str, Any]:
        objective_text = objective.strip() or "Review RedAmon launch plan."
        target_text = target.strip() or "operator_selected_target"
        intent = self.operator_actions.create_intent(
            action="redamon_launch_review",
            session_id=session_id,
            request_id=request_id,
            target=target_text,
            priority="high",
            proposed_by="daise",
            governed_by="blackfong",
            human_reason="RedAmon launch proposal created for operator review.",
            metadata={
                "workflow": "redamon_mode",
                "objective": objective_text,
                "target": target_text,
                "launch_contract": self.integration_registry.launch_contract("redamon_mode", execute=False),
            },
            initial_status="waiting_for_consent",
        )
        return {
            "success": True,
            "status": "review_required",
            "intent": intent,
            "consent_rule": self.policy_engine.required_consent_level("launch_redamon_mode"),
            "mode": self.status(session_id=session_id),
        }

    def launch(
        self,
        *,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        intent_id: Optional[str] = None,
        consent: Optional[Dict[str, Any]] = None,
        execute: bool = True,
    ) -> Dict[str, Any]:
        decision = self.policy_engine.evaluate_consent(
            action="launch_redamon_mode",
            consent=consent or {},
            target="redamon_mode",
        )
        if not decision.get("approved"):
            return {
                "success": False,
                "status": "requires_consent",
                "consent": decision,
                "mode": self.status(session_id=session_id),
            }
        contract = self.integration_registry.launch_contract("redamon_mode", execute=execute)
        if intent_id:
            self.operator_actions.record_result(
                intent_id,
                session_id=session_id,
                request_id=request_id,
                result=contract,
                status="completed" if contract.get("success") else "failed",
                human_reason="RedAmon launch executed after explicit approval." if contract.get("success") else str(contract.get("error") or "RedAmon launch failed."),
                metadata={"workflow": "redamon_mode", "consent": decision},
            )
        else:
            self.memory_service.persist_workflow_event(
                source="blackfong",
                event_type="operator_action_result",
                payload={
                    "schema_version": "1.0",
                    "intent_id": f"redamon-launch-{int(time.time())}",
                    "action": "redamon_launch",
                    "status": "completed" if contract.get("success") else "failed",
                    "text": "RedAmon launch executed." if contract.get("success") else str(contract.get("error") or "RedAmon launch failed."),
                    "reason_code": "completed" if contract.get("success") else "action_failed",
                    "human_reason": "RedAmon launch executed after explicit approval." if contract.get("success") else str(contract.get("error") or "RedAmon launch failed."),
                    "result": contract,
                    "metadata": {"workflow": "redamon_mode", "consent": decision},
                },
                confidence=0.88,
                session_id=session_id,
                request_id=request_id,
                tags=["redamon_mode", "security_workflow"],
            )
        return {
            "success": bool(contract.get("success")),
            "status": "launched" if contract.get("success") and execute else contract.get("status"),
            "launch": contract,
            "consent": decision,
            "mode": self.status(session_id=session_id),
        }

