"""
Document ingestion pipeline for OBEOS knowledge memory.

Registers all source files (djvu text, EPUB, PDF text, visual JP2), selects a
single canonical text source per issue range (priority: djvu_text > epub >
pdf_text), cleans and chunks it, and embeds the result into the shared
``daise_knowledge`` Chroma collection. Visual sources are registered with a
``pending_processing`` status for future OCR/CLIP work.

Lifecycle events are emitted at every stage:
``knowledge_source_added`` -> ``knowledge_cleaned`` -> ``knowledge_chunked``
-> ``knowledge_embedded`` (with ``knowledge_ingestion_failed`` on errors).
"""

from .source_registry import SourceRecord, SourceRegistry

__all__ = ["SourceRecord", "SourceRegistry"]
