from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .events import Event
from .spine_adapters import SpineAdapterRegistry
from .spine_contract import (
    ADAPTER_CONTRACT_VERSION,
    AuthoritySource,
    SpineSignal,
    resolve_authoritative_signal,
)
from .storage import EventStore


LEARNING_AGENTS = ("lilo", "stitch", "securityplus", "thirty_days")


@dataclass(frozen=True)
class UnifiedLearnerProfile:
    """Synthesized layer above separate learning-agent state."""

    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    source_profiles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    adapter_contract_version: str = ADAPTER_CONTRACT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "adapter_contract_version": self.adapter_contract_version,
            "profile_type": "federated_synthesis",
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "recommendations": self.recommendations,
            "source_profiles": self.source_profiles,
            "conflicts": self.conflicts,
            "authority_order": [
                AuthoritySource.EVENT_MEMORY.value,
                AuthoritySource.UNIFIED_LEARNER_PROFILE.value,
                AuthoritySource.LIVE_ADAPTER_RESPONSE.value,
                AuthoritySource.COMPANION_PROPOSAL.value,
                AuthoritySource.CACHED_LOCAL_PROJECT_STATE.value,
            ],
        }


class LearnerProfileService:
    """
    Builds the unified learner profile without replacing local project state.
    """

    def __init__(
        self,
        *,
        adapter_registry: Optional[SpineAdapterRegistry] = None,
        event_store: Optional[EventStore] = None,
    ) -> None:
        self.event_store = event_store or EventStore()
        self.adapter_registry = adapter_registry or SpineAdapterRegistry(event_store=self.event_store)

    def synthesize(self, *, persist: bool = False) -> UnifiedLearnerProfile:
        source_profiles: Dict[str, Dict[str, Any]] = {}
        strengths: List[str] = []
        weaknesses: List[str] = []
        recommendations: List[str] = []

        for agent in LEARNING_AGENTS:
            response = self.adapter_registry.run(agent, "status")
            source_profiles[agent] = response.to_dict()
            metrics = response.metrics

            if agent == "lilo":
                if int(metrics.get("pcep_question_count", 0)) > 0:
                    strengths.append("PCEP foundation content is available for Lilo fold-in.")
                if int(metrics.get("question_count", 0)) < 50:
                    weaknesses.append("Lilo's fallback certification question bank is still shallow.")
                    recommendations.append("Expand Lilo with more certification scenarios and explanations.")

            if agent == "stitch":
                if int(metrics.get("exercise_count", 0)) > 0:
                    strengths.append("Stitch has a working Python exercise foundation.")
                if int(metrics.get("exercise_count", 0)) < 30:
                    weaknesses.append("Stitch needs deeper Python exercise coverage.")
                    recommendations.append("Add project tracks, tests, debugging drills, and concept checks to Stitch.")

            if agent == "securityplus":
                if int(metrics.get("objective_count", 0)) > 0:
                    strengths.append("Security+ objective-level structure exists.")
                if int(metrics.get("question_count", 0)) < 150:
                    weaknesses.append("Security+ question coverage is too small for serious teaching depth.")
                    recommendations.append("Grow Security+ to at least 300 questions across all SY0-701 domains.")

            if agent == "thirty_days":
                if int(metrics.get("lesson_count", 0)) >= 30:
                    strengths.append("30 Days of Python has full static lesson coverage.")
                recommendations.append("Convert 30 Days lessons into tracked exercises delegated to Stitch.")

        profile = UnifiedLearnerProfile(
            strengths=dedupe(strengths),
            weaknesses=dedupe(weaknesses),
            recommendations=dedupe(recommendations),
            source_profiles=source_profiles,
            conflicts=[],
        )

        if persist:
            self.event_store.init_db()
            self.event_store.add_event(
                Event.new(
                    source="daise",
                    type="learner_profile_update",
                    payload=profile.to_dict(),
                    confidence=0.86,
                    tags=["spine", "learning", "profile"],
                )
            )

        return profile

    def resolve_learning_conflict(
        self,
        *,
        key: str,
        event_memory_value: Any,
        unified_profile_value: Any,
        live_adapter_value: Any,
        cached_project_value: Any,
    ) -> Dict[str, Any]:
        """Public helper used by tests and HUD flows to enforce spine authority."""
        resolution = resolve_authoritative_signal(
            [
                SpineSignal(key, event_memory_value, AuthoritySource.EVENT_MEMORY, 1.0),
                SpineSignal(key, unified_profile_value, AuthoritySource.UNIFIED_LEARNER_PROFILE, 0.9),
                SpineSignal(key, live_adapter_value, AuthoritySource.LIVE_ADAPTER_RESPONSE, 0.8),
                SpineSignal(key, cached_project_value, AuthoritySource.CACHED_LOCAL_PROJECT_STATE, 0.7),
            ],
            event_store=self.event_store,
            context={"domain": "learning"},
        )
        return resolution.to_dict()


def dedupe(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
