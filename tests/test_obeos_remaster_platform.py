from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from blackfong_ecosystem.cli import cli
from blackfong_ecosystem.conversation_memory import ConversationMemoryService
from blackfong_ecosystem.docker_stack import DockerStackService
from blackfong_ecosystem.obeos_linux import (
    BOOT_HOME_OPTIONS,
    BOOTSTRAP_STAGE_ACTIVATE_HOME,
    BOOTSTRAP_STAGE_SAFE,
    OBEOSLinuxPlatformService,
)
from blackfong_ecosystem.operational_interfaces import WebDashboard
from blackfong_ecosystem.storage import EventStore, NullVectorStore
from blackfong_ecosystem.ui.blackcomputer_tui import BlackcomputerTUIApp


def build_memory(tmp_path: Path) -> ConversationMemoryService:
    event_store = EventStore(tmp_path / "events.db")
    event_store.init_db()
    return ConversationMemoryService(event_store=event_store, vector_store=NullVectorStore())


def stage_runtime_workspace(root: Path) -> Path:
    package_root = root / "blackfong_ecosystem"
    hud_root = package_root / "ui" / "hud"
    pycache_root = package_root / "__pycache__"
    branding_assets = root / "assets" / "branding"
    plymouth_theme = branding_assets / "plymouth" / "blackcomputer"
    plymouth_frames = plymouth_theme / "frames"
    hud_root.mkdir(parents=True)
    pycache_root.mkdir(parents=True)
    branding_assets.mkdir(parents=True)
    plymouth_frames.mkdir(parents=True)
    (package_root / "__init__.py").write_text("__version__ = 'test'\n", encoding="utf-8")
    (package_root / "cli.py").write_text("def cli():\n    pass\n", encoding="utf-8")
    (hud_root / "dashboard.html").write_text("<main>HUD</main>\n", encoding="utf-8")
    (pycache_root / "cli.cpython-312.pyc").write_bytes(b"compiled")
    (branding_assets / "obeos-boot-screen.png").write_bytes(b"obeos boot png fixture")
    (plymouth_theme / "blackcomputer.plymouth").write_text(
        "[Plymouth Theme]\n"
        "Name=Blackcomputer\n"
        "ModuleName=script\n\n"
        "[script]\n"
        "ImageDir=/usr/share/plymouth/themes/blackcomputer\n"
        "ScriptFile=/usr/share/plymouth/themes/blackcomputer/blackcomputer.script\n",
        encoding="utf-8",
    )
    (plymouth_theme / "blackcomputer.script").write_text("frame = Image(\"frames/blackcomputer_000.png\");\n", encoding="utf-8")
    (plymouth_theme / "background.png").write_bytes(b"background png fixture")
    (plymouth_frames / "blackcomputer_000.png").write_bytes(b"frame png fixture")
    (root / "pyproject.toml").write_text(
        "[project]\nname='blackfong_ecosystem'\n[project.scripts]\nblackfong='blackfong_ecosystem.cli:cli'\n",
        encoding="utf-8",
    )
    (root / "desktop_shell.py").write_text("print('desktop')\n", encoding="utf-8")
    (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (root / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    (root / "requirements.docker-hud.txt").write_text("flask>=3.0.0\n", encoding="utf-8")
    return root


def test_obeos_materialize_bundle_creates_linux_boot_contract(tmp_path: Path):
    workspace = stage_runtime_workspace(tmp_path / "workspace")
    service = OBEOSLinuxPlatformService(workspace_root=workspace)

    result = service.materialize_deployment_bundle(tmp_path / "bundle")

    manifest_path = tmp_path / "bundle" / "etc" / "obeos" / "manifest.json"
    bootstrap_stage_path = tmp_path / "bundle" / "etc" / "obeos" / "bootstrap_stage.json"
    service_map_path = tmp_path / "bundle" / "etc" / "obeos" / "service_dependency_map.json"
    degraded_path = tmp_path / "bundle" / "etc" / "obeos" / "degraded_boot_fallback.md"
    bundle_readme_path = tmp_path / "bundle" / "README.md"
    checklist_path = tmp_path / "bundle" / "etc" / "obeos" / "vm_checklist.md"
    snapshot_path = tmp_path / "bundle" / "etc" / "obeos" / "vm_acceptance_snapshot.json"
    service_path = tmp_path / "bundle" / "etc" / "systemd" / "system" / "obeos-tui.service"
    bootstrap_path = tmp_path / "bundle" / "opt" / "obeos" / "bin" / "bootstrap-kubuntu-guest.sh"
    launcher_path = tmp_path / "bundle" / "opt" / "obeos" / "bin" / "launch-blackcomputer-tui.sh"
    session_authority_path = tmp_path / "bundle" / "etc" / "obeos" / "session_authority.md"
    requirements_path = tmp_path / "bundle" / "opt" / "blackfong" / "requirements-obeos-linux.txt"
    packaged_cli = tmp_path / "bundle" / "opt" / "blackfong" / "blackfong_ecosystem" / "cli.py"
    packaged_hud = tmp_path / "bundle" / "opt" / "blackfong" / "blackfong_ecosystem" / "ui" / "hud" / "dashboard.html"
    desktop_file = tmp_path / "bundle" / "usr" / "share" / "applications" / "obeos-return-to-tui.desktop"
    remaster_script = tmp_path / "bundle" / "remaster" / "build-kubuntu-remaster.sh"
    branding_profile = tmp_path / "bundle" / "opt" / "obeos" / "share" / "branding" / "profile.json"
    branding_boot_screen = tmp_path / "bundle" / "opt" / "obeos" / "share" / "branding" / "obeos-boot-screen.png"
    branding_splash = tmp_path / "bundle" / "opt" / "obeos" / "share" / "branding" / "obeos-splash.svg"
    branding_wallpaper = tmp_path / "bundle" / "opt" / "obeos" / "share" / "branding" / "obeos-wallpaper.svg"
    blackcomputer_plymouth = tmp_path / "bundle" / "opt" / "obeos" / "share" / "branding" / "plymouth" / "blackcomputer"
    branding_script = tmp_path / "bundle" / "opt" / "obeos" / "bin" / "apply-obeos-branding.sh"
    grub_theme = tmp_path / "bundle" / "opt" / "obeos" / "share" / "branding" / "grub" / "theme.txt"
    sddm_theme = tmp_path / "bundle" / "opt" / "obeos" / "share" / "branding" / "sddm" / "theme.conf"
    sddm_qml = tmp_path / "bundle" / "opt" / "obeos" / "share" / "branding" / "sddm" / "Main.qml"
    plasma_lock_screen = tmp_path / "bundle" / "opt" / "obeos" / "share" / "branding" / "plasma" / "kscreenlockerrc"
    plasma_colors = tmp_path / "bundle" / "opt" / "obeos" / "share" / "branding" / "plasma" / "OBEOS.colors"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bootstrap_stage = json.loads(bootstrap_stage_path.read_text(encoding="utf-8"))
    service_map = json.loads(service_map_path.read_text(encoding="utf-8"))
    acceptance_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert result["status"] == "ok"
    assert manifest["base_distribution"] == "Kubuntu 24.04 LTS"
    assert manifest["shell_rule"] == "OBEOS must remain Ubuntu-compatible at the shell level."
    assert manifest["session_authority_rule"] == "OBEOS owns the session. Plasma is a managed workspace."
    assert manifest["packaging_rule"] == "Packaging follows stability."
    assert manifest["branding_profile"]["identity"] == "O.B.E.O.S."
    assert manifest["branding_profile"]["assistant"] == "D.A.I.S.E."
    assert manifest["branding_profile"]["boot_screen_asset"] == "/opt/obeos/share/branding/obeos-boot-screen.png"
    assert manifest["boot_profile"]["default_target"] == "multi-user.target"
    assert manifest["bootstrap_contract"]["default_stage"] == BOOTSTRAP_STAGE_SAFE
    assert manifest["bootstrap_contract"]["activation_stage"] == BOOTSTRAP_STAGE_ACTIVATE_HOME
    assert manifest["boot_profile"]["home_options"] == list(BOOT_HOME_OPTIONS)
    assert manifest["manifest_hash"] == result["manifest_hash"]
    assert service_map["required_order"] == ["event-store", "awareness", "daise", "hud", "desktop-contract"]
    assert acceptance_snapshot["status"] == "pending"
    assert acceptance_snapshot["bootstrap_contract"]["activation_command"] == "pkexec /usr/local/bin/blackfong obeos activate-home"
    assert bootstrap_stage["stage"] == BOOTSTRAP_STAGE_SAFE
    assert bootstrap_stage["default_target"] == "graphical.target"
    assert bootstrap_stage["home_boot_enabled"] is False
    assert "manifest_hash" in acceptance_snapshot
    assert service_path.exists()
    assert "ExecStart=/opt/obeos/bin/launch-blackcomputer-tui.sh" in service_path.read_text(encoding="utf-8")
    assert "Description=O.B.E.O.S. Home (Blackcomputer TUI)" in service_path.read_text(encoding="utf-8")
    assert "Conflicts=getty@tty1.service" in service_path.read_text(encoding="utf-8")
    assert "Environment=BLACKFONG_TUI_BIN=/opt/blackfong/.venv/bin/blackfong" in service_path.read_text(encoding="utf-8")
    assert bootstrap_path.exists()
    assert b"\r\n" not in bootstrap_path.read_bytes()
    bootstrap_text = bootstrap_path.read_text(encoding="utf-8")
    assert "python3-venv" in bootstrap_text
    assert "python3 -m venv /opt/blackfong/.venv" in bootstrap_text
    assert "pip install --no-deps -e /opt/blackfong" in bootstrap_text
    assert "/etc/sudoers.d/obeos-systemctl" in bootstrap_text
    assert "chown -R obeos:obeos /opt/blackfong /opt/daise /var/lib/obeos /etc/obeos" in bootstrap_text
    assert "systemctl set-default graphical.target" in bootstrap_text
    assert "systemctl disable obeos-tui.service" in bootstrap_text
    assert "--activate-home" in bootstrap_text
    assert "pkexec /usr/local/bin/blackfong obeos activate-home" in bootstrap_text
    assert "blackfong desktop workspace" in bootstrap_text
    assert "blackfong tui" in bootstrap_text
    assert launcher_path.exists()
    assert b"\r\n" not in launcher_path.read_bytes()
    launcher_text = launcher_path.read_text(encoding="utf-8")
    assert "/opt/blackfong/.venv/bin/blackfong" in launcher_text
    assert "[O.B.E.O.S. DEGRADED MODE]" in launcher_text
    assert "O.B.E.O.S. Home (Blackcomputer TUI) could not start." in launcher_text
    assert "exec bash -l" in launcher_text
    assert requirements_path.exists()
    requirements_text = requirements_path.read_text(encoding="utf-8")
    assert "PySide6>=6.7.0,<7.0.0" in requirements_text
    assert "textual>=0.28.0,<0.31.0" in requirements_text
    assert "chromadb" not in requirements_text
    assert (tmp_path / "bundle" / "opt" / "blackfong" / "pyproject.toml").exists()
    assert packaged_cli.exists()
    assert packaged_hud.exists()
    assert not (tmp_path / "bundle" / "opt" / "blackfong" / "blackfong_ecosystem" / "__pycache__").exists()
    assert result["runtime_payload"]
    assert bundle_readme_path.exists()
    bundle_readme_text = bundle_readme_path.read_text(encoding="utf-8")
    assert "O.B.E.O.S. Kubuntu Remaster Bundle" in bundle_readme_text
    assert "Thoughtglass HUD" in bundle_readme_text
    assert "Blackfong Workspace" in bundle_readme_text
    assert checklist_path.exists()
    checklist_text = checklist_path.read_text(encoding="utf-8")
    assert "OBEOS Kubuntu VM-First Checklist" in checklist_text
    assert "Start Thoughtglass HUD from Plasma" in checklist_text
    assert "Launch Blackfong Workspace" in checklist_text
    assert degraded_path.exists()
    assert "fall back to a standard shell login" in degraded_path.read_text(encoding="utf-8")
    assert session_authority_path.exists()
    session_authority_text = session_authority_path.read_text(encoding="utf-8")
    assert "Thoughtglass HUD is the supported browser-based secondary cockpit." in session_authority_text
    assert "Blackfong Workspace is the canonical native local shell" in session_authority_text
    assert desktop_file.exists()
    assert "Return to O.B.E.O.S. Home" in desktop_file.read_text(encoding="utf-8")
    assert remaster_script.exists()
    assert b"\r\n" not in remaster_script.read_bytes()
    assert "xorriso -osirrox on" in remaster_script.read_text(encoding="utf-8")
    remaster_text = remaster_script.read_text(encoding="utf-8")
    assert "apply-obeos-branding.sh" in remaster_text
    assert "obeos-24.04.iso" in remaster_text
    assert branding_profile.exists()
    assert branding_boot_screen.exists()
    assert branding_boot_screen.read_bytes() == b"obeos boot png fixture"
    assert branding_splash.exists()
    assert branding_wallpaper.exists()
    assert (blackcomputer_plymouth / "blackcomputer.plymouth").exists()
    assert (blackcomputer_plymouth / "blackcomputer.script").exists()
    assert (blackcomputer_plymouth / "background.png").exists()
    assert (blackcomputer_plymouth / "frames" / "blackcomputer_000.png").exists()
    assert branding_script.exists()
    assert grub_theme.exists()
    assert "O.B.E.O.S." in grub_theme.read_text(encoding="utf-8")
    assert sddm_theme.exists()
    assert "background=background.png" in sddm_theme.read_text(encoding="utf-8")
    sddm_qml_text = sddm_qml.read_text(encoding="utf-8")
    assert 'source: "background.png"' in sddm_qml_text
    assert "Image.PreserveAspectFit" in sddm_qml_text
    assert 'source = "background.svg"' in sddm_qml_text
    assert plasma_lock_screen.exists()
    assert "Image=/usr/share/wallpapers/O.B.E.O.S/contents/images/3840x2160.png" in plasma_lock_screen.read_text(encoding="utf-8")
    assert plasma_colors.exists()
    assert "ColorScheme=OBEOS" in plasma_colors.read_text(encoding="utf-8")
    branding_script_text = branding_script.read_text(encoding="utf-8")
    assert "GRUB_THEME_DIR" in branding_script_text
    assert "SDDM_THEME_DIR" in branding_script_text
    assert 'BLACKCOMPUTER_PLYMOUTH_SOURCE="$BRANDING_DIR/plymouth/blackcomputer"' in branding_script_text
    assert 'BLACKCOMPUTER_PLYMOUTH_THEME_DIR="/usr/share/plymouth/themes/blackcomputer"' in branding_script_text
    assert 'ACTIVE_PLYMOUTH_FILE="$BLACKCOMPUTER_PLYMOUTH_THEME_DIR/blackcomputer.plymouth"' in branding_script_text
    assert 'update-alternatives --set default.plymouth "$ACTIVE_PLYMOUTH_FILE"' in branding_script_text
    assert 'plymouth-set-default-theme "$ACTIVE_PLYMOUTH_NAME"' in branding_script_text
    assert 'BOOT_SCREEN_ASSET="$BRANDING_DIR/obeos-boot-screen.png"' in branding_script_text
    assert 'cp "$BOOT_SCREEN_ASSET" "$PLYMOUTH_THEME_DIR/splash.png"' in branding_script_text
    assert 'cp "$BOOT_SCREEN_ASSET" "$SDDM_THEME_DIR/background.png"' in branding_script_text
    assert 'cp "$BOOT_SCREEN_ASSET" "$WALLPAPER_PNG_TARGET"' in branding_script_text
    assert 'ACTIVE_WALLPAPER_TARGET="$WALLPAPER_PNG_TARGET"' in branding_script_text
    assert 'rm -f "$PLYMOUTH_THEME_DIR/splash.png" "$SDDM_THEME_DIR/background.png" "$WALLPAPER_PNG_TARGET"' in branding_script_text
    assert 'logo.image = Image("$PLYMOUTH_IMAGE");' in branding_script_text
    assert "plasma-apply-colorscheme" in branding_script_text


def test_obeos_vm_acceptance_snapshot_captures_blocked_prerequisites(tmp_path: Path):
    service = OBEOSLinuxPlatformService(workspace_root=tmp_path)
    output = tmp_path / "vm_acceptance_snapshot.json"

    snapshot = service.vm_acceptance_snapshot(output_path=output)

    assert output.exists()
    assert snapshot["manifest_hash"]
    assert snapshot["vm_profile"]["architecture"] == "amd64"
    assert snapshot["status"] in {"accepted", "blocked"}
    assert snapshot["acceptance_checks"]
    assert "host_probe" in snapshot
    assert "guest_probe" in snapshot
    if snapshot["status"] == "blocked":
        assert snapshot["missing_prerequisites"]
        assert snapshot["next_actions"]


def test_vm_acceptance_requires_tui_boot_home_contract(tmp_path: Path):
    service = OBEOSLinuxPlatformService(workspace_root=tmp_path)
    guest_probe = {
        "is_kubuntu": True,
        "os_release": {"PRETTY_NAME": "Kubuntu 24.04 LTS"},
        "default_target": {"ok": True, "output": "multi-user.target"},
        "obeos_tui_unit": {"ok": True, "output": "[Unit]\nDescription=O.B.E.O.S. Home (Blackcomputer TUI)"},
        "obeos_tui_enabled": {"ok": True, "output": "enabled"},
        "obeos_tui_active": {"ok": False, "output": "inactive"},
        "workspace_shell_available": True,
        "desktop_status": {
            "command_contract": {
                "home": "systemctl isolate multi-user.target",
                "stop": "systemctl isolate multi-user.target",
            },
            "workspace_shell": {
                "script_path": str(tmp_path / "desktop_shell.py"),
                "available": True,
            }
        },
    }

    checks = service._vm_acceptance_checks(guest_probe=guest_probe, missing_prerequisites=[])

    tui_check = next(check for check in checks if check["id"] == "obeos_tui_service")
    assert tui_check["status"] == "passed"


def test_vm_acceptance_blocks_until_activate_home_stage_is_run(tmp_path: Path):
    service = OBEOSLinuxPlatformService(workspace_root=tmp_path)
    guest_probe = {
        "is_kubuntu": True,
        "os_release": {"PRETTY_NAME": "Kubuntu 24.04 LTS"},
        "default_target": {"ok": True, "output": "graphical.target"},
        "obeos_tui_unit": {"ok": True, "output": "[Unit]\nDescription=O.B.E.O.S. Home (Blackcomputer TUI)"},
        "obeos_tui_enabled": {"ok": True, "output": "disabled"},
        "obeos_tui_active": {"ok": True, "output": "inactive"},
        "bootstrap_stage": {
            "stage": BOOTSTRAP_STAGE_SAFE,
            "default_target": "graphical.target",
            "home_boot_enabled": False,
            "activation_command": "pkexec /usr/local/bin/blackfong obeos activate-home",
        },
        "workspace_shell_available": True,
        "desktop_status": {
            "command_contract": {
                "home": "systemctl isolate multi-user.target",
                "stop": "systemctl isolate multi-user.target",
                "activate_home": "pkexec /usr/local/bin/blackfong obeos activate-home",
            },
            "workspace_shell": {
                "script_path": str(tmp_path / "desktop_shell.py"),
                "available": True,
            },
        },
    }

    checks = service._vm_acceptance_checks(guest_probe=guest_probe, missing_prerequisites=[])

    tui_check = next(check for check in checks if check["id"] == "obeos_tui_service")
    assert tui_check["status"] == "blocked"
    assert tui_check["blocked_by"] == "activate_home_pending"

    actions = service._vm_acceptance_next_actions([], checks=checks)
    assert any("blackfong obeos activate-home" in action for action in actions)


def test_cli_exposes_desktop_and_obeos_contract_commands(tmp_path: Path, monkeypatch):
    # `verify release-evidence` and `verify shotset` write reports under the workspace root.
    # Point them at a temp tree so the test never rewrites tracked docs in the repo.
    from blackfong_ecosystem import verification as verification_module
    from blackfong_ecosystem.config import Paths

    from blackfong_ecosystem import cli as cli_module

    temp_paths = Paths.from_root(tmp_path)
    monkeypatch.setattr(verification_module, "paths", temp_paths)
    monkeypatch.setattr(cli_module, "paths", temp_paths)
    runner = CliRunner()

    installer_status = runner.invoke(cli, ["install-plan", "status"])
    manifest = runner.invoke(cli, ["obeos", "manifest"])
    shell_compat = runner.invoke(cli, ["obeos", "shell-compat"])
    vm_checklist = runner.invoke(cli, ["obeos", "vm-checklist"])
    vm_snapshot = runner.invoke(cli, ["obeos", "vm-acceptance-snapshot"])
    activate_home = runner.invoke(cli, ["obeos", "activate-home", "--dry-run"])
    desktop = runner.invoke(cli, ["desktop", "status"])
    workspace = runner.invoke(cli, ["desktop", "workspace", "--dry-run"])
    home = runner.invoke(cli, ["home", "--dry-run"])
    docker_status = runner.invoke(cli, ["docker", "status"])
    docker_up = runner.invoke(cli, ["docker", "up", "--dry-run"])
    docker_logs = runner.invoke(cli, ["docker", "logs", "--dry-run"])
    verify_bundle = runner.invoke(cli, ["verify", "bundle"])
    verify_evidence = runner.invoke(cli, ["verify", "evidence", "--profile", "amd64-pc"])
    verify_shotset = runner.invoke(
        cli,
        ["verify", "shotset", "--output-dir", str(Path.cwd() / "data" / "test-shotset")],
        env={"BLACKFONG_SHOTSET_PLATFORM": "offscreen"},
    )

    assert installer_status.exit_code == 0
    assert '"state": "migration_input_only"' in installer_status.output
    assert manifest.exit_code == 0
    assert '"base_distribution": "Kubuntu 24.04 LTS"' in manifest.output
    assert shell_compat.exit_code == 0
    assert '"rule": "OBEOS must remain Ubuntu-compatible at the shell level."' in shell_compat.output
    assert vm_checklist.exit_code == 0
    assert "OBEOS Kubuntu VM-First Checklist" in vm_checklist.output
    assert vm_snapshot.exit_code == 0
    assert '"acceptance_checks"' in vm_snapshot.output
    assert activate_home.exit_code == 0
    assert '"command_name": "activate-home"' in activate_home.output
    assert desktop.exit_code == 0
    assert '"command_contract"' in desktop.output
    assert workspace.exit_code == 0
    assert '"action": "workspace"' in workspace.output
    assert home.exit_code == 0
    assert '"action": "home"' in home.output
    assert '"home_surface": "blackcomputer_tui"' in home.output
    assert docker_status.exit_code == 0
    assert '"stack_rule": "Docker services are workers, not owners."' in docker_status.output
    assert docker_up.exit_code == 0
    assert '"status": "planned"' in docker_up.output
    assert docker_logs.exit_code == 0
    assert '"action": "logs"' in docker_logs.output
    assert verify_bundle.exit_code == 0
    assert '"mode": "bundle"' in verify_bundle.output
    assert verify_evidence.exit_code == 0
    assert '"mode": "release_evidence"' in verify_evidence.output
    assert verify_shotset.exit_code == 0
    assert '"mode": "shotset"' in verify_shotset.output


def test_tui_boot_home_contract_and_commands_are_available():
    app = BlackcomputerTUIApp()

    installer_status = app._execute_slash_command("/obeos installer-status")
    desktop = app._execute_slash_command("/desktop status")
    home = app._execute_slash_command("/home")
    manifest = app._execute_slash_command("/obeos manifest")
    vm_checklist = app._execute_slash_command("/obeos vm-checklist")
    vm_snapshot = app._execute_slash_command("/obeos vm-acceptance-snapshot")
    activate_home = app._execute_slash_command("/obeos activate-home")
    docker = app._execute_slash_command("/docker status")

    assert app.BOOT_HOME_OPTIONS == BOOT_HOME_OPTIONS
    assert app.ASCII_BOOT_INTRO.strip()
    assert installer_status["installer_status"]["state"] == "migration_input_only"
    assert desktop["status"] == "ok"
    assert desktop["desktop"]["boot_profile"]["home_options"] == list(BOOT_HOME_OPTIONS)
    assert home["action"] == "home"
    assert home["surface"] == "blackcomputer_tui"
    assert manifest["status"] == "ok"
    assert manifest["obeos"]["base_distribution"] == "Kubuntu 24.04 LTS"
    assert vm_checklist["status"] == "ok"
    assert "OBEOS Kubuntu VM-First Checklist" in vm_checklist["vm_checklist"]
    assert vm_snapshot["status"] == "ok"
    assert "acceptance_checks" in vm_snapshot["vm_acceptance_snapshot"]
    assert activate_home["command_name"] == "activate-home"
    assert docker["status"] == "ok"
    assert docker["docker"]["stack_rule"] == "Docker services are workers, not owners."


def test_dashboard_command_catalog_and_execute_support_desktop_and_obeos(tmp_path: Path):
    memory = build_memory(tmp_path)
    dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=memory, orchestrator_instance=None)
    client = dashboard.app.test_client()

    commands = client.get("/api/commands").get_json()
    installer_status = client.post("/api/commands/execute", json={"command": "/obeos installer-status"}).get_json()
    desktop = client.post("/api/commands/execute", json={"command": "/desktop status"}).get_json()
    home = client.post("/api/commands/execute", json={"command": "/home"}).get_json()
    workspace = client.post("/api/commands/execute", json={"command": "/desktop workspace"}).get_json()
    docker = client.post("/api/commands/execute", json={"command": "/docker status"}).get_json()
    shell_compat = client.post("/api/commands/execute", json={"command": "/obeos shell-compat"}).get_json()
    vm_checklist = client.post("/api/commands/execute", json={"command": "/obeos vm-checklist"}).get_json()
    vm_snapshot = client.post("/api/commands/execute", json={"command": "/obeos vm-acceptance-snapshot"}).get_json()
    activate_home = client.post("/api/commands/execute", json={"command": "/obeos activate-home"}).get_json()

    assert any(command["command"] == "/obeos installer-status" for command in commands["commands"])
    assert installer_status["installer_status"]["state"] == "migration_input_only"
    assert any(command["command"] == "/desktop start" for command in commands["commands"])
    assert any(command["command"] == "/desktop workspace" for command in commands["commands"])
    assert any(command["command"] == "/docker up" for command in commands["commands"])
    assert any(command["command"] == "/home" for command in commands["commands"])
    assert any(command["command"] == "/obeos manifest" for command in commands["commands"])
    assert any(command["command"] == "/obeos vm-checklist" for command in commands["commands"])
    assert any(command["command"] == "/obeos vm-acceptance-snapshot" for command in commands["commands"])
    assert any(command["command"] == "/obeos activate-home" for command in commands["commands"])
    assert desktop["status"] == "ok"
    assert desktop["command"] == "/desktop status"
    assert "boot_profile" in desktop["desktop"]
    assert home["command"] == "/home"
    assert home["action"] == "home"
    assert home["surface"] == "blackcomputer_tui"
    assert workspace["command"] == "/desktop workspace"
    assert workspace["action"] == "workspace"
    assert docker["command"] == "/docker status"
    assert docker["docker"]["service_name"] == "blackfong-hud"
    assert shell_compat["status"] == "ok"
    assert shell_compat["command"] == "/obeos shell-compat"
    assert shell_compat["shell_compatibility"]["rule"] == "OBEOS must remain Ubuntu-compatible at the shell level."
    assert vm_checklist["status"] == "ok"
    assert "OBEOS Kubuntu VM-First Checklist" in vm_checklist["vm_checklist"]
    assert vm_snapshot["status"] == "ok"
    assert "acceptance_checks" in vm_snapshot["vm_acceptance_snapshot"]
    assert activate_home["command"] == "/obeos activate-home"


def test_workspace_launch_contract_is_managed_and_dry_runnable(tmp_path: Path):
    script = tmp_path / "desktop_shell.py"
    script.write_text("print('workspace')\n", encoding="utf-8")
    service = OBEOSLinuxPlatformService(workspace_root=tmp_path)

    result = service.launch_workspace(execute=False)

    assert result["action"] == "workspace"
    assert result["status"] == "planned"
    assert result["managed_surface_rule"] == "PySide6 is a managed workspace surface, not a second authority."
    assert "desktop_shell.py" in result["command_line"][1]


def test_docker_stack_service_plans_commands_from_workspace_root(tmp_path: Path):
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    service = DockerStackService(workspace_root=tmp_path)

    up = service.up(execute=False, build=True)
    down = service.down(execute=False)
    logs = service.logs(execute=False, tail=25)

    assert up["status"] == "planned"
    assert "--build" in up["command_line"]
    assert down["action"] == "down"
    assert logs["action"] == "logs"
    assert logs["command_line"][-2:] == ["25", "blackfong-hud"]
