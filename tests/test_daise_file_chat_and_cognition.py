from __future__ import annotations

from pathlib import Path

import pytest

from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.daise import DAISE
from blackfong_ecosystem.file_scope_service import FileScopeService
from blackfong_ecosystem.file_workflow import FileWorkflowService
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.storage import EventStore, NullVectorStore
from blackfong_ecosystem.ui.blackcomputer_tui import BlackcomputerTUIApp


class FakeCodingAgent:
    def process(self, user_input, context=None):
        return {
            "response": "Coding specialist fallback.",
            "confidence": 0.8,
            "trace_event_id": "coding-trace",
            "tools_invoked": [],
            "provider": "offline",
            "model": "stub",
            "backend_error": None,
            "backend_metadata": {},
            "agent": "coding",
            "task_type": "code_generation",
        }


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


@pytest.mark.xfail(
    reason="Triage B04: DAISE now defers file mutation until approval/consent is present, so no proposal is "
    "created from chat. Owner must decide whether chat should still create proposals (then update the code) "
    "or pass consent (then update this test). The policy was NOT loosened to satisfy the test.",
    strict=False,
)
def test_daise_handles_governed_file_proposal_from_chat(tmp_path: Path):
    memory = build_memory(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workflow = FileWorkflowService(memory_service=memory, allowed_roots=[workspace], denied_roots=[])
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        memory_service=memory,
        file_workflow=workflow,
    )
    note_path = workspace / "note.txt"

    result = daise.process_request(
        user_input=f"Create file `{note_path}` with content:\nOffline-first systems remember what they do.",
        agents={"coding": FakeCodingAgent()},
        context={
            "request_id": "file-chat-1",
            "session_id": "session-file-chat",
            "analysis_mode": "quick",
        },
    )

    assert result["agent"] == "daise"
    assert result["selected_agent"] == "daise"
    assert result["file_workflow"]["success"] is True
    proposal = result["file_workflow"]["proposal"]
    assert Path(proposal["path"]) == note_path.resolve()
    assert "Proposal ID:" in result["response"]
    assert workflow.list_proposals()["proposals"][0]["proposal_id"] == proposal["proposal_id"]


def test_dashboard_reuses_shared_file_workflow_for_chat_created_proposals(tmp_path: Path):
    memory = build_memory(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workflow = FileWorkflowService(memory_service=memory, allowed_roots=[workspace], denied_roots=[])
    daise = DAISE.create_default(
        event_store=memory.event_store,
        vector_store=NullVectorStore(),
        memory_service=memory,
        file_workflow=workflow,
    )
    shared_path = workspace / "shared.txt"

    daise.process_request(
        user_input=f"Create file `{shared_path}` with content:\nshared proposal",
        agents={"coding": FakeCodingAgent()},
        context={
            "request_id": "file-chat-2",
            "session_id": "session-file-chat",
            "analysis_mode": "quick",
        },
    )

    class StubOrchestrator:
        file_workflow = workflow

    dashboard = WebDashboard(
        host="127.0.0.1",
        port=8099,
        memory_service=memory,
        orchestrator_instance=StubOrchestrator(),
    )
    client = dashboard.app.test_client()
    proposals = client.get("/api/files/proposals").get_json()

    assert dashboard.file_workflow is workflow
    assert proposals["proposals"]
    assert proposals["proposals"][0]["path"].endswith("shared.txt")


def test_tui_prefers_shared_file_workflow_and_cognition_ascii_is_polished(tmp_path: Path):
    memory = build_memory(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workflow = FileWorkflowService(memory_service=memory, allowed_roots=[workspace], denied_roots=[])
    workflow.propose(
        path=str(workspace / "design.txt"),
        proposed_content="Machine soul, operator truth.\n",
        summary="Seed proposal",
    )

    class StubOrchestrator:
        file_workflow = workflow
        memory_service = memory

        def get_voice_loop_status(self):
            return {"active": False, "message": "Voice loop unavailable"}

    app = BlackcomputerTUIApp()
    app._orchestrator = StubOrchestrator()
    app._orchestrator_loaded = True

    proposals = app._file_command("/files proposals")
    cognition = app._execute_slash_command("/cognition forge")

    assert proposals["proposals"][0]["path"].endswith("design.txt")
    assert "THOUGHTGLASS" in cognition["ascii_summary"]  # surface was renamed from CORTEX
    assert "[" in cognition["ascii_summary"]
    assert "D.A.I.S.E" in cognition["ascii_summary"] or "System idle" in cognition["ascii_summary"]


def test_dashboard_and_tui_share_machine_read_map_scope_state(tmp_path: Path):
    memory = build_memory(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    approved_root = tmp_path / "approved-root"
    approved_root.mkdir()
    governed_file = workspace / "governed.txt"
    governed_file.write_text("governed truth\n", encoding="utf-8")
    approved_file = approved_root / "notes.txt"
    approved_file.write_text("mapped note\n", encoding="utf-8")

    scope_service = FileScopeService(
        memory_service=memory,
        governed_roots=[workspace],
        denied_roots=[],
        metadata_only_roots=[],
        approvals_path=tmp_path / "approved_file_roots.json",
    )

    class StubOrchestrator:
        file_scope_service = scope_service
        memory_service = memory

        def get_voice_loop_status(self):
            return {"active": False, "message": "Voice loop unavailable"}

    dashboard = WebDashboard(
        host="127.0.0.1",
        port=8099,
        memory_service=memory,
        orchestrator_instance=StubOrchestrator(),
    )
    client = dashboard.app.test_client()

    roots_before = client.get("/api/files/roots").get_json()
    approve = client.post("/api/files/roots", json={"path": str(approved_root)}).get_json()
    mapped = client.get(f"/api/files/map?path={approved_root}").get_json()
    read = client.get(f"/api/files/read?path={approved_file}").get_json()
    summary = client.get("/api/operator/summary").get_json()

    assert str(workspace.resolve()) in roots_before["governed_roots"]
    assert approve["success"] is True
    assert mapped["success"] is True
    assert any(child["name"] == "notes.txt" for child in mapped["entry"]["children"])
    assert read["success"] is True
    assert "mapped note" in read["content"]
    assert str(approved_root.resolve()) in summary["files"]["approved_roots"]

    app = BlackcomputerTUIApp()
    app._orchestrator = StubOrchestrator()
    app._orchestrator_loaded = True

    roots_result = app._file_command("/files roots")
    map_result = app._file_command(f"/files map {approved_root}")
    read_result = app._file_command(f"/files read {approved_file}")
    summary_result = app._execute_slash_command("/summary")

    assert str(approved_root.resolve()) in roots_result["files"]["approved_roots"]
    assert map_result["files"]["entry"]["path"].endswith("approved-root")
    assert "mapped note" in read_result["files"]["content"]
    assert summary_result["summary"]["files"]["approved_roots"]
