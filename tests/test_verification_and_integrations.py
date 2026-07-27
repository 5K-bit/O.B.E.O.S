from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from blackfong_ecosystem.external_integrations import ExternalIntegrationRegistry
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.redamon_mode import RedAmonWorkflowService
from blackfong_ecosystem.verification import VerificationService


def _write_file(path: Path, content: str = "test") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_tacklebox(root: Path) -> None:
    _write_file(root / "CodingAgent-main" / "README.md")
    _write_file(root / "CodingAgent-main" / "main.py", "print('coding agent')")
    _write_file(root / "CodingAgent-main" / "pyproject.toml", "[project]\nname='codingagent'")

    _write_file(root / "redamon" / "README.md")
    _write_file(root / "redamon" / "docker-compose.yml", "services: {}")
    _write_file(root / "redamon" / "agentic" / "api.py", "app = object()")

    _write_file(root / "ai-monitor" / "README.md")
    _write_file(root / "ai-monitor" / "ai_monitor.py", "print('ai monitor')")
    _write_file(root / "ai-monitor" / "pyproject.toml", "[project]\nname='ai-monitor'")

    _write_file(root / "dreamgraph-main" / "README.md")


def test_external_integration_registry_detects_tacklebox_projects(tmp_path: Path) -> None:
    _seed_tacklebox(tmp_path)
    registry = ExternalIntegrationRegistry(workspace_root=tmp_path, tacklebox_root=tmp_path)

    listing = registry.list_integrations()

    assert listing["counts"]["available"] == 4
    coding = registry.describe("codingagent_external")
    assert coding["available"] is True
    assert coding["launch_supported"] is True
    assert coding["obeos_policy"]["authority"] == "external_sidecar"

    dreamgraph = registry.describe("dreamgraph_reference")
    assert dreamgraph["visual_reference_only"] is True
    assert any("graph" in idea or "node" in idea for idea in dreamgraph["visual_borrow_ideas"])


def test_redamon_workflow_requires_explicit_consent(tmp_path: Path) -> None:
    _seed_tacklebox(tmp_path)
    registry = ExternalIntegrationRegistry(workspace_root=tmp_path, tacklebox_root=tmp_path)
    workflow = RedAmonWorkflowService(integration_registry=registry)

    denied = workflow.launch(execute=False)
    approved = workflow.launch(consent={"approved": True, "level": 2}, execute=False)

    assert denied["status"] == "requires_consent"
    assert approved["status"] == "planned"
    assert approved["launch"]["integration_id"] == "redamon_mode"


def test_verification_full_writes_structured_report(tmp_path: Path) -> None:
    _seed_tacklebox(tmp_path / "tacklebox")
    registry = ExternalIntegrationRegistry(workspace_root=tmp_path, tacklebox_root=tmp_path / "tacklebox")
    services = SimpleNamespace(integration_registry=registry)
    verification = VerificationService(workspace_root=tmp_path, services=services)

    verification.automated = lambda: {  # type: ignore[method-assign]
        "mode": "automated",
        "status": "passed",
        "summary": "155 passed, 2 warnings",
    }
    verification.surfaces = lambda: {  # type: ignore[method-assign]
        "mode": "surfaces",
        "status": "passed",
        "checks": {
            "hud": {"status": "passed"},
            "workspace": {"status": "passed"},
            "tinyprogrammer": {"status": "passed", "crash_loop_detected": False},
            "thoughtglass": {"status": "passed"},
        },
    }
    verification.integrations = lambda: {  # type: ignore[method-assign]
        "mode": "integrations",
        "status": "passed",
        "checks": {
            "redamon_mode": {"available": True},
        },
    }

    report = verification.full()
    report_path = Path(report["report_path"])

    assert report["status"] == "passed"
    assert report_path.exists()
    latest = report_path.parent / "latest-full.json"
    assert latest.exists()
    assert json.loads(latest.read_text(encoding="utf-8"))["status"] == "passed"


def test_release_evidence_writes_json_and_markdown_reports(tmp_path: Path) -> None:
    services = SimpleNamespace(integration_registry=ExternalIntegrationRegistry(workspace_root=tmp_path, tacklebox_root=tmp_path))
    verification = VerificationService(workspace_root=tmp_path, services=services)

    verification.surfaces = lambda: {  # type: ignore[method-assign]
        "mode": "surfaces",
        "status": "passed",
        "checks": {
            "hud": {"status": "passed"},
            "workspace": {"status": "passed"},
            "tinyprogrammer": {"status": "passed", "crash_loop_detected": False},
            "thoughtglass": {"status": "passed"},
        },
    }
    verification.integrations = lambda: {  # type: ignore[method-assign]
        "mode": "integrations",
        "status": "passed",
        "checks": {"redamon_mode": {"available": True}},
    }

    report = verification.release_evidence(profile="amd64-pc", include_automated=False)

    report_path = Path(report["report_path"])
    markdown_path = Path(report["markdown_path"])
    latest_json = report_path.parent / "latest-release-evidence.json"

    assert report["status"] == "in_progress"
    assert report_path.exists()
    assert markdown_path.exists()
    assert latest_json.exists()
    assert "Kubuntu 24.04 LTS" in markdown_path.read_text(encoding="utf-8")


def test_shotset_writes_manifest_and_capture_index(tmp_path: Path) -> None:
    services = SimpleNamespace(integration_registry=ExternalIntegrationRegistry(workspace_root=tmp_path, tacklebox_root=tmp_path))
    verification = VerificationService(workspace_root=tmp_path, services=services)

    def fake_capture(output_dir: Path) -> list[dict[str, str]]:
        paths = []
        for name in ("workspace-thoughtglass-home.png", "workspace-memory-chamber.png"):
            target = output_dir / name
            target.write_bytes(b"fake-image")
            paths.append({"name": name, "caption": name, "path": str(target)})
        return paths

    verification._capture_workspace_release_shots = fake_capture  # type: ignore[method-assign]
    report = verification.shotset()

    manifest_path = Path(report["manifest_path"])

    assert report["status"] == "passed"
    assert len(report["captures"]) == 2
    assert manifest_path.exists()
    assert "workspace-thoughtglass-home.png" in manifest_path.read_text(encoding="utf-8")


def test_hud_routes_expose_integrations_and_redamon(tmp_path: Path, monkeypatch) -> None:
    _seed_tacklebox(tmp_path)
    monkeypatch.setenv("BLACKFONG_TACKLEBOX_ROOT", str(tmp_path))
    dashboard = WebDashboard(host="127.0.0.1", port=8098)
    client = dashboard.app.test_client()

    integrations = client.get("/api/integrations")
    redamon = client.get("/api/redamon/status")
    verification = client.get("/api/verify/integrations")

    assert integrations.status_code == 200
    assert redamon.status_code == 200
    assert verification.status_code == 200
    assert verification.get_json()["mode"] == "integrations"
