"""Tests for Phase 2 OBEOS workspace improvements."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from blackfong_ecosystem.backend_warmup import warm_ollama_model
from blackfong_ecosystem.chat_backends import ChatBackendResult, OllamaChatBackend
from blackfong_ecosystem.cognition_renderer import normalize_renderer_inputs
from blackfong_ecosystem.operator_session import (
    begin_operator_session,
    get_shared_session_id,
    link_surface,
    reset_operator_session,
)
from blackfong_ecosystem.unified_orchestrator import UnifiedOrchestrator
from blackfong_ecosystem.workspace_shortcuts import WORKSPACE_SHORTCUTS, shortcuts_json_for_hud
from blackfong_ecosystem.workspace_window_manager import DEFAULT_TAB_GROUPS, WorkspaceWindowManager


def test_shortcuts_catalog_exports_for_hud():
    assert len(WORKSPACE_SHORTCUTS) >= 10
    exported = shortcuts_json_for_hud()
    assert exported[0]["sequence"]
    assert exported[0]["id"]


def test_operator_session_link_surface(tmp_path, monkeypatch):
    from blackfong_ecosystem import operator_session

    session_file = tmp_path / "operator_session.json"
    monkeypatch.setattr(operator_session, "SESSION_FILE", session_file)
    reset_operator_session()
    session_id = get_shared_session_id(surface="desktop")
    payload = link_surface("hud")
    assert payload["session_id"] == session_id
    assert "hud" in payload["linked_surfaces"]


def test_operator_session_can_start_fresh_local_without_rewriting_shared(tmp_path, monkeypatch):
    from blackfong_ecosystem import operator_session

    session_file = tmp_path / "operator_session.json"
    monkeypatch.setattr(operator_session, "SESSION_FILE", session_file)
    reset_operator_session()
    shared_session_id = get_shared_session_id(surface="desktop")

    local = begin_operator_session(surface="desktop", mode="fresh_local")

    assert local["scope"] == "local"
    assert local["session_id"] != shared_session_id
    assert get_shared_session_id() == shared_session_id


def test_normalize_renderer_inputs():
    payload = normalize_renderer_inputs(
        snapshot={"active_layer": "memory", "focus": {"zoom": 1.5, "orbit": 0.2}},
        graph_payload={"graph": {"nodes": [{"id": "a"}], "edges": []}},
    )
    assert payload["active_layer"] == "memory"
    assert payload["zoom"] == 1.5
    assert len(payload["nodes"]) == 1


def test_unified_orchestrator_fast_path_short_message():
    assert UnifiedOrchestrator._should_use_fast_path("hello there", {"analysis_mode": "quick"}) is True
    assert UnifiedOrchestrator._should_use_fast_path("x" * 300, {"analysis_mode": "quick"}) is False
    assert UnifiedOrchestrator._should_use_fast_path("deep analysis please", {"analysis_mode": "quick"}) is False


def test_workspace_window_manager_tab_groups_persist(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QMainWindow

    QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    manager = WorkspaceWindowManager(QMainWindow(), settings)
    settings.setValue("workspace/tab_groups", json.dumps({"custom": ["dock_trace", "workbench_chat"]}))
    groups = manager.list_tab_groups()
    assert groups["custom"] == ["dock_trace", "workbench_chat"]
    assert "debug" in DEFAULT_TAB_GROUPS

    payload = {"collapsed": [], "floating": [], "float_geometries": {"dock_tiny": "AAAA"}}
    settings.setValue("workspace/window_manager", json.dumps(payload))
    manager.restore_persisted()
    assert manager._load_persist_payload()["float_geometries"]["dock_tiny"] == "AAAA"


def test_ollama_generate_stream_falls_back_to_generate(monkeypatch):
    backend = OllamaChatBackend(model="test-model")
    tokens: list[str] = []

    def fake_generate(self, messages, **options):
        return ChatBackendResult(text="hello", provider="ollama", model="test-model")

    monkeypatch.setattr(OllamaChatBackend, "generate", fake_generate)

    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client_cls.return_value.__enter__.return_value = client
        stream_ctx = MagicMock()
        stream_ctx.__enter__.return_value = stream_ctx
        stream_ctx.iter_lines.return_value = iter([])
        client.stream.return_value = stream_ctx
        result = backend.generate_stream([{"role": "user", "content": "hi"}], on_token=tokens.append)
    assert result.text == "hello"


def test_warm_ollama_model_handles_connection_error(monkeypatch):
    def raise_error(*args, **kwargs):
        raise ConnectionError("offline")

    monkeypatch.setattr("blackfong_ecosystem.backend_warmup.httpx.Client", raise_error)
    outcome = warm_ollama_model()
    assert outcome["status"] == "error"


def test_quick_num_predict_default_is_generous():
    from blackfong_ecosystem.config import chat_settings

    assert chat_settings.jarvis_quick_num_predict >= 384


def test_brain_metrics_line_from_snapshot():
    from blackfong_ecosystem.workspace_boot_splash import _brain_metrics_line

    line = _brain_metrics_line({"cognition": {"graph": {"nodes": [{"id": "a"}, {"id": "b"}]}}})
    assert "Brain scale" in line
    assert "2 nodes" in line


def test_boot_animation_assets_exist():
    from blackfong_ecosystem.workspace_boot_splash import boot_asset_path, branding_root

    assert (branding_root() / "obeos-mark.png").is_file()
    assert (branding_root() / "obeos-wordmark.png").is_file()
    for name in ("earth", "forming", "ring", "wordmark", "end"):
        assert boot_asset_path(name).is_file(), name


def test_tinyprogrammer_warming_does_not_block_overall_ready():
    from blackfong_ecosystem.startup_coordinator import StartupCoordinator

    coordinator = StartupCoordinator()
    coordinator.begin_boot(surface="workspace")
    for key in coordinator.snapshot()["subsystem_order"]:
        if key != "tinyprogrammer":
            coordinator.set_subsystem(key, state="ready", summary="ready")
    coordinator.set_subsystem("tinyprogrammer", state="warming", summary="launching")
    snapshot = coordinator.snapshot()
    assert snapshot["overall_state"] == "ready"


def test_operator_session_link_helpers(tmp_path, monkeypatch):
    from blackfong_ecosystem import operator_session

    session_file = tmp_path / "operator_session.json"
    monkeypatch.setattr(operator_session, "SESSION_FILE", session_file)
    operator_session.reset_operator_session()
    operator_session.link_surface("desktop")
    operator_session.link_surface("hud")
    assert operator_session.is_multi_surface_linked() is True
    assert operator_session.session_link_label() == "Desktop + HUD linked"


def test_effective_shortcuts_apply_overrides():
    from blackfong_ecosystem.workspace_shortcuts import effective_shortcuts

    resolved = {item.action_id: item.sequence for item in effective_shortcuts({"refresh": "Ctrl+R"})}
    assert resolved["refresh"] == "Ctrl+R"
