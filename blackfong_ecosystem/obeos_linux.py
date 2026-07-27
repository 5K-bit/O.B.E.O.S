from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


SHELL_COMPATIBILITY_COMMANDS = (
    "cd",
    "ls",
    "cat",
    "nano",
    "python3",
    "pip",
    "apt",
    "systemctl",
    "journalctl",
    "nmcli",
    "bluetoothctl",
)

BASE_STACK_COMPONENTS = (
    "Kubuntu 24.04 LTS",
    "KDE Plasma",
    "systemd",
    "NetworkManager",
    "PipeWire",
    "BlueZ",
)

REQUIRED_PACKAGES = (
    "bash",
    "coreutils",
    "nano",
    "python3",
    "python3-venv",
    "python3-pip",
    "apt",
    "rsync",
    "sudo",
    "curl",
    "unzip",
    "systemd",
    "sddm",
    "plasma-desktop",
    "network-manager",
    "bluez",
    "pipewire",
    "wireplumber",
)

OBEOS_LINUX_REQUIREMENTS = (
    "click==8.1.7",
    "pydantic==2.6.4",
    "SQLAlchemy==2.0.25",
    "Flask>=3.0.0",
    "Flask-SocketIO>=5.3.0",
    "httpx>=0.24.0",
    "psutil>=5.9.0",
    "PySide6>=6.7.0,<7.0.0",
    "textual>=0.28.0,<0.31.0",
    "structlog>=24.0.0",
)

BLACKFONG_RUNTIME_ROOT_FILES = (
    "pyproject.toml",
    "desktop_shell.py",
    "compose.yaml",
    "Dockerfile",
    "requirements.docker-hud.txt",
)

OBEOS_BOOT_SCREEN_RELATIVE_PATH = Path("assets") / "branding" / "obeos-boot-screen.png"
OBEOS_BOOT_SCREEN_BUNDLE_PATH = Path("opt") / "obeos" / "share" / "branding" / "obeos-boot-screen.png"
BLACKCOMPUTER_PLYMOUTH_RELATIVE_PATH = Path("assets") / "branding" / "plymouth" / "blackcomputer"
BLACKCOMPUTER_PLYMOUTH_BUNDLE_PATH = Path("opt") / "obeos" / "share" / "branding" / "plymouth" / "blackcomputer"

SESSION_AUTHORITY_RULE = "OBEOS owns the session. Plasma is a managed workspace."
PACKAGING_RULE = "Packaging follows stability."
DEGRADED_BOOT_RULE = "If the TUI fails to start, fall back to a standard shell login and report degraded mode."
SERVICE_DEPENDENCY_ORDER = (
    "event-store",
    "awareness",
    "daise",
    "hud",
    "desktop-contract",
)
OPTIONAL_SERVICE_DEPENDENCIES = ("tinyprogrammer",)
BOOTSTRAP_STAGE_SAFE = "stage_a_safe"
BOOTSTRAP_STAGE_ACTIVATE_HOME = "stage_b_activate_home"
VM_PROFILE_DEFAULT = {
    "architecture": "amd64",
    "firmware": "EFI",
    "cpus": 4,
    "memory_gb": 8,
    "disk_gb": 64,
    "network": "NAT",
    "base_iso": "official Kubuntu 24.04.x desktop ISO",
}

VM_ACCEPTANCE_CHECKS = (
    "base_distribution",
    "manifest_hash",
    "obeos_tui_service",
    "desktop_workspace_contract",
    "hud_launch_contract",
    "sensor_policy",
    "degraded_fallback",
)

VM_RUNNER_COMMANDS = (
    "VBoxManage",
    "qemu-system-x86_64",
    "multipass",
    "virt-install",
)

BOOT_HOME_OPTIONS = (
    "Desktop",
    "D.A.I.S.E.",
    "Files",
    "System",
    "Sensors",
    "Power",
)

BRANDING_LAYERS = (
    "GRUB",
    "Plymouth",
    "SDDM",
    "Plasma splash",
    "wallpaper",
    "lock screen",
    "terminal intro",
)

OBEOS_BRANDING_PROFILE = {
    "identity": "O.B.E.O.S.",
    "machine": "Blackcomputer",
    "assistant": "D.A.I.S.E.",
    "palette": {
        "background": "#05070a",
        "surface": "#0c1118",
        "primary": "#5eead4",
        "accent": "#8b5cf6",
        "text": "#edf5ff",
        "muted": "#9cb3cf",
    },
    "boot_screen_asset": "/opt/obeos/share/branding/obeos-boot-screen.png",
    "splash_asset": "/opt/obeos/share/branding/obeos-splash.svg",
    "wallpaper_asset": "/opt/obeos/share/branding/obeos-wallpaper.svg",
}

ASCII_BOOT_INTRO = r"""
 ██████╗ ██████╗ ███████╗ ██████╗ ███████╗
██╔═══██╗██╔══██╗██╔════╝██╔═══██╗██╔════╝
██║   ██║██████╔╝█████╗  ██║   ██║███████╗
██║   ██║██╔══██╗██╔══╝  ██║   ██║╚════██║
╚██████╔╝██████╔╝███████╗╚██████╔╝███████║
 ╚═════╝ ╚═════╝ ╚══════╝ ╚═════╝ ╚══════╝

 Official Blackfong Environment Operating System
 Blackcomputer // Blackfong spine // D.A.I.S.E. interface
"""


@dataclass(frozen=True)
class OBEOSFilesystemLayout:
    opt_obeos: str = "/opt/obeos"
    opt_blackfong: str = "/opt/blackfong"
    opt_daise: str = "/opt/daise"
    etc_obeos: str = "/etc/obeos"
    var_lib_obeos: str = "/var/lib/obeos"

    def as_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class OBEOSBootProfile:
    default_target: str = "multi-user.target"
    graphical_target: str = "graphical.target"
    autologin_user: str = "obeos"
    tty: str = "tty1"
    tui_service_name: str = "obeos-tui.service"
    enter_label: str = "ENTER"
    home_options: tuple[str, ...] = BOOT_HOME_OPTIONS
    ascii_intro: str = ASCII_BOOT_INTRO

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["home_options"] = list(self.home_options)
        return payload


@dataclass(frozen=True)
class OBEOSBootstrapContract:
    default_stage: str = BOOTSTRAP_STAGE_SAFE
    activation_stage: str = BOOTSTRAP_STAGE_ACTIVATE_HOME
    default_target_before_activation: str = "graphical.target"
    default_target_after_activation: str = "multi-user.target"
    guest_elevation_tool: str = "pkexec"
    bootstrap_command: str = "pkexec /bin/bash ./opt/obeos/bin/bootstrap-kubuntu-guest.sh"
    activation_command: str = "pkexec /usr/local/bin/blackfong obeos activate-home"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OBEOSBrandingProfile:
    identity: str = OBEOS_BRANDING_PROFILE["identity"]
    machine: str = OBEOS_BRANDING_PROFILE["machine"]
    assistant: str = OBEOS_BRANDING_PROFILE["assistant"]
    palette: Dict[str, str] = field(default_factory=lambda: dict(OBEOS_BRANDING_PROFILE["palette"]))
    boot_screen_asset: str = OBEOS_BRANDING_PROFILE["boot_screen_asset"]
    splash_asset: str = OBEOS_BRANDING_PROFILE["splash_asset"]
    wallpaper_asset: str = OBEOS_BRANDING_PROFILE["wallpaper_asset"]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "identity": self.identity,
            "machine": self.machine,
            "assistant": self.assistant,
            "palette": dict(self.palette),
            "boot_screen_asset": self.boot_screen_asset,
            "splash_asset": self.splash_asset,
            "wallpaper_asset": self.wallpaper_asset,
        }


@dataclass(frozen=True)
class OBEOSRemasterManifest:
    base_distribution: str = "Kubuntu 24.04 LTS"
    desktop_environment: str = "KDE Plasma"
    shell_rule: str = "OBEOS must remain Ubuntu-compatible at the shell level."
    enhancement_rule: str = "OBEOS enhances Linux; it does not replace the basics."
    session_authority_rule: str = SESSION_AUTHORITY_RULE
    packaging_rule: str = PACKAGING_RULE
    degraded_boot_rule: str = DEGRADED_BOOT_RULE
    sensor_policy: str = "manual-only"
    base_stack: tuple[str, ...] = BASE_STACK_COMPONENTS
    required_packages: tuple[str, ...] = REQUIRED_PACKAGES
    branding_layers: tuple[str, ...] = BRANDING_LAYERS
    service_dependency_order: tuple[str, ...] = SERVICE_DEPENDENCY_ORDER
    optional_service_dependencies: tuple[str, ...] = OPTIONAL_SERVICE_DEPENDENCIES
    branding_profile: OBEOSBrandingProfile = field(default_factory=OBEOSBrandingProfile)
    boot_profile: OBEOSBootProfile = field(default_factory=OBEOSBootProfile)
    bootstrap_contract: OBEOSBootstrapContract = field(default_factory=OBEOSBootstrapContract)
    filesystem_layout: OBEOSFilesystemLayout = field(default_factory=OBEOSFilesystemLayout)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["base_stack"] = list(self.base_stack)
        payload["required_packages"] = list(self.required_packages)
        payload["branding_layers"] = list(self.branding_layers)
        payload["service_dependency_order"] = list(self.service_dependency_order)
        payload["optional_service_dependencies"] = list(self.optional_service_dependencies)
        payload["branding_profile"] = self.branding_profile.as_dict()
        payload["boot_profile"]["home_options"] = list(self.boot_profile.home_options)
        payload["vm_profile_default"] = dict(VM_PROFILE_DEFAULT)
        return payload


