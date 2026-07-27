from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from ..events import Event
from ..storage import EventStore, VectorStore


@dataclass
class IngestionEngine:
    """
    Engine 1: Ingestion

    - Watches folders
    - Imports capsules
    - Normalizes inputs into canonical events
    """

    event_store: EventStore
    vector_store: VectorStore

    def ingest_event(self, event: Event) -> None:
        """
        Ingest a pre-normalized event into the system.
        Black Box and Project 3000 can call this once they emit v0 events.
        """
        self.event_store.add_event(event)


@dataclass
class MemoryEngine:
    """
    Engine 2: Memory

    - Maintains event memory (via EventStore)
    - Will later maintain short-term session memory and concept graph
    """

    event_store: EventStore

    def recent_events(self, limit: int = 100) -> List[Event]:
        return self.event_store.recent_events(limit=limit)

    def events_between(self, start_ms: int, end_ms: int, limit: int = 1000) -> List[Event]:
        return self.event_store.list_events_between(start_ms=start_ms, end_ms=end_ms, limit=limit)

    def events_by_type(self, event_type: str, limit: int = 100) -> List[Event]:
        return self.event_store.list_events_by_type(type=event_type, limit=limit)

    def events_around(self, center_ms: int, window_ms: int, limit: int = 1000) -> List[Event]:
        return self.event_store.list_events_around(center_ms=center_ms, window_ms=window_ms, limit=limit)

    # new convenience wrappers
    def list_events_by_source_and_type(self, source: str, type: str, limit: int = 100) -> List[Event]:
        return self.event_store.list_events_by_source_and_type(source, type, limit=limit)

    def search_payload(self, keyword: str, limit: int = 100) -> List[Event]:
        return self.event_store.search_payload(keyword, limit=limit)

    def events_for_task(self, task: str) -> List[Event]:
        # simple keyword-based task relevance for now
        return self.search_payload(task)

    def events_around_event(self, event_id: str, window_ms: int, limit: int = 1000) -> List[Event]:
        return self.event_store.events_around_event(event_id, window_ms=window_ms, limit=limit)


@dataclass
class SynthesisEngine:
    """
    Engine 3: Synthesis

    - Given a task:
      - Retrieve related vectors
      - Query time-based events
      - Pull entity relationships
      - Build context window
      - Generate hypothesis
    """

    event_store: EventStore
    vector_store: VectorStore

    def build_context(self, task: str) -> Dict[str, Any]:
        """
        Structured context including both raw events and semantic hits.

        - recent_events: general time-ordered events
        - task_events: payload matches for the task string
        - semantic_context: vector search results
        """
        # raw recent events
        recent = self.event_store.list_events(limit=50)
        # events specifically relevant to the task
        task_evts = self.event_store.search_payload(task, limit=50)
        semantic_context = self.vector_store.search(task, k=5)
        return {
            "task": task,
            "recent_events": [e.to_dict() for e in recent],
            "task_events": [e.to_dict() for e in task_evts],
            "semantic_context": semantic_context,
        }

    def generate_hypothesis(self, task: str, context: Dict[str, Any]) -> Dict[str, Any]:
        # v0 stub: simple hypothesis based on task. Returns metadata for reasoning.
        hypothesis = f"[DAISE v0 stub] Hypothesis for task: {task}"
        recent_events = context.get("recent_events", [])
        task_events = context.get("task_events", [])
        semantic_context = context.get("semantic_context", [])
        # baseline confidence with simple signals
        base = 0.3
        base += 0.1 * len(task_events)
        base += 0.05 * len(semantic_context)
        if context.get("sensor_data"):
            base += 0.1
        confidence = min(1.0, base)
        return {
            "hypothesis": hypothesis,
            "confidence": confidence,
            "used_events": [e.get("id") for e in recent_events[:5]],
            "used_semantic": [s.get("id") for s in semantic_context[:5]],
        }


@dataclass
class ReflectionEngine:
    """
    Engine 4: Reflection

    - After producing output:
      - Summarize reasoning
      - Store session_summary event
      - Mark assumptions used
    """

    event_store: EventStore

    def reflect(
        self,
        *,
        task: str,
        hypothesis: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        # v0: simple reflection payload
        return {
            "task": task,
            "hypothesis": hypothesis,
            "notes": "v0 reflection stub",
            "context_snapshot_size": len(context.get("recent_events", [])),
        }

    def persist_session_summary(self, summary_payload: Dict[str, Any]) -> Event:
        event = Event.new(
            source="daise",
            type="session_summary",
            payload=summary_payload,
            confidence=1.0,
        )
        self.event_store.add_event(event)
        return event


@dataclass
class EvolutionEngine:
    """
    Engine 5: Evolution (Controlled)

    - Updates confidence weights, relationships, thresholds
    - Explicitly does NOT alter core constitution or identity
    """

    event_store: EventStore

    def update_from_feedback(self, feedback: Dict[str, Any]) -> None:
        # v0: placeholder for future controlled evolution logic.
        _ = feedback
        return None

