"""Evidence projection over spatial history and canonical events, without another raw-data store."""

from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone
import hashlib


class ContextFabric:
    def __init__(self, engine, event_reader=None, memory_reader=None):
        self.engine = engine
        self.event_reader = event_reader
        self.memory_reader = memory_reader

    def describe(self, *, viewport=None, at=None, selected=None):
        snapshot = self.engine.query(viewport=viewport, at=at, limit=1000)
        observations = snapshot["observations"]
        if selected:
            observations = [
                o for o in observations if o["entity_id"] == selected
            ] or observations
        now = datetime.now(timezone.utc)
        groups = {}
        for observation in observations:
            position = observation["position"]
            key = (
                observation["layer"],
                round(position["latitude"]),
                round(position["longitude"]),
            )
            groups.setdefault(key, []).append(observation)
        situations = []
        for (layer, lat, lon), items in sorted(
            groups.items(), key=lambda item: -len(item[1])
        )[:20]:
            times = [datetime.fromisoformat(o["observed_at"]) for o in items]
            modes = dict(Counter(o["measurement_kind"] for o in items))
            evidence = [
                {
                    "entity_id": o["entity_id"],
                    "provider": o["provenance"]["provider"],
                    "observed_at": o["observed_at"],
                    "source_url": o["provenance"]["source_url"],
                }
                for o in items[:25]
            ]
            situations.append(
                {
                    "situation_id": hashlib.sha256(
                        f"{layer}:{lat}:{lon}".encode()
                    ).hexdigest()[:20],
                    "summary": f"{len(items)} {layer} records near {lat}°, {lon}°",
                    "location_bounds": {
                        "south": min(o["position"]["latitude"] for o in items),
                        "north": max(o["position"]["latitude"] for o in items),
                        "west": min(o["position"]["longitude"] for o in items),
                        "east": max(o["position"]["longitude"] for o in items),
                    },
                    "time_bounds": {
                        "start": min(times).isoformat(),
                        "end": max(times).isoformat(),
                    },
                    "freshness_seconds": max(0, (now - max(times)).total_seconds()),
                    "uncertainty": "Proximity is a search aid, not evidence of a causal relationship.",
                    "measurement_kinds": modes,
                    "evidence": evidence,
                    "suggestions": [
                        "Inspect source attribution and freshness",
                        "Compare this area across the timeline",
                    ],
                }
            )
        related = (
            list(self.event_reader(selected, 20))
            if self.event_reader and selected
            else []
        )
        memories = (
            list(self.memory_reader(selected, 10))
            if self.memory_reader and selected
            else []
        )
        return {
            "schema_version": "1.0",
            "generated_at": now.isoformat(),
            "situations": situations,
            "count": len(observations),
            "truncated": snapshot["next_cursor"] is not None,
            "related_events": related,
            "memory_references": memories,
            "explanation": "This summary describes reported data. Missing contacts do not establish absence.",
            "camera_action": None,
        }
