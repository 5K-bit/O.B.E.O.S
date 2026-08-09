from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .config import paths
from .external_integrations import ExternalIntegrationRegistry
from .installer_plan import InstallerPlanService
from .obeos_linux import OBEOSLinuxPlatformService
from .service_registry import get_ui_services


class VerificationService:
    """Structured validation harness for graphical-first OBEOS."""

    def __init__(self, *, workspace_root: Optional[Path] = None, services: Any = None) -> None:
        self.workspace_root = Path(workspace_root or paths.root)
        self.services = services or get_ui_services()
        self.report_root = self.workspace_root / "data" / "verification"
        self.integration_registry = getattr(self.services, "integration_registry", None) or ExternalIntegrationRegistry(workspace_root=self.workspace_root)

    def automated(self) -> Dict[str, Any]:
        completed, python_command, launcher_attempts = self._run_python_module("pytest", ["-q"], cwd=self.workspace_root)
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        warnings = [line for line in stdout.splitlines() if "warning" in line.lower()]
        return {
            "schema_version": "1.0",
            "mode": "automated",
            "status": "passed" if completed.returncode == 0 else "failed",
            "command_line": completed.args,
            "python_command": python_command,
            "launcher_attempts": launcher_attempts,
            "return_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "warnings": warnings,
            "summary": stdout.splitlines()[-1] if stdout else "",
        }

    def surfaces(self) -> Dict[str, Any]:
        hud_probe = self._probe_hud()
        workspace_probe = self._probe_workspace()
        tiny_probe = self._probe_tinyprogrammer()
        cognition_probe = self._probe_cognition()
        shortcuts_probe = self._probe_workspace_shortcuts()
        checks = {
            "hud": hud_probe,
            "workspace": workspace_probe,
            "tinyprogrammer": tiny_probe,
            "thoughtglass": cognition_probe,
            "workspace_shortcuts": shortcuts_probe,
        }
        status = "passed" if all(item.get("status") == "passed" for item in checks.values()) else "degraded"
        return {
            "schema_version": "1.0",
            "mode": "surfaces",
            "status": status,
            "checks": checks,
        }

    def integrations(self) -> Dict[str, Any]:
        registry = self.integration_registry.list_integrations()
        integrations = registry.get("integrations", [])
        checks = {
            "codingagent_external": self.integration_registry.describe("codingagent_external"),
            "redamon_mode": self.integration_registry.describe("redamon_mode"),
            "dreamgraph_reference": self.integration_registry.describe("dreamgraph_reference"),
            "ai_monitor": self.integration_registry.describe("ai_monitor"),
        }
        checks["redamon_mode"]["operator_approval_required"] = True
        checks["dreamgraph_reference"]["obeos_guardrail"] = "visual_reference_only"
        status = "passed" if any(item.get("available") for item in integrations) else "degraded"
        return {
            "schema_version": "1.0",
            "mode": "integrations",
            "status": status,
            "registry": registry,
            "checks": checks,
        }

    def bundle(self) -> Dict[str, Any]:
        """Export diagnostics zip: verification, layout, logs, session."""
        from .operator_session import load_operator_session
        from .workspace_shortcuts import shortcuts_json_for_hud

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bundle_dir = self.report_root / f"bundle-{timestamp}"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "generated_at": datetime.now().isoformat(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "workspace_root": str(self.workspace_root),
            "surfaces": self.surfaces(),
            "shortcuts": shortcuts_json_for_hud(),
            "operator_session": load_operator_session(),
        }
        (bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        tiny_log = paths.data_root / "logs" / "tinyprogrammer.log"
        if tiny_log.exists():
            shutil.copy2(tiny_log, bundle_dir / "tinyprogrammer.log")
        zip_path = self.report_root / f"obeos-diagnostics-{timestamp}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in bundle_dir.rglob("*"):
                if file_path.is_file():
                    archive.write(file_path, arcname=file_path.relative_to(bundle_dir).as_posix())
        return {
            "schema_version": "1.0",
            "mode": "bundle",
            "status": "passed",
            "bundle_path": str(zip_path),
            "files": [str(path.relative_to(self.report_root)) for path in bundle_dir.iterdir()],
        }

    def full(self) -> Dict[str, Any]:
        automated = self.automated()
        surfaces = self.surfaces()
        integrations = self.integrations()
        report = {
            "schema_version": "1.0",
            "mode": "full",
            "generated_at": time.time(),
            "generated_at_iso": datetime.now().isoformat(),
            "workspace_root": str(self.workspace_root),
            "environment": {
                "platform": sys.platform,
                "python": sys.version.split()[0],
            },
            "results": {
                "automated": automated,
                "surfaces": surfaces,
                "integrations": integrations,
            },
            "status": self._aggregate_status(automated, surfaces, integrations),
            "blockers": self._blockers(automated, surfaces, integrations),
            "recommended_next_actions": self._next_actions(automated, surfaces, integrations),
        }
        saved = self._write_report("full", report)
        report["report_path"] = str(saved)
        return report

    def release_evidence(self, *, profile: str = "amd64-pc", include_automated: bool = False) -> Dict[str, Any]:
        installer_service = InstallerPlanService()
        installer_plan = installer_service.build_plan(profile)
        installer_status = installer_service.current_status()
        installer_validation = installer_service.validate_plan(installer_plan)

        platform_service = OBEOSLinuxPlatformService(workspace_root=self.workspace_root)
        manifest = platform_service.manifest()
        surfaces = self.surfaces()
        integrations = self.integrations()
        shotset = self._current_shotset_status()
        automated = self.automated() if include_automated else {
            "schema_version": "1.0",
            "mode": "automated",
            "status": "skipped",
            "reason": "not_requested",
        }

        timestamp = datetime.now()
        report = {
            "schema_version": "1.0",
            "mode": "release_evidence",
            "generated_at": time.time(),
            "generated_at_iso": timestamp.isoformat(),
            "workspace_root": str(self.workspace_root),
            "profile": profile,
            "installer_status": installer_status,
            "installer_validation": installer_validation,
            "manifest": manifest,
            "shotset": shotset,
            "verification": {
                "automated": automated,
                "surfaces": surfaces,
                "integrations": integrations,
            },
            "status": self._release_evidence_status(
                installer_status=installer_status,
                installer_validation=installer_validation,
                surfaces=surfaces,
                integrations=integrations,
                automated=automated,
            ),
            "blockers": self._release_evidence_blockers(
                installer_status=installer_status,
                installer_validation=installer_validation,
                surfaces=surfaces,
                integrations=integrations,
                automated=automated,
                shotset=shotset,
            ),
            "recommended_next_actions": self._release_evidence_actions(
                installer_status=installer_status,
                installer_validation=installer_validation,
                surfaces=surfaces,
                integrations=integrations,
                automated=automated,
                shotset=shotset,
            ),
        }
        json_path = self._write_report("release-evidence", report)
        markdown_text = self._render_release_evidence_markdown(report)
        markdown_dir = self.workspace_root / "docs"
        markdown_dir.mkdir(parents=True, exist_ok=True)
        latest_markdown = markdown_dir / "release-evidence-latest.md"
        latest_markdown.write_text(markdown_text, encoding="utf-8")
        report["report_path"] = str(json_path)
        report["markdown_path"] = str(latest_markdown)
        latest_json = self.report_root / "latest-release-evidence.json"
        latest_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    def shotset(self, *, output_dir: Optional[Path] = None) -> Dict[str, Any]:
        output_dir = Path(output_dir or (self.workspace_root / "docs" / "release-shots"))
        output_dir.mkdir(parents=True, exist_ok=True)
        captures = self._capture_workspace_release_shots(output_dir)
        manifest = {
            "schema_version": "1.0",
            "mode": "shotset",
            "generated_at_iso": datetime.now().isoformat(),
            "workspace_root": str(self.workspace_root),
            "output_dir": str(output_dir),
            "captures": captures,
        }
        manifest_path = output_dir / "shotset-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return {
            **manifest,
            "status": "passed" if captures else "failed",
            "manifest_path": str(manifest_path),
        }

    def _current_shotset_status(self) -> Dict[str, Any]:
        manifest_path = self.workspace_root / "docs" / "release-shots" / "shotset-manifest.json"
        if not manifest_path.exists():
            return {
                "available": False,
                "capture_count": 0,
                "manifest_path": str(manifest_path),
            }
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            return {
                "available": False,
                "capture_count": 0,
                "manifest_path": str(manifest_path),
                "status": "unreadable",
            }
        captures = payload.get("captures", [])
        return {
            "available": True,
            "capture_count": len(captures) if isinstance(captures, list) else 0,
            "manifest_path": str(manifest_path),
            "output_dir": str(payload.get("output_dir") or manifest_path.parent),
        }

    def _run_command(self, command_line: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command_line, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8")

    def _capture_workspace_release_shots(self, output_dir: Path) -> list[Dict[str, Any]]:
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication
        from desktop_shell import MainWindow, apply_workspace_theme

        app_args = ["blackfong-release-shotset"]
        explicit_platform = os.getenv("BLACKFONG_SHOTSET_PLATFORM", "").strip()
        if explicit_platform:
            app_args.extend(["-platform", explicit_platform])
        elif platform.system().lower() != "windows":
            app_args.extend(["-platform", "offscreen"])
        app = QApplication.instance() or QApplication(app_args)
        apply_workspace_theme(app)
        screenshot_font = QFont("Arial")
        screenshot_font.setPointSize(10)
        app.setFont(screenshot_font)
        app.setStyleSheet(
            app.styleSheet()
            + """
            QWidget, QLabel, QPushButton, QToolButton, QLineEdit, QComboBox, QTextEdit, QTabBar::tab {
                font-family: Arial, "Segoe UI", sans-serif;
            }
            """
        )
        window = MainWindow(start_timer=False, initial_refresh=False)
        window.show()
        app.processEvents()

        captures: list[Dict[str, Any]] = []

        def capture_current(name: str, caption: str) -> None:
            target = output_dir / name
            pixmap = window.grab()
            pixmap.save(str(target))
            captures.append(
                {
                    "name": name,
                    "caption": caption,
                    "path": str(target),
                }
            )

        window.workbench_tabs.setCurrentWidget(window.brain_pane)
        window.brain_pane.refresh()
        app.processEvents()
        capture_current("workspace-thoughtglass-home.png", "Blackfong Workspace on Thoughtglass Home")

        window.workbench_tabs.setCurrentWidget(window.memory_chamber_pane)
        window.memory_chamber_pane.refresh()
        app.processEvents()
        capture_current("workspace-memory-chamber.png", "Blackfong Workspace on Memory Chamber")

        window.workbench_tabs.setCurrentWidget(window.chat_pane)
        app.processEvents()
        capture_current("workspace-daise-chat.png", "Blackfong Workspace on D.A.I.S.E. chat")

        window.close()
        app.processEvents()
        return captures

    def _python_command_candidates(self) -> list[list[str]]:
        candidates: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()

        def add(command: list[str]) -> None:
            key = tuple(command)
            if key in seen:
                return
            seen.add(key)
            candidates.append(command)

        executable = str(sys.executable or "").strip()
        if executable and self._path_exists(Path(executable)):
            add([executable])

        local_windows = self.workspace_root / ".venv" / "Scripts" / "python.exe"
        if self._path_exists(local_windows):
            add([str(local_windows)])

        local_posix = self.workspace_root / ".venv" / "bin" / "python"
        if self._path_exists(local_posix):
            add([str(local_posix)])

        py_launcher = shutil.which("py")
        if py_launcher:
            add([py_launcher, "-3.11"])
            add([py_launcher])

        for name in ("python", "python3"):
            resolved = shutil.which(name)
            if resolved:
                add([resolved])

        return candidates

    @staticmethod
    def _path_exists(path: Path) -> bool:
        try:
            return path.exists()
        except OSError:
            return False

    @staticmethod
    def _is_python_launcher_failure(completed: subprocess.CompletedProcess[str]) -> bool:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        combined = f"{stdout}\n{stderr}"
        return (
            completed.returncode != 0
            and (
                "Unable to create process using" in combined
                or "Access is denied" in combined
            )
        )

    def _run_python_module(
        self,
        module: str,
        arguments: list[str],
        *,
        cwd: Path,
    ) -> tuple[subprocess.CompletedProcess[str], list[str], list[Dict[str, Any]]]:
        attempts: list[Dict[str, Any]] = []
        candidates = self._python_command_candidates()
        if not candidates:
            completed = subprocess.CompletedProcess(
                args=["<no-python>", "-m", module, *arguments],
                returncode=1,
                stdout="",
                stderr="No usable Python launcher was found for OBEOS verification.",
            )
            return completed, [], attempts

        for candidate in candidates:
            command_line = [*candidate, "-m", module, *arguments]
            try:
                completed = self._run_command(command_line, cwd=cwd)
            except OSError as exc:
                attempts.append(
                    {
                        "command_line": command_line,
                        "status": "launcher_error",
                        "error": str(exc),
                    }
                )
                continue
            if self._is_python_launcher_failure(completed):
                attempts.append(
                    {
                        "command_line": command_line,
                        "status": "launcher_failed",
                        "return_code": completed.returncode,
                        "stderr": (completed.stderr or "").strip(),
                    }
                )
                continue
            return completed, candidate, attempts

        completed = subprocess.CompletedProcess(
            args=[*candidates[0], "-m", module, *arguments],
            returncode=1,
            stdout="",
            stderr="All Python launcher candidates failed before the module could run.",
        )
        return completed, candidates[0], attempts

    def _probe_hud(self) -> Dict[str, Any]:
        from .operational_interfaces import WebDashboard

        dashboard = WebDashboard(host="127.0.0.1", port=8099, memory_service=self.services.memory_service, orchestrator_instance=None)
        client = dashboard.app.test_client()
        index = client.get("/")
        commands = client.get("/api/commands")
        shortcuts = client.get("/api/workspace/shortcuts")
        summary = client.get("/api/operator/summary")
        security = client.get("/api/integrations")
        memory_chamber = client.get("/api/memory-chamber/summary")
        ok = all(
            response.status_code == 200
            for response in (index, commands, shortcuts, summary, security, memory_chamber)
        )
        return {
            "status": "passed" if ok else "failed",
            "index_status": index.status_code,
            "commands_status": commands.status_code,
            "shortcuts_status": shortcuts.status_code,
            "summary_status": summary.status_code,
            "integrations_status": security.status_code,
            "memory_chamber_status": memory_chamber.status_code,
        }

    def _probe_workspace(self) -> Dict[str, Any]:
        try:
            from desktop_shell import workspace_probe
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}
        probe = workspace_probe()
        status = "passed" if probe.get("success") and probe.get("thoughtglass_home_default") and probe.get("memory_chamber_available") else "failed"
        return {"status": status, **probe}

    def _probe_tinyprogrammer(self) -> Dict[str, Any]:
        status = self.services.tinyprogrammer.get_status(require_stream=True)
        process = self.services.tinyprogrammer.process_status(require_stream=True)
        log_tail = self.services.tinyprogrammer.get_log_tail(lines=12)
        crash_loop = bool(process.get("return_code") not in (None, 0) and process.get("ownership") == "managed")
        return {
            "status": "passed" if "state" in status else "failed",
            "state": status.get("state"),
            "online": status.get("online"),
            "stream_available": status.get("stream_available"),
            "native_window_state": process.get("native_window_state"),
            "management_state": process.get("management_state"),
            "crash_loop_detected": crash_loop,
            "log_path": log_tail.get("log_path"),
            "retry_count": status.get("retry_count"),
            "max_retries": status.get("max_retries"),
            "last_error_code": status.get("last_error_code"),
            "startup_ready_state": "degraded" if status.get("last_error_code") else "warming" if not status.get("online") else "active",
        }

    def _probe_workspace_shortcuts(self) -> Dict[str, Any]:
        try:
            from .workspace_shortcuts import WORKSPACE_SHORTCUTS, shortcuts_json_for_hud
            from .thoughtglass_scene_3d import OPENGL_AVAILABLE

            return {
                "status": "passed",
                "shortcut_count": len(WORKSPACE_SHORTCUTS),
                "hud_export_count": len(shortcuts_json_for_hud()),
                "thoughtglass_3d_opengl": OPENGL_AVAILABLE,
            }
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def _probe_cognition(self) -> Dict[str, Any]:
        snapshot = self.services.cognition_service.build_snapshot(
            layer="live_brain",
            focus={"layer": "live_brain", "mode": "thoughtglass"},
            request_states={},
            request_threads={},
            backend_bootstrap={"status": "idle", "started_at": None, "completed_at": None, "error": None},
            sensor_mode="off",
            voice_status={"active": False, "message": "Verification does not own the voice loop."},
        )
        nodes = snapshot.get("nodes", [])
        return {
            "status": "passed" if nodes and snapshot.get("trace_ribbon") else "failed",
            "active_layer": snapshot.get("active_layer"),
            "node_count": len(nodes),
            "trace_ribbon_present": bool(snapshot.get("trace_ribbon")),
            "fault_count": len(snapshot.get("faults", [])),
            "startup_state": (snapshot.get("overlays", {}) or {}).get("startup", {}).get("overall_state"),
            "memory_chamber_overlay": bool((snapshot.get("overlays", {}) or {}).get("memory_chamber")),
        }

    def _aggregate_status(self, automated: Dict[str, Any], surfaces: Dict[str, Any], integrations: Dict[str, Any]) -> str:
        if automated.get("status") == "failed":
            return "failed"
        if any(item.get("status") == "failed" for item in surfaces.get("checks", {}).values()):
            return "degraded"
        if integrations.get("status") != "passed":
            return "degraded"
        return "passed"

    @staticmethod
    def _blockers(automated: Dict[str, Any], surfaces: Dict[str, Any], integrations: Dict[str, Any]) -> list[str]:
        blockers: list[str] = []
        if automated.get("status") == "failed":
            blockers.append("Automated tests are failing.")
        if surfaces.get("checks", {}).get("workspace", {}).get("status") == "failed":
            blockers.append("PySide6 workspace probe is not healthy.")
        if surfaces.get("checks", {}).get("tinyprogrammer", {}).get("crash_loop_detected"):
            blockers.append("TinyProgrammer looks crash-looped under managed launch.")
        if not integrations.get("checks", {}).get("redamon_mode", {}).get("available"):
            blockers.append("RedAmon is not available from the tacklebox root.")
        return blockers

    @staticmethod
    def _next_actions(automated: Dict[str, Any], surfaces: Dict[str, Any], integrations: Dict[str, Any]) -> list[str]:
        actions: list[str] = []
        if automated.get("status") == "failed":
            actions.append("Inspect pytest failures before shipping a new OBEOS bundle.")
        if surfaces.get("checks", {}).get("workspace", {}).get("status") == "failed":
            actions.append("Repair the PySide6 workspace launch/import path before treating it as the local operator home.")
        if surfaces.get("checks", {}).get("tinyprogrammer", {}).get("crash_loop_detected"):
            actions.append("Investigate TinyProgrammer managed boot stability before another VM update.")
        if integrations.get("checks", {}).get("redamon_mode", {}).get("available"):
            actions.append("Use RedAmon only through the review-and-launch workflow, never as casual chat authority.")
        return actions

    @staticmethod
    def _release_evidence_status(
        *,
        installer_status: Dict[str, Any],
        installer_validation: Dict[str, Any],
        surfaces: Dict[str, Any],
        integrations: Dict[str, Any],
        automated: Dict[str, Any],
    ) -> str:
        if not installer_validation.get("ok"):
            return "blocked"
        if surfaces.get("status") == "failed":
            return "blocked"
        if automated.get("status") == "failed":
            return "blocked"
        if not installer_status.get("shipping_ready"):
            return "in_progress"
        if integrations.get("status") != "passed":
            return "degraded"
        if surfaces.get("status") != "passed":
            return "degraded"
        return "ready_for_trial"

    @staticmethod
    def _release_evidence_blockers(
        *,
        installer_status: Dict[str, Any],
        installer_validation: Dict[str, Any],
        surfaces: Dict[str, Any],
        integrations: Dict[str, Any],
        automated: Dict[str, Any],
        shotset: Dict[str, Any],
    ) -> list[str]:
        blockers: list[str] = []
        if not installer_validation.get("ok"):
            blockers.append("Installer plan validation is failing.")
        if not installer_status.get("shipping_ready"):
            blockers.append(installer_status.get("promotion_gate") or "External installer is not shipping-ready yet.")
        if surfaces.get("status") != "passed":
            blockers.append("Surface verification is not fully passed yet.")
        if automated.get("status") == "failed":
            blockers.append("Automated tests are failing.")
        if integrations.get("status") != "passed":
            blockers.append("Optional integration verification is degraded.")
        return blockers

    @staticmethod
    def _release_evidence_actions(
        *,
        installer_status: Dict[str, Any],
        installer_validation: Dict[str, Any],
        surfaces: Dict[str, Any],
        integrations: Dict[str, Any],
        automated: Dict[str, Any],
        shotset: Dict[str, Any],
    ) -> list[str]:
        actions: list[str] = []
        if not installer_validation.get("ok"):
            actions.append("Repair the installer manifest layout before another packaging pass.")
        if not installer_status.get("shipping_ready"):
            actions.append("Keep using the Kubuntu materialize/remaster path as shipping truth until the external installer is Plasma-aligned.")
        if surfaces.get("status") != "passed":
            actions.append("Capture and resolve any degraded surface checks before calling the build packaging-ready.")
        if automated.get("status") == "failed":
            actions.append("Address the failing automated tests or rerun release evidence without automated mode if you only need a fast doctrine snapshot.")
        if integrations.get("status") != "passed":
            actions.append("Treat integration availability as optional and keep their degraded state visible in the release evidence.")
        if shotset.get("available") and int(shotset.get("capture_count") or 0) > 0:
            actions.append("Capture the remaining manual Thoughtglass HUD, TUI boot-home, and guest branding shots to complete the release shot list.")
        else:
            actions.append("Run `blackfong verify shotset` and then capture the remaining manual HUD, TUI, and guest branding shots.")
        return actions

    def _render_release_evidence_markdown(self, report: Dict[str, Any]) -> str:
        installer_status = report["installer_status"]
        installer_validation = report["installer_validation"]
        manifest = report["manifest"]
        shotset = report.get("shotset", {})
        automated = report["verification"]["automated"]
        surfaces = report["verification"]["surfaces"]
        integrations = report["verification"]["integrations"]
        blockers = report.get("blockers", [])
        next_actions = report.get("recommended_next_actions", [])

        blocker_lines = "\n".join(f"- {item}" for item in blockers) or "- none"
        action_lines = "\n".join(f"- {item}" for item in next_actions) or "- none"

        return f"""# O.B.E.O.S. Release Evidence Snapshot

Generated: `{report["generated_at_iso"]}`

## Status

- release evidence status: `{report["status"]}`
- profile: `{report["profile"]}`
- workspace root: `{report["workspace_root"]}`

## Installer Truth

- installer state: `{installer_status.get("state")}`
- shipping ready: `{installer_status.get("shipping_ready")}`
- recommended v1 path: `{installer_status.get("recommended_v1_path")}`
- installer validation ok: `{installer_validation.get("ok")}`
- doctrine gap: {installer_status.get("doctrine_gap")}

## Platform Doctrine

- base distribution: `{manifest.get("base_distribution")}`
- desktop environment: `{manifest.get("desktop_environment")}`
- session rule: `{manifest.get("session_authority_rule")}`
- packaging rule: `{manifest.get("packaging_rule")}`
- manifest hash: `{manifest.get("manifest_hash")}`

## Shotset

- native workspace shotset available: `{shotset.get("available")}`
- captured images: `{shotset.get("capture_count")}`
- shotset manifest: `{shotset.get("manifest_path")}`

## Verification

- automated: `{automated.get("status")}`
- surfaces: `{surfaces.get("status")}`
- integrations: `{integrations.get("status")}`

## Blockers

{blocker_lines}

## Recommended Next Actions

{action_lines}
"""

    def _write_report(self, mode: str, report: Dict[str, Any]) -> Path:
        self.report_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.report_root / f"{timestamp}-{mode}.json"
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")
        latest = self.report_root / f"latest-{mode}.json"
        latest.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return target
