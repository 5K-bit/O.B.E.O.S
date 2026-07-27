from __future__ import annotations

from pathlib import Path

from blackfong_ecosystem.blackbox_archive import ArchiveCaptureProfileManager, BlackBoxCapsuleSealer
from blackfong_ecosystem.cognitive_expansion import DAISEStyleProfileService, DreamLayerService, MemoryArtifactService
from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.events import EVENT_TYPES, Event
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.sentinel import SentinelDetectionService
from blackfong_ecosystem.storage import EventStore, NullVectorStore


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


class FakeTinyProgrammer:
    def __init__(self) -> None:
        self.briefs = []
        self.controls = []
        self.auto_stream = True
        self.adopt_external = True
        self.autostart_enabled = True
        self.shutdown_policy = "ask"

    def get_status(self, *, probe_live=False, require_stream=None):
        return {
            "online": True,
            "state": "running",
            "mood": "focused",
            "current_program_type": "generative_glyphs",
            "stream_enabled": True,
        }

    def send_brief(self, brief, *, metadata=None):
        self.briefs.append((brief, metadata))
        return {"success": True, "brief": brief, "metadata": metadata or {}}

    def send_control(self, action, value=None):
        self.controls.append((action, value))
        return {"success": True, "action": action, "value": value}

    def get_recent(self, limit=10):
        return {"programs": [], "reflections": []}

    def get_bbs(self, limit=12):
        return {"success": False, "posts": []}

    def get_log_tail(self, *, lines=40, max_chars=12000):
        return {"success": True, "content": ""}


def test_cognitive_expansion_event_types_validate():
    for event_type in {
        "daise_style_profile_generated",
        "dream_layer_brief_generated",
        "memory_artifact_created",
        "blackbox_capsule_sealed",
        "sentinel_detection_event",
        "sentinel_raw_artifact_captured",
        "archive_capture_profile_changed",
    }:
        assert event_type in EVENT_TYPES
        event = Event.new(source="daise", type=event_type, payload={"ok": True})
        assert event.type == event_type


def test_dream_layer_generates_auditable_style_and_applies_to_tinyprogrammer(tmp_path: Path):
    store = EventStore(tmp_path / "events.db")
    fake_tiny = FakeTinyProgrammer()

    profile = DAISEStyleProfileService(store).generate_profile(persist=True)["style_profile"]
    result = DreamLayerService(store, tinyprogrammer=fake_tiny).apply_to_tinyprogrammer(profile)

    assert result["success"] is True
    assert store.count_events(type="daise_style_profile_generated") == 1
    assert store.count_events(type="dream_layer_brief_generated") == 1
    assert fake_tiny.briefs
    control_names = {name for name, _ in fake_tiny.controls}
    assert {"mood", "theme", "tempo", "program_weights", "style_profile", "color_bias", "style_tags"}.issubset(control_names)


def test_thoughtglass_memory_chamber_nodes_include_expansion_state(tmp_path: Path):
    memory = build_memory(tmp_path)
    store = memory.event_store
    MemoryArtifactService(store).create_artifact(
        artifact_type="conversation_constellation",
        title="First constellation",
        content={"nodes": 1, "edges": 0},
    )
    SentinelDetectionService(store).emit_detection(
        modality="camera",
        detector="test_detector",
        detection={"label": "motion"},
        raw_artifact={"path": str(tmp_path / "frame.jpg"), "media_type": "image"},
    )
    DAISEStyleProfileService(store).generate_profile(persist=True)
    ArchiveCaptureProfileManager(store).set_profile("metadata_only", reason="test")

    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    fake_tiny = FakeTinyProgrammer()
    dashboard.tinyprogrammer = fake_tiny
    dashboard.cognition_service.tinyprogrammer = fake_tiny
    client = dashboard.app.test_client()

    payload = client.get("/api/cognition/snapshot?layer=memory").get_json()

    node_ids = {node["id"] for node in payload["nodes"]}
    assert {"memory_chamber", "blackbox_archive", "sentinel", "dream_layer"}.issubset(node_ids)
    expansion = payload["source_snapshots"]["cognitive_expansion"]
    assert expansion["memory_artifacts"] == 1
    assert expansion["sentinel_detections"] == 1
    assert expansion["capture_profile"] == "metadata_only"


def test_blackbox_capsule_sealing_and_capture_profiles(tmp_path: Path):
    store = EventStore(tmp_path / "events.db")
    manager = ArchiveCaptureProfileManager(store)

    blocked = manager.set_profile("continuous_raw", consent={"approved": False, "level": 1}, reason="test")
    assert blocked["activated"] is False
    assert manager.current_profile()["profile"] == "bounded_raw"

    activated = manager.set_profile("continuous_raw", consent={"approved": True, "level": 2}, reason="test")
    assert activated["activated"] is True
    assert manager.current_profile()["profile"] == "continuous_raw"

    event = Event.new(source="sentinel", type="sentinel_detection_event", payload={"label": "motion"})
    artifact_path = tmp_path / "frame.txt"
    artifact_path.write_text("frame bytes", encoding="utf-8")
    sealed = BlackBoxCapsuleSealer(store, capsules_root=tmp_path / "capsules").seal_daily_capsule(
        "2026-05-09",
        events=[event],
        raw_artifacts=[{"path": str(artifact_path), "media_type": "text", "trigger_event_id": event.id}],
    )

    capsule_dir = Path(sealed["capsule_dir"])
    assert (capsule_dir / "manifest.json").exists()
    assert (capsule_dir / "hashes.json").exists()
    assert (capsule_dir / "events.jsonl").exists()
    assert (capsule_dir / "raw_artifacts.jsonl").exists()
    assert Path(sealed["raw_import_path"]).exists()
    assert store.count_events(type="blackbox_capsule_sealed") == 1


def test_sentinel_metadata_only_skips_raw_artifact_event(tmp_path: Path):
    store = EventStore(tmp_path / "events.db")
    ArchiveCaptureProfileManager(store).set_profile("metadata_only", reason="test")

    result = SentinelDetectionService(store).emit_detection(
        modality="audio",
        detector="sound_spike",
        detection={"label": "clap"},
        raw_artifact={"path": str(tmp_path / "audio.wav"), "media_type": "audio"},
    )

    assert result["raw_capture_state"] == "skipped_metadata_only"
    assert store.count_events(type="sentinel_detection_event") == 1
    assert store.count_events(type="sentinel_raw_artifact_captured") == 0

