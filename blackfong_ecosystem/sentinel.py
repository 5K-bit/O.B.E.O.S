from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .blackbox_archive import ArchiveCaptureProfileManager, SCHEMA_VERSION, normalize_capture_profile
from .device_registry import local_device_report
from .events import Event
from .storage import EventStore


logger = logging.getLogger(__name__)


@dataclass
class SentinelDetectionService:
    """Normalize real-world observations into canonical OBEOS events."""

    event_store: EventStore

    def emit_detection(
        self,
        *,
        modality: str,
        detector: str,
        detection: Dict[str, Any],
        confidence: float = 0.8,
        raw_artifact: Optional[Dict[str, Any]] = None,
        capture_profile: Optional[str] = None,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.event_store.init_db()
        profile = normalize_capture_profile(
            capture_profile or ArchiveCaptureProfileManager(self.event_store).current_profile()["profile"]
        )
        payload = {
            "schema_version": SCHEMA_VERSION,
            "modality": modality,
            "detector": detector,
            "detection": dict(detection or {}),
            "capture_profile": profile,
            "observed_at": time.time(),
            "provenance": {
                "source": "sentinel",
                "origin_service": "sentinel_detection_service",
                "collection_method": detector,
                "schema_version": SCHEMA_VERSION,
            },
        }
        detection_event = Event.new(
            source="sentinel",
            type="sentinel_detection_event",
            payload=payload,
            confidence=confidence,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["sentinel", modality, detector],
        )
        self.event_store.add_event(detection_event)

        raw_event_id = None
        raw_capture_state = "not_requested"
        if raw_artifact:
            raw_result = self._emit_raw_artifact(
                raw_artifact,
                profile=profile,
                trigger_event_id=detection_event.id,
                confidence=confidence,
                session_id=session_id,
                correlation_id=correlation_id,
                request_id=request_id,
            )
            raw_event_id = raw_result.get("event_id")
            raw_capture_state = raw_result["state"]

        return {
            "schema_version": SCHEMA_VERSION,
            "event_id": detection_event.id,
            "raw_artifact_event_id": raw_event_id,
            "raw_capture_state": raw_capture_state,
            "capture_profile": profile,
            "detection": payload,
        }

    def _emit_raw_artifact(
        self,
        raw_artifact: Dict[str, Any],
        *,
        profile: str,
        trigger_event_id: str,
        confidence: float,
        session_id: Optional[str],
        correlation_id: Optional[str],
        request_id: Optional[str],
    ) -> Dict[str, Any]:
        if profile == "metadata_only":
            return {"state": "skipped_metadata_only", "event_id": None}
        artifact = dict(raw_artifact or {})
        artifact_path = Path(str(artifact.get("path") or ""))
        payload = {
            "schema_version": SCHEMA_VERSION,
            "capture_profile": profile,
            "trigger_event_id": trigger_event_id,
            "artifact": artifact,
            "artifact_exists": artifact_path.exists() if str(artifact_path) else False,
            "captured_at": time.time(),
        }
        event = Event.new(
            source="sentinel",
            type="sentinel_raw_artifact_captured",
            payload=payload,
            confidence=confidence,
            parent_event=trigger_event_id,
            session_id=session_id,
            correlation_id=correlation_id,
            request_id=request_id,
            tags=["sentinel", "raw_artifact", profile],
        )
        self.event_store.add_event(event)
        return {"state": "captured", "event_id": event.id}


def build_sentinel_agent_report(*, device_id: str = "sentinel", label: str = "Sentinel") -> Dict[str, Any]:
    """Collect the Raspberry Pi Sentinel health payload sent to the Blackcomputer hub."""

    return local_device_report(device_id=device_id, label=label)


def run_sentinel_agent(
    *,
    hub: str,
    device_id: str = "sentinel",
    label: str = "Sentinel",
    token: str = "",
    interval_seconds: float = 10.0,
    once: bool = False,
) -> None:
    """Post Sentinel health reports to the Blackcomputer Legion device API."""

    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - setup docs install httpx
        raise RuntimeError("Sentinel agent requires httpx. Install project requirements on the Pi.") from exc

    hub_url = str(hub or "").rstrip("/")
    if not hub_url:
        raise ValueError("hub is required")
    endpoint = f"{hub_url}/api/devices/{device_id}/report"
    headers = {"X-Sentinel-Agent-Token": token} if token else {}
    interval = max(2.0, float(interval_seconds or 10.0))

    with httpx.Client(timeout=10.0) as client:
        while True:
            report = build_sentinel_agent_report(device_id=device_id, label=label)
            try:
                response = client.post(endpoint, json=report, headers=headers)
                response.raise_for_status()
                if once:
                    return
            except Exception as exc:
                if once:
                    raise RuntimeError(f"Sentinel report failed: {exc}") from exc
                logger.warning("sentinel_agent_report_failed: %s", exc)
            time.sleep(interval)
