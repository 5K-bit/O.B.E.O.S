from __future__ import annotations

from pathlib import Path

from blackfong_ecosystem.installer_plan import InstallerPlanService


def build_installer_root(root: Path) -> Path:
    manifests = root / "manifests"
    profiles = manifests / "profiles"
    profiles.mkdir(parents=True)
    (manifests / "base.yaml").write_text("packages:\n  - systemd\n", encoding="utf-8")
    (manifests / "features.yaml").write_text("feature_groups:\n  obeos_core: {}\n", encoding="utf-8")
    (profiles / "amd64-pc.yaml").write_text("id: amd64-pc\narch: amd64\n", encoding="utf-8")
    return root


def test_build_plan_includes_current_obeos_doctrine_notes(tmp_path: Path):
    root = build_installer_root(tmp_path / "installer")
    plan = InstallerPlanService(installer_root=root).build_plan("amd64-pc")

    assert any("Kubuntu 24.04 LTS" in note for note in plan.notes)
    assert any("migration/build input" in note for note in plan.notes)
    assert any("D.A.I.S.E." in note for note in plan.notes)
    assert plan.bundle_display_names["jarvis_agent"] == "D.A.I.S.E Compatibility Assistant Path"
    assert plan.installer_status["state"] == "migration_input_only"
    assert plan.installer_status["shipping_ready"] is False
    assert plan.installer_status["recommended_v1_path"] == "kubuntu_materialize_remaster"


def test_validate_plan_accepts_minimal_manifest_layout(tmp_path: Path):
    root = build_installer_root(tmp_path / "installer")
    service = InstallerPlanService(installer_root=root)

    plan = service.build_plan("amd64-pc")
    validation = service.validate_plan(plan)

    assert validation["ok"] is True
    assert all(check["ok"] for check in validation["checks"])
    assert validation["missing_bundles"] == []
    assert validation["plan"]["bundle_display_names"]["obeos_core"] == "O.B.E.O.S Core"
    assert validation["installer_status"]["state"] == "migration_input_only"


def test_current_status_reports_installer_as_migration_input(tmp_path: Path):
    root = tmp_path / "missing-installer-root"
    service = InstallerPlanService(installer_root=root)

    status = service.current_status()

    assert status["state"] == "migration_input_only"
    assert status["shipping_ready"] is False
    assert status["installer_root_exists"] is False
    assert "Kubuntu 24.04 LTS + KDE Plasma" in status["promotion_gate"]
