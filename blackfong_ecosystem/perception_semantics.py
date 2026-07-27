from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .policy_engine import SCHEMA_VERSION


class PerceptionGate:
    """Policy and freshness gate for using sensor observations in replies."""

    def __init__(self, *, max_age_seconds: float = 15.0, min_confidence: float = 0.6) -> None:
        self.max_age_seconds = max_age_seconds
        self.min_confidence = min_confidence

    def decide(
        self,
        *,
        modality: str,
        mode_enabled: bool,
        observation: Optional[Dict[str, Any]],
        policy_allowed: bool = True,
        expected_modality: Optional[str] = None,
        max_age_seconds: Optional[float] = None,
        min_confidence: Optional[float] = None,
    ) -> Dict[str, Any]:
        observation = dict(observation or {})
        now = time.time()
        expected = expected_modality or modality
        observed_modality = str(observation.get("modality") or modality)
        observed_at = self._observed_at(observation)
        age_seconds = None if observed_at is None else max(0.0, now - observed_at)
        max_age = self.max_age_seconds if max_age_seconds is None else max_age_seconds
        threshold = self.min_confidence if min_confidence is None else min_confidence
        confidence = self._confidence(observation, modality)

        checks = {
            "mode_enabled": bool(mode_enabled),
            "policy_allowed": bool(policy_allowed),
            "observation_present": bool(observation),
            "observation_fresh": age_seconds is not None and age_seconds <= max_age,
            "correct_modality": observed_modality == expected,
            "confidence_sufficient": confidence >= threshold,
        }
        allowed = all(checks.values())
        reasons = [name for name, passed in checks.items() if not passed]
        return {
            "schema_version": SCHEMA_VERSION,
            "modality": modality,
            "expected_modality": expected,
            "observed_modality": observed_modality,
            "perception_allowed": allowed,
            "decision_state": "approve" if allowed else "defer",
            "reason": "perception_context_allowed" if allowed else ",".join(reasons),
            "checks": checks,
            "confidence": confidence,
            "required_confidence": threshold,
            "freshness": {
                "age_seconds": age_seconds,
                "max_age_seconds": max_age,
                "status": "fresh" if age_seconds is not None and age_seconds <= max_age else "stale_or_missing",
            },
            "provenance": {
                "source": "sensor_capability_snapshot",
                "origin_service": "perception_gate",
                "derived_from": [str(observation.get("event_id"))] if observation.get("event_id") else [],
                "collection_method": "policy_freshness_confidence_gate",
                "observed_at": now,
                "schema_version": SCHEMA_VERSION,
            },
        }

    @staticmethod
    def _observed_at(observation: Dict[str, Any]) -> Optional[float]:
        for key in ("observed_at", "timestamp"):
            value = observation.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        provenance = observation.get("provenance") if isinstance(observation.get("provenance"), dict) else {}
        value = provenance.get("observed_at")
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    def _confidence(observation: Dict[str, Any], modality: str) -> float:
        keys = [
            "confidence",
            f"{modality}_confidence",
            "vision_confidence",
            "screen_confidence",
            "audio_confidence",
        ]
        for key in keys:
            value = observation.get(key)
            if isinstance(value, (int, float)):
                return max(0.0, min(1.0, float(value)))
        return 0.0
