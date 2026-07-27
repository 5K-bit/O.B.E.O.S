from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import paths
from .events import Event
from .storage import EventStore


SCHEMA_VERSION = "1.0"
DEFAULT_CAPTURE_PROFILE = "bounded_raw"

CAPTURE_PROFILES: Dict[str, Dict[str, Any]] = {
    "bounded_raw": {
        "description": "Event-triggered raw artifacts with hashes and summaries.",
        "raw_artifacts": "event_triggered",
        "requires_explicit_consent": False,
    },
    "continuous_raw": {
        "description": "Operator-enabled continuous raw capture profile.",
        "raw_artifacts": "continuous",
        "requires_explicit_consent": True,
        "minimum_consent_level": 2,
    },
    "metadata_only": {
        "description": "Canonical events, hashes, and summaries without raw media.",
        "raw_artifacts": "none",
        "requires_explicit_consent": False,
    },
}


def normalize_capture_profile(profile: str | None) -> str:
    normalized = str(profile or DEFAULT_CAPTURE_PROFILE).strip().lower()
    if normalized not in CAPTURE_PROFILES:
        valid = ", ".join(sorted(CAPTURE_PROFILES))
        raise ValueError(f"Unknown archive capture profile {profile!r}. Valid profiles: {valid}")
    return normalized


def _consent_approves(profile: str, consent: Optional[Dict[str, Any]]) -> bool:
    definition = CAPTURE_PROFILES[profile]
    if not definition.get("requires_explicit_consent"):
        return True
    consent = dict(consent or {})
    required_level = int(definition.get("minimum_consent_level", 1))
    return bool(consent.get("approved")) and int(consent.get("level") or 0) >= required_level


@dataclass
class ArchiveCaptureProfileManager:
    """Event-backed capture profile manager for Black Box archival behavior."""

    event_store: EventStore

    def current_profile(self) -> Dict[str, Any]:
        self.event_store.init_db()
        events = self.event_store.list_events_by_type("archive_capture_profile_changed", limit=50)
        for event in events:
            payload = event.payload or {}
            if payload.get("activated") is True:
                profile = normalize_capture_profile(str(payload.get("profile") or DEFAULT_CAPTURE_PROFILE))
                return {
                    "schema_version": SCHEMA_VERSION,
                    "profile": profile,
                    "definition": CAPTURE_PROFILES[profile],
                    "event_id": event.id,
                    "activated_at": payload.get("requested_at"),
                }
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": DEFAULT_CAPTURE_PROFILE,
            "definition": CAPTURE_PROFILES[DEFAULT_CAPTURE_PROFILE],
            "event_id": None,
            "activated_at": None,
        }

    def set_profile(
        self,
        profile: str,
        *,
        consent: Optional[Dict[str, Any]] = None,
        reason: str = "",
        requested_by: str = "operator",
    ) -> Dict[str, Any]:
        self.event_store.init_db()
        normalized = normalize_capture_profile(profile)
        activated = _consent_approves(normalized, consent)
        required_level = CAPTURE_PROFILES[normalized].get("minimum_consent_level")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "profile": normalized,
            "definition": CAPTURE_PROFILES[normalized],
            "requested_by": requested_by,
            "requested_at": time.time(),
            "reason": reason,
            "consent": consent or {},
            "activated": activated,
            "decision_state": "approved" if activated else "requires_consent",
            "required_consent_level": required_level,
        }
        event = Event.new(
            source="blackbox",
            type="archive_capture_profile_changed",
            payload=payload,
            confidence=0.95 if activated else 0.7,
            tags=["blackbox", "archive", "capture_profile", normalized],
        )
        self.event_store.add_event(event)
        return {**payload, "event_id": event.id}


@dataclass
class BlackBoxCapsuleSealer:
    """Create sealed local capsule directories from canonical events and raw artifacts."""

    event_store: EventStore
    capsules_root: Path = paths.capsules_root

    def seal_daily_capsule(
        self,
        date_str: str,
        *,
        events: Iterable[Event],
        raw_artifacts: Optional[Iterable[Dict[str, Any]]] = None,
        capture_profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.event_store.init_db()
        profile = normalize_capture_profile(capture_profile or ArchiveCaptureProfileManager(self.event_store).current_profile()["profile"])
        capsule_dir = self.capsules_root / date_str
        capsule_dir.mkdir(parents=True, exist_ok=True)

        event_list = list(events)
        artifacts = [self._normalize_artifact(item) for item in (raw_artifacts or [])]
        events_path = capsule_dir / "events.jsonl"
        artifacts_path = capsule_dir / "raw_artifacts.jsonl"
        raw_import_path = self.capsules_root / f"{date_str}.raw"
        raw_import_path.parent.mkdir(parents=True, exist_ok=True)

        with events_path.open("w", encoding="utf-8") as handle, raw_import_path.open("w", encoding="utf-8") as raw_handle:
            for event in event_list:
                line = json.dumps(event.to_dict(), sort_keys=True)
                handle.write(line + "\n")
                raw_handle.write(line + "\n")

        with artifacts_path.open("w", encoding="utf-8") as handle:
            for artifact in artifacts:
                handle.write(json.dumps(artifact, sort_keys=True) + "\n")

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "capsule_date": date_str,
            "capture_profile": profile,
            "events_count": len(event_list),
            "raw_artifact_count": len(artifacts),
            "raw_import_path": str(raw_import_path),
            "sealed_at": time.time(),
        }
        (capsule_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        hashes = self._compute_hashes(capsule_dir)
        (capsule_dir / "hashes.json").write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")

        event = Event.new(
            source="blackbox",
            type="blackbox_capsule_sealed",
            payload={
                **manifest,
                "capsule_dir": str(capsule_dir),
                "hashes": hashes,
            },
            confidence=1.0,
            tags=["blackbox", "capsule", "sealed", profile],
        )
        self.event_store.add_event(event)
        return {
            "schema_version": SCHEMA_VERSION,
            "event_id": event.id,
            "capsule_dir": str(capsule_dir),
            "raw_import_path": str(raw_import_path),
            "manifest": manifest,
            "hashes": hashes,
        }

    @staticmethod
    def _normalize_artifact(artifact: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(artifact or {})
        artifact_path = Path(str(data.get("path") or ""))
        if artifact_path.exists() and artifact_path.is_file():
            data.setdefault("sha256", _hash_file(artifact_path))
            data.setdefault("size_bytes", artifact_path.stat().st_size)
        data.setdefault("schema_version", SCHEMA_VERSION)
        data.setdefault("captured_at", time.time())
        return data

    @staticmethod
    def _compute_hashes(capsule_dir: Path) -> Dict[str, str]:
        hashes: Dict[str, str] = {}
        for file_path in sorted(path for path in capsule_dir.rglob("*") if path.is_file()):
            if file_path.name == "hashes.json":
                continue
            hashes[str(file_path.relative_to(capsule_dir))] = _hash_file(file_path)
        return hashes


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
