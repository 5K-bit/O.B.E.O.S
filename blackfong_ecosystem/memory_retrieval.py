"""Semantic retrieval orchestration behind ConversationMemoryService."""

from __future__ import annotations
import time
from typing import Any, Dict, List
from .retrieval_profiles import retrieval_profile_settings
from .storage import NullVectorStore


class SemanticRetrieval:
    def _retrieve_semantic_context(
        self,
        *,
        query: str,
        retrieval_profile: str,
        skip_vector_search: bool,
    ) -> Dict[str, Any]:
        settings = retrieval_profile_settings(retrieval_profile)
        empty = {
            "hits": [],
            "citations": [],
            "context": "",
            "timing_ms": {
                "embedding": None,
                "vector_query": 0.0,
                "rerank_bounding": 0.0,
                "total_retrieval": 0.0,
            },
        }
        if (
            skip_vector_search
            or not settings.get("include_semantic")
            or not str(query or "").strip()
            or isinstance(self.vector_store, NullVectorStore)
        ):
            return empty

        vector_k = int(settings.get("vector_k") or 0)
        if vector_k <= 0:
            return empty

        query_tokens = self._tokenize(query)
        hinted_collections = self._detect_collection_hints(query)
        query_started = time.perf_counter()
        try:
            results = self.vector_store.search(query, k=max(vector_k * 3, vector_k))
        except Exception:
            results = []
        query_completed = time.perf_counter()

        rerank_started = time.perf_counter()
        annotated_hits: List[Dict[str, Any]] = []
        for collection in hinted_collections:
            summary_hit = self._collection_summary_hit(
                collection,
                query=query,
                query_tokens=query_tokens,
                retrieval_profile=retrieval_profile,
            )
            if summary_hit is not None:
                annotated_hits.append(summary_hit)
        for result in self._search_hinted_collection_results(
            query=query,
            hinted_collections=hinted_collections,
            retrieval_profile=retrieval_profile,
            limit=max(vector_k * 2, vector_k),
        ):
            enriched = self._annotate_semantic_hit(
                result,
                query_tokens=query_tokens,
                retrieval_profile=retrieval_profile,
                hinted_collections=hinted_collections,
            )
            if enriched is not None:
                annotated_hits.append(enriched)
        for result in results:
            enriched = self._annotate_semantic_hit(
                result,
                query_tokens=query_tokens,
                retrieval_profile=retrieval_profile,
                hinted_collections=hinted_collections,
            )
            if enriched is not None:
                annotated_hits.append(enriched)
        deduped_hits = self._dedupe_semantic_hits(annotated_hits)
        deduped_hits.sort(
            key=lambda item: (float(item["score"]), float(item["relevance"])),
            reverse=True,
        )
        selected_hits = deduped_hits[: int(settings.get("citation_limit") or vector_k)]
        selected_hits = self._limit_collection_focused_hits(
            selected_hits,
            query=query,
            retrieval_profile=retrieval_profile,
            hinted_collections=hinted_collections,
        )
        context = self._render_retrieval_context(
            selected_hits,
            max_chars=int(settings.get("context_chars") or 0),
        )
        rerank_completed = time.perf_counter()
        return {
            "hits": selected_hits,
            "citations": [dict(item["citation"]) for item in selected_hits],
            "context": context,
            "timing_ms": {
                "embedding": None,
                "vector_query": round((query_completed - query_started) * 1000, 2),
                "rerank_bounding": round((rerank_completed - rerank_started) * 1000, 2),
                "total_retrieval": round((rerank_completed - query_started) * 1000, 2),
            },
        }
