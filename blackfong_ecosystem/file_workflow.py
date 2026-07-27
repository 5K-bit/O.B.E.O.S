from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import paths
from .conversation_memory import ConversationMemoryService, conversation_memory_service
from .policy_engine import PolicyEngine, SCHEMA_VERSION


FILE_WORKFLOW_STATES = {
    "scanned",
    "read",
    "proposed",
    "approved",
    "pre_apply_validation",
    "applied",
    "post_apply_validation",
    "validated",
    "rolled_back",
    "rejected",
    "expired",
    "failed",
}


@dataclass
class WorkspacePolicy:
    allowed_roots: List[str]
    denied_roots: List[str]
    read_only_roots: List[str]
    hidden_system_roots_blocked: bool = True
    blocked_path_parts: List[str] = field(
        default_factory=lambda: [
            ".git",
            ".venv",
            "__pycache__",
            "$Recycle.Bin",
            "Windows",
            "System32",
            "Program Files",
            "Program Files (x86)",
        ]
    )

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "schema_version": SCHEMA_VERSION}


@dataclass
class FileProposal:
    proposal_id: str
    path: str
    state: str
    original_hash: str
    proposed_hash: str
    proposed_content: str
    original_content: str
    summary: str
    created_at: float
    proposal_expiry_timestamp: float
    validation_status: Dict[str, Any] = field(default_factory=dict)
    required_consent_level: Dict[str, Any] = field(default_factory=dict)
    policy_decision: Dict[str, Any] = field(default_factory=dict)
    state_history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("proposed_content", None)
        data.pop("original_content", None)
        data["schema_version"] = SCHEMA_VERSION
        data["expired"] = time.time() >= self.proposal_expiry_timestamp
        return data


