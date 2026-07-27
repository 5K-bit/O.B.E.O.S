"""
Summaries - extractive-first, optionally LLM-synthesized.

``build_summary`` produces a grounded summary of a whole collection
(``collection_summary``) or a single source (``source_summary``). The deterministic
core is always real: representative excerpts pulled from on-disk chunks, frequent
content terms, plus coverage/timeline facts. When a local model is available (and
the compute mode permits) the model rephrases those grounded facts into prose;
otherwise the extractive text is returned as-is. Nothing is fabricated in either
path - the model may only reorganize the supplied grounding.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ingestion.source_registry import SourceRegistry, slugify_collection
from . import collections as collections_mod
from . import compute as compute_mod
from . import coverage as coverage_mod
from . import sources as src
from . import timeline as timeline_mod

KIND_COLLECTION = "collection_summary"
KIND_SOURCE = "source_summary"

_MAX_EXCERPTS = 6
_EXCERPT_CHARS = 320
_TOP_TERMS = 15

_STOPWORDS = frozenset(
    """
    the a an and or but if then else of to in on at by for with from into over under
    is are was were be been being do does did has have had this that these those it its
    as not no yes can could should would may might must will shall i you he she they we
    them his her their our your my me us him also more most other some such than too very
    s t re ve ll d m o who what when where which while whom whose why how all any
    each few many one two three about above after again against between both during before
    """.split()
)


def _stable_summary_id(collection: str, kind: str, key: str) -> str:
    payload = f"{collection}|{kind}|{key}"
    return f"sum_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _tokenize_terms(text: str) -> List[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", text.lower())
    return [w for w in words if w not in _STOPWORDS]


def _frequent_terms(chunks: List[src.ChunkRef], limit: int = _TOP_TERMS) -> List[Dict[str, Any]]:
    counter: Counter = Counter()
    for ref in chunks:
        counter.update(_tokenize_terms(ref.text))
    return [{"term": term, "count": count} for term, count in counter.most_common(limit)]


def _representative_excerpts(chunks: List[src.ChunkRef], limit: int = _MAX_EXCERPTS) -> List[Dict[str, Any]]:
    """Pick the longest chunks (most informative) as representative excerpts."""
    ranked = sorted(chunks, key=lambda r: len(r.text), reverse=True)
    excerpts: List[Dict[str, Any]] = []
    for ref in ranked[:limit]:
        text = ref.text.strip().replace("\n", " ")
        excerpts.append(
            {
                "chunk_id": ref.chunk_id,
                "source_id": ref.source_id,
                "section": str(ref.metadata.get("section", "")),
                "excerpt": text[:_EXCERPT_CHARS],
            }
        )
    return excerpts


def _extractive_text(
    *,
    collection: str,
    domain: str,
    kind: str,
    facts: Dict[str, Any],
    terms: List[Dict[str, Any]],
    excerpts: List[Dict[str, Any]],
) -> str:
    label = "collection" if kind == KIND_COLLECTION else "source"
    lines: List[str] = []
    domain_text = domain or "undeclared"
    lines.append(f"Summary of {label} '{collection}' (domain: {domain_text}).")

    fact_bits: List[str] = []
    if "sources" in facts:
        fact_bits.append(f"{facts['sources']} source(s)")
    if "embedded" in facts:
        fact_bits.append(f"{facts['embedded']} embedded")
    if "total_chunks" in facts:
        fact_bits.append(f"{facts['total_chunks']} chunks")
    if facts.get("timeline_events"):
        fact_bits.append(f"{facts['timeline_events']} timeline events")
    if fact_bits:
        lines.append("Contents: " + ", ".join(fact_bits) + ".")

    sequence = facts.get("sequence") or {}
    if sequence.get("available"):
        cont = "continuous" if sequence.get("continuous") else "with gaps"
        lines.append(
            f"Sequence: {sequence.get('min')}-{sequence.get('max')} ({cont})."
        )

    if terms:
        lines.append("Key terms: " + ", ".join(t["term"] for t in terms[:10]) + ".")

    if excerpts:
        lines.append("Representative excerpts:")
        for ex in excerpts:
            tag = f" [{ex['section']}]" if ex.get("section") else ""
            lines.append(f"- ({ex['chunk_id']}{tag}) {ex['excerpt']}")

    return "\n".join(lines)


def build_summary(
    registry: SourceRegistry,
    collection: str,
    *,
    kind: str = KIND_COLLECTION,
    source_id: Optional[str] = None,
    chunks_dir: Path,
    collection_registry: Any = None,
    mode: Any = None,
) -> Dict[str, Any]:
    """Build a grounded summary record for a collection or single source.

    Returns ``found=False`` with a reason when there is nothing real to summarize,
    so no fabricated summary is ever produced.
    """
    slug = slugify_collection(collection)
    chunks_dir = Path(chunks_dir)
    records = src.collection_records(registry, collection)
    if not records:
        return {
            "found": False,
            "collection": slug,
            "reason": f"no registered sources for collection '{slug}'",
        }

    domain = src.resolve_domain(collection, records, collection_registry)

    if kind == KIND_SOURCE:
        if not source_id:
            return {"found": False, "collection": slug, "reason": "source_summary requires a source_id"}
        record = registry.get(source_id)
        if record is None or record.resolved_collection() != slug:
            return {
                "found": False,
                "collection": slug,
                "reason": f"source '{source_id}' is not part of collection '{slug}'",
            }
        chunks = src.read_source_chunks(chunks_dir, source_id)
        if not chunks:
            return {
                "found": False,
                "collection": slug,
                "reason": f"source '{source_id}' has no embedded chunks to summarize",
            }
        source_ids = [source_id]
        facts: Dict[str, Any] = {
            "sources": 1,
            "embedded": 1 if record.embedded else 0,
            "total_chunks": len(chunks),
            "issue_range": record.issue_range,
        }
        summary_key = source_id
        question = f"Summarize the source '{record.series} {record.issue_range}' in the '{slug}' collection."
    else:
        embedded = src.embedded_text_sources(registry, collection)
        if not embedded:
            return {
                "found": False,
                "collection": slug,
                "reason": "no embedded sources to summarize",
            }
        chunks = []
        for rec in embedded:
            chunks.extend(src.read_source_chunks(chunks_dir, rec.source_id))
        source_ids = [rec.source_id for rec in embedded]
        cov = coverage_mod.build_coverage(
            registry, collection, chunks_dir=chunks_dir, collection_registry=collection_registry
        )
        tl = timeline_mod.build_timeline(registry, collection, chunks_dir=chunks_dir)
        facts = {
            "sources": cov.get("total_sources", len(records)),
            "embedded": cov.get("embedded_sources", len(embedded)),
            "total_chunks": cov.get("total_chunks", len(chunks)),
            "sequence": cov.get("sequence", {}),
            "timeline_events": tl.get("event_count", 0),
        }
        summary_key = "collection"
        question = f"Summarize the '{slug}' collection: what it contains and what it is about."

    terms = _frequent_terms(chunks)
    excerpts = _representative_excerpts(chunks)
    supporting_chunk_ids = [ex["chunk_id"] for ex in excerpts]

    extractive_text = _extractive_text(
        collection=slug,
        domain=domain,
        kind=kind,
        facts=facts,
        terms=terms,
        excerpts=excerpts,
    )

    synthesis = compute_mod.synthesize(
        system=(
            "You write concise, factual summaries of a knowledge collection or source "
            "for an operator. Use only the supplied facts and excerpts. Do not invent "
            "titles, numbers, or details not present in the grounding."
        ),
        grounding={
            "collection": slug,
            "domain": domain or "(undeclared)",
            "facts": facts,
            "key_terms": [t["term"] for t in terms],
            "excerpts": [ex["excerpt"] for ex in excerpts],
        },
        question=question,
        mode=mode,
        fallback=lambda: extractive_text,
        agent="daise",
        response_mode="standard",
    )

    return {
        "found": True,
        "summary_id": _stable_summary_id(slug, kind, summary_key),
        "collection": slug,
        "domain": domain,
        "kind": kind,
        "source_ids": source_ids,
        "supporting_chunk_ids": supporting_chunk_ids,
        "key_terms": terms,
        "facts": facts,
        "text": synthesis.text,
        "extractive_text": extractive_text,
        "compute_used": "local_llm" if synthesis.used_llm else "extractive",
        "fallback_reason": synthesis.fallback_reason,
        "confidence": 1.0 if not synthesis.used_llm else 0.85,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


__all__ = ["build_summary", "KIND_COLLECTION", "KIND_SOURCE"]
