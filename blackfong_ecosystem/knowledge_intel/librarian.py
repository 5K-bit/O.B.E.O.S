"""
Librarian agent - the grounded query interface over the knowledge layer.

``LibrarianAgent.answer`` combines deterministic facts (inventory/coverage/gaps)
with semantic retrieval (RAG over the embedded chunks), then synthesizes a grounded
natural-language answer with full provenance attached. It is fully domain-agnostic:
the same code answers questions about comics, cybersecurity, programming, or project
docs - only the data differs.

Degradation policy (offline-first):
- Retrieval and facts are deterministic and always real.
- When a local model is available (and the mode permits), it rephrases the retrieved
  passages into prose, grounded strictly in those passages.
- When no model is available, a structured-facts answer is returned: the top passages
  with their provenance. Nothing is fabricated; an empty knowledge base yields an
  honest "no relevant passages found" answer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..events import Event
from ..ingestion.source_registry import SourceRegistry, slugify_collection
from ..storage import EventStore
from . import collections as collections_mod
from . import compute as compute_mod
from . import coverage as coverage_mod
from . import gaps as gaps_mod
from . import sources as src
from .provenance import KnowledgeProvenanceService

_MAX_EXCERPT_CHARS = 500


class LibrarianAgent:
    """Answers questions over one collection or the whole knowledge base."""

    def __init__(
        self,
        *,
        registry: SourceRegistry,
        vector_store: Any = None,
        chunks_dir: Path,
        collection_registry: Any = None,
        event_store: Optional[EventStore] = None,
    ) -> None:
        self.registry = registry
        self.vector_store = vector_store
        self.chunks_dir = Path(chunks_dir)
        self.collection_registry = collection_registry
        self.event_store = event_store
        self.provenance = KnowledgeProvenanceService(
            registry=registry,
            chunks_dir=self.chunks_dir,
            vector_store=vector_store,
            collection_registry=collection_registry,
        )

    # ---- retrieval -----------------------------------------------------

    def _retrieve(self, question: str, *, collection: Optional[str], top_k: int) -> List[Dict[str, Any]]:
        if self.vector_store is None:
            return []
        slug = slugify_collection(collection) if collection else ""
        # Over-fetch when filtering by collection so the post-filter still has depth.
        fetch_k = top_k * 5 if slug else top_k
        try:
            if slug:
                results = self.vector_store.search(
                    question,
                    k=fetch_k,
                    metadata_filter={"collection": slug},
                )
            else:
                results = self.vector_store.search(question, k=fetch_k)
        except TypeError:
            results = self.vector_store.search(question, k=fetch_k)
        except Exception:
            return []
        if slug:
            results = [
                r for r in results
                if str((r.get("metadata") or {}).get("collection", "")) == slug
            ]
        return results[:top_k]

    def _collection_facts(self, collection: str) -> Dict[str, Any]:
        inv = collections_mod.build_inventory(
            self.registry, collection, chunks_dir=self.chunks_dir, collection_registry=self.collection_registry
        )
        if not inv.get("found"):
            return {}
        cov = coverage_mod.build_coverage(
            self.registry, collection, chunks_dir=self.chunks_dir, collection_registry=self.collection_registry
        )
        gap_record = gaps_mod.build_gaps(
            self.registry, collection, chunks_dir=self.chunks_dir, collection_registry=self.collection_registry
        )
        return {
            "collection": inv.get("collection"),
            "domain": inv.get("domain", ""),
            "sources": inv.get("sources", 0),
            "embedded": inv.get("embedded", 0),
            "total_chunks": inv.get("total_chunks", 0),
            "sequence_available": (cov.get("sequence", {}) or {}).get("available", False),
            "gap_count": gap_record.get("gap_count", 0),
        }

    # ---- answer --------------------------------------------------------

    def answer(
        self,
        question: str,
        *,
        collection: Optional[str] = None,
        top_k: int = 6,
        mode: Any = None,
    ) -> Dict[str, Any]:
        """Answer a question, grounded in retrieved passages + deterministic facts."""
        question = (question or "").strip()
        slug = slugify_collection(collection) if collection else ""

        results = self._retrieve(question, collection=collection, top_k=top_k)
        annotated = self.provenance.annotate(results) if results else []

        provenance: List[Dict[str, Any]] = []
        passages: List[Dict[str, Any]] = []
        for item in annotated:
            prov = item.get("provenance", {}) or {}
            meta = item.get("metadata", {}) or {}
            text = str(item.get("text", "") or "")
            provenance.append(
                {
                    "chunk_id": prov.get("chunk_id") or item.get("id"),
                    "collection": prov.get("collection", ""),
                    "source_file": prov.get("source_file", ""),
                    "section": prov.get("section", "") or str(meta.get("section_title", "")),
                    "relevance": prov.get("confidence", round(float(item.get("relevance", 0.0)), 4)),
                    "retrieval_rank": prov.get("retrieval_rank"),
                }
            )
            passages.append(
                {
                    "rank": prov.get("retrieval_rank"),
                    "source_file": prov.get("source_file", ""),
                    "section": prov.get("section", "") or str(meta.get("section_title", "")),
                    "excerpt": text[:_MAX_EXCERPT_CHARS],
                }
            )

        facts = self._collection_facts(collection) if slug else {}

        fallback_text = self._structured_answer(question, collection=slug, passages=passages, facts=facts)

        grounding = {
            "collection": slug or "(whole knowledge base)",
            "facts": facts,
            "passages": [
                {
                    "source": p["source_file"],
                    "section": p["section"],
                    "text": p["excerpt"],
                }
                for p in passages
            ],
        }

        synthesis = compute_mod.synthesize(
            system=(
                "You are the OBEOS knowledge librarian. Answer the question using ONLY "
                "the retrieved passages and facts provided. Cite sources by their file "
                "name when helpful. If the passages do not answer the question, say so "
                "and point to what is available. Never invent sources or facts."
            ),
            grounding=grounding,
            question=question,
            mode=mode,
            fallback=lambda: fallback_text,
            agent="daise",
            response_mode="standard",
        )

        record = {
            "question": question,
            "collection": slug,
            "answer": synthesis.text,
            "used_llm": synthesis.used_llm,
            "compute_used": "local_llm" if synthesis.used_llm else "structured_facts",
            "fallback_reason": synthesis.fallback_reason,
            "retrieved_count": len(passages),
            "provenance": provenance,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        self._emit(record)
        return record

    def _structured_answer(
        self,
        question: str,
        *,
        collection: str,
        passages: List[Dict[str, Any]],
        facts: Dict[str, Any],
    ) -> str:
        where = f"collection '{collection}'" if collection else "the knowledge base"
        if not passages:
            return (
                f"No relevant passages found in {where} for: \"{question}\". "
                "Nothing in the embedded sources matches this query."
            )
        lines = [f"Based on {len(passages)} passage(s) from {where}:"]
        for i, p in enumerate(passages, start=1):
            name = Path(p["source_file"]).name if p["source_file"] else "(unknown source)"
            sect = f" \u00a7 {p['section']}" if p.get("section") else ""
            excerpt = " ".join(p["excerpt"].split())
            lines.append(f"{i}. [{name}{sect}] {excerpt}")
        if facts:
            lines.append(
                f"(Collection facts: {facts.get('embedded', 0)} embedded of "
                f"{facts.get('sources', 0)} sources, {facts.get('total_chunks', 0)} chunks.)"
            )
        return "\n".join(lines)

    def _emit(self, record: Dict[str, Any]) -> None:
        if self.event_store is None:
            return
        ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        self.event_store.add_event(
            Event.new(
                source="daise",
                type="knowledge_librarian_answered",
                payload={
                    "question": record["question"],
                    "collection": record["collection"],
                    "used_llm": record["used_llm"],
                    "compute_used": record["compute_used"],
                    "retrieved_count": record["retrieved_count"],
                    "provenance_count": len(record["provenance"]),
                },
                timestamp_ms=ts_ms,
                tags=["knowledge", "phase2", "librarian"],
            )
        )


__all__ = ["LibrarianAgent"]
