from __future__ import annotations

import functools
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import paths


MANIFEST_FILENAME = "obeos_ecosystem.toml"


class EcosystemManifestError(RuntimeError):
    """Raised when the first-party ecosystem manifest cannot be read."""


def manifest_path(workspace_root: Optional[Path] = None) -> Path:
    return Path(workspace_root or paths.root).resolve() / MANIFEST_FILENAME


@functools.lru_cache(maxsize=8)
def _load_manifest_cached(path_text: str, mtime_ns: int) -> Dict[str, Any]:
    del mtime_ns
    path = Path(path_text)
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EcosystemManifestError(f"Missing ecosystem manifest: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise EcosystemManifestError(f"Invalid ecosystem manifest {path}: {exc}") from exc


def load_ecosystem_manifest(workspace_root: Optional[Path] = None) -> Dict[str, Any]:
    path = manifest_path(workspace_root)
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise EcosystemManifestError(f"Missing ecosystem manifest: {path}") from exc
    manifest = _load_manifest_cached(str(path), stat.st_mtime_ns)
    projects = manifest.get("projects")
    if not isinstance(projects, list):
        raise EcosystemManifestError(f"Ecosystem manifest {path} must contain [[projects]] entries")
    return manifest


def iter_ecosystem_projects(workspace_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(workspace_root or paths.root).resolve()
    manifest = load_ecosystem_manifest(root)
    projects: List[Dict[str, Any]] = []
    for raw in manifest.get("projects", []):
        if not isinstance(raw, dict):
            continue
        project = dict(raw)
        rel_path = Path(str(project.get("path") or ""))
        project["path"] = rel_path.as_posix()
        project["resolved_path"] = str((root / rel_path).resolve())
        project["available"] = Path(project["resolved_path"]).is_dir()
        projects.append(project)
    return projects


def ecosystem_collections(workspace_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    collections: List[Dict[str, Any]] = []
    for project in iter_ecosystem_projects(workspace_root):
        knowledge = project.get("knowledge") or {}
        if not isinstance(knowledge, dict):
            continue
        collection = str(knowledge.get("collection") or project.get("id") or "").strip()
        if not collection:
            continue
        collections.append(
            {
                "project_id": project.get("id"),
                "directory": project.get("path"),
                "resolved_directory": project.get("resolved_path"),
                "collection": collection,
                "series": str(knowledge.get("series") or project.get("id") or collection),
                "domain": str(knowledge.get("domain") or "obeos_ecosystem"),
                "description": str(knowledge.get("description") or project.get("description") or ""),
                "category": str(knowledge.get("category") or "ecosystem"),
                "available": bool(project.get("available")),
            }
        )
    return collections
