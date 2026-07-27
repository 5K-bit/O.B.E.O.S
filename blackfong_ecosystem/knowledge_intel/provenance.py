"""
Knowledge provenance.

Maps any retrieval result or chunk id back to its real source: collection,
source_id, source file, chunk id, section/page (when the cleaner preserved
markers), and confidence. Never fabricates: an unknown chunk id returns
``{"found": False, "reason": ...}``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ingestion.source_registry import SourceRegistry
from . import sources as src


class KnowledgeProvenanceService:
    """Assembles provenance from the chunk store + registry (+ optional vector store)."""

    def __init__(
        self,
        *,
        registry: SourceRegistry,
        chunks_dir: Path,
        vector_store: Any = None,
        collection_registry: Any = None,
    ) -> None:
        self.registry = registry
        self.chunks_dir = Path(chunks_dir)
        self.vector_store = vector_store
        self.collection_registry = collection_registry

    def _resolve_domain(self, collection: str, meta_domain: str, record: Any) -> str:
        if meta_domain:
            return meta_domain
        if self.collection_registry is not None and collection:
            declared = self.collection_registry.domain_for(collection)
            if declared:
                return declared
        return record.domain if record else ""

    def _from_vector_store(self, chunk_id: str) -> Optional[Dict[str, Any]]:
        """Best-effort exact lookup in Chroma; returns None if unavailable/missing."""
        collection = getattr(self.vector_store, "_collection", None)
        if collection is None:
            return None
        try:
            got = collection.get(ids=[chunk_id], include=["metadatas", "documents"])
        except Exception:
            return None
        ids = got.get("ids") or []
        if not ids:
            return None
        metas = got.get("metadatas") or [{}]
        docs = got.get("documents") or [""]
        return {"metadata": metas[0] or {}, "text": docs[0] or ""}

    def for_chunk(self, chunk_id: str) -> Dict[str, Any]:
        """Return a provenance record for a single chunk id."""
        meta: Dict[str, Any] = {}
        text = ""

        hit = self._from_vector_store(chunk_id)
        if hit is not None:
            meta = hit["metadata"]
            text = hit["text"]
        else:
            ref = src.find_chunk(self.chunks_dir, chunk_id)
            if ref is None:
                return {
                    "found": False,
                    "chunk_id": chunk_id,
                    "reason": "chunk_id not found in vector store or chunk files",
                }
            meta = ref.metadata
            text = ref.text

        source_id = str(meta.get("source_id", ""))
        record = self.registry.get(source_id) if source_id else None
        collection = str(meta.get("collection") or (record.resolved_collection() if record else ""))

        return {
            "found": True,
            "chunk_id": chunk_id,
            "collection": collection,
            "domain": self._resolve_domain(collection, str(meta.get("domain", "")), record),
            "source_id": source_id,
            "source_file": record.path if record else "",
            "source_type": str(meta.get("source_type", "") or (record.source_type if record else "")),
            "series": str(meta.get("series", "") or (record.series if record else "")),
            "issue_range": str(meta.get("issue_range", "") or (record.issue_range if record else "")),
            "section": str(meta.get("section", "")),
            "page": str(meta.get("page", "")),
            "chunk_index": meta.get("chunk_index"),
            "confidence": 1.0,  # exact, on-disk provenance
            "text_preview": text[:280],
        }

    def annotate(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Enrich vector-search results with provenance + retrieval rank.

        Each result is expected to look like ``{"id", "metadata", "relevance"}``
        (the shape returned by ``ChromaVectorStore.search``).
        """
        annotated: List[Dict[str, Any]] = []
        for rank, result in enumerate(results, start=1):
            meta = result.get("metadata", {}) or {}
            source_id = str(meta.get("source_id", ""))
            record = self.registry.get(source_id) if source_id else None
            annotated.append(
                {
                    **result,
                    "provenance": {
                        "collection": str(meta.get("collection") or (record.resolved_collection() if record else "")),
                        "source_id": source_id,
                        "source_file": record.path if record else "",
                        "chunk_id": result.get("id"),
                        "section": str(meta.get("section", "")),
                        "page": str(meta.get("page", "")),
                        "confidence": round(float(result.get("relevance", 0.0)), 4),
                        "retrieval_rank": rank,
                    },
                }
            )
        return annotated

    def build_map(self, collection: str) -> Dict[str, Any]:
        """A full provenance map for a collection: every chunk_id -> source mapping.

        Returns ``{"collection", "chunk_count", "sources":[...], "chunks":{id:{...}}}``.
        Pure on-disk derivation; safe to persist as a real artifact.
        """
        records = src.embedded_text_sources(self.registry, collection)
        domain = src.resolve_domain(collection, records, self.collection_registry)
        chunks: Dict[str, Any] = {}
        source_rows: List[Dict[str, Any]] = []
        for record in records:
            refs = src.read_source_chunks(self.chunks_dir, record.source_id)
            source_rows.append(
                {
                    "source_id": record.source_id,
                    "source_file": record.path,
                    "issue_range": record.issue_range,
                    "chunk_count": len(refs),
                }
            )
            for ref in refs:
                chunks[ref.chunk_id] = {
                    "source_id": ref.source_id,
                    "source_file": record.path,
                    "issue_range": record.issue_range,
                    "section": str(ref.metadata.get("section", "")),
                    "page": str(ref.metadata.get("page", "")),
                    "chunk_index": ref.metadata.get("chunk_index"),
                }
        return {
            "collection": src.slugify_collection(collection),
            "domain": domain,
            "chunk_count": len(chunks),
            "sources": source_rows,
            "chunks": chunks,
        }
