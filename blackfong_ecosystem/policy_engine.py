from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = "1.0"
DECISION_STATES = {"approve", "reject", "defer", "clarify", "unknown"}
CONSENT_LEVELS = {
    1: {"label": "low-risk confirmation", "examples": ["launch_tinyprogrammer", "sensor_snapshot"]},
    2: {"label": "medium-risk approval", "examples": ["apply_file_diff", "start_live_sensor_mode"]},
    3: {"label": "high-risk typed confirmation", "examples": ["rollback_file_change", "future_destructive_action"]},
}
ACTION_CONSENT_LEVELS = {
    "launch_tinyprogrammer": 1,
    "tinyprogrammer_launch": 1,
    "launch_codingagent_external": 1,
    "sensor_snapshot": 1,
    "apply_file_diff": 2,
    "file_apply": 2,
    "start_live_sensor_mode": 2,
    "sensor_live_start": 2,
    "launch_redamon_mode": 2,
    "redamon_launch": 2,
    "rollback_file_change": 3,
    "file_rollback": 3,
}

POLICY_PRECEDENCE = [
    {"category": "safety", "priority": 1, "description": "Protect people, privacy, hardware capture modes, and irreversible actions."},
    {"category": "integrity", "priority": 2, "description": "Protect event truth, provenance, and non-stale claims."},
    {"category": "governance", "priority": 3, "description": "Keep D.A.I.S.E as the decision layer and enforce role boundaries."},
    {"category": "performance", "priority": 4, "description": "Prefer responsive behavior without weakening safety or integrity."},
    {"category": "user_convenience", "priority": 5, "description": "Make the system easy to use when higher policies allow it."},
]

PHASE_57_EVENT_TYPES = {
    "awareness_snapshot",
    "performance_snapshot",
    "latency_observation",
    "routing_quality_observation",
    "memory_retrieval_observation",
    "backend_health_observation",
    "environment_snapshot",
    "sensor_capability_snapshot",
    "audio_device_observation",
    "camera_device_observation",
    "queue_pressure_observation",
    "consistency_conflict",
    "request_outcome",
    "agent_trust_update",
    "policy_decision",
    "capability_snapshot",
    "identity_resolution_event",
    "operator_consent",
    "file_workflow_state",
    "file_proposal_created",
    "file_proposal_expired",
    "file_pre_apply_validation",
    "file_change_applied",
    "file_post_apply_validation",
    "perception_gate_decision",
    "vision_sensor_observation",
    "screen_snapshot_observation",
    "audio_sensor_observation",
    "companion_brief_scope_decision",
    "tinyprogrammer_process_state",
    "notification_created",
    "notification_read",
    "notification_action_taken",
    "notification_delivery_result",
}