class FileWorkflowService:
    """Auditable file proposal workflow for Blackfong-governed edits."""

    def __init__(
        self,
        *,
        memory_service: Optional[ConversationMemoryService] = None,
        policy_engine: Optional[PolicyEngine] = None,
        allowed_roots: Optional[List[Path]] = None,
        denied_roots: Optional[List[Path]] = None,
        read_only_roots: Optional[List[Path]] = None,
        default_ttl_seconds: int = 1800,
    ) -> None:
        self.memory_service = memory_service or conversation_memory_service
        self.policy_engine = policy_engine or PolicyEngine()
        self.default_ttl_seconds = default_ttl_seconds
        self.allowed_roots = [self._resolve(root) for root in (allowed_roots if allowed_roots is not None else [paths.root])]
        default_denied = [
            Path("C:/Windows"),
            Path("C:/Program Files"),
            Path("C:/Program Files (x86)"),
        ]
        self.denied_roots = [self._resolve(root) for root in (denied_roots if denied_roots is not None else default_denied)]
        self.read_only_roots = [self._resolve(root) for root in (read_only_roots if read_only_roots is not None else [])]
        self.proposals: Dict[str, FileProposal] = {}

    def workspace_policy(self) -> Dict[str, Any]:
        return WorkspacePolicy(
            allowed_roots=[str(path) for path in self.allowed_roots],
            denied_roots=[str(path) for path in self.denied_roots],
            read_only_roots=[str(path) for path in self.read_only_roots],
        ).to_dict()

    def scan(self, path: str = ".") -> Dict[str, Any]:
        target = self._resolve_target(path)
        allowed = self._path_policy_decision(target, mutation=False)
        if not allowed["allowed"]:
            return {"success": False, "state": "failed", "policy_decision": allowed, "path": str(target)}

        entries: List[Dict[str, Any]] = []
        if target.is_dir():
            for child in sorted(target.iterdir(), key=lambda item: item.name.lower()):
                child_decision = self._path_policy_decision(child, mutation=False, allow_missing=True)
                if not child_decision["allowed"]:
                    continue
                entries.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "kind": "directory" if child.is_dir() else "file",
                    }
                )
        else:
            entries.append({"name": target.name, "path": str(target), "kind": "file"})

        event = self._emit_state(
            state="scanned",
            path=str(target),
            payload={"entries": entries, "policy_decision": allowed},
        )
        return {"success": True, "state": "scanned", "path": str(target), "entries": entries, "event_id": event.id}

    def read(self, path: str) -> Dict[str, Any]:
        target = self._resolve_target(path)
        allowed = self._path_policy_decision(target, mutation=False)
        if not allowed["allowed"]:
            return {"success": False, "state": "failed", "policy_decision": allowed, "path": str(target)}
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"success": False, "state": "failed", "error": "Only UTF-8 text files are supported in the safe workflow.", "path": str(target)}
        except OSError as exc:
            return {"success": False, "state": "failed", "error": str(exc), "path": str(target)}
        event = self._emit_state(
            state="read",
            path=str(target),
            payload={"size": len(text), "sha256": self._hash_text(text), "policy_decision": allowed},
        )
        return {"success": True, "state": "read", "path": str(target), "content": text, "event_id": event.id}

    def propose(
        self,
        *,
        path: str,
        proposed_content: str,
        summary: str = "",
        ttl_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        target = self._resolve_target(path)
        allowed = self._path_policy_decision(target, mutation=True)
        required = self.policy_engine.required_consent_level("apply_file_diff")
        if not allowed["allowed"]:
            return {
                "success": False,
                "state": "failed",
                "path": str(target),
                "policy_decision": allowed,
                "required_consent_level": required,
            }
        try:
            original_content = target.read_text(encoding="utf-8") if target.exists() else ""
        except UnicodeDecodeError:
            return {"success": False, "state": "failed", "error": "Only UTF-8 text files are supported in the safe workflow.", "path": str(target)}
        except OSError as exc:
            return {"success": False, "state": "failed", "error": str(exc), "path": str(target)}

        now = time.time()
        proposal = FileProposal(
            proposal_id=str(uuid.uuid4())[:12],
            path=str(target),
            state="proposed",
            original_hash=self._hash_text(original_content),
            proposed_hash=self._hash_text(proposed_content),
            proposed_content=proposed_content,
            original_content=original_content,
            summary=summary,
            created_at=now,
            proposal_expiry_timestamp=now + float(ttl_seconds or self.default_ttl_seconds),
            required_consent_level=required,
            policy_decision=allowed,
        )
        self.proposals[proposal.proposal_id] = proposal
        event = self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="file_proposal_created",
            payload={
                **proposal.to_dict(),
                "provenance": self._provenance("file_workflow_proposal"),
            },
            confidence=0.96,
            tags=["file_workflow", "proposal"],
        )
        self._transition(proposal, "proposed", {"proposal_event_id": event.id})
        return {"success": True, "proposal": proposal.to_dict(), "event_id": event.id}

    def approve(self, proposal_id: str, *, consent: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        proposal = self._get_proposal(proposal_id)
        if proposal is None:
            return {"success": False, "state": "failed", "error": "Unknown file proposal."}
        if self._mark_expired_if_needed(proposal):
            return {"success": False, "proposal": proposal.to_dict(), "error": "File proposal has expired."}

        consent_decision = self.policy_engine.evaluate_consent(
            action="apply_file_diff",
            consent=consent,
            target=proposal_id,
        )
        self._emit_operator_consent(action="apply_file_diff", target=proposal_id, decision=consent_decision, consent=consent)
        proposal.policy_decision = consent_decision
        if not consent_decision["approved"]:
            self._transition(proposal, "rejected", {"policy_decision": consent_decision})
            return {"success": False, "proposal": proposal.to_dict(), "policy_decision": consent_decision}
        self._transition(proposal, "approved", {"policy_decision": consent_decision})
        return {"success": True, "proposal": proposal.to_dict(), "policy_decision": consent_decision}

    def pre_apply_validation(self, proposal_id: str) -> Dict[str, Any]:
        proposal = self._get_proposal(proposal_id)
        if proposal is None:
            return {"success": False, "state": "failed", "error": "Unknown file proposal."}
        validation = self._pre_apply_validation(proposal)
        proposal.validation_status["pre_apply"] = validation
        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="file_pre_apply_validation",
            payload={**proposal.to_dict(), "validation": validation, "provenance": self._provenance("file_pre_apply_validation")},
            confidence=0.94 if validation["passed"] else 0.62,
            tags=["file_workflow", "validation"],
        )
        self._transition(proposal, "pre_apply_validation", {"validation": validation})
        if not validation["passed"]:
            self._transition(proposal, "failed", {"validation": validation})
        return {"success": validation["passed"], "proposal": proposal.to_dict(), "validation": validation}

    def apply(self, proposal_id: str) -> Dict[str, Any]:
        proposal = self._get_proposal(proposal_id)
        if proposal is None:
            return {"success": False, "state": "failed", "error": "Unknown file proposal."}
        if proposal.state != "approved":
            return {"success": False, "proposal": proposal.to_dict(), "error": "Proposal must be approved before apply."}
        validation = self._pre_apply_validation(proposal)
        proposal.validation_status["pre_apply"] = validation
        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="file_pre_apply_validation",
            payload={**proposal.to_dict(), "validation": validation, "provenance": self._provenance("file_pre_apply_validation")},
            confidence=0.94 if validation["passed"] else 0.62,
            tags=["file_workflow", "validation"],
        )
        self._transition(proposal, "pre_apply_validation", {"validation": validation})
        if not validation["passed"]:
            self._transition(proposal, "failed", {"validation": validation})
            return {"success": False, "proposal": proposal.to_dict(), "validation": validation}

        target = Path(proposal.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(proposal.proposed_content, encoding="utf-8")
        except OSError as exc:
            self._transition(proposal, "failed", {"error": str(exc)})
            return {"success": False, "proposal": proposal.to_dict(), "error": str(exc)}

        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="file_change_applied",
            payload={**proposal.to_dict(), "provenance": self._provenance("file_change_applied")},
            confidence=0.92,
            tags=["file_workflow", "apply"],
        )
        self._transition(proposal, "applied", {})
        post = self._post_apply_validation(proposal)
        proposal.validation_status["post_apply"] = post
        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="file_post_apply_validation",
            payload={**proposal.to_dict(), "validation": post, "provenance": self._provenance("file_post_apply_validation")},
            confidence=0.94 if post["passed"] else 0.6,
            tags=["file_workflow", "validation"],
        )
        self._transition(proposal, "post_apply_validation", {"validation": post})
        self._transition(proposal, "validated" if post["passed"] else "failed", {"validation": post})
        return {"success": post["passed"], "proposal": proposal.to_dict(), "validation": post}

    def rollback(self, proposal_id: str, *, consent: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        proposal = self._get_proposal(proposal_id)
        if proposal is None:
            return {"success": False, "state": "failed", "error": "Unknown file proposal."}
        expected = f"ROLLBACK {proposal_id}"
        consent_payload = dict(consent or {})
        consent_payload.setdefault("expected_phrase", expected)
        consent_decision = self.policy_engine.evaluate_consent(
            action="rollback_file_change",
            consent=consent_payload,
            target=proposal_id,
        )
        self._emit_operator_consent(action="rollback_file_change", target=proposal_id, decision=consent_decision, consent=consent_payload)
        if not consent_decision["approved"]:
            return {
                "success": False,
                "proposal": proposal.to_dict(),
                "policy_decision": consent_decision,
                "expected_phrase": expected,
            }
        try:
            Path(proposal.path).write_text(proposal.original_content, encoding="utf-8")
        except OSError as exc:
            self._transition(proposal, "failed", {"error": str(exc)})
            return {"success": False, "proposal": proposal.to_dict(), "error": str(exc)}
        self._transition(proposal, "rolled_back", {"policy_decision": consent_decision})
        return {"success": True, "proposal": proposal.to_dict()}

    def list_proposals(self) -> Dict[str, Any]:
        for proposal in list(self.proposals.values()):
            self._mark_expired_if_needed(proposal)
        return {"proposals": [proposal.to_dict() for proposal in self.proposals.values()]}

    def _pre_apply_validation(self, proposal: FileProposal) -> Dict[str, Any]:
        if self._mark_expired_if_needed(proposal):
            return {"passed": False, "reason": "proposal_expired", "state": proposal.state}
        current_path = Path(proposal.path)
        path_policy = self._path_policy_decision(current_path, mutation=True)
        if not path_policy["allowed"]:
            return {"passed": False, "reason": "workspace_policy_blocked", "policy_decision": path_policy}
        try:
            current_text = current_path.read_text(encoding="utf-8") if current_path.exists() else ""
        except OSError as exc:
            return {"passed": False, "reason": "read_failed", "error": str(exc)}
        current_hash = self._hash_text(current_text)
        if current_hash != proposal.original_hash:
            return {
                "passed": False,
                "reason": "file_changed_since_proposal",
                "expected_hash": proposal.original_hash,
                "current_hash": current_hash,
            }
        return {"passed": True, "reason": "ready_to_apply", "current_hash": current_hash}

    def _post_apply_validation(self, proposal: FileProposal) -> Dict[str, Any]:
        try:
            current_text = Path(proposal.path).read_text(encoding="utf-8")
        except OSError as exc:
            return {"passed": False, "reason": "read_failed", "error": str(exc)}
        current_hash = self._hash_text(current_text)
        return {
            "passed": current_hash == proposal.proposed_hash,
            "reason": "applied_hash_matches" if current_hash == proposal.proposed_hash else "applied_hash_mismatch",
            "expected_hash": proposal.proposed_hash,
            "current_hash": current_hash,
        }

    def _transition(self, proposal: FileProposal, state: str, extra: Dict[str, Any]) -> None:
        if state not in FILE_WORKFLOW_STATES:
            state = "failed"
        proposal.state = state
        payload = {
            **proposal.to_dict(),
            **extra,
            "state": state,
            "provenance": self._provenance("file_workflow_state_transition"),
        }
        event = self._emit_state(state=state, path=proposal.path, payload=payload, proposal_id=proposal.proposal_id)
        proposal.state_history.append({"state": state, "timestamp": time.time(), "event_id": event.id})

    def _emit_state(self, *, state: str, path: str, payload: Dict[str, Any], proposal_id: Optional[str] = None):
        return self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="file_workflow_state",
            payload={
                "schema_version": SCHEMA_VERSION,
                "state": state,
                "proposal_id": proposal_id,
                "path": path,
                **payload,
            },
            confidence=0.9,
            tags=["file_workflow", state],
        )

    def _emit_operator_consent(self, *, action: str, target: str, decision: Dict[str, Any], consent: Optional[Dict[str, Any]]) -> None:
        expires_at = (consent or {}).get("expires_at") or time.time() + 300
        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="operator_consent",
            payload={
                "schema_version": SCHEMA_VERSION,
                "level": decision.get("provided_level"),
                "required_level": decision.get("required_level"),
                "action": action,
                "target": target,
                "approved": decision.get("approved"),
                "expires_at": expires_at,
                "policy_decision": decision,
                "provenance": self._provenance("operator_consent"),
            },
            confidence=0.95 if decision.get("approved") else 0.7,
            tags=["consent", action],
        )

    def _mark_expired_if_needed(self, proposal: FileProposal) -> bool:
        if proposal.state in {"expired", "applied", "validated", "rolled_back", "rejected", "failed"}:
            return proposal.state == "expired"
        if time.time() < proposal.proposal_expiry_timestamp:
            return False
        proposal.state = "expired"
        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="file_proposal_expired",
            payload={**proposal.to_dict(), "provenance": self._provenance("file_proposal_expired")},
            confidence=0.98,
            tags=["file_workflow", "expired"],
        )
        self._emit_state(
            state="expired",
            path=proposal.path,
            payload={**proposal.to_dict(), "reason": "proposal_expiry_timestamp_elapsed"},
            proposal_id=proposal.proposal_id,
        )
        return True

    def _path_policy_decision(self, path: Path, *, mutation: bool, allow_missing: bool = False) -> Dict[str, Any]:
        path = self._resolve(path)
        if not allow_missing and not path.exists() and not mutation:
            return self._deny("path_missing", path)
        if not self._within_any(path, self.allowed_roots):
            return self._deny("outside_allowed_roots", path)
        if self._within_any(path, self.denied_roots):
            return self._deny("inside_denied_root", path)
        if any(part.startswith(".") or part in WorkspacePolicy([], [], []).blocked_path_parts for part in path.parts):
            return self._deny("hidden_or_system_path_blocked", path)
        if mutation and self._within_any(path, self.read_only_roots):
            return self._deny("read_only_root", path)
        return {
            "schema_version": SCHEMA_VERSION,
            "allowed": True,
            "decision_state": "approve",
            "path": str(path),
            "mutation": mutation,
            "workspace_policy": self.workspace_policy(),
            "provenance": self._provenance("workspace_policy_check"),
        }

    def _deny(self, reason: str, path: Path) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "allowed": False,
            "decision_state": "reject",
            "reason": reason,
            "path": str(path),
            "workspace_policy": self.workspace_policy(),
            "provenance": self._provenance("workspace_policy_check"),
        }

    def _resolve_target(self, path: str) -> Path:
        target = Path(path)
        if not target.is_absolute():
            target = paths.root / target
        return self._resolve(target)

    @staticmethod
    def _resolve(path: Path) -> Path:
        return path.expanduser().resolve()

    @staticmethod
    def _within_any(path: Path, roots: List[Path]) -> bool:
        for root in roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _provenance(method: str) -> Dict[str, Any]:
        return {
            "source": "event_store+workspace_policy",
            "origin_service": "file_workflow",
            "derived_from": [],
            "collection_method": method,
            "observed_at": time.time(),
            "schema_version": SCHEMA_VERSION,
        }

    def _get_proposal(self, proposal_id: str) -> Optional[FileProposal]:
        return self.proposals.get(str(proposal_id))
