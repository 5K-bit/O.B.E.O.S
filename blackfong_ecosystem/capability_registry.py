from __future__ import annotations

import time
from copy import deepcopy
from typing import Any, Dict


SCHEMA_VERSION = "1.0"


CAPABILITY_REGISTRY: Dict[str, Dict[str, Any]] = {
    "daise_brain": {
        "status": "enabled",
        "description": "D.A.I.S.E evaluates requests, routes specialists, and emits final responses.",
        "risk": "low",
    },
    "event_store": {
        "status": "enabled",
        "description": "Canonical append-only local event database.",
        "risk": "low",
    },
    "conversation_memory": {
        "status": "enabled",
        "description": "Session transcript, summaries, pinned memory, and recall bundle.",
        "risk": "medium",
    },
    "hud": {
        "status": "enabled",
        "description": "Secondary local browser cockpit for chat, memory, traces, commands, and diagnostics alongside the native Blackfong Workspace.",
        "risk": "low",
    },
    "tui": {
        "status": "enabled",
        "description": "Terminal boot-home and fallback operator surface with the same authority contract as the rest of O.B.E.O.S.",
        "risk": "low",
    },
    "operator_actions": {
        "status": "enabled",
        "description": "Shared event-backed operator intent queue, action history, and change summaries.",
        "risk": "low",
    },
    "agents": {
        "status": "enabled",
        "description": "Specialists report upward to D.A.I.S.E rather than debating endlessly.",
        "risk": "medium",
        "members": ["coding", "lilo", "stitch", "securityplus", "thirty_days", "tinyprogrammer", "vision", "voice"],
    },
    "spine_authority": {
        "status": "enabled",
        "description": "Conflict resolution follows event memory, learner profile, live adapter, companion proposal, then cached project state.",
        "risk": "low",
    },
    "adapter_contracts": {
        "status": "enabled",
        "description": "Callable agents must satisfy adapter_contract_version 1.0 and admission checks.",
        "risk": "low",
    },
    "spine_modes": {
        "status": "enabled",
        "description": "Resource-aware startup modes: minimal, learning, creative, and full.",
        "risk": "low",
    },
    "learner_profile": {
        "status": "enabled",
        "description": "Federated synthesis of Lilo, Stitch, Security+, PCEP, and 30 Days without replacing local state.",
        "risk": "medium",
    },
    "vision_tools": {
        "status": "bounded",
        "description": "Torchvision, YOLOv5, OpenCV, and COCO-Stuff are knowledge sources and bounded local tools.",
        "risk": "medium",
    },
    "voice_controls": {
        "status": "gated",
        "description": "Backend voice loop can be started explicitly; passive listening is not assumed.",
        "risk": "high",
    },
    "sensor_awareness": {
        "status": "capability_only",
        "description": "Detects local readiness and hardware availability without continuous capture.",
        "risk": "high",
    },
    "tinyprogrammer_companion": {
        "status": "bounded",
        "description": "Companion node may receive creative briefs and feedback, not bypass D.A.I.S.E.",
        "risk": "medium",
    },
    "memory_chamber": {
        "status": "enabled",
        "description": "Thoughtglass-backed visual cognitive archive sourced from canonical events, memory artifacts, sessions, and capsules.",
        "risk": "medium",
    },
    "sentinel": {
        "status": "bounded",
        "description": "Environmental observations normalize into EventStore detections; raw artifacts follow the active archive capture profile.",
        "risk": "high",
    },
    "dream_layer": {
        "status": "bounded",
        "description": "D.A.I.S.E generates auditable style profiles and creative briefs for TinyProgrammer.",
        "risk": "medium",
    },
    "blackbox_archive": {
        "status": "enabled",
        "description": "Daily sealed capsules with bounded raw artifacts, manifests, and hashes; continuous capture requires explicit profile activation.",
        "risk": "high",
    },
    "skill_forge": {
        "status": "enabled",
        "description": "Skill metadata and callable capability packages extend the existing capability registry and dispatcher.",
        "risk": "medium",
    },
    "forge": {
        "status": "enabled",
        "description": "Device provisioning extends installer, Kubuntu, Docker, and node deployment contracts.",
        "risk": "high",
    },
    "file_mutation": {
        "status": "governed_project_roots",
        "description": "Available only through the governed project-root proposal workflow with validation, approval, apply, and rollback.",
        "risk": "high",
    },
}


def get_capability_registry() -> Dict[str, Dict[str, Any]]:
    """Return the single source of truth for declared O.B.E.O.S capabilities."""
    return deepcopy(CAPABILITY_REGISTRY)


def capability_snapshot() -> Dict[str, Any]:
    """Build a versioned capability snapshot for awareness and HUD surfaces."""
    registry = get_capability_registry()
    enabled = [
        name
        for name, item in registry.items()
        if item.get("status") in {"enabled", "fallback", "gated", "bounded", "capability_only", "governed_project_roots"}
    ]
    disabled = [name for name, item in registry.items() if str(item.get("status", "")).startswith("disabled")]
    return {
        "schema_version": SCHEMA_VERSION,
        "timestamp": time.time(),
        "source_of_truth": "capability_registry",
        "capabilities": registry,
        "summary": {
            "enabled_or_available": enabled,
            "disabled": disabled,
            "total": len(registry),
        },
    }
