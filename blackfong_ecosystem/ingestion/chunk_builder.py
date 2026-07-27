"""
Chunk builder for the knowledge ingestion pipeline.

Reuses the deterministic sentence-aware chunker from
``blackfong_ecosystem.knowledge`` and emits a JSONL chunk file per source with a
stable schema that the embedding step consumes directly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from ..knowledge import _chunk_text
from .source_registry import COPYRIGHT_PRIVATE, SOURCE_TYPE_MARKDOWN, SourceRecord

# Markdown section chunks above this many characters are sub-split (keeping their
# section title) so embeddings stay focused.
_MD_MAX_SECTION_CHARS = 1500
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _issue_range_slug(issue_range: str) -> str:
    """``"1-8"`` -> ``"001_008"``; non-numeric ranges fall back to a slug."""
    nums = re.findall(r"\d+", issue_range)
    if len(nums) >= 2:
        return f"{int(nums[0]):03d}_{int(nums[1]):03d}"
    if len(nums) == 1:
        return f"{int(nums[0]):03d}"
    return _slug(issue_range)


def chunk_prefix(series: str, issue_range: str) -> str:
    return f"{_slug(series)}_{_issue_range_slug(issue_range)}"


_ISSUE_MARK_RE = re.compile(r"\[ISSUE:\s*([^\]]+)\]")
_PAGE_MARK_RE = re.compile(r"\[PAGE\s+([^\]]+)\]")


def _section_and_page(text: str) -> tuple[str, str]:
    """Best-effort section/page from the inline markers preserved by the cleaner.

    Returns ("", "") when no markers are present; never fabricates values.
    """
    issue = _ISSUE_MARK_RE.search(text)
    page = _PAGE_MARK_RE.search(text)
    section_val = issue.group(1).strip() if issue else ""
    page_val = page.group(1).strip() if page else ""
    return section_val, page_val


def _chunk_markdown(text: str) -> List[tuple[str, str]]:
    """Split Markdown into ``(section_title, chunk_text)`` on heading boundaries.

    Fenced code blocks are respected (``#`` inside a fence is not a heading).
    Sections larger than ``_MD_MAX_SECTION_CHARS`` are sub-split with the
    sentence-aware chunker while keeping their heading as the section title. Real
    content only - nothing is dropped or invented.
    """
    sections: List[tuple[str, List[str]]] = []
    current_title = ""
    current_lines: List[str] = []
    in_fence = False

    def _flush() -> None:
        if current_lines and any(ln.strip() for ln in current_lines):
            sections.append((current_title, list(current_lines)))

    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            current_lines.append(line)
            continue
        heading = None if in_fence else _HEADING_RE.match(line)
        if heading is not None:
            _flush()
            current_title = heading.group(2).strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    _flush()

    if not sections:
        body = text.strip()
        return [("", body)] if body else []

    out: List[tuple[str, str]] = []
    for title, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        if len(body) <= _MD_MAX_SECTION_CHARS:
            out.append((title, body))
        else:
            for piece in _chunk_text(body):
                if piece.strip():
                    out.append((title, piece))
    return out


@dataclass
class ChunkBuildResult:
    chunk_ids: List[str] = field(default_factory=list)
    texts: List[str] = field(default_factory=list)
    metadatas: List[Dict[str, Any]] = field(default_factory=list)
    jsonl_path: Path | None = None

    def __len__(self) -> int:
        return len(self.chunk_ids)


def build_chunks(
    record: SourceRecord,
    cleaned_text: str,
    *,
    chunks_dir: Path,
    content_type: str = "lore",
) -> ChunkBuildResult:
    """Chunk ``cleaned_text`` and write ``{source_id}.jsonl`` to ``chunks_dir``.

    Each JSONL line::

        {"chunk_id": "spawn_001_008_0001",
         "source": "Spawn 1-8",
         "text": "...",
         "metadata": {"series": "Spawn", "issue_range": "1-8", "type": "lore",
                      "source_id": "...", "source_type": "djvu_text",
                      "copyright_status": "private_reference_only"}}
    """
    is_markdown = record.source_type == SOURCE_TYPE_MARKDOWN
    collection = record.resolved_collection()
    domain = record.domain or ""

    if is_markdown:
        # Each Markdown file is its own document: a per-source prefix keeps chunk
        # ids globally unique even when many files share issue_range="unknown".
        prefix = record.source_id
        file_name = Path(record.path).name
        source_label = file_name or f"{record.series} {record.issue_range}"
        md_chunks = _chunk_markdown(cleaned_text)
        chunks = [body for _title, body in md_chunks]
        section_titles = [title for title, _body in md_chunks]
    else:
        chunks = _chunk_text(cleaned_text)
        prefix = chunk_prefix(record.series, record.issue_range)
        source_label = f"{record.series} {record.issue_range}"
        file_name = ""
        section_titles = []

    result = ChunkBuildResult()
    chunks_dir = Path(chunks_dir)
    chunks_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = chunks_dir / f"{record.source_id}.jsonl"

    lines: List[str] = []
    for i, text in enumerate(chunks, start=1):
        if not text.strip():
            continue
        chunk_id = f"{prefix}_{i:04d}"
        metadata = {
            "series": record.series,
            "issue_range": record.issue_range,
            "collection": collection,
            "domain": domain,
            "type": content_type,
            "source_id": record.source_id,
            "source_type": record.source_type,
            "copyright_status": record.copyright_status or COPYRIGHT_PRIVATE,
            "chunk_index": i,
        }
        if is_markdown:
            metadata["file_name"] = file_name
            metadata["relative_path"] = record.path
            section_title = section_titles[i - 1] if i - 1 < len(section_titles) else ""
            if section_title:
                metadata["section"] = section_title
                metadata["section_title"] = section_title
        else:
            section, page = _section_and_page(text)
            if section:
                metadata["section"] = section
            if page:
                metadata["page"] = page
        record_obj = {
            "chunk_id": chunk_id,
            "source": source_label,
            "text": text,
            "metadata": metadata,
        }
        lines.append(json.dumps(record_obj, ensure_ascii=False))
        result.chunk_ids.append(chunk_id)
        result.texts.append(text)
        result.metadatas.append(metadata)

    jsonl_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    result.jsonl_path = jsonl_path
    return result
