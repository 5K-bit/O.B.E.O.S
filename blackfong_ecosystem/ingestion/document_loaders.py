"""
Document loaders for the knowledge ingestion pipeline.

Each loader takes a path and returns extracted plain text. Heavy parser
dependencies (``ebooklib``, ``beautifulsoup4``, ``pypdf``) are imported lazily so
the core install stays light: EPUB/PDF are only parsed when they actually become
the canonical source (i.e. on djvu fallback).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict

from .source_registry import (
    SOURCE_TYPE_DJVU,
    SOURCE_TYPE_EPUB,
    SOURCE_TYPE_MARKDOWN,
    SOURCE_TYPE_PDF,
)


class LoaderError(RuntimeError):
    """Raised when a document cannot be loaded/parsed."""


class MissingDependencyError(LoaderError):
    """Raised when an optional parser dependency is not installed."""


def load_djvu_text(path: Path) -> str:
    """Load a plain-text export of a djvu document.

    These are exported as ``.txt`` already, so this is a straight UTF-8 read with
    a lenient fallback for stray bytes.
    """
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def load_markdown(path: Path) -> str:
    """Load a Markdown document as text, preserving headings, code, and lists.

    Markdown is already plain text, so this is a UTF-8 read with a lenient fallback.
    No structure is stripped here; heading-aware chunking happens downstream.
    """
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def load_epub(path: Path) -> str:
    """Extract readable text from an EPUB.

    Lazily imports ``ebooklib`` and ``beautifulsoup4``.
    """
    path = Path(path)
    try:
        import ebooklib  # type: ignore
        from ebooklib import epub  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only without dep
        raise MissingDependencyError(
            "EPUB parsing requires 'ebooklib'. Install with: pip install '.[ingestion]'"
        ) from exc
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only without dep
        raise MissingDependencyError(
            "EPUB parsing requires 'beautifulsoup4'. Install with: pip install '.[ingestion]'"
        ) from exc

    try:
        book = epub.read_epub(str(path))
    except Exception as exc:  # ebooklib raises a variety of errors
        raise LoaderError(f"Failed to read EPUB {path}: {exc}") from exc

    parts = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        try:
            html = item.get_content().decode("utf-8", errors="replace")
        except Exception:
            continue
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n")
        if text and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def load_pdf_text(path: Path) -> str:
    """Extract embedded text from a (text-layer) PDF.

    Lazily imports ``pypdf``. Intended for ``*_text.pdf`` files that already carry
    a text layer; image-only PDFs will yield little/no text and should fail
    validation downstream.
    """
    path = Path(path)
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised only without dep
        raise MissingDependencyError(
            "PDF parsing requires 'pypdf'. Install with: pip install '.[ingestion]'"
        ) from exc

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise LoaderError(f"Failed to read PDF {path}: {exc}") from exc

    parts = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


# Maps source_type -> loader callable.
LOADERS: Dict[str, Callable[[Path], str]] = {
    SOURCE_TYPE_DJVU: load_djvu_text,
    SOURCE_TYPE_EPUB: load_epub,
    SOURCE_TYPE_PDF: load_pdf_text,
    SOURCE_TYPE_MARKDOWN: load_markdown,
}


def load_source(source_type: str, path: Path) -> str:
    """Dispatch to the loader for ``source_type``."""
    loader = LOADERS.get(source_type)
    if loader is None:
        raise LoaderError(f"No loader registered for source type: {source_type!r}")
    return loader(Path(path))
