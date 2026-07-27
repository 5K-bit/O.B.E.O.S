"""
Knowledge intelligence layer - Phase 1.

Domain-agnostic enrichment over any ingested collection. Given a collection name,
``run_phase1`` produces real, provenance-backed records:

- inventory  (sources, embedded vs reference, chunk counts) -> JSON artifact + event
- coverage   (sequence continuity, embedded ratio, presence flags) -> JSON + event
- timeline   (source-order events, no LLM) -> JSON + event
- provenance (per-chunk source map) -> JSON + queryable service
- graph      (collection/source nodes + edges + mentions) -> Memory Chamber tables + event

No LLM, HUD, compression, librarian, or influence layer in this phase. Nothing is
fabricated: when a collection has no embeddable sources the orchestrator returns an
empty result with a reason and emits ``knowledge_phase1_failed``.
"""

from .provenance import KnowledgeProvenanceService
from .collections import build_inventory, list_collections
from .collection_registry import CollectionMetadata, CollectionRegistry
from .coverage import build_coverage
from .compute import (
    ComputeMode,
    SynthesisResult,
    local_llm_available,
    resolve_compute_mode,
    synthesize,
)
from .gaps import build_gaps, detect_gaps, explain_gaps
from .librarian import LibrarianAgent
from .summaries import build_summary
from .timeline import build_timeline
from .orchestrator import Phase1Result, run_phase1

__all__ = [
    "KnowledgeProvenanceService",
    "CollectionMetadata",
    "CollectionRegistry",
    "list_collections",
    "build_inventory",
    "build_coverage",
    "build_timeline",
    "ComputeMode",
    "SynthesisResult",
    "local_llm_available",
    "resolve_compute_mode",
    "synthesize",
    "detect_gaps",
    "build_gaps",
    "explain_gaps",
    "build_summary",
    "LibrarianAgent",
    "run_phase1",
    "Phase1Result",
]