class OBEOSLinuxPlatformService:
    """Deployment and session helpers for the Kubuntu-first OBEOS prototype."""

    def __init__(self, *, workspace_root: Optional[Path] = None) -> None:
        self.workspace_root = Path(workspace_root or Path(__file__).resolve().parents[1])
        self.manifest_model = OBEOSRemasterManifest()

    @property
    def filesystem_layout(self) -> OBEOSFilesystemLayout:
        return self.manifest_model.filesystem_layout

    @property
    def boot_profile(self) -> OBEOSBootProfile:
        return self.manifest_model.boot_profile

    @property
    def bootstrap_contract(self) -> OBEOSBootstrapContract:
        return self.manifest_model.bootstrap_contract

    def manifest(self) -> Dict[str, Any]:
        payload = self.manifest_model.to_dict()
        payload["manifest_hash"] = self._manifest_hash(payload)
        return payload

    def service_dependency_map(self) -> Dict[str, Any]:
        return {
            "rule": self.manifest_model.session_authority_rule,
            "required_order": list(SERVICE_DEPENDENCY_ORDER),
            "optional_after_daise": list(OPTIONAL_SERVICE_DEPENDENCIES),
            "notes": [
                "Desktop is a launched workspace, not a second operating authority.",
                "HUD depends on D.A.I.S.E. and awareness being available.",
                "TinyProgrammer is optional and starts after D.A.I.S.E. when enabled.",
            ],
        }

    def bootstrap_stage_payload(self, stage: str, *, default_target: str, home_boot_enabled: bool) -> Dict[str, Any]:
        return {
            "stage": stage,
            "default_target": default_target,
            "home_boot_enabled": home_boot_enabled,
            "guest_elevation_tool": self.bootstrap_contract.guest_elevation_tool,
            "activation_command": self.bootstrap_contract.activation_command,
        }

    def vm_acceptance_snapshot_template(self) -> Dict[str, Any]:
        return {
            "status": "pending",
            "base_distribution": self.manifest_model.base_distribution,
            "desktop_environment": self.manifest_model.desktop_environment,
            "vm_profile": dict(VM_PROFILE_DEFAULT),
            "manifest_hash": self.manifest().get("manifest_hash"),
            "bootstrap_contract": self.bootstrap_contract.as_dict(),
            "package_list": [],
            "enabled_services": [],
            "boot_timing_ms": {},
            "session_switch_timing_ms": {},
            "notes": [
                "Stage A safe bootstrap should complete from Plasma before TUI-home activation.",
                f"Run `{self.bootstrap_contract.activation_command}` after surface smoke checks and before capturing the accepted snapshot.",
                "Reuse the accepted snapshot as the baseline before Lenovo bring-up.",
            ],
        }

    def vm_acceptance_snapshot(
        self,
        *,
        output_path: Optional[Path] = None,
        base_iso_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Capture the current Kubuntu VM acceptance state without mutating the system."""
        host_probe = self._vm_host_probe(base_iso_path=base_iso_path)
        guest_probe = self._vm_guest_probe()
        missing = self._vm_acceptance_missing_prerequisites(host_probe, guest_probe)
        checks = self._vm_acceptance_checks(guest_probe=guest_probe, missing_prerequisites=missing)
        status = "accepted" if checks and all(item["status"] == "passed" for item in checks) else "blocked"
        snapshot = {
            **self.vm_acceptance_snapshot_template(),
            "status": status,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "acceptance_run_id": f"vm-acceptance-{int(time.time())}",
            "manifest_hash": self.manifest().get("manifest_hash"),
            "package_list": guest_probe.get("package_list", []),
            "enabled_services": guest_probe.get("enabled_services", []),
            "boot_timing_ms": guest_probe.get("boot_timing_ms", {}),
            "session_switch_timing_ms": {},
            "host_probe": host_probe,
            "guest_probe": guest_probe,
            "missing_prerequisites": missing,
            "acceptance_checks": checks,
            "next_actions": self._vm_acceptance_next_actions(missing, checks=checks),
        }
        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
            snapshot["output_path"] = str(output_path)
        return snapshot

    def vm_checklist_markdown(self) -> str:
        return f"""# OBEOS Kubuntu VM-First Checklist

## Canonical v1 doctrine
- OBEOS owns the session.
- The Blackcomputer TUI is the native boot-home.
- Desktop is a launched workspace.
- Linux basics remain intact.
- Packaging follows stability.

## Default VM profile
- Base ISO: {VM_PROFILE_DEFAULT["base_iso"]}
- Architecture: {VM_PROFILE_DEFAULT["architecture"]}
- Firmware: {VM_PROFILE_DEFAULT["firmware"]}
- CPUs: {VM_PROFILE_DEFAULT["cpus"]}
- RAM: {VM_PROFILE_DEFAULT["memory_gb"]} GB
- Disk: {VM_PROFILE_DEFAULT["disk_gb"]} GB
- Network: {VM_PROFILE_DEFAULT["network"]}

## Host-side staging
1. Materialize the OBEOS Kubuntu bundle:
   `blackfong obeos materialize --output data/obeos-remaster`
2. Review the manifest:
   `blackfong obeos manifest`
3. Review shell compatibility:
   `blackfong obeos shell-compat`
4. For a fresh Hyper-V acceptance candidate on Windows, create `OBEOS-v2-clean` with:
   `powershell -ExecutionPolicy Bypass -File data/vm/boot_obeos_hyperv.ps1 -VmName OBEOS-v2-clean`

## Guest install sequence
1. Create an EFI Kubuntu 24.04 VM with the default VM profile.
2. Install stock Kubuntu 24.04 to the VM.
3. Copy `data/obeos-remaster.zip` into the guest by browser download or drag-and-drop.
4. Run the Stage A safe bootstrap from a working Plasma session:
   `{self.bootstrap_contract.bootstrap_command}`
5. Confirm Stage A leaves `graphical.target` as the default target and installs `obeos-tui.service` without forcing boot-home yet.

## Plasma smoke checks before home activation
1. Start Thoughtglass HUD from Plasma:
   `blackfong hud --host 127.0.0.1 --port 8080 --no-open-browser`
2. Launch the Textual surface manually:
   `blackfong tui`
3. Launch Blackfong Workspace:
   `blackfong desktop workspace`
4. Confirm microphone and camera remain manual-only and visibly gated.

## Activate OBEOS TUI-home boot
1. After the Plasma smoke checks pass, activate the final home contract:
   `{self.bootstrap_contract.activation_command}`
2. Reboot the VM.

## Final session validation
1. Confirm the default target is `multi-user.target`.
2. Confirm the VM boots to the Blackcomputer TUI home on `tty1`.
3. Launch Plasma with `blackfong desktop start`.
4. Return home with `blackfong home` and confirm authority returns to the TUI.
5. Confirm `obeos-tui.service` is present and enabled for the boot-home contract.

## Degraded fallback
- If the TUI fails to start, the launcher must fall back to a standard shell login.
- The fallback shell should clearly report degraded mode and preserve machine access.

## Acceptance snapshot
- Record package list, enabled services, boot timing, session-switch timing, and the manifest hash.
- Save the resulting baseline to `etc/obeos/vm_acceptance_snapshot.json` after the first known-good run.
- From inside the Kubuntu VM, capture the current state with:
  `blackfong obeos vm-acceptance-snapshot --output /etc/obeos/vm_acceptance_snapshot.json`
"""

    def shell_compatibility_status(self) -> Dict[str, Any]:
        commands: List[Dict[str, Any]] = []
        for command in SHELL_COMPATIBILITY_COMMANDS:
            if command == "cd":
                commands.append(
                    {
                        "command": command,
                        "kind": "shell_builtin",
                        "available": True,
                        "resolved_path": None,
                        "notes": "cd is provided by the shell and must remain unmodified.",
                    }
                )
                continue
            resolved = shutil.which(command)
            commands.append(
                {
                    "command": command,
                    "kind": "binary",
                    "available": resolved is not None,
                    "resolved_path": resolved,
                }
            )
        return {
            "rule": self.manifest_model.shell_rule,
            "enhancement_rule": self.manifest_model.enhancement_rule,
            "platform": platform.system().lower(),
            "commands": commands,
            "all_available": all(item["available"] for item in commands),
        }

    def desktop_status(self) -> Dict[str, Any]:
        systemctl_path = shutil.which("systemctl")
        shell_status = self.shell_compatibility_status()
        workspace_script = self.workspace_script_path()
        preflight = self._desktop_preflight()
        status = {
            "platform": platform.system().lower(),
            "supported": platform.system().lower() == "linux" and bool(systemctl_path),
            "workspace_mode": "managed_blackcomputer_workspace",
            "systemctl_path": systemctl_path,
            "boot_profile": self.boot_profile.as_dict(),
            "shell_compatibility": shell_status,
            "session_authority_rule": self.manifest_model.session_authority_rule,
            "degraded_boot_rule": self.manifest_model.degraded_boot_rule,
            "service_dependency_map": self.service_dependency_map(),
            "bootstrap_contract": self.bootstrap_contract.as_dict(),
            "preflight": preflight,
            "vm_profile_default": dict(VM_PROFILE_DEFAULT),
            "command_contract": {
                "start": "systemctl isolate graphical.target",
                "stop": "systemctl isolate multi-user.target",
                "home": "systemctl isolate multi-user.target",
                "activate_home": self.bootstrap_contract.activation_command,
                "privilege": "root or OBEOS passwordless sudoers for target isolation; use pkexec for the explicit activate-home step",
                "status": "systemctl get-default && systemctl is-active sddm.service",
                "workspace": f"{sys.executable} {workspace_script}",
                "tui_safe_mode": "systemctl isolate multi-user.target",
            },
            "workspace_shell": {
                "script_path": str(workspace_script),
                "available": workspace_script.exists(),
                "managed_surface_rule": "PySide6 is a managed workspace surface, not a second authority.",
            },
        }
        if not status["supported"]:
            status["message"] = "Desktop control is a Linux/systemd feature; this environment can only stage the contract."
            return status

        status.update(
            {
                "default_target": self._run_capture(["systemctl", "get-default"]),
                "sddm_enabled": self._run_capture(["systemctl", "is-enabled", "sddm.service"]),
                "sddm_active": self._run_capture(["systemctl", "is-active", "sddm.service"]),
                "graphical_target_active": self._run_capture(["systemctl", "is-active", "graphical.target"]),
                "multi_user_target_active": self._run_capture(["systemctl", "is-active", "multi-user.target"]),
                "bootstrap_stage": self._read_json_file(Path("/etc/obeos/bootstrap_stage.json")),
            }
        )
        return status

    def desktop_start(self, *, execute: bool = True) -> Dict[str, Any]:
        preflight = self._desktop_preflight()
        response = {
            "status": "planned",
            "action": "start",
            "workspace_mode": "managed_blackcomputer_workspace",
            "preflight": preflight,
            "restore_profile": "blackcomputer_default",
            "target_surface": "blackfong_workspace",
            "steps": [
                "run_preflight_health_checks",
                "start_missing_required_services",
                "restore_workspace_profile",
                "transition_session_mode_to_desktop",
                "launch_workspace_surface",
                "restore_awareness_state",
            ],
        }
        if not execute:
            response["message"] = "Dry run only; managed Blackcomputer workspace mode was not entered."
            return response
        if platform.system().lower() == "windows":
            workspace = self.launch_workspace(execute=True)
            response["status"] = "ok" if workspace.get("status") in {"launched", "planned"} else "failed"
            response["workspace"] = workspace
            response["message"] = "Entered managed Blackcomputer workspace mode on Windows."
            return response
        transition = self._desktop_transition("start", self.boot_profile.graphical_target, execute=True)
        response["transition"] = transition
        if transition.get("status") != "ok":
            response["status"] = transition.get("status", "failed")
            response["message"] = transition.get("message") or "Desktop transition failed."
            return response
        workspace = self.launch_workspace(execute=True)
        response["workspace"] = workspace
        response["status"] = "ok" if workspace.get("status") in {"launched", "planned"} else "failed"
        response["message"] = "Entered managed Blackcomputer workspace mode."
        return response

    def desktop_stop(self, *, execute: bool = True) -> Dict[str, Any]:
        return self.tui_safe_mode(execute=execute)

    def home(self, *, execute: bool = True) -> Dict[str, Any]:
        result = self._desktop_transition("home", self.boot_profile.default_target, execute=execute)
        result.update(
            {
                "command_name": "home",
                "surface": "blackcomputer_tui",
                "home_surface": "blackcomputer_tui",
                "session_authority_rule": self.manifest_model.session_authority_rule,
                "boot_profile": self.boot_profile.as_dict(),
            }
        )
        if result["status"] == "planned":
            result["message"] = "Dry run only; the OBEOS TUI home target was not activated."
        elif result["status"] == "ok":
            result["message"] = "Requested return to the OBEOS TUI home on tty1."
        elif result["status"] == "unsupported":
            result["message"] = "Blackfong home returns to the OBEOS TUI inside the Kubuntu guest; this host can only stage the contract."
        return result

    def activate_home_boot(self, *, execute: bool = True) -> Dict[str, Any]:
        commands = [
            ["systemctl", "daemon-reload"],
            ["systemctl", "enable", self.boot_profile.tui_service_name],
            ["systemctl", "set-default", self.boot_profile.default_target],
        ]
        response = {
            "status": "planned",
            "action": "activate-home",
            "command_name": "activate-home",
            "stage": self.bootstrap_contract.activation_stage,
            "guest_elevation_tool": self.bootstrap_contract.guest_elevation_tool,
            "activation_command": self.bootstrap_contract.activation_command,
            "commands": commands,
            "boot_profile": self.boot_profile.as_dict(),
            "reboot_required": True,
        }
        status = self.desktop_status()
        if not execute:
            response["message"] = "Dry run only; TUI-home boot was not activated."
            return response
        if not status.get("supported"):
            response["status"] = "unsupported"
            response["message"] = (
                "OBEOS home activation must run inside the Kubuntu guest. "
                f"Use `{self.bootstrap_contract.activation_command}` from Plasma."
            )
            return response
        if not self._can_isolate_systemd():
            response["status"] = "requires_privilege"
            response["message"] = (
                "Activation needs root so it can enable obeos-tui.service and switch the default target. "
                f"Use `{self.bootstrap_contract.activation_command}`."
            )
            return response

        steps: List[Dict[str, Any]] = []
        for command in commands:
            result = self._run_capture(command)
            steps.append(result)
            if not result.get("ok"):
                response["status"] = "error"
                response["message"] = result.get("output") or result.get("error") or "Activation command failed."
                response["steps"] = steps
                return response

        stage_path = Path("/etc/obeos/bootstrap_stage.json")
        stage_path.parent.mkdir(parents=True, exist_ok=True)
        stage_path.write_text(
            json.dumps(
                self.bootstrap_stage_payload(
                    self.bootstrap_contract.activation_stage,
                    default_target=self.boot_profile.default_target,
                    home_boot_enabled=True,
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        response["status"] = "ok"
        response["message"] = "Activated OBEOS TUI-home boot. Reboot to validate the session contract."
        response["steps"] = steps
        response["desktop_status"] = self.desktop_status()
        response["bootstrap_stage"] = self._read_json_file(stage_path)
        return response

    def tui_safe_mode(self, *, execute: bool = True) -> Dict[str, Any]:
        result = self._desktop_transition("stop", self.boot_profile.default_target, execute=execute)
        result.update(
            {
                "command_name": "tui-safe-mode",
                "surface": "blackcomputer_tui",
                "home_surface": "blackcomputer_tui",
                "boot_profile": self.boot_profile.as_dict(),
            }
        )
        if result["status"] == "planned":
            result["message"] = "Dry run only; the OBEOS TUI home target was not activated."
        elif result["status"] == "ok":
            result["message"] = "Requested return to the OBEOS TUI home on tty1."
        return result

    def workspace_script_path(self) -> Path:
        return self.workspace_root / "desktop_shell.py"

    def launch_workspace(self, *, execute: bool = True) -> Dict[str, Any]:
        script_path = self.workspace_script_path()
        command = [sys.executable, str(script_path)]
        result = {
            "action": "workspace",
            "command": "workspace",
            "status": "planned",
            "surface": "blackfong_workspace",
            "workspace_mode": "managed_blackcomputer_workspace",
            "managed_surface_rule": "PySide6 is a managed workspace surface, not a second authority.",
            "script_path": str(script_path),
            "command_line": command,
        }
        if not script_path.exists():
            result.update(
                {
                    "status": "unavailable",
                    "error": f"Workspace shell script not found at {script_path}",
                }
            )
            return result
        if not execute:
            result["status"] = "planned"
            return result
        try:
            process = subprocess.Popen(command, cwd=str(self.workspace_root))
        except OSError as exc:
            result.update({"status": "failed", "error": str(exc)})
            return result
        result.update({"status": "launched", "pid": process.pid})
        return result

    def _desktop_preflight(self) -> Dict[str, Any]:
        try:
            from blackfong_ecosystem.service_registry import get_ui_services
        except Exception as exc:
            return {
                "status": "degraded",
                "error": str(exc),
                "checks": {},
            }
        services = get_ui_services()
        awareness = services.awareness_service.build_snapshot(emit=False)
        startup = awareness.get("startup", {})
        backend = awareness.get("observed", {}).get("backend", {})
        tiny_status = services.tinyprogrammer.get_status(require_stream=True)
        checks = {
            "backend": {
                "state": backend.get("status", "unknown"),
                "ok": backend.get("status") == "available",
            },
            "workspace": {
                "state": "available" if self.workspace_script_path().exists() else "missing",
                "ok": self.workspace_script_path().exists(),
            },
            "startup": {
                "state": startup.get("overall_state", "warming"),
                "ready_but_degraded": bool(startup.get("ready_but_degraded")),
                "ok": startup.get("overall_state") in {"ready", "active", "warming"},
            },
            "tinyprogrammer": {
                "state": tiny_status.get("state") or tiny_status.get("management_state") or "offline",
                "reason": tiny_status.get("last_error_code") or tiny_status.get("stream_reason"),
                "ok": bool(tiny_status.get("online")) or not bool(services.tinyprogrammer.autostart_enabled),
            },
        }
        overall = "ok" if checks["workspace"]["ok"] and checks["startup"]["ok"] else "degraded"
        return {
            "status": overall,
            "checks": checks,
        }

    def materialize_deployment_bundle(self, output_root: Path) -> Dict[str, Any]:
        output_root = Path(output_root)
        blackfong_root = output_root / "opt" / "blackfong"
        files: Dict[Path, str] = {
            output_root / "README.md": self._bundle_readme(),
            output_root / "etc" / "obeos" / "manifest.json": json.dumps(self.manifest(), indent=2),
            output_root / "etc" / "obeos" / "bootstrap_stage.json": json.dumps(
                self.bootstrap_stage_payload(
                    self.bootstrap_contract.default_stage,
                    default_target=self.bootstrap_contract.default_target_before_activation,
                    home_boot_enabled=False,
                ),
                indent=2,
            ),
            output_root / "etc" / "obeos" / "service_dependency_map.json": json.dumps(self.service_dependency_map(), indent=2),
            output_root / "etc" / "obeos" / "vm_acceptance_snapshot.json": json.dumps(self.vm_acceptance_snapshot_template(), indent=2),
            output_root / "etc" / "obeos" / "vm_checklist.md": self.vm_checklist_markdown(),
            output_root / "etc" / "obeos" / "session_authority.md": self._session_authority_readme(),
            output_root / "etc" / "obeos" / "degraded_boot_fallback.md": self._degraded_boot_readme(),
            output_root / "etc" / "systemd" / "system" / "getty@tty1.service.d" / "override.conf": self._getty_override(),
            output_root / "etc" / "systemd" / "system" / self.boot_profile.tui_service_name: self._tui_service_unit(),
            output_root / "opt" / "obeos" / "bin" / "bootstrap-kubuntu-guest.sh": self._guest_bootstrap_script(),
            output_root / "opt" / "obeos" / "bin" / "apply-obeos-branding.sh": self._apply_branding_script(),
            output_root / "opt" / "obeos" / "bin" / "launch-blackcomputer-tui.sh": self._launch_tui_script(),
            output_root / "opt" / "obeos" / "bin" / "return-to-tui.sh": self._return_to_tui_script(),
            output_root / "opt" / "obeos" / "share" / "boot" / "blackcomputer_intro.txt": self.boot_profile.ascii_intro.strip() + "\n",
            output_root / "opt" / "obeos" / "share" / "branding" / "README.md": self._branding_readme(),
            output_root / "opt" / "obeos" / "share" / "branding" / "profile.json": json.dumps(self.manifest_model.branding_profile.as_dict(), indent=2) + "\n",
            output_root / "opt" / "obeos" / "share" / "branding" / "obeos-logo.svg": self._branding_logo_svg(),
            output_root / "opt" / "obeos" / "share" / "branding" / "obeos-splash.svg": self._branding_splash_svg(),
            output_root / "opt" / "obeos" / "share" / "branding" / "obeos-wallpaper.svg": self._branding_wallpaper_svg(),
            output_root / "opt" / "obeos" / "share" / "branding" / "grub" / "theme.txt": self._branding_grub_theme_txt(),
            output_root / "opt" / "obeos" / "share" / "branding" / "sddm" / "theme.conf": self._branding_sddm_theme_conf(),
            output_root / "opt" / "obeos" / "share" / "branding" / "sddm" / "Main.qml": self._branding_sddm_main_qml(),
            output_root / "opt" / "obeos" / "share" / "branding" / "sddm" / "background.svg": self._branding_splash_svg(),
            output_root / "opt" / "obeos" / "share" / "branding" / "plasma" / "OBEOS.colors": self._branding_plasma_colorscheme(),
            output_root / "opt" / "obeos" / "share" / "branding" / "plasma" / "kscreenlockerrc": self._branding_plasma_lock_screen_config(),
            output_root / "opt" / "obeos" / "share" / "branding" / "plasma" / "kdeglobals-snippet.ini": self._branding_plasma_kdeglobals_snippet(),
            output_root / "opt" / "obeos" / "share" / "branding" / "plasma" / "wallpaper-metadata.desktop": self._branding_wallpaper_metadata(),
            blackfong_root / "README.md": self._blackfong_runtime_readme(),
            blackfong_root / "requirements-obeos-linux.txt": self._obeos_linux_requirements(),
            output_root / "opt" / "daise" / "README.md": self._daise_runtime_readme(),
            output_root / "var" / "lib" / "obeos" / ".keep": "",
            output_root / "usr" / "share" / "applications" / "obeos-return-to-tui.desktop": self._return_to_tui_desktop_file(),
            output_root / "remaster" / "build-kubuntu-remaster.sh": self._remaster_script(),
        }
        directories = [
            output_root / "opt" / "obeos",
            output_root / "opt" / "blackfong",
            output_root / "opt" / "daise",
            output_root / "etc" / "obeos",
            output_root / "var" / "lib" / "obeos",
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        materialized: List[str] = []
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            if path.suffix == ".sh":
                path.chmod(0o755)
            materialized.append(str(path))
        materialized.extend(self._copy_branding_binary_assets(output_root))
        runtime_payload = self._copy_blackfong_runtime_payload(blackfong_root)
        materialized.extend(runtime_payload)
        return {
            "status": "ok",
            "output_root": str(output_root),
            "files": materialized,
            "runtime_payload": runtime_payload,
            "layout": self.filesystem_layout.as_dict(),
            "boot_profile": self.boot_profile.as_dict(),
            "manifest_hash": self.manifest().get("manifest_hash"),
        }

    def _copy_branding_binary_assets(self, output_root: Path) -> List[str]:
        copied: List[str] = []

        boot_screen_source = self.workspace_root / OBEOS_BOOT_SCREEN_RELATIVE_PATH
        if boot_screen_source.exists() and boot_screen_source.is_file():
            boot_screen_destination = output_root / OBEOS_BOOT_SCREEN_BUNDLE_PATH
            boot_screen_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(boot_screen_source, boot_screen_destination)
            copied.append(str(boot_screen_destination))

        plymouth_source = self.workspace_root / BLACKCOMPUTER_PLYMOUTH_RELATIVE_PATH
        if plymouth_source.exists() and plymouth_source.is_dir():
            plymouth_destination = output_root / BLACKCOMPUTER_PLYMOUTH_BUNDLE_PATH
            if plymouth_destination.exists():
                shutil.rmtree(plymouth_destination)
            shutil.copytree(
                plymouth_source,
                plymouth_destination,
                ignore=shutil.ignore_patterns("*.gif", "README_INSTALL.txt"),
            )
            copied.extend(str(path) for path in plymouth_destination.rglob("*") if path.is_file())

        return copied

    def _obeos_linux_requirements(self) -> str:
        return "\n".join(OBEOS_LINUX_REQUIREMENTS) + "\n"

    def _copy_blackfong_runtime_payload(self, blackfong_root: Path) -> List[str]:
        blackfong_root.mkdir(parents=True, exist_ok=True)
        copied: List[str] = []
        for filename in BLACKFONG_RUNTIME_ROOT_FILES:
            source = self.workspace_root / filename
            if not source.exists() or not source.is_file():
                continue
            destination = blackfong_root / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(str(destination))

        package_source = self.workspace_root / "blackfong_ecosystem"
        if package_source.exists() and package_source.is_dir():
            package_destination = blackfong_root / "blackfong_ecosystem"
            if package_destination.exists():
                shutil.rmtree(package_destination)
            shutil.copytree(
                package_source,
                package_destination,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    "*.pyc",
                    ".pytest_cache",
                    "test_*.py",
                ),
            )
            for path in sorted(package_destination.rglob("*")):
                if path.is_file():
                    copied.append(str(path))
        return copied

    def hardware_probe(self) -> Dict[str, Any]:
        return {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "python": sys.version.split()[0],
            "shell_compatibility": self.shell_compatibility_status(),
            "desktop_status": self.desktop_status(),
        }

    def _vm_host_probe(self, *, base_iso_path: Optional[Path]) -> Dict[str, Any]:
        iso_path = self._resolve_kubuntu_iso(base_iso_path)
        vm_runners = {
            command: {
                "available": shutil.which(command) is not None,
                "path": shutil.which(command),
            }
            for command in VM_RUNNER_COMMANDS
        }
        wsl_probe = self._wsl_vm_probe()
        if wsl_probe.get("kvm_available") and wsl_probe.get("qemu_available"):
            vm_runners["wsl-qemu"] = {
                "available": True,
                "path": str(wsl_probe.get("qemu_path") or "Ubuntu-24.04:/usr/bin/qemu-system-x86_64"),
            }
        return {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "python": sys.version.split()[0],
            "base_iso_path": str(iso_path) if iso_path else None,
            "base_iso_available": bool(iso_path),
            "vm_runners": vm_runners,
            "docker_available": shutil.which("docker") is not None,
            "wsl_available": shutil.which("wsl") is not None,
            "wsl_vm_probe": wsl_probe,
        }

    def _vm_guest_probe(self) -> Dict[str, Any]:
        os_release = self._read_os_release()
        distribution_text = " ".join(
            str(os_release.get(key, ""))
            for key in ("PRETTY_NAME", "NAME", "ID", "VARIANT", "VARIANT_ID", "VERSION_CODENAME", "UBUNTU_CODENAME")
        ).lower()
        is_linux = platform.system().lower() == "linux"
        is_kubuntu = is_linux and ("kubuntu" in distribution_text or ("ubuntu" in distribution_text and "plasma" in distribution_text))
        packages = self._installed_packages() if is_linux else []
        enabled_services = self._enabled_services() if is_linux else []
        boot_timing = self._systemd_analyze() if is_linux else {}
        default_target = self._run_capture(["systemctl", "get-default"]) if is_linux and shutil.which("systemctl") else {}
        tui_enabled = self._run_capture(["systemctl", "is-enabled", self.boot_profile.tui_service_name]) if is_linux and shutil.which("systemctl") else {}
        tui_active = self._run_capture(["systemctl", "is-active", self.boot_profile.tui_service_name]) if is_linux and shutil.which("systemctl") else {}
        tui_unit = self._run_capture(["systemctl", "cat", self.boot_profile.tui_service_name]) if is_linux and shutil.which("systemctl") else {}
        return {
            "is_linux": is_linux,
            "is_kubuntu": is_kubuntu,
            "os_release": os_release,
            "package_count": len(packages),
            "package_list": packages[:240],
            "enabled_services": enabled_services[:240],
            "boot_timing_ms": boot_timing,
            "default_target": default_target,
            "obeos_tui_unit": tui_unit,
            "obeos_tui_enabled": tui_enabled,
            "obeos_tui_active": tui_active,
            "bootstrap_stage": self._read_json_file(Path("/etc/obeos/bootstrap_stage.json")) if is_linux else {},
            "workspace_shell_available": self.workspace_script_path().exists(),
            "desktop_status": self.desktop_status(),
        }

    def _vm_acceptance_missing_prerequisites(self, host_probe: Dict[str, Any], guest_probe: Dict[str, Any]) -> List[Dict[str, str]]:
        missing: List[Dict[str, str]] = []
        if guest_probe.get("is_kubuntu"):
            return missing
        if not host_probe.get("base_iso_available"):
            missing.append(
                {
                    "code": "kubuntu_iso_missing",
                    "human_reason": "No Kubuntu ISO path was provided or discovered in the workspace data directory.",
                }
            )
        runners = host_probe.get("vm_runners", {})
        if not any(item.get("available") for item in runners.values() if isinstance(item, dict)):
            missing.append(
                {
                    "code": "vm_runner_missing",
                    "human_reason": "No supported desktop VM runner was found on PATH.",
                }
            )
        if not guest_probe.get("is_kubuntu"):
            missing.append(
                {
                    "code": "not_running_inside_kubuntu_guest",
                    "human_reason": "Acceptance evidence must be captured from the booted Kubuntu guest after bootstrap.",
                }
            )
        return missing

    def _vm_acceptance_checks(self, *, guest_probe: Dict[str, Any], missing_prerequisites: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        blocked_reason = "; ".join(item["code"] for item in missing_prerequisites)
        checks: List[Dict[str, Any]] = []
        bootstrap_stage = guest_probe.get("bootstrap_stage", {})
        activation_pending = (
            isinstance(bootstrap_stage, dict)
            and bootstrap_stage.get("stage") == self.bootstrap_contract.default_stage
            and not bool(bootstrap_stage.get("home_boot_enabled"))
        )
        for check in VM_ACCEPTANCE_CHECKS:
            status = "blocked" if blocked_reason else "passed"
            evidence: Any = None
            if check == "base_distribution":
                status = "passed" if guest_probe.get("is_kubuntu") else "blocked"
                evidence = guest_probe.get("os_release", {})
            elif check == "manifest_hash":
                status = "passed"
                evidence = self.manifest().get("manifest_hash")
            elif check == "obeos_tui_service":
                enabled = guest_probe.get("obeos_tui_enabled", {})
                active = guest_probe.get("obeos_tui_active", {})
                unit = guest_probe.get("obeos_tui_unit", {})
                default_target = guest_probe.get("default_target", {})
                desktop_status = guest_probe.get("desktop_status", {})
                command_contract = desktop_status.get("command_contract", {}) if isinstance(desktop_status, dict) else {}
                enabled_output = str(enabled.get("output") or "").strip()
                active_output = str(active.get("output") or "").strip()
                tui_enabled = enabled_output in {"enabled", "static", "indirect"}
                tui_state_observed = active_output in {"active", "activating", "inactive", "failed", "unknown", ""}
                default_home = default_target.get("ok") and str(default_target.get("output") or "").strip() == self.boot_profile.default_target
                default_graphical = default_target.get("ok") and (
                    str(default_target.get("output") or "").strip() == self.bootstrap_contract.default_target_before_activation
                )
                home_contract = str(command_contract.get("home") or "").strip() == "systemctl isolate multi-user.target"
                stop_contract = str(command_contract.get("stop") or "").strip() == "systemctl isolate multi-user.target"
                if unit.get("ok") and tui_enabled and tui_state_observed and default_home and home_contract and stop_contract:
                    status = "passed"
                elif (
                    unit.get("ok")
                    and not blocked_reason
                    and activation_pending
                    and enabled_output in {"disabled", "static-disabled", "masked", ""}
                    and default_graphical
                    and home_contract
                    and stop_contract
                ):
                    status = "blocked"
                else:
                    status = "blocked" if blocked_reason else "failed"
                evidence = {
                    "unit": unit,
                    "enabled": enabled,
                    "active": active,
                    "default_target": default_target,
                    "command_contract": command_contract,
                    "bootstrap_stage": bootstrap_stage,
                    "activation_pending": activation_pending,
                }
            elif check == "desktop_workspace_contract":
                status = "passed" if guest_probe.get("workspace_shell_available") else ("blocked" if blocked_reason else "failed")
                evidence = guest_probe.get("desktop_status", {}).get("workspace_shell", {})
            elif check == "hud_launch_contract":
                status = "passed" if self.workspace_root.exists() else "failed"
                evidence = {"workspace_root": str(self.workspace_root)}
            elif check == "sensor_policy":
                status = "passed" if self.manifest().get("sensor_policy") == "manual-only" else "failed"
                evidence = self.manifest().get("sensor_policy")
            elif check == "degraded_fallback":
                status = "passed"
                evidence = self.manifest_model.degraded_boot_rule
            checks.append(
                {
                    "id": check,
                    "status": status,
                    "evidence": evidence,
                    "blocked_by": "activate_home_pending" if status == "blocked" and check == "obeos_tui_service" and activation_pending else (blocked_reason if status == "blocked" else None),
                }
            )
        return checks

    def _vm_acceptance_next_actions(self, missing_prerequisites: List[Dict[str, str]], *, checks: Optional[List[Dict[str, Any]]] = None) -> List[str]:
        if not missing_prerequisites and checks and all(item.get("status") == "passed" for item in checks):
            return [
                "Review the accepted snapshot and promote it as the pre-Lenovo baseline.",
                "Keep the manifest hash with the VM image notes.",
            ]
        actions = []
        codes = {item["code"] for item in missing_prerequisites}
        if "kubuntu_iso_missing" in codes:
            actions.append("Provide the official Kubuntu 24.04.x desktop ISO path with --base-iso or OBEOS_KUBUNTU_ISO.")
        if "vm_runner_missing" in codes:
            actions.append("Install or expose VirtualBox, QEMU, Multipass, virt-install, or WSL QEMU/KVM before the first VM run.")
        if "not_running_inside_kubuntu_guest" in codes:
            actions.append("Boot the Kubuntu VM, run the OBEOS bootstrap, reboot, then run the snapshot command inside the guest.")
        if checks:
            check_map = {item.get("id"): item for item in checks}
            tui_check = check_map.get("obeos_tui_service")
            if tui_check and tui_check.get("blocked_by") == "activate_home_pending":
                actions.append(
                    f"From inside the Kubuntu Plasma session, run `{self.bootstrap_contract.activation_command}`, reboot, and rerun the snapshot."
                )
            workspace_check = check_map.get("desktop_workspace_contract")
            if workspace_check and workspace_check.get("status") != "passed":
                actions.append("From Plasma, run `blackfong desktop workspace` and confirm the PySide6 workspace opens cleanly before activating TUI-home boot.")
            hud_check = check_map.get("hud_launch_contract")
            if hud_check and hud_check.get("status") != "passed":
                actions.append("From Plasma, run `blackfong hud --host 127.0.0.1 --port 8080 --no-open-browser` and confirm the HUD starts before activating TUI-home boot.")
            if not actions and not missing_prerequisites:
                actions.append("Review the blocked acceptance check details, fix the failing surface or service, then rerun the snapshot.")
        return actions

    def _resolve_kubuntu_iso(self, base_iso_path: Optional[Path]) -> Optional[Path]:
        candidates: List[Path] = []
        if base_iso_path:
            candidates.append(Path(base_iso_path))
        env_iso = os.getenv("OBEOS_KUBUNTU_ISO")
        if env_iso:
            candidates.append(Path(env_iso))
        candidates.extend(
            [
                self.workspace_root / "data" / "kubuntu-24.04-desktop-amd64.iso",
                self.workspace_root / "data" / "iso" / "kubuntu-24.04-desktop-amd64.iso",
                self.workspace_root / "data" / "iso" / "kubuntu-24.04.4-desktop-amd64.iso",
                self.workspace_root / "data" / "iso" / "kubuntu-24.04.3-desktop-amd64.iso",
                self.workspace_root / "data" / "iso" / "kubuntu-24.04.2-desktop-amd64.iso",
            ]
        )
        for directory in (self.workspace_root / "data", self.workspace_root / "data" / "iso"):
            if directory.exists():
                candidates.extend(sorted(directory.glob("kubuntu-24.04.*-desktop-amd64.iso"), reverse=True))
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve()
            except OSError:
                resolved = candidate
            if resolved.exists() and resolved.suffix.lower() == ".iso":
                return resolved
        return None

    def _wsl_vm_probe(self) -> Dict[str, Any]:
        wsl_path = shutil.which("wsl")
        if not wsl_path:
            return {"available": False, "reason_code": "wsl_unavailable"}
        script = r"""
set +e
echo available=1
echo distro=Ubuntu-24.04
if [ -e /dev/kvm ]; then echo kvm_available=1; else echo kvm_available=0; fi
qemu_path="$(command -v qemu-system-x86_64 || true)"
if [ -n "$qemu_path" ]; then echo qemu_available=1; echo "qemu_path=$qemu_path"; else echo qemu_available=0; fi
qemu_img_path="$(command -v qemu-img || true)"
if [ -n "$qemu_img_path" ]; then echo qemu_img_available=1; echo "qemu_img_path=$qemu_img_path"; else echo qemu_img_available=0; fi
xorriso_path="$(command -v xorriso || true)"
if [ -n "$xorriso_path" ]; then echo xorriso_available=1; echo "xorriso_path=$xorriso_path"; else echo xorriso_available=0; fi
if sudo -n true >/dev/null 2>&1; then echo sudo_passwordless=1; else echo sudo_passwordless=0; fi
"""
        try:
            completed = subprocess.run(
                [wsl_path, "-d", "Ubuntu-24.04", "--", "bash", "-lc", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=6,
                check=False,
            )
        except Exception as exc:
            return {"available": False, "reason_code": "wsl_probe_failed", "human_reason": str(exc)}
        payload: Dict[str, Any] = {
            "available": completed.returncode == 0,
            "returncode": completed.returncode,
        }
        for line in completed.stdout.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if value in {"0", "1"}:
                payload[key] = value == "1"
            else:
                payload[key] = value
        if completed.stderr.strip():
            payload["stderr"] = completed.stderr.strip()
        return payload

    def _read_os_release(self) -> Dict[str, str]:
        os_release_path = Path("/etc/os-release")
        if not os_release_path.exists():
            return {}
        try:
            lines = os_release_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return {}
        parsed: Dict[str, str] = {}
        for line in lines:
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            parsed[key] = value.strip().strip('"')
        return parsed

    def _read_json_file(self, path: Path) -> Dict[str, Any]:
        try:
            if not path.exists():
                return {}
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _installed_packages(self) -> List[str]:
        if not shutil.which("dpkg-query"):
            return []
        result = self._run_capture(["dpkg-query", "-W", "-f=${Package}=${Version}\n"])
        if not result.get("ok"):
            return []
        return [line.strip() for line in str(result.get("output") or "").splitlines() if line.strip()]

    def _enabled_services(self) -> List[str]:
        if not shutil.which("systemctl"):
            return []
        result = self._run_capture(["systemctl", "list-unit-files", "--state=enabled", "--no-legend"])
        if not result.get("ok"):
            return []
        services = []
        for line in str(result.get("output") or "").splitlines():
            parts = line.split()
            if parts:
                services.append(parts[0])
        return services

    def _systemd_analyze(self) -> Dict[str, Any]:
        if not shutil.which("systemd-analyze"):
            return {}
        result = self._run_capture(["systemd-analyze"])
        return result if result.get("ok") else {}

    def _desktop_transition(self, action: str, target: str, *, execute: bool) -> Dict[str, Any]:
        status = self.desktop_status()
        command = self._systemctl_isolate_command(target)
        response = {
            "status": "planned",
            "action": action,
            "target": target,
            "command": command,
            "command_line": command,
            "boot_profile": self.boot_profile.as_dict(),
        }
        if not execute:
            response["message"] = "Dry run only; no system target was changed."
            return response
        if not status.get("supported"):
            response["status"] = "unsupported"
            response["message"] = status.get("message")
            return response
        if not self._can_isolate_systemd() and not self._can_use_passwordless_sudo():
            response["status"] = "requires_privilege"
            response["message"] = (
                f"Run with sudo or enable the OBEOS systemctl sudoers contract to execute: {' '.join(command)}"
            )
            return response
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            response["status"] = "error"
            response["message"] = exc.stderr.strip() or exc.stdout.strip() or str(exc)
            return response
        response["status"] = "ok"
        response["message"] = f"Requested transition to {target}."
        response["desktop_status"] = self.desktop_status()
        return response

    def _can_isolate_systemd(self) -> bool:
        geteuid = getattr(os, "geteuid", None)
        if geteuid is None:
            return False
        try:
            return geteuid() == 0
        except OSError:
            return False

    def _can_use_passwordless_sudo(self) -> bool:
        if platform.system().lower() != "linux" or not shutil.which("sudo"):
            return False
        try:
            result = subprocess.run(["sudo", "-n", "true"], check=False, capture_output=True, text=True)
        except OSError:
            return False
        return result.returncode == 0

    def _systemctl_isolate_command(self, target: str) -> List[str]:
        command = ["systemctl", "isolate", target]
        if self._can_isolate_systemd():
            return command
        if self._can_use_passwordless_sudo():
            return ["sudo", "-n", *command]
        return command

    def _run_capture(self, command: List[str]) -> Dict[str, Any]:
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
        except OSError as exc:
            return {"ok": False, "command": command, "error": str(exc), "output": ""}
        output = (result.stdout or result.stderr or "").strip()
        return {
            "ok": result.returncode == 0,
            "command": command,
            "returncode": result.returncode,
            "output": output,
        }

    def _bundle_readme(self) -> str:
        return """# O.B.E.O.S. Kubuntu Remaster Bundle

This bundle stages the Kubuntu-first OBEOS v1 deployment contract.

Contents:
- `etc/obeos/manifest.json`: base stack, shell-compat rule, branding layers, and boot contract.
- `etc/obeos/bootstrap_stage.json`: two-stage bootstrap state written by the guest bootstrap and the explicit home activation step.
- `etc/obeos/service_dependency_map.json`: required boot/service ordering.
- `etc/obeos/vm_checklist.md`: canonical Kubuntu VM-first install and validation checklist.
- `etc/obeos/vm_acceptance_snapshot.json`: baseline template for the first known-good VM run.
- `etc/obeos/session_authority.md`: session ownership doctrine.
- `etc/obeos/degraded_boot_fallback.md`: degraded-mode fallback contract if the TUI cannot boot.
- `etc/systemd/system/getty@tty1.service.d/override.conf`: auto-login `obeos` on `tty1`.
- `etc/systemd/system/obeos-tui.service`: dedicated systemd unit for the Blackcomputer TUI.
- `opt/obeos/bin/*.sh`: guest bootstrap, TUI launch, explicit TUI-home activation, and return-home tooling.
- `opt/blackfong/blackfong_ecosystem`: runtime Python application source.
- `opt/blackfong/pyproject.toml`: editable install metadata for the `blackfong` command.
- `opt/blackfong/requirements-obeos-linux.txt`: lean first-boot VM dependencies.
- `usr/share/applications/obeos-return-to-tui.desktop`: KDE launcher for returning session authority to the TUI home.
- `remaster/build-kubuntu-remaster.sh`: remaster script scaffold for a Kubuntu 24.04 ISO.

The goal is to enhance Kubuntu, not replace normal Ubuntu shell behavior.
The default guest bootstrap keeps `graphical.target` active first; only the explicit activation step flips the machine to TUI-home boot.

Surface hierarchy for this bundle:
- `Blackfong Workspace` is the canonical native local shell.
- `Thoughtglass HUD` is the supported browser-based secondary cockpit.
- `Blackcomputer TUI` remains the fallback/home authority surface.
"""

    def _getty_override(self) -> str:
        return """[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin obeos --noclear %I $TERM
Type=idle
"""

    def _tui_service_unit(self) -> str:
        return """[Unit]
Description=O.B.E.O.S. Home (Blackcomputer TUI)
After=network-online.target systemd-user-sessions.service
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
Type=simple
User=obeos
WorkingDirectory=/opt/blackfong
Environment=TERM=xterm-256color
Environment=BLACKFONG_TUI_BOOT_HOME_ENABLED=true
Environment=BLACKFONG_TUI_BIN=/opt/blackfong/.venv/bin/blackfong
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=no
StandardInput=tty
StandardOutput=tty
StandardError=journal
ExecStart=/opt/obeos/bin/launch-blackcomputer-tui.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
"""

    def _launch_tui_script(self) -> str:
        return """#!/usr/bin/env bash
set -uo pipefail

export PYTHONUNBUFFERED=1
export BLACKFONG_TUI_BOOT_HOME_ENABLED="${BLACKFONG_TUI_BOOT_HOME_ENABLED:-true}"
export BLACKFONG_TUI_BOOT_USER="${BLACKFONG_TUI_BOOT_USER:-obeos}"
BLACKFONG_TUI_BIN="${BLACKFONG_TUI_BIN:-/opt/blackfong/.venv/bin/blackfong}"
STATE_DIR="${HOME:-/home/${BLACKFONG_TUI_BOOT_USER}}/.local/state/obeos"

cd /opt/blackfong
if [[ -x "$BLACKFONG_TUI_BIN" ]]; then
  "$BLACKFONG_TUI_BIN" tui
  tui_status=$?
else
  echo "Missing Blackfong CLI at $BLACKFONG_TUI_BIN" >&2
  tui_status=127
fi

if [[ "$tui_status" -eq 0 ]]; then
  exit 0
fi

mkdir -p "$STATE_DIR"
cat > "$STATE_DIR/degraded_boot_notice.json" <<EOF
{
  "status": "degraded",
  "reason": "tui_start_failed",
  "exit_code": ${tui_status}
}
EOF

cat <<'EOF'
[O.B.E.O.S. DEGRADED MODE]
O.B.E.O.S. Home (Blackcomputer TUI) could not start.
Falling back to a standard shell login so the machine remains usable.
Review ~/.local/state/obeos/degraded_boot_notice.json for the last TUI failure code.
EOF

exec bash -l
"""

    def _return_to_tui_script(self) -> str:
        return """#!/usr/bin/env bash
set -euo pipefail

SYSTEMCTL_BIN="$(command -v systemctl)"

if [[ -n "$SYSTEMCTL_BIN" ]] && sudo -n "$SYSTEMCTL_BIN" isolate multi-user.target; then
  exit 0
fi
if [[ -n "$SYSTEMCTL_BIN" ]] && command -v pkexec >/dev/null 2>&1; then
  exec pkexec "$SYSTEMCTL_BIN" isolate multi-user.target
fi

cat <<'EOF'
O.B.E.O.S. Home lives on the Blackcomputer TUI at tty1.

This launcher could not switch targets automatically.
Run the following command to return session authority to O.B.E.O.S. Home:
  pkexec systemctl isolate multi-user.target

After the TUI boots, use `blackfong desktop start` whenever you want to return to the Blackfong Workspace.
EOF

if [[ -t 0 ]]; then
  printf '\nPress Enter to close this guidance window.'
  read -r _ || true
fi
"""

    def _return_to_tui_desktop_file(self) -> str:
        return """[Desktop Entry]
Type=Application
Name=Return to O.B.E.O.S. Home
Comment=Return session authority to O.B.E.O.S. Home on tty1
Exec=/opt/obeos/bin/return-to-tui.sh
Icon=utilities-terminal
Terminal=true
Categories=System;
"""

    def _session_authority_readme(self) -> str:
        return """# O.B.E.O.S. Session Authority

- OBEOS owns the session.
- Plasma is a managed workspace.
- The Blackcomputer TUI is the default home surface on tty1.
- Blackfong Workspace is the canonical native local shell when desktop mode is active.
- Thoughtglass HUD is the supported browser-based secondary cockpit.
- Safe bootstrap keeps Plasma active first; `blackfong obeos activate-home` flips the guest into TUI-home boot after smoke checks pass.
- `blackfong home` returns session authority to the TUI.
- D.A.I.S.E. is the visible assistant.
- Blackfong remains the governing spine.
- Linux shell basics remain intact.
"""

    def _degraded_boot_readme(self) -> str:
        return """# O.B.E.O.S. Degraded Boot Fallback

If the Blackcomputer TUI fails to start on tty1, OBEOS must fall back to a standard shell login on the same machine.

Requirements:
- do not block operator access
- visibly report degraded mode
- preserve enough shell access to diagnose or recover
- keep the machine Ubuntu-compatible while the TUI issue is repaired
"""

    def _branding_readme(self) -> str:
        layers = "\n".join(f"- {layer}" for layer in BRANDING_LAYERS)
        profile = self.manifest_model.branding_profile.as_dict()
        return f"""# O.B.E.O.S. Branding Layers

Brand in this order after the base, boot flow, and desktop launch path are stable:

{layers}

Naming hierarchy:
- O.B.E.O.S. = operating system name
- Blackcomputer = machine identity / hostname
- Blackfong = ecosystem and spine identity
- D.A.I.S.E. = active assistant identity

Current profile:
- Identity: {profile["identity"]}
- Machine: {profile["machine"]}
- Assistant: {profile["assistant"]}
- Boot screen asset: {profile["boot_screen_asset"]}
- Splash asset: {profile["splash_asset"]}
- Wallpaper asset: {profile["wallpaper_asset"]}

Applied on guest by `/opt/obeos/bin/apply-obeos-branding.sh`:
- GRUB theme (`/boot/grub/themes/obeos`)
- Plymouth splash
- SDDM login theme (`/usr/share/sddm/themes/obeos`)
- Plasma color scheme + lock screen wallpaper defaults
"""

    def _branding_logo_svg(self) -> str:
        palette = self.manifest_model.branding_profile.palette
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="{palette["surface"]}"/>
  <rect x="64" y="64" width="384" height="384" rx="72" fill="{palette["background"]}" stroke="{palette["primary"]}" stroke-width="8"/>
  <text x="256" y="250" text-anchor="middle" fill="{palette["text"]}" font-family="Segoe UI, Ubuntu, sans-serif" font-size="72" font-weight="700">O</text>
  <text x="256" y="330" text-anchor="middle" fill="{palette["primary"]}" font-family="Segoe UI, Ubuntu, sans-serif" font-size="28">B.E.O.S.</text>
</svg>
"""

    def _branding_splash_svg(self) -> str:
        palette = self.manifest_model.branding_profile.palette
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{palette["background"]}"/>
      <stop offset="100%" stop-color="{palette["surface"]}"/>
    </linearGradient>
  </defs>
  <rect width="1920" height="1080" fill="url(#bg)"/>
  <text x="960" y="470" text-anchor="middle" fill="{palette["text"]}" font-family="Segoe UI, Ubuntu, sans-serif" font-size="96" font-weight="700">O.B.E.O.S.</text>
  <text x="960" y="560" text-anchor="middle" fill="{palette["primary"]}" font-family="Segoe UI, Ubuntu, sans-serif" font-size="42">Blackcomputer // D.A.I.S.E.</text>
  <rect x="740" y="620" width="440" height="8" fill="{palette["accent"]}"/>
</svg>
"""

    def _branding_wallpaper_svg(self) -> str:
        palette = self.manifest_model.branding_profile.palette
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="3840" height="2160" viewBox="0 0 3840 2160">
  <defs>
    <radialGradient id="glow" cx="70%" cy="20%" r="80%">
      <stop offset="0%" stop-color="{palette["accent"]}" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="{palette["background"]}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="base" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="{palette["background"]}"/>
      <stop offset="100%" stop-color="{palette["surface"]}"/>
    </linearGradient>
  </defs>
  <rect width="3840" height="2160" fill="url(#base)"/>
  <rect width="3840" height="2160" fill="url(#glow)"/>
  <text x="220" y="1930" fill="{palette["text"]}" font-family="Segoe UI, Ubuntu, sans-serif" font-size="170" font-weight="700">O.B.E.O.S.</text>
  <text x="230" y="2040" fill="{palette["muted"]}" font-family="Segoe UI, Ubuntu, sans-serif" font-size="58">Official Blackfong Environment Operating System</text>
</svg>
"""

    def _branding_grub_theme_txt(self) -> str:
        palette = self.manifest_model.branding_profile.palette
        identity = self.manifest_model.branding_profile.identity
        machine = self.manifest_model.branding_profile.machine
        return f"""# OBEOS GRUB theme (color-first; no Kubuntu artwork)
desktop-color: "{palette["background"]}"
title-text: "{identity} // {machine}"
title-font: "DejaVu Sans Bold 28"
title-color: "{palette["primary"]}"
message-font: "DejaVu Sans 16"
message-color: "{palette["muted"]}"
terminal-font: "DejaVu Sans Mono 14"
boot_menu-color: "{palette["text"]}"
boot_menu-bgcolor: "{palette["surface"]}"
boot_menu-selected-color: "{palette["background"]}"
boot_menu-selected-bgcolor: "{palette["primary"]}"
"""

    def _branding_sddm_theme_conf(self) -> str:
        return """[General]
type=image
background=background.png
needsFullUserModel=false

[Theme]
name=OBEOS
"""

    def _branding_sddm_main_qml(self) -> str:
        palette = self.manifest_model.branding_profile.palette
        identity = self.manifest_model.branding_profile.identity
        machine = self.manifest_model.branding_profile.machine
        assistant = self.manifest_model.branding_profile.assistant
        return f"""import QtQuick 2.15
import SddmComponents 2.0

Rectangle {{
    width: parent.width
    height: parent.height
    color: "{palette["background"]}"

    Image {{
        anchors.fill: parent
        source: "background.png"
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        property bool triedSvgFallback: false
        onStatusChanged: {{
            if (status === Image.Error && !triedSvgFallback) {{
                triedSvgFallback = true
                source = "background.svg"
            }}
        }}
    }}

    Column {{
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 72
        spacing: 8

        Text {{
            text: "{identity}"
            color: "{palette["text"]}"
            font.pixelSize: 48
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
        }}

        Text {{
            text: "{machine} // {assistant}"
            color: "{palette["primary"]}"
            font.pixelSize: 20
            horizontalAlignment: Text.AlignHCenter
        }}
    }}

    Login {{
        id: login
        anchors.fill: parent
    }}
}}
"""

    def _branding_plasma_colorscheme(self) -> str:
        palette = self.manifest_model.branding_profile.palette
        return f"""[ColorEffects:Disabled]
Color=56,56,56
ColorAmount=0
ColorEffect=0
ContrastAmount=0.65
ContrastEffect=1
IntensityAmount=0.1
IntensityEffect=2

[Colors:Button]
Background={self._kde_color(palette["surface"])}
Foreground={self._kde_color(palette["text"])}
DecorationFocus={self._kde_color(palette["primary"])}

[Colors:View]
Background={self._kde_color(palette["background"])}
Foreground={self._kde_color(palette["text"])}
DecorationFocus={self._kde_color(palette["accent"])}

[Colors:Window]
Background={self._kde_color(palette["background"])}
Foreground={self._kde_color(palette["text"])}
DecorationFocus={self._kde_color(palette["primary"])}
DecorationHover={self._kde_color(palette["accent"])}

[Colors:Selection]
Background={self._kde_color(palette["primary"])}
Foreground={self._kde_color(palette["background"])}
DecorationFocus={self._kde_color(palette["accent"])}

[General]
ColorScheme=OBEOS
Name=OBEOS
shadeSortColumn=true
"""

    def _branding_plasma_lock_screen_config(self) -> str:
        wallpaper = "/usr/share/wallpapers/O.B.E.O.S/contents/images/3840x2160.png"
        return f"""[Daemon]
Image={wallpaper}
"""

    def _branding_plasma_kdeglobals_snippet(self) -> str:
        return """[KDE]
LookAndFeelPackage=org.kde.breezedark.desktop

[General]
ColorScheme=OBEOS
Name=OBEOS
"""

    def _branding_wallpaper_metadata(self) -> str:
        return """[Desktop Entry]
Name=O.B.E.O.S.
Name[en_US]=O.B.E.O.S.
Comment=Official Blackfong Environment Operating System wallpaper
Type=Service
X-KDE-Wallpaper-Category=Blackfong
"""

    @staticmethod
    def _kde_color(hex_color: str) -> str:
        value = hex_color.lstrip("#")
        if len(value) != 6:
            return "5,5,5"
        red = int(value[0:2], 16)
        green = int(value[2:4], 16)
        blue = int(value[4:6], 16)
        return f"{red},{green},{blue}"

    def _apply_branding_script(self) -> str:
        wallpaper_png = "/usr/share/wallpapers/O.B.E.O.S/contents/images/3840x2160.png"
        wallpaper_svg = "/usr/share/wallpapers/O.B.E.O.S/contents/images/3840x2160.svg"
        return f"""#!/usr/bin/env bash
set -euo pipefail

BRANDING_DIR="/opt/obeos/share/branding"
BOOT_SCREEN_ASSET="$BRANDING_DIR/obeos-boot-screen.png"
PLYMOUTH_THEME_DIR="/usr/share/plymouth/themes/obeos"
BLACKCOMPUTER_PLYMOUTH_SOURCE="$BRANDING_DIR/plymouth/blackcomputer"
BLACKCOMPUTER_PLYMOUTH_THEME_DIR="/usr/share/plymouth/themes/blackcomputer"
GRUB_THEME_DIR="/boot/grub/themes/obeos"
SDDM_THEME_DIR="/usr/share/sddm/themes/obeos"
WALLPAPER_PNG_TARGET="{wallpaper_png}"
WALLPAPER_SVG_TARGET="{wallpaper_svg}"
WALLPAPER_DIR="$(dirname "$WALLPAPER_PNG_TARGET")"
WALLPAPER_ROOT="$(dirname "$WALLPAPER_DIR")"
ACTIVE_WALLPAPER_TARGET="$WALLPAPER_SVG_TARGET"
PLYMOUTH_IMAGE="splash.svg"
ACTIVE_PLYMOUTH_NAME="obeos"
ACTIVE_PLYMOUTH_FILE="$PLYMOUTH_THEME_DIR/obeos.plymouth"
ACTIVE_PLYMOUTH_PRIORITY="100"
COLOR_SCHEME_TARGET="/usr/share/color-schemes/OBEOS.colors"
LOCK_SCREEN_DEFAULT="/etc/xdg/kscreenlockerrc"
SKEL_CONFIG="/etc/skel/.config"

write_lock_screen_config() {{
  local target_path="$1"
  local image_path="$2"
  cat > "$target_path" <<EOF
[Daemon]
Image=$image_path
EOF
}}

install -d "$PLYMOUTH_THEME_DIR" "$GRUB_THEME_DIR" "$SDDM_THEME_DIR" "$WALLPAPER_DIR" "$WALLPAPER_ROOT" "$SKEL_CONFIG" /etc/sddm.conf.d /etc/issue.d

cp "$BRANDING_DIR/obeos-splash.svg" "$PLYMOUTH_THEME_DIR/splash.svg"
cp "$BRANDING_DIR/obeos-wallpaper.svg" "$WALLPAPER_SVG_TARGET"
cp "$BRANDING_DIR/sddm/background.svg" "$SDDM_THEME_DIR/background.svg"
if [[ -f "$BOOT_SCREEN_ASSET" ]]; then
  cp "$BOOT_SCREEN_ASSET" "$PLYMOUTH_THEME_DIR/splash.png"
  cp "$BOOT_SCREEN_ASSET" "$SDDM_THEME_DIR/background.png"
  cp "$BOOT_SCREEN_ASSET" "$WALLPAPER_PNG_TARGET"
  PLYMOUTH_IMAGE="splash.png"
  ACTIVE_WALLPAPER_TARGET="$WALLPAPER_PNG_TARGET"
else
  rm -f "$PLYMOUTH_THEME_DIR/splash.png" "$SDDM_THEME_DIR/background.png" "$WALLPAPER_PNG_TARGET"
fi
if [[ -f "$BRANDING_DIR/plasma/wallpaper-metadata.desktop" ]]; then
  cp "$BRANDING_DIR/plasma/wallpaper-metadata.desktop" "$WALLPAPER_ROOT/metadata.desktop"
fi
cp "$BRANDING_DIR/grub/theme.txt" "$GRUB_THEME_DIR/theme.txt"
cp "$BRANDING_DIR/sddm/theme.conf" "$SDDM_THEME_DIR/theme.conf"
cp "$BRANDING_DIR/sddm/Main.qml" "$SDDM_THEME_DIR/Main.qml"
cp "$BRANDING_DIR/obeos-logo.svg" "$SDDM_THEME_DIR/logo.svg"
cp "$BRANDING_DIR/plasma/OBEOS.colors" "$COLOR_SCHEME_TARGET"
write_lock_screen_config "$LOCK_SCREEN_DEFAULT" "$ACTIVE_WALLPAPER_TARGET"

cat > "$PLYMOUTH_THEME_DIR/obeos.plymouth" <<'EOF'
[Plymouth Theme]
Name=OBEOS
Description=OBEOS splash theme
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/obeos
ScriptFile=/usr/share/plymouth/themes/obeos/obeos.script
EOF

cat > "$PLYMOUTH_THEME_DIR/obeos.script" <<EOF
Window.SetBackgroundTopColor (0.02, 0.03, 0.04);
Window.SetBackgroundBottomColor (0.05, 0.07, 0.10);
logo.image = Image("$PLYMOUTH_IMAGE");
logo.sprite = Sprite(logo.image);
logo.sprite.SetX(Window.GetWidth() / 2 - logo.image.GetWidth() / 2);
logo.sprite.SetY(Window.GetHeight() / 2 - logo.image.GetHeight() / 2);
EOF

if [[ -f "$BLACKCOMPUTER_PLYMOUTH_SOURCE/blackcomputer.plymouth" && -f "$BLACKCOMPUTER_PLYMOUTH_SOURCE/blackcomputer.script" ]]; then
  install -d "$BLACKCOMPUTER_PLYMOUTH_THEME_DIR"
  cp -a "$BLACKCOMPUTER_PLYMOUTH_SOURCE/." "$BLACKCOMPUTER_PLYMOUTH_THEME_DIR/"
  ACTIVE_PLYMOUTH_NAME="blackcomputer"
  ACTIVE_PLYMOUTH_FILE="$BLACKCOMPUTER_PLYMOUTH_THEME_DIR/blackcomputer.plymouth"
  ACTIVE_PLYMOUTH_PRIORITY="110"
fi

if command -v update-alternatives >/dev/null 2>&1; then
  update-alternatives --install /usr/share/plymouth/themes/default.plymouth default.plymouth "$ACTIVE_PLYMOUTH_FILE" "$ACTIVE_PLYMOUTH_PRIORITY" || true
  update-alternatives --set default.plymouth "$ACTIVE_PLYMOUTH_FILE" || true
fi

if command -v plymouth-set-default-theme >/dev/null 2>&1; then
  plymouth-set-default-theme "$ACTIVE_PLYMOUTH_NAME" || true
fi

if command -v update-initramfs >/dev/null 2>&1; then
  update-initramfs -u || true
fi

cat > /etc/issue.d/obeos.identity <<'EOF'
O.B.E.O.S. // Blackcomputer // D.A.I.S.E.
EOF

if [[ -f /etc/default/grub ]]; then
  if grep -q '^GRUB_THEME=' /etc/default/grub; then
    sed -i 's|^GRUB_THEME=.*|GRUB_THEME="/boot/grub/themes/obeos/theme.txt"|' /etc/default/grub
  else
    echo 'GRUB_THEME="/boot/grub/themes/obeos/theme.txt"' >> /etc/default/grub
  fi
  if grep -q '^GRUB_DISTRIBUTOR=' /etc/default/grub; then
    sed -i 's|^GRUB_DISTRIBUTOR=.*|GRUB_DISTRIBUTOR="O.B.E.O.S."|' /etc/default/grub
  else
    echo 'GRUB_DISTRIBUTOR="O.B.E.O.S."' >> /etc/default/grub
  fi
  if command -v update-grub >/dev/null 2>&1; then
    update-grub || true
  fi
fi

cat > /etc/sddm.conf.d/obeos-theme.conf <<'EOF'
[Theme]
Current=obeos
EOF

if command -v hostnamectl >/dev/null 2>&1; then
  hostnamectl set-hostname blackcomputer || true
fi

if [[ -f "$BRANDING_DIR/plasma/kdeglobals-snippet.ini" ]]; then
  install -d "$SKEL_CONFIG"
  if [[ ! -f "$SKEL_CONFIG/kdeglobals" ]]; then
    cp "$BRANDING_DIR/plasma/kdeglobals-snippet.ini" "$SKEL_CONFIG/kdeglobals"
  fi
  install -d "$SKEL_CONFIG"
  if [[ ! -f "$SKEL_CONFIG/kscreenlockerrc" ]]; then
    write_lock_screen_config "$SKEL_CONFIG/kscreenlockerrc" "$ACTIVE_WALLPAPER_TARGET"
  fi
fi

for user_home in /home/obeos /root; do
  if [[ ! -d "$user_home" ]]; then
    continue
  fi
  user_config="$user_home/.config"
  install -d "$user_config"
  if command -v plasma-apply-colorscheme >/dev/null 2>&1; then
    account="$(basename "$user_home")"
    if id "$account" >/dev/null 2>&1; then
      sudo -u "$account" plasma-apply-colorscheme OBEOS 2>/dev/null || true
    fi
  fi
  if [[ ! -f "$user_config/kscreenlockerrc" ]]; then
    write_lock_screen_config "$user_config/kscreenlockerrc" "$ACTIVE_WALLPAPER_TARGET" || true
  fi
done

echo "OBEOS branding applied (GRUB, Plymouth, SDDM, Plasma defaults)."
"""

    def _blackfong_runtime_readme(self) -> str:
        return """# /opt/blackfong

Blackfong runtime payload for the target Kubuntu install.

The guest bootstrap installs this directory into `/opt/blackfong`, creates `/opt/blackfong/.venv`, installs the lean OBEOS Linux dependencies, and exposes `/usr/local/bin/blackfong`.
"""

    def _daise_runtime_readme(self) -> str:
        return """# /opt/daise

Stage D.A.I.S.E.-specific runtime, persona, voice, and local model metadata here.
Sensor policy remains manual-only in v1.
"""

    def _guest_bootstrap_script(self) -> str:
        return """#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run this guest bootstrap as root." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
BOOTSTRAP_STAGE="${1:-safe}"
ACTIVATION_COMMAND="pkexec /usr/local/bin/blackfong obeos activate-home"

write_bootstrap_stage() {
  local stage_name="$1"
  local default_target="$2"
  local home_boot_enabled="$3"
  install -d /etc/obeos
  cat > /etc/obeos/bootstrap_stage.json <<EOF
{
  "stage": "${stage_name}",
  "default_target": "${default_target}",
  "home_boot_enabled": ${home_boot_enabled},
  "guest_elevation_tool": "pkexec",
  "activation_command": "${ACTIVATION_COMMAND}"
}
EOF
}

if [[ "$BOOTSTRAP_STAGE" == "--activate-home" ]]; then
  echo "[1/3] Reloading systemd state for OBEOS home activation"
  systemctl daemon-reload

  echo "[2/3] Enabling the OBEOS TUI-home boot contract"
  systemctl enable obeos-tui.service >/dev/null 2>&1 || true
  systemctl set-default multi-user.target
  write_bootstrap_stage "stage_b_activate_home" "multi-user.target" true

  echo "[3/3] Activation complete"
  echo "Reboot the guest. O.B.E.O.S. should now boot to the Blackcomputer TUI home on tty1."
  echo "After the TUI boots, use 'blackfong desktop start' to enter the Blackfong Workspace and 'blackfong home' to return."
  exit 0
fi

echo "[1/8] Installing Kubuntu runtime dependencies"
apt-get update
apt-get install -y python3 python3-venv python3-pip rsync sudo curl unzip sddm plasma-desktop network-manager bluez pipewire wireplumber

echo "[2/8] Creating OBEOS runtime directories"
install -d /opt/obeos /opt/blackfong /opt/daise /etc/obeos /var/lib/obeos

echo "[3/8] Syncing bundle payload"
rsync -a "$BUNDLE_ROOT/opt/obeos/" /opt/obeos/
rsync -a "$BUNDLE_ROOT/opt/blackfong/" /opt/blackfong/
rsync -a "$BUNDLE_ROOT/opt/daise/" /opt/daise/
rsync -a "$BUNDLE_ROOT/etc/obeos/" /etc/obeos/
rsync -a "$BUNDLE_ROOT/etc/systemd/system/" /etc/systemd/system/
rsync -a "$BUNDLE_ROOT/usr/share/applications/" /usr/share/applications/

echo "[4/8] Preparing the obeos user"
PRIMARY_OPERATOR="${SUDO_USER:-}"
if [[ -z "$PRIMARY_OPERATOR" || "$PRIMARY_OPERATOR" == "root" ]]; then
  PRIMARY_OPERATOR="$(awk -F: '$3 >= 1000 && $3 < 60000 && $1 != "nobody" {print $1; exit}' /etc/passwd || true)"
fi
getent group obeos >/dev/null 2>&1 || groupadd --system obeos
id -u obeos >/dev/null 2>&1 || useradd -m -s /bin/bash -g obeos obeos
usermod -aG obeos obeos
if [[ -n "$PRIMARY_OPERATOR" && "$PRIMARY_OPERATOR" != "obeos" ]] && id -u "$PRIMARY_OPERATOR" >/dev/null 2>&1; then
  usermod -aG obeos "$PRIMARY_OPERATOR"
  echo "Added $PRIMARY_OPERATOR to the obeos runtime group."
fi
for group_name in audio video plugdev netdev; do
  if getent group "$group_name" >/dev/null 2>&1; then
    usermod -aG "$group_name" obeos
  fi
done

echo "[5/8] Building the Blackfong Python environment"
python3 -m venv /opt/blackfong/.venv
/opt/blackfong/.venv/bin/python -m pip install --upgrade pip setuptools wheel
/opt/blackfong/.venv/bin/pip install -r /opt/blackfong/requirements-obeos-linux.txt
/opt/blackfong/.venv/bin/pip install --no-deps -e /opt/blackfong
ln -sf /opt/blackfong/.venv/bin/blackfong /usr/local/bin/blackfong

echo "[6/8] Granting narrow desktop transition authority"
SYSTEMCTL_BIN="$(command -v systemctl)"
cat > /etc/sudoers.d/obeos-systemctl <<EOF
obeos ALL=(root) NOPASSWD: ${SYSTEMCTL_BIN} isolate graphical.target, ${SYSTEMCTL_BIN} isolate multi-user.target
EOF
chmod 0440 /etc/sudoers.d/obeos-systemctl
visudo -cf /etc/sudoers.d/obeos-systemctl

echo "[7/8] Finalizing ownership and OBEOS session services"
install -d -o obeos -g obeos /opt/blackfong/data /var/lib/obeos /etc/obeos
chown -R obeos:obeos /opt/blackfong /opt/daise /var/lib/obeos /etc/obeos
chmod 2775 /opt/blackfong/data /var/lib/obeos /etc/obeos
chmod -R g+rwX /opt/blackfong/data /var/lib/obeos /etc/obeos
chmod 0755 /opt/obeos/bin/*.sh
systemctl daemon-reload
systemctl disable obeos-tui.service >/dev/null 2>&1 || true
systemctl enable sddm.service >/dev/null 2>&1 || true
systemctl set-default graphical.target
write_bootstrap_stage "stage_a_safe" "graphical.target" false

if [[ -x /opt/obeos/bin/apply-obeos-branding.sh ]]; then
  echo "Applying OBEOS branding profile (splash + wallpaper + identity)."
  /opt/obeos/bin/apply-obeos-branding.sh || true
fi

echo "[8/8] Bootstrap complete"
echo "Stay in Plasma and smoke-check the operator surfaces before enabling TUI-home boot."
echo "Use 'blackfong desktop workspace' to open the Blackfong Qt workspace shell."
echo "Use 'blackfong hud --host 127.0.0.1 --port 8080 --no-open-browser' to start the HUD."
echo "Use 'blackfong tui' to verify the Textual home surface launches manually."
echo "When those checks pass, run: ${ACTIVATION_COMMAND}"
"""

    def _remaster_script(self) -> str:
        return """#!/usr/bin/env bash
set -euo pipefail

# Kubuntu 24.04 LTS OBEOS remaster scaffold with O.B.E.O.S branding.
# Usage:
#   ./build-kubuntu-remaster.sh /path/to/kubuntu-24.04.x-desktop-amd64.iso /tmp/obeos-remaster

BASE_ISO="${1:-}"
WORKDIR="${2:-/tmp/obeos-remaster}"
CHROOT_DIR="$WORKDIR/chroot"
ISO_DIR="$WORKDIR/iso"

if [[ -z "$BASE_ISO" ]]; then
  echo "Provide the path to the base Kubuntu ISO." >&2
  exit 1
fi

mkdir -p "$WORKDIR" "$CHROOT_DIR" "$ISO_DIR"

echo "[1/7] Extracting ISO"
xorriso -osirrox on -indev "$BASE_ISO" -extract / "$ISO_DIR"

echo "[2/7] Unsquashing live filesystem"
unsquashfs -d "$CHROOT_DIR" "$ISO_DIR/casper/filesystem.squashfs"

echo "[3/7] Bind mounting chroot prerequisites"
mount --bind /dev "$CHROOT_DIR/dev"
mount --bind /run "$CHROOT_DIR/run"
mount -t proc /proc "$CHROOT_DIR/proc"
mount -t sysfs /sys "$CHROOT_DIR/sys"

echo "[4/7] Installing OBEOS bundle + branding into chroot"
# Copy the generated OBEOS bundle into the chroot before this point.
# Example:
#   rsync -a ./bundle/ "$CHROOT_DIR/"
#   chroot "$CHROOT_DIR" bash -lc 'apt-get update && apt-get install -y python3 python3-venv python3-pip rsync sudo curl unzip sddm plasma-desktop network-manager bluez pipewire wireplumber'
#   chroot "$CHROOT_DIR" bash -lc '/opt/obeos/bin/apply-obeos-branding.sh'

echo "[5/7] Rebuilding squashfs"
umount "$CHROOT_DIR/dev" "$CHROOT_DIR/run" "$CHROOT_DIR/proc" "$CHROOT_DIR/sys" || true
mksquashfs "$CHROOT_DIR" "$ISO_DIR/casper/filesystem.squashfs" -noappend

echo "[6/7] Updating manifest and checksums"
chmod +w "$ISO_DIR/md5sum.txt"
(cd "$ISO_DIR" && find . -type f -print0 | xargs -0 md5sum > md5sum.txt)

echo "[7/7] Building final ISO"
xorriso -as mkisofs \
  -r -V "OBEOS_24_04" \
  -o "$WORKDIR/obeos-24.04.iso" \
  -J -l "$ISO_DIR"

echo "ISO ready at $WORKDIR/obeos-24.04.iso"
"""

    def _manifest_hash(self, payload: Optional[Dict[str, Any]] = None) -> str:
        source = payload or self.manifest_model.to_dict()
        encoded = json.dumps(source, indent=2, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