@dataclass
class PolicyDecision:
    decision_state: str
    approved: bool
    winning_policy: Dict[str, Any]
    reasons: List[str]
    risk_flags: List[str]
    triggered_policies: List[Dict[str, Any]]
    provenance: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PolicyEngine:
    """Small operational doctrine layer for D.A.I.S.E and local tools."""

    def __init__(self, schema_version: str = SCHEMA_VERSION) -> None:
        self.schema_version = schema_version

    def manifest(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "precedence": POLICY_PRECEDENCE,
            "doctrine": {
                "blackfong": "Blackfong adjudicates truth.",
                "daise": "D.A.I.S.E communicates truth.",
                "tinyprogrammer": "TinyProgrammer creates within bounded companion scope.",
                "jarvis": "Jarvis is only a legacy alias for D.A.I.S.E, never a second authority.",
            },
            "decision_states": sorted(DECISION_STATES),
            "consent_levels": CONSENT_LEVELS,
            "rules": [
                "never_modify_files_without_approval",
                "never_act_on_ambiguous_high_risk_action",
                "never_claim_hidden_state_access",
                "never_use_stale_observations_as_current_facts",
                "never_bypass_daise_for_tinyprogrammer",
                "never_activate_camera_or_microphone_without_explicit_mode",
                "telegram_transport_denies_privileged_operator_actions",
            ],
        }

    def evaluate(self, action: str, context: Optional[Dict[str, Any]] = None) -> PolicyDecision:
        context = dict(context or {})
        triggered: List[Dict[str, Any]] = []
        text = str(context.get("text") or "")
        lower_text = text.lower()
        transport = str(context.get("transport") or "").strip().lower()

        def add(category: str, state: str, rule: str, reason: str, flag: str) -> None:
            triggered.append(
                {
                    "category": category,
                    "priority": self._priority_for(category),
                    "decision_state": state,
                    "rule": rule,
                    "reason": reason,
                    "risk_flag": flag,
                }
                )

        if transport == "telegram":
            privileged_reason = self._telegram_privileged_reason(lower_text, action=action, context=context)
            if privileged_reason is not None:
                add(
                    "governance",
                    "reject",
                    "telegram_transport_denies_privileged_operator_actions",
                    privileged_reason,
                    "telegram_capability_boundary",
                )

        if context.get("claim_hidden_state") or (action == "final_response" and self._mentions_hidden_state_claim(lower_text)):
            add(
                "safety",
                "reject",
                "never_claim_hidden_state_access",
                "The system must not claim hidden camera, microphone, screenshot, or private state access.",
                "hidden_state_claim",
            )

        if context.get("active_capture_requested") or self._requests_active_capture(lower_text):
            if not context.get("explicit_capture_mode"):
                add(
                    "safety",
                    "clarify",
                    "never_activate_camera_or_microphone_without_explicit_mode",
                    "Active microphone or camera capture requires an explicit operator-enabled mode.",
                    "capture_requires_explicit_mode",
                )

        if action == "file_mutation" or context.get("file_mutation_requested"):
            consent = self.evaluate_consent(
                action=str(context.get("consent_action") or "apply_file_diff"),
                consent=context.get("consent"),
                target=context.get("target"),
            )
            if not context.get("approval_granted") or not consent["approved"]:
                add(
                    "integrity",
                    "defer",
                    "never_modify_files_without_approval",
                    "File mutation requires approval plus propose, diff preview, validation, apply, rollback workflow, and adequate operator consent.",
                    "file_mutation_requires_approval",
                )

        if context.get("stale_fact_as_current"):
            add(
                "integrity",
                "defer",
                "never_use_stale_observations_as_current_facts",
                "Stale observations must be labeled with freshness instead of presented as current facts.",
                "stale_observation",
            )

        if context.get("tinyprogrammer_bypass"):
            add(
                "governance",
                "reject",
                "never_bypass_daise_for_tinyprogrammer",
                "TinyProgrammer must stay bounded to briefs, proposals, summaries, reflection, and feedback.",
                "tinyprogrammer_bypass",
            )

        if context.get("high_risk") and context.get("ambiguous"):
            add(
                "safety",
                "clarify",
                "never_act_on_ambiguous_high_risk_action",
                "High-risk ambiguous requests should ask for clarification before execution.",
                "ambiguous_high_risk_action",
            )

        if context.get("insufficient_evidence"):
            add(
                "governance",
                "unknown",
                "unknown_is_first_class",
                "D.A.I.S.E lacks enough evidence and should return an intentional unknown state.",
                "insufficient_evidence",
            )

        if not triggered:
            triggered.append(
                {
                    "category": "user_convenience",
                    "priority": self._priority_for("user_convenience"),
                    "decision_state": "approve",
                    "rule": "allowed_by_default_when_safe",
                    "reason": "No higher-precedence policy blocked the action.",
                    "risk_flag": "none",
                }
            )

        triggered.sort(key=lambda item: item["priority"])
        winning = triggered[0]
        state = str(winning["decision_state"])
        if state not in DECISION_STATES:
            state = "unknown"
        return PolicyDecision(
            decision_state=state,
            approved=state == "approve",
            winning_policy=winning,
            reasons=[str(item["reason"]) for item in triggered],
            risk_flags=[str(item["risk_flag"]) for item in triggered if item.get("risk_flag") != "none"],
            triggered_policies=triggered,
            provenance={
                "source": "policy_manifest",
                "origin_service": "policy_engine",
                "derived_from": [],
                "collection_method": "rule_evaluation",
                "observed_at": time.time(),
                "schema_version": self.schema_version,
            },
        )

    def required_consent_level(self, action: str) -> Dict[str, Any]:
        normalized = str(action or "").strip().lower()
        level = ACTION_CONSENT_LEVELS.get(normalized, 1)
        return {
            "action": normalized,
            "level": level,
            **CONSENT_LEVELS[level],
            "schema_version": self.schema_version,
        }

    def evaluate_consent(
        self,
        *,
        action: str,
        consent: Optional[Dict[str, Any]] = None,
        target: Any = None,
    ) -> Dict[str, Any]:
        required = self.required_consent_level(action)
        consent = dict(consent or {})
        provided_level = int(consent.get("level") or 0)
        approved = bool(consent.get("approved")) and provided_level >= int(required["level"])
        if int(required["level"]) >= 3:
            expected = str(consent.get("expected_phrase") or f"CONFIRM {str(action).upper()}")
            approved = approved and str(consent.get("typed_confirmation") or "") == expected
        return {
            "schema_version": self.schema_version,
            "action": str(action or "").strip().lower(),
            "target": target,
            "required_level": required,
            "provided_level": provided_level,
            "approved": approved,
            "decision_state": "approve" if approved else "defer",
            "reason": "Operator consent satisfies the required level." if approved else "Operator consent is missing or below the required level.",
            "provenance": {
                "source": "policy_manifest",
                "origin_service": "policy_engine",
                "derived_from": [],
                "collection_method": "consent_level_evaluation",
                "observed_at": time.time(),
                "schema_version": self.schema_version,
            },
        }

    def check_schema_compatibility(self, event_type: str, version: Optional[str]) -> Dict[str, Any]:
        version_text = str(version or "").strip()
        supported = "1.0"
        if not version_text:
            status = "unknown"
            reason = "No schema version was provided."
        elif event_type not in PHASE_57_EVENT_TYPES:
            status = "unknown"
            reason = "Event type is not governed by the Phase 5.7 compatibility table."
        else:
            parsed = self._parse_version(version_text)
            supported_parsed = self._parse_version(supported)
            if parsed is None:
                status = "unknown"
                reason = "Version string could not be parsed."
            elif parsed == supported_parsed:
                status = "supported"
                reason = "Version is explicitly supported."
            elif parsed[0] == supported_parsed[0] and parsed[1] < supported_parsed[1]:
                status = "backward_compatible"
                reason = "Older minor version is backward-compatible with the supported major version."
            elif parsed[0] > supported_parsed[0]:
                status = "incompatible"
                reason = "Future major version is incompatible until the schema is upgraded."
            else:
                status = "unknown"
                reason = "Version may be forward-compatible, but support has not been declared."

        return {
            "event_type": event_type,
            "schema_version": version_text or None,
            "supported_version": supported,
            "compatibility_status": status,
            "reason": reason,
            "provenance": {
                "source": "policy_manifest",
                "origin_service": "policy_engine",
                "derived_from": [],
                "collection_method": "schema_version_check",
                "observed_at": time.time(),
                "schema_version": self.schema_version,
            },
        }

    def _priority_for(self, category: str) -> int:
        for item in POLICY_PRECEDENCE:
            if item["category"] == category:
                return int(item["priority"])
        return 99

    @staticmethod
    def _parse_version(version: str) -> Optional[tuple[int, int]]:
        match = re.match(r"^(\d+)\.(\d+)$", version)
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    @staticmethod
    def _requests_active_capture(lower_text: str) -> bool:
        capture_verbs = ("turn on", "activate", "start", "record", "listen", "watch", "capture")
        capture_targets = ("camera", "webcam", "microphone", "mic")
        return any(verb in lower_text for verb in capture_verbs) and any(target in lower_text for target in capture_targets)

    @staticmethod
    def _mentions_hidden_state_claim(lower_text: str) -> bool:
        return any(
            phrase in lower_text
            for phrase in (
                "i can see you",
                "i am watching",
                "i can hear you",
                "i accessed your camera",
                "i accessed your microphone",
                "i read your hidden",
            )
        )

    @staticmethod
    def _telegram_privileged_reason(lower_text: str, *, action: str, context: Dict[str, Any]) -> Optional[str]:
        if action == "file_mutation" or context.get("file_mutation_requested"):
            return (
                "Telegram transport is limited to chat, status, and bounded agent conversation. "
                "Privileged file mutation must stay on trusted O.B.E.O.S operator surfaces."
            )

        capability_boundary = set(str(item).strip().lower() for item in (context.get("capabilities") or []))
        if capability_boundary and not capability_boundary.issuperset({"chat", "status_read", "agent_chat"}):
            return (
                "Telegram transport did not present the canonical restricted capability set, "
                "so O.B.E.O.S will not honor privileged operator actions from that path."
            )

        privileged_patterns = {
            "filesystem mutation": (
                ("write", "edit", "modify", "delete", "remove", "rename", "move", "create"),
                ("file", "folder", "directory", "repo", "repository", "codebase", "patch"),
            ),
            "shell execution": (
                ("run", "execute", "launch", "open", "start"),
                ("command", "powershell", "cmd", "bash", "terminal", "script"),
            ),
            "service control": (
                ("restart", "start", "stop", "kill", "reload"),
                ("server", "service", "backend", "hud", "ollama", "docker", "worker"),
            ),
            "deployment": (
                ("deploy", "publish", "release", "ship", "push"),
                ("server", "ec2", "aws", "cloud", "production", "github"),
            ),
            "credentials": (
                ("show", "reveal", "rotate", "change", "set", "create", "delete", "reset"),
                ("password", "secret", "token", "api key", "apikey", "credential", "account", "login"),
            ),
            "installation": (
                ("install", "uninstall", "upgrade", "downgrade"),
                ("package", "dependency", "plugin", "service", "docker", "pip", "apt", "brew"),
            ),
        }
        for label, (verbs, nouns) in privileged_patterns.items():
            if any(verb in lower_text for verb in verbs) and any(noun in lower_text for noun in nouns):
                return (
                    "Telegram transport is limited to chat, status, and bounded agent conversation. "
                    f"Requests involving {label} are denied centrally on that transport."
                )
        return None
