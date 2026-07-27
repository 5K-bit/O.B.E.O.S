"""
Text cleaning + validation for the knowledge ingestion pipeline.

Goals:
- Strip scan/archive boilerplate and page-header noise.
- Repair hyphenated line breaks (``word-\nbreak`` -> ``wordbreak``).
- Normalize whitespace while preserving issue and page boundaries as inline
  ``[ISSUE: ...]`` / ``[PAGE n]`` markers so downstream chunks keep locality.
- ``validate_text`` decides whether a cleaned source is good enough to embed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List


# Minimum characters for a text source to be considered embeddable.
MIN_CHARS = 200

# If more than this fraction of non-empty lines look like boilerplate, reject.
MAX_BOILERPLATE_RATIO = 0.6

# Lines matching these patterns are archive/scan boilerplate to drop.
_BOILERPLATE_PATTERNS = [
    re.compile(r"^\s*this\s+(book|file|document)\s+was\s+(scanned|produced)", re.I),
    re.compile(r"archive\.org", re.I),
    re.compile(r"^\s*scanned\s+by\b", re.I),
    re.compile(r"^\s*digiti[sz]ed\s+by\b", re.I),
    re.compile(r"^\s*ocr\b.*\bby\b", re.I),
    re.compile(r"^\s*\[?\s*page\s+intentionally\s+left\s+blank\s*\]?\s*$", re.I),
    re.compile(r"^\s*end\s+of\s+(the\s+)?project\s+gutenberg", re.I),
    re.compile(r"^\s*all\s+rights\s+reserved\.?\s*$", re.I),
]

# A standalone page-number-ish line (e.g. "12", "- 12 -", "Page 12").
_PAGE_NUMBER_LINE = re.compile(r"^\s*[-—]?\s*(?:page\s+)?(\d{1,4})\s*[-—]?\s*$", re.I)

# Issue boundary markers commonly seen in comic text dumps.
_ISSUE_MARKER = re.compile(r"^\s*(?:issue|#)\s*(\d{1,4})\b.*$", re.I)

# Form-feed / explicit page-break characters.
_FORM_FEED = "\f"


def _is_boilerplate(line: str) -> bool:
    return any(p.search(line) for p in _BOILERPLATE_PATTERNS)


def _fix_hyphenation(text: str) -> str:
    # Join "exam-\nple" -> "example" (letter, hyphen, EOL, letter).
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)


@dataclass
class CleanResult:
    text: str
    original_chars: int
    cleaned_chars: int
    dropped_lines: int = 0
    page_markers: int = 0
    issue_markers: int = 0
    notes: List[str] = field(default_factory=list)


def clean_text(raw: str) -> CleanResult:
    """Clean a raw extracted text blob; preserve issue/page boundaries inline."""
    original_chars = len(raw)
    # Normalize newlines and convert form-feeds to explicit page breaks first.
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(_FORM_FEED, "\n[PAGE BREAK]\n")
    text = _fix_hyphenation(text)

    out_lines: List[str] = []
    dropped = 0
    page_markers = 0
    issue_markers = 0

    for line in text.split("\n"):
        stripped = line.strip()

        if stripped == "[PAGE BREAK]":
            out_lines.append("[PAGE]")
            page_markers += 1
            continue

        if not stripped:
            out_lines.append("")
            continue

        if _is_boilerplate(stripped):
            dropped += 1
            continue

        issue_match = _ISSUE_MARKER.match(stripped)
        if issue_match and len(stripped) <= 40:
            out_lines.append(f"[ISSUE: {issue_match.group(1)}]")
            issue_markers += 1
            continue

        page_match = _PAGE_NUMBER_LINE.match(stripped)
        if page_match:
            out_lines.append(f"[PAGE {page_match.group(1)}]")
            page_markers += 1
            continue

        # Collapse runs of internal whitespace within a kept line.
        out_lines.append(re.sub(r"[ \t]+", " ", stripped))

    # Collapse 3+ blank lines down to a single blank line.
    joined = "\n".join(out_lines)
    joined = re.sub(r"\n{3,}", "\n\n", joined).strip()

    return CleanResult(
        text=joined,
        original_chars=original_chars,
        cleaned_chars=len(joined),
        dropped_lines=dropped,
        page_markers=page_markers,
        issue_markers=issue_markers,
    )


def clean_markdown(raw: str) -> CleanResult:
    """Light, structure-preserving clean for Markdown documents.

    Unlike :func:`clean_text` (tuned for scanned comic/book dumps), this keeps
    Markdown intact: headings, fenced code blocks, indentation, and list markers
    are all preserved. It only normalizes line endings, strips trailing whitespace
    on each line, and collapses 3+ blank lines. Nothing is fabricated or reflowed.
    """
    original_chars = len(raw)
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    joined = "\n".join(lines)
    joined = re.sub(r"\n{3,}", "\n\n", joined).strip()
    return CleanResult(
        text=joined,
        original_chars=original_chars,
        cleaned_chars=len(joined),
        dropped_lines=0,
        page_markers=0,
        issue_markers=0,
    )


@dataclass
class ValidationResult:
    valid: bool
    char_count: int
    boilerplate_ratio: float
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "char_count": self.char_count,
            "boilerplate_ratio": round(self.boilerplate_ratio, 4),
            "reasons": list(self.reasons),
        }


def validate_text(text: str, *, min_chars: int = MIN_CHARS) -> ValidationResult:
    """Decide whether cleaned text is good enough to embed.

    Checks: non-empty, meets min char count, and is not mostly marker/boilerplate
    lines (so a PDF with no real text layer fails and triggers fallback).
    """
    reasons: List[str] = []
    char_count = len(text.strip())

    if char_count == 0:
        return ValidationResult(False, 0, 1.0, ["empty"])

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    marker_lines = sum(
        1
        for ln in lines
        if ln.startswith("[PAGE") or ln.startswith("[ISSUE") or ln == "[PAGE]"
    )
    boilerplate_ratio = marker_lines / len(lines) if lines else 1.0

    if char_count < min_chars:
        reasons.append(f"too_short:{char_count}<{min_chars}")
    if boilerplate_ratio > MAX_BOILERPLATE_RATIO:
        reasons.append(f"mostly_boilerplate:{boilerplate_ratio:.2f}")

    return ValidationResult(
        valid=not reasons,
        char_count=char_count,
        boilerplate_ratio=boilerplate_ratio,
        reasons=reasons,
    )
