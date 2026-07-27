from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .config import paths
from .ecosystem_manifest import EcosystemManifestError, ecosystem_collections, manifest_path
from .ingestion.source_registry import STATUS_EMBEDDED, SourceRegistry
from .knowledge_intel.collection_registry import CollectionRegistry
from .storage import NullVectorStore


ALLOWED_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".toml",
    ".ini",
    ".cfg",
    ".log",
}
EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    ".venv",
    ".venv-codex",
    ".venv311-codex",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
}
MAX_BYTES = 512_000


def sync_governed_ecosystem_collections(
    *,
    memory_service: Any,
    workspace_root: Path,
    max_files_per_root: int = 80,
    force: bool = False,
) -> Dict[str, Any]:
    workspace = Path(workspace_root).resolve()
    source_registry = memory_service._source_registry or SourceRegistry(paths.source_registry_path)
    collection_registry = memory_service._collection_registry or CollectionRegistry(paths.collection_registry_path)
    memory_service._source_registry = source_registry
    memory_service._collection_registry = collection_registry

    paths.knowledge_root.mkdir(parents=True, exist_ok=True)
    paths.knowledge_chunks_path.mkdir(parents=True, exist_ok=True)
    paths.knowledge_cleaned_path.mkdir(parents=True, exist_ok=True)

    vector_store = getattr(memory_service, "vector_store", None)
    collection_reports: List[Dict[str, Any]] = []

    try:
        collection_specs = ecosystem_collections(workspace)
        manifest = str(manifest_path(workspace))
        manifest_error = None
    except EcosystemManifestError as exc:
        collection_specs = []
        manifest = str(manifest_path(workspace))
        manifest_error = str(exc)

    for spec in collection_specs:
        root = Path(str(spec["resolved_directory"]))
        available = root.exists() and root.is_dir()
        collection_registry.upsert(
            spec["collection"],
            domain=spec["domain"],
            description=spec["description"],
            category=spec["category"],
        )
        report = {
            "project_id": spec.get("project_id"),
            "collection": spec["collection"],
            "directory": str(root),
            "manifest_path": manifest,
            "available": available,
            "registered_sources": 0,
            "embedded_sources": 0,
            "chunk_count": 0,
            "ingested_files": [],
        }
        if not available:
            collection_reports.append(report)
            continue

        for file_path in _iter_text_files(root, max_files=max_files_per_root):
            issue_range = str(file_path.relative_to(root).parent.as_posix() or "root")
            record, changed = source_registry.register(
                source_type="repo_text",
                path=file_path,
                series=spec["series"],
                issue_range=issue_range,
                collection=spec["collection"],
                domain=spec["domain"],
                compute_hash=True,
            )
            report["registered_sources"] += 1

            needs_ingest = force or changed or not record.embedded or record.chunk_count <= 0
            if not needs_ingest:
                report["embedded_sources"] += 1
                report["chunk_count"] += int(record.chunk_count or 0)
                continue

            text = _read_text(file_path)
            if not text.strip():
                continue

            chunks = _chunk_text(text)
            if not chunks:
                continue

            relative_path = file_path.relative_to(root).as_posix()
            chunk_records: List[Dict[str, Any]] = []
            vector_ids: List[str] = []
            vector_texts: List[str] = []
            vector_metadatas: List[Dict[str, Any]] = []
            for chunk_index, chunk_text in enumerate(chunks):
                chunk_id = f"ecosystem:{record.source_id}:{chunk_index}"
                metadata = {
                    "source_id": record.source_id,
                    "collection": spec["collection"],
                    "domain": spec["domain"],
                    "source_path": str(file_path),
                    "section": relative_path,
                    "chunk_index": chunk_index,
                    "source_type": "repo_text",
                    "title": relative_path,
                }
                vector_ids.append(chunk_id)
                vector_texts.append(chunk_text)
                vector_metadatas.append(metadata)
                chunk_records.append(
                    {
                        "chunk_id": chunk_id,
                        "source": relative_path,
                        "text": chunk_text,
                        "metadata": metadata,
                    }
                )

            cleaned_path = paths.knowledge_cleaned_path / f"{record.source_id}.txt"
            cleaned_path.write_text(text, encoding="utf-8")
            chunk_path = paths.knowledge_chunks_path / f"{record.source_id}.jsonl"
            chunk_path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in chunk_records) + "\n",
                encoding="utf-8",
            )

            if not isinstance(vector_store, NullVectorStore) and vector_ids:
                vector_store.upsert(ids=vector_ids, texts=vector_texts, metadatas=vector_metadatas)

            record.embedded = True
            record.processing_status = STATUS_EMBEDDED
            record.chunk_count = len(chunk_records)
            source_registry.upsert(record)

            report["embedded_sources"] += 1
            report["chunk_count"] += len(chunk_records)
            report["ingested_files"].append(relative_path)

            if changed:
                memory_service.persist_workflow_event(
                    source="daise",
                    event_type="knowledge_source_added",
                    payload={
                        "source_id": record.source_id,
                        "collection": spec["collection"],
                        "path": str(file_path),
                    },
                    tags=["knowledge", spec["collection"]],
                )
            memory_service.persist_workflow_event(
                source="daise",
                event_type="knowledge_embedded",
                payload={
                    "source_id": record.source_id,
                    "collection": spec["collection"],
                    "path": str(file_path),
                    "chunk_count": len(chunk_records),
                },
                tags=["knowledge", spec["collection"]],
            )

        collection_reports.append(report)

    source_registry.save()
    collection_registry.save()
    return {
        "status": "ok",
        "workspace_root": str(workspace),
        "manifest_path": manifest,
        "manifest_error": manifest_error,
        "max_files_per_root": max_files_per_root,
        "collections": collection_reports,
    }


def _iter_text_files(root: Path, *, max_files: int) -> Iterable[Path]:
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= max_files:
            break
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        count += 1
        yield path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _chunk_text(text: str, *, chunk_chars: int = 1400, overlap_chars: int = 180) -> List[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + chunk_chars)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks
