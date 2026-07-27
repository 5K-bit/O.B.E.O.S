"""
Collection metadata (taxonomy) layer.

A small, separate JSON store (``data/knowledge/collections.json``) that holds the
canonical taxonomy for each collection - ``domain`` and an optional human-readable
``description`` (plus optional ``subdomain``/``category``). It is intentionally kept
apart from chunks, embeddings, and the SourceRegistry so that:

- domain is declared once per collection and inherited by future ingestions
- nothing is hardcoded in the ingestion pipeline (no ``if collection == "spawn"``)
- intel services and backfill resolve domain from this single authority

Stored separately from chunks/embeddings by design.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..ingestion.source_registry import slugify_collection


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class CollectionMetadata:
    """Taxonomy for a single collection."""

    collection: str
    domain: str = ""
    description: str = ""
    subdomain: str = ""
    category: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CollectionMetadata":
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class CollectionRegistry:
    """JSON-backed store of collection taxonomy, keyed by collection slug."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._records: Dict[str, CollectionMetadata] = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        self._records = {}
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # Corrupt/unreadable: start empty but do not destroy the file on disk.
            self._records = {}
            return
        items = raw.get("collections", []) if isinstance(raw, dict) else raw
        for item in items:
            try:
                meta = CollectionMetadata.from_dict(item)
            except TypeError:
                continue
            slug = slugify_collection(meta.collection)
            if slug:
                meta.collection = slug
                self._records[slug] = meta

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": _now_iso(),
            "collections": [r.to_dict() for r in self._records.values()],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # ---- queries -------------------------------------------------------

    def get(self, collection: str) -> Optional[CollectionMetadata]:
        return self._records.get(slugify_collection(collection))

    def all(self) -> List[CollectionMetadata]:
        return list(self._records.values())

    def domain_for(self, collection: str) -> str:
        """Return the declared domain for a collection (``""`` when unknown)."""
        meta = self.get(collection)
        return meta.domain if meta else ""

    # ---- mutation ------------------------------------------------------

    def upsert(
        self,
        collection: str,
        *,
        domain: Optional[str] = None,
        description: Optional[str] = None,
        subdomain: Optional[str] = None,
        category: Optional[str] = None,
    ) -> CollectionMetadata:
        """Create or update a collection's taxonomy. Only provided fields change."""
        slug = slugify_collection(collection)
        existing = self._records.get(slug)
        if existing is None:
            meta = CollectionMetadata(collection=slug)
        else:
            meta = existing
        if domain is not None:
            meta.domain = domain
        if description is not None:
            meta.description = description
        if subdomain is not None:
            meta.subdomain = subdomain
        if category is not None:
            meta.category = category
        meta.updated_at = _now_iso()
        self._records[slug] = meta
        return meta
