from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from .events import Event
from .storage import EventStore
from .tinyprogrammer_companion import TinyProgrammerCompanion, tinyprogrammer_companion


SCHEMA_VERSION = "1.0"

DEFAULT_STYLE_PROFILE: Dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "profile_name": "blackfong_direct",
    "mood": "focused",
    "theme": "blackfong",
    "tempo": "deliberate",
    "style_tags": ["dark", "clean", "geometric", "blackfong"],
    "color_bias": ["black", "white", "cyan", "acid_green"],
    "program_weights": {
        "pattern": 3,
        "spiral": 3,
        "generative_glyphs": 4,
        "cellular_automata": 2,
        "starfield": 2,
        "random_walker": 1,
    },
    "prompt_directives": [
        "Prefer dark backgrounds with sharp luminous contrast.",
        "Favor clean geometry, grids, glyphs, symbols, signal traces, and tactical motion.",
        "Make the result feel like Blackfong influence, not a separate personality.",
    ],
}


def normalize_style_profile(profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = {**DEFAULT_STYLE_PROFILE, **dict(profile or {})}
    data["schema_version"] = SCHEMA_VERSION
    data["style_tags"] = _string_list(data.get("style_tags")) or DEFAULT_STYLE_PROFILE["style_tags"]
    data["color_bias"] = _string_list(data.get("color_bias")) or DEFAULT_STYLE_PROFILE["color_bias"]
    data["program_weights"] = _program_weights(data.get("program_weights"))
    data["prompt_directives"] = _string_list(data.get("prompt_directives")) or DEFAULT_STYLE_PROFILE["prompt_directives"]
    data["mood"] = str(data.get("mood") or DEFAULT_STYLE_PROFILE["mood"]).strip().lower()
    data["theme"] = str(data.get("theme") or DEFAULT_STYLE_PROFILE["theme"]).strip().lower()
    data["tempo"] = str(data.get("tempo") or DEFAULT_STYLE_PROFILE["tempo"]).strip().lower()
    return data


@dataclass
class MemoryArtifactService:
    """Create visual/archive artifacts as first-class EventStore memories."""

    event_store: EventStore

    def create_artifact(
        self,
        *,
        artifact_type: str,
        title: str,
        content: Dict[str, Any],
        source_event_ids: Optional[Iterable[str]] = None,
        confidence: float = 1.0,
    ) -> Event:
        self.event_store.init_db()
        payload = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": artifact_type,
            "title": title,
            "content": dict(content or {}),
            "source_event_ids": list(source_event_ids or []),
            "created_at": time.time(),
        }
        event = Event.new(
            source="memory_chamber",
            type="memory_artifact_created",
            payload=payload,
            confidence=confidence,
            tags=["memory_chamber", "artifact", artifact_type],
        )
        self.event_store.add_event(event)
        return event


@dataclass
class DAISEStyleProfileService:
    """Generate auditable DAISE style profiles for creative companion influence."""

    event_store: EventStore

    def generate_profile(
        self,
        *,
        influences: Optional[Iterable[Event]] = None,
        overrides: Optional[Dict[str, Any]] = None,
        persist: bool = True,
    ) -> Dict[str, Any]:
        self.event_store.init_db()
        influence_events = list(influences) if influences is not None else self.event_store.recent_events(limit=40)
        profile = normalize_style_profile(overrides)
        profile["influence"] = self._summarize_influence(influence_events)
        if profile["influence"]["sentinel_events"] > 0:
            profile["mood"] = "determined"
            profile["style_tags"] = sorted(set(profile["style_tags"] + ["alert", "tactical"]))
        if profile["influence"]["memory_events"] > 0:
            profile["style_tags"] = sorted(set(profile["style_tags"] + ["memory"]))
        profile["generated_at"] = time.time()
        profile["source_event_ids"] = [event.id for event in influence_events[:12]]

        event_id = None
        if persist:
            event = Event.new(
                source="daise",
                type="daise_style_profile_generated",
                payload=profile,
                confidence=0.92,
                tags=["daise", "style_profile", "tinyprogrammer", profile["profile_name"]],
            )
            self.event_store.add_event(event)
            event_id = event.id
        return {"schema_version": SCHEMA_VERSION, "event_id": event_id, "style_profile": profile}

    @staticmethod
    def _summarize_influence(events: List[Event]) -> Dict[str, Any]:
        return {
            "event_count": len(events),
            "sentinel_events": sum(1 for event in events if event.source == "sentinel" or event.type.startswith("sentinel_")),
            "memory_events": sum(1 for event in events if event.type in {"knowledge_ingested", "session_summary", "memory_artifact_created"}),
            "tinyprogrammer_events": sum(1 for event in events if event.source == "tinyprogrammer"),
        }


