"""Stable cross-component contracts for OBEOS v1."""

from .event_v1 import EVENT_CONTRACT_VERSION, EventEnvelope
from .health_v1 import HEALTH_CONTRACT_VERSION, HealthRecord, aggregate_health

__all__ = [
    "EVENT_CONTRACT_VERSION",
    "EventEnvelope",
    "HEALTH_CONTRACT_VERSION",
    "HealthRecord",
    "aggregate_health",
]
