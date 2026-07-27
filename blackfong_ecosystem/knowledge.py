"""
Knowledge artifact ingestion. Text files as cognitive input, not raw files.

Fixed-size chunking (deterministic). Stable chunk IDs. Canonical knowledge_ingested event.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from .config import paths
from .events import Event
from .storage import EventStore, VectorStore


# Fixed-size chunking: 512 characters. Deterministic and predictable.
CHUNK_SIZE = 512


import re

def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> List[str]:
    """Split text into sentence-aware chunks with light overlap.

    - Break on sentence boundaries (period, exclamation, question) when possible.
    - Ensure chunk does not exceed chunk_size by starting a new chunk when necessary.
    - Carry over the last 50 characters of the previous chunk as overlap to
      maintain context.
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks: List[str] = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) <= chunk_size:
            current += sent + " "
        else:
            if current:
                chunks.append(current.strip())
            # start new chunk with overlap
            overlap = current[-50:] if len(current) > 50 else current
            current = overlap + sent + " "
            # enforce cap on new chunk
            if len(current) > chunk_size:
                chunks.append(current[:chunk_size].strip())
                current = current[chunk_size:]
    if current:
        chunks.append(current.strip())
    # ensure no chunk exceeds limit
    capped: List[str] = []
    for c in chunks:
        if len(c) > chunk_size:
            capped.append(c[:chunk_size])
        else:
            capped.append(c)
    return capped


def _stable_chunk_id(source_path: str, chunk_index: int) -> str:
    """Deterministic chunk ID: sha256(source_path + chunk_index)."""
    payload = f"{source_path}{chunk_index}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _content_hash(content: str) -> str:
    """SHA-256 of chunk content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def ingest_file(
    file_path: Path,
    vector_store: VectorStore,
    event_store: EventStore,
) -> Tuple[int, str]:
    """
    Ingest a single text file as knowledge artifacts.

    Chunks with fixed size, stable IDs, metadata (source_path, chunk_index,
    ingestion_timestamp, content_hash). Emits knowledge_ingested event after
    successful vector insertion.

    Returns:
        (chunk_count, knowledge_ingested_event_id)
    """
    path_str = str(file_path.resolve())
    raw = file_path.read_text(encoding="utf-8", errors="replace")
    chunks = _chunk_text(raw)
    if not chunks:
        # Empty file: still emit event with chunk_count 0
        ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        event = Event.new(
            source="daise",
            type="knowledge_ingested",
            payload={
                "source_path": path_str,
                "chunk_count": 0,
                "timestamp": ts_ms,
            },
            confidence=1.0,
            timestamp_ms=ts_ms,
        )
        event_store.add_event(event)
        return 0, event.id

    ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    ids = []
    texts = []
    metadatas = []
    for i, chunk in enumerate(chunks):
        cid = _stable_chunk_id(path_str, i)
        ids.append(cid)
        texts.append(chunk)
        words = chunk.split()
        keywords = words[:5]
        metadatas.append({
            "source_path": path_str,
            "chunk_index": i,
            "ingestion_timestamp": ts_ms,
            "content_hash": _content_hash(chunk),
            "length": len(chunk),
            "keywords": keywords,
            "source_type": Path(path_str).suffix.lower(),
        })
    vector_store.add(ids=ids, texts=texts, metadatas=metadatas)

    event = Event.new(
        source="daise",
        type="knowledge_ingested",
        payload={
            "source_path": path_str,
            "chunk_count": len(chunks),
            "timestamp": ts_ms,
        },
        confidence=1.0,
        timestamp_ms=ts_ms,
    )
    event_store.add_event(event)
    return len(chunks), event.id


def ingest_directory(
    dir_path: Path,
    vector_store: VectorStore,
    event_store: EventStore,
    extensions: Tuple[str, ...] = (".txt", ".md", ".jsonl"),
) -> Tuple[int, List[str]]:
    """
    Ingest all supported text files under dir_path.

    Returns:
        (total_chunks, list of knowledge_ingested event ids)
    """
    total = 0
    event_ids = []
    for ext in extensions:
        for fp in sorted(dir_path.glob(f"*{ext}")):
            if fp.is_file():
                n, eid = ingest_file(fp, vector_store, event_store)
                total += n
                event_ids.append(eid)
    return total, event_ids
