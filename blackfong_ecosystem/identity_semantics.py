from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional

from .conversation_memory import ConversationMemoryService, conversation_memory_service


SCHEMA_VERSION = "1.0"


IDENTITY_DOCTRINE: Dict[str, Any] = {
    "canonical_identities": {
        "daise": {
            "display_name": "D.A.I.S.E",
            "role": "assistant_identity",
            "doctrine": "D.A.I.S.E communicates truth.",
            "legacy_aliases": ["jarvis", "jarvis_agent"],
        },
        "blackfong": {
            "display_name": "Blackfong",
            "role": "spine_governance_ecosystem",
            "doctrine": "Blackfong adjudicates truth.",
            "legacy_aliases": ["daise_core", "daise_orchestrator"],
        },
        "tinyprogrammer": {
            "display_name": "TinyProgrammer",
            "role": "creative_companion",
            "doctrine": "TinyProgrammer creates within bounded companion scope.",
            "legacy_aliases": ["tiny"],
        },
    },
    "rules": [
        "Jarvis is never a second authority.",
        "Jarvis is only a compatibility name for D.A.I.S.E-facing commands.",
        "Blackfong is never presented as the speaking assistant unless explicitly in diagnostic/operator context.",
    ],
}


@dataclass
class IdentityResolution:
    input_name: str
    canonical_identity: str
    display_name: str
    resolution_method: str
    confidence: float
    doctrine: str
    alias_used: bool

    def to_dict(self) -> Dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}


class IdentityRegistry:
    """Machine-readable D.A.I.S.E/Blackfong/Jarvis identity doctrine."""

    def __init__(self, doctrine: Optional[Dict[str, Any]] = None) -> None:
        self.doctrine = doctrine or IDENTITY_DOCTRINE
        self.alias_map = self._build_alias_map()

    def manifest(self) -> Dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, **self.doctrine}

    def resolve(self, name: str) -> IdentityResolution:
        raw = str(name or "").strip()
        normalized = self._normalize(raw)
        if normalized in self.doctrine["canonical_identities"]:
            data = self.doctrine["canonical_identities"][normalized]
            return IdentityResolution(
                input_name=raw,
                canonical_identity=normalized,
                display_name=str(data["display_name"]),
                resolution_method="canonical",
                confidence=1.0,
                doctrine=str(data["doctrine"]),
                alias_used=False,
            )
        canonical = self.alias_map.get(normalized)
        if canonical:
            data = self.doctrine["canonical_identities"][canonical]
            return IdentityResolution(
                input_name=raw,
                canonical_identity=canonical,
                display_name=str(data["display_name"]),
                resolution_method="legacy_alias_map",
                confidence=0.96,
                doctrine=str(data["doctrine"]),
                alias_used=True,
            )
        return IdentityResolution(
            input_name=raw,
            canonical_identity=normalized or "unknown",
            display_name=raw or "Unknown",
            resolution_method="unknown",
            confidence=0.0,
            doctrine="No identity doctrine matched the supplied name.",
            alias_used=False,
        )

    def resolve_mentions(self, text: str) -> List[IdentityResolution]:
        found: List[IdentityResolution] = []
        seen = set()
        for name in self._candidate_names():
            pattern = r"\b" + re.escape(name) + r"\b"
            if not re.search(pattern, text or "", flags=re.IGNORECASE):
                continue
            resolution = self.resolve(name)
            key = (resolution.input_name.lower(), resolution.canonical_identity)
            if key in seen:
                continue
            seen.add(key)
            found.append(resolution)
        return found

    def emit_resolution_event(
        self,
        resolution: IdentityResolution,
        *,
        memory_service: ConversationMemoryService = conversation_memory_service,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        origin: str = "blackfong",
        context: Optional[Dict[str, Any]] = None,
    ):
        payload = {
            **resolution.to_dict(),
            "context": context or {},
            "resolved_at": time.time(),
            "provenance": {
                "source": "identity_registry",
                "origin_service": "blackfong_identity_semantics",
                "derived_from": [],
                "collection_method": resolution.resolution_method,
                "observed_at": time.time(),
                "schema_version": SCHEMA_VERSION,
            },
        }
        return memory_service.persist_workflow_event(
            source=origin,
            event_type="identity_resolution_event",
            payload=payload,
            confidence=resolution.confidence,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["identity", resolution.canonical_identity],
        )

    def _build_alias_map(self) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        for canonical, data in self.doctrine["canonical_identities"].items():
            for alias in data.get("legacy_aliases", []):
                alias_map[self._normalize(alias)] = canonical
        return alias_map

    def _candidate_names(self) -> Iterable[str]:
        names = set(self.doctrine["canonical_identities"].keys())
        names.update(self.alias_map.keys())
        return sorted(names, key=len, reverse=True)

    @staticmethod
    def _normalize(name: str) -> str:
        return str(name or "").strip().lower().replace(".", "").replace("-", "_").replace(" ", "_")


identity_registry = IdentityRegistry()
