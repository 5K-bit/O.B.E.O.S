from __future__ import annotations

import json
import platform
import string
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import paths
from .conversation_memory import ConversationMemoryService, conversation_memory_service


SCHEMA_VERSION = "1.0"
DEFAULT_SCOPE_MAX_READ_BYTES = 262_144
DEFAULT_SCOPE_MAP_LIMIT = 120
DEFAULT_SCOPE_MAP_DEPTH = 2
BLOCKED_PATH_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "$Recycle.Bin",
    "Windows",
    "System32",
    "Program Files",
    "Program Files (x86)",
}


class FileScopeService:
    """Machine-wide read-and-map service with explicit scope approvals."""

    def __init__(
        self,
        *,
        memory_service: Optional[ConversationMemoryService] = None,
        governed_roots: Optional[Iterable[Path]] = None,
        denied_roots: Optional[Iterable[Path]] = None,
        metadata_only_roots: Optional[Iterable[Path]] = None,
        approvals_path: Optional[Path] = None,
    ) -> None:
        self.memory_service = memory_service or conversation_memory_service
        self.governed_roots = [self._resolve(root) for root in (governed_roots or [paths.root])]
        default_denied = [
            Path("C:/Windows"),
            Path("C:/Program Files"),
            Path("C:/Program Files (x86)"),
            Path("C:/ProgramData"),
        ]
        default_metadata_only = list(default_denied)
        self.denied_roots = [self._resolve(root) for root in (denied_roots or default_denied)]
        self.metadata_only_roots = [self._resolve(root) for root in (metadata_only_roots or default_metadata_only)]
        self.approvals_path = approvals_path or (paths.data_root / "approved_file_roots.json")

    def list_roots(
        self,
        *,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        emit: bool = False,
    ) -> Dict[str, Any]:
        approved_roots = self._approved_roots()
        discovery = [self._describe_root(root, approved_roots=approved_roots) for root in self._discover_candidate_roots()]
        payload = {
            "schema_version": SCHEMA_VERSION,
            "governed_roots": [str(root) for root in self.governed_roots],
            "approved_roots": [str(root) for root in approved_roots],
            "discovered_roots": discovery,
            "counts": {
                "governed": len(self.governed_roots),
                "approved": len(approved_roots),
                "candidates": sum(1 for root in discovery if root.get("state") == "candidate"),
                "metadata_only": sum(1 for root in discovery if root.get("state") == "metadata_only"),
            },
            "policy": {
                "mode": "read_map_first",
                "write_boundary": "governed_project_roots_only",
                "approval_required_outside_governed_roots": True,
                "blocked_path_parts": sorted(BLOCKED_PATH_PARTS),
            },
        }
        if emit:
            self.memory_service.persist_workflow_event(
                source="blackfong",
                event_type="file_scope_discovery",
                payload={
                    **payload,
                    "provenance": self._provenance("list_roots"),
                },
                confidence=0.9,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
                tags=["file_scope", "roots"],
            )
        return payload

    def approve_root(
        self,
        path: str,
        *,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = self._resolve_path(path)
        decision = self._approval_decision(target)
        if not decision["approved"]:
            return {
                "success": False,
                "action": "approve_root",
                "path": str(target),
                "reason_code": decision["reason_code"],
                "human_reason": decision["human_reason"],
                "roots": self.list_roots(),
            }
        approved_roots = self._approved_roots()
        if target not in approved_roots and target not in self.governed_roots:
            approved_roots.append(target)
            self._save_approved_roots(approved_roots)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "root": str(target),
            "state": "approved",
            "provenance": self._provenance("approve_root"),
        }
        event = self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="file_scope_root_approved",
            payload=payload,
            confidence=0.96,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["file_scope", "approved_root"],
        )
        return {
            "success": True,
            "action": "approve_root",
            "path": str(target),
            "event_id": event.id,
            "roots": self.list_roots(),
        }

    def map_path(
        self,
        path: str,
        *,
        depth: int = DEFAULT_SCOPE_MAP_DEPTH,
        limit: int = DEFAULT_SCOPE_MAP_LIMIT,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = self._resolve_path(path)
        decision = self._access_decision(target, mode="map")
        if not decision["allowed"]:
            return {
                "success": False,
                "action": "map",
                "path": str(target),
                "reason_code": decision["reason_code"],
                "human_reason": decision["human_reason"],
                "access": decision,
                "roots": self.list_roots(),
            }
        if not target.exists():
            return {
                "success": False,
                "action": "map",
                "path": str(target),
                "reason_code": "path_missing",
                "human_reason": "That path does not exist on this machine.",
                "access": decision,
            }
        entry = self._map_entry(target, depth=max(0, int(depth)), limit=max(1, int(limit)))
        event = self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="file_scope_map",
            payload={
                "schema_version": SCHEMA_VERSION,
                "path": str(target),
                "depth": max(0, int(depth)),
                "limit": max(1, int(limit)),
                "entry": entry,
                "provenance": self._provenance("map_path"),
            },
            confidence=0.92,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["file_scope", "map"],
        )
        return {
            "success": True,
            "action": "map",
            "path": str(target),
            "entry": entry,
            "event_id": event.id,
            "access": decision,
        }

    def read_path(
        self,
        path: str,
        *,
        max_bytes: int = DEFAULT_SCOPE_MAX_READ_BYTES,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        target = self._resolve_path(path)
        decision = self._access_decision(target, mode="read")
        if not decision["allowed"]:
            return {
                "success": False,
                "action": "read",
                "path": str(target),
                "reason_code": decision["reason_code"],
                "human_reason": decision["human_reason"],
                "access": decision,
                "roots": self.list_roots(),
            }
        if not target.exists():
            return {
                "success": False,
                "action": "read",
                "path": str(target),
                "reason_code": "path_missing",
                "human_reason": "That path does not exist on this machine.",
                "access": decision,
            }
        if target.is_dir():
            return {
                "success": False,
                "action": "read",
                "path": str(target),
                "reason_code": "directory_requires_map",
                "human_reason": "That path is a directory. Use map or browse on it instead of read.",
                "access": decision,
            }
        try:
            raw = target.read_bytes()
        except OSError as exc:
            return {
                "success": False,
                "action": "read",
                "path": str(target),
                "reason_code": "read_failed",
                "human_reason": str(exc),
                "access": decision,
            }
        if b"\x00" in raw[:4096]:
            return {
                "success": False,
                "action": "read",
                "path": str(target),
                "reason_code": "binary_file_blocked",
                "human_reason": "This read-map phase only opens text content. Binary files stay metadata-only.",
                "size_bytes": len(raw),
                "access": decision,
            }
        truncated = len(raw) > max_bytes
        sample = raw[:max_bytes]
        text, encoding = self._decode_text(sample)
        event = self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="file_scope_read",
            payload={
                "schema_version": SCHEMA_VERSION,
                "path": str(target),
                "size_bytes": len(raw),
                "truncated": truncated,
                "encoding": encoding,
                "provenance": self._provenance("read_path"),
            },
            confidence=0.94,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["file_scope", "read"],
        )
        return {
            "success": True,
            "action": "read",
            "path": str(target),
            "content": text,
            "encoding": encoding,
            "size_bytes": len(raw),
            "truncated": truncated,
            "event_id": event.id,
            "access": decision,
        }

    def _map_entry(self, target: Path, *, depth: int, limit: int) -> Dict[str, Any]:
        entry: Dict[str, Any] = {
            "name": target.name or str(target),
            "path": str(target),
            "kind": "directory" if target.is_dir() else "file",
            "size_bytes": target.stat().st_size if target.exists() and target.is_file() else None,
            "state": self._path_state(target),
        }
        if not target.is_dir() or depth <= 0:
            return entry
        children: List[Dict[str, Any]] = []
        try:
            for child in sorted(target.iterdir(), key=lambda item: item.name.lower()):
                if len(children) >= limit:
                    break
                child_state = self._path_state(child)
                child_entry = {
                    "name": child.name,
                    "path": str(child),
                    "kind": "directory" if child.is_dir() else "file",
                    "state": child_state,
                }
                if child.is_dir() and depth > 1 and child_state in {"governed", "approved"}:
                    child_entry["children"] = self._map_entry(child, depth=depth - 1, limit=max(10, limit // 2)).get("children", [])
                children.append(child_entry)
        except OSError as exc:
            entry["error"] = str(exc)
        entry["children"] = children
        entry["truncated"] = len(children) >= limit
        return entry

    def _discover_candidate_roots(self) -> List[Path]:
        roots: List[Path] = []
        seen: set[str] = set()

        def add(candidate: Path) -> None:
            try:
                resolved = self._resolve(candidate)
            except Exception:
                return
            key = str(resolved).lower()
            if key in seen or not resolved.exists():
                return
            seen.add(key)
            roots.append(resolved)

        add(paths.root)
        home = Path.home()
        add(home)
        for name in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"):
            add(home / name)

        if os_name() == "windows":
            for letter in string.ascii_uppercase:
                drive = Path(f"{letter}:/")
                if drive.exists():
                    add(drive)
        else:
            add(Path("/"))

        return roots

    def _describe_root(self, root: Path, *, approved_roots: List[Path]) -> Dict[str, Any]:
        state = self._path_state(root, approved_roots=approved_roots)
        return {
            "path": str(root),
            "name": root.name or str(root),
            "state": state,
            "human_reason": {
                "governed": "Governed project root with read-write proposal workflow.",
                "approved": "Approved read-map scope for D.A.I.S.E.",
                "metadata_only": "System-sensitive location. Metadata only in this phase.",
                "candidate": "Candidate root. Approve it before deep mapping or file reads.",
                "blocked": "Blocked path in the current read-map phase.",
            }.get(state, "Candidate root."),
        }

    def _access_decision(self, path: Path, *, mode: str) -> Dict[str, Any]:
        state = self._path_state(path)
        if state in {"governed", "approved"}:
            return {
                "allowed": True,
                "scope_state": state,
                "reason_code": "approved_scope",
                "human_reason": "Path is inside an approved read-map scope.",
                "path": str(path),
                "mode": mode,
            }
        if state == "metadata_only":
            return {
                "allowed": False,
                "scope_state": state,
                "reason_code": "metadata_only_root",
                "human_reason": "This system-sensitive path stays metadata-only in the current phase.",
                "path": str(path),
                "mode": mode,
            }
        return {
            "allowed": False,
            "scope_state": state,
            "reason_code": "scope_approval_required",
            "human_reason": "Approve this root before D.A.I.S.E browses or reads it outside governed project roots.",
            "path": str(path),
            "mode": mode,
        }

    def _approval_decision(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {
                "approved": False,
                "reason_code": "path_missing",
                "human_reason": "That root does not exist on this machine.",
            }
        state = self._path_state(path)
        if state == "metadata_only":
            return {
                "approved": False,
                "reason_code": "metadata_only_root",
                "human_reason": "That path is system-sensitive and cannot become an approved read scope in this phase.",
            }
        if state == "blocked":
            return {
                "approved": False,
                "reason_code": "blocked_path",
                "human_reason": "That path is blocked by the current safety envelope.",
            }
        return {
            "approved": True,
            "reason_code": "approved",
            "human_reason": "Root approved for read and map access.",
        }

    def _path_state(self, path: Path, *, approved_roots: Optional[List[Path]] = None) -> str:
        resolved = self._resolve(path)
        approved_roots = approved_roots or self._approved_roots()
        if self._within_any(resolved, self.governed_roots):
            return "governed"
        if self._within_any(resolved, approved_roots):
            return "approved"
        if self._within_any(resolved, self.metadata_only_roots) or any(part in BLOCKED_PATH_PARTS for part in resolved.parts):
            return "metadata_only"
        if self._within_any(resolved, self.denied_roots):
            return "blocked"
        return "candidate"

    def _approved_roots(self) -> List[Path]:
        if not self.approvals_path.exists():
            return []
        try:
            raw = json.loads(self.approvals_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        roots: List[Path] = []
        for item in raw if isinstance(raw, list) else []:
            try:
                roots.append(self._resolve(Path(str(item))))
            except Exception:
                continue
        unique: Dict[str, Path] = {}
        for root in roots:
            unique[str(root).lower()] = root
        return list(unique.values())

    def _save_approved_roots(self, roots: List[Path]) -> None:
        self.approvals_path.parent.mkdir(parents=True, exist_ok=True)
        payload = sorted({str(self._resolve(root)) for root in roots})
        self.approvals_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _decode_text(raw: bytes) -> tuple[str, str]:
        for encoding in ("utf-8", "utf-8-sig", "cp1252"):
            try:
                return raw.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace"), "utf-8-replace"

    @staticmethod
    def _resolve_path(path: str) -> Path:
        target = Path(str(path or "").strip())
        if not target.is_absolute():
            target = paths.root / target
        return FileScopeService._resolve(target)

    @staticmethod
    def _resolve(path: Path) -> Path:
        return path.expanduser().resolve()

    @staticmethod
    def _within_any(path: Path, roots: Iterable[Path]) -> bool:
        for root in roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _provenance(method: str) -> Dict[str, Any]:
        return {
            "source": "local_filesystem+scope_policy",
            "origin_service": "file_scope_service",
            "derived_from": [],
            "collection_method": method,
            "observed_at": time.time(),
            "schema_version": SCHEMA_VERSION,
        }


def os_name() -> str:
    return platform.system().strip().lower()