@dataclass
class DreamLayerService:
    """Creative bridge from DAISE memory/style state into TinyProgrammer controls."""

    event_store: EventStore
    tinyprogrammer: TinyProgrammerCompanion = tinyprogrammer_companion

    def generate_brief(self, style_profile: Optional[Dict[str, Any]] = None, *, persist: bool = True) -> Dict[str, Any]:
        self.event_store.init_db()
        profile = normalize_style_profile(style_profile)
        tags = ", ".join(profile["style_tags"])
        colors = ", ".join(profile["color_bias"])
        directives = " ".join(profile["prompt_directives"])
        brief = (
            f"DAISE direct persona style: {tags}. Use {colors} color bias. "
            f"Mood {profile['mood']}, theme {profile['theme']}, tempo {profile['tempo']}. {directives}"
        )
        payload = {
            "schema_version": SCHEMA_VERSION,
            "brief": brief,
            "style_profile": profile,
            "generated_at": time.time(),
        }
        event_id = None
        if persist:
            event = Event.new(
                source="dream_layer",
                type="dream_layer_brief_generated",
                payload=payload,
                confidence=0.9,
                tags=["dream_layer", "brief", "tinyprogrammer"],
            )
            self.event_store.add_event(event)
            event_id = event.id
        return {"schema_version": SCHEMA_VERSION, "event_id": event_id, **payload}

    def apply_to_tinyprogrammer(self, style_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        profile = normalize_style_profile(style_profile)
        brief = self.generate_brief(profile, persist=True)
        metadata = {
            "source": "dream_layer",
            "style_profile": profile,
            "brief_event_id": brief.get("event_id"),
        }
        results = {
            "brief": self.tinyprogrammer.send_brief(brief["brief"], metadata=metadata),
            "mood": self.tinyprogrammer.send_control("mood", profile["mood"]),
            "theme": self.tinyprogrammer.send_control("theme", profile["theme"]),
            "tempo": self.tinyprogrammer.send_control("tempo", profile["tempo"]),
            "program_weights": self.tinyprogrammer.send_control("program_weights", profile["program_weights"]),
            "style_profile": self.tinyprogrammer.send_control("style_profile", profile),
            "color_bias": self.tinyprogrammer.send_control("color_bias", profile["color_bias"]),
            "style_tags": self.tinyprogrammer.send_control("style_tags", profile["style_tags"]),
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "success": all(item.get("success", True) for item in results.values()),
            "brief_event_id": brief.get("event_id"),
            "style_profile": profile,
            "results": results,
        }


def _string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _program_weights(value: Any) -> Dict[str, int]:
    if not isinstance(value, dict):
        return dict(DEFAULT_STYLE_PROFILE["program_weights"])
    weights: Dict[str, int] = {}
    for key, raw_value in value.items():
        try:
            weight = int(raw_value)
        except (TypeError, ValueError):
            continue
        if weight > 0:
            weights[str(key)] = weight
    return weights or dict(DEFAULT_STYLE_PROFILE["program_weights"])

