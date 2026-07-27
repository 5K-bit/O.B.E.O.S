from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _default_tacklebox_root() -> Path:
    configured = os.getenv("BLACKFONG_TACKLEBOX_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Documents" / "tacklebox"


def _venv_python(project_root: Path) -> str:
    if os.name == "nt":
        candidate = project_root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = project_root / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


@dataclass(frozen=True)
class ExternalIntegrationDefinition:
    integration_id: str
    label: str
    category: str
    role: str
    project_name: str
    relative_path: str
    summary: str
    launch_modes: tuple[str, ...] = ()
    launch_supported: bool = False
    visual_reference_only: bool = False

    def resolve_path(self, tacklebox_root: Path) -> Path:
        return tacklebox_root / self.relative_path


class ExternalIntegrationRegistry:
    """Detect and describe optional tacklebox projects that can complement OBEOS."""

    DEFINITIONS: tuple[ExternalIntegrationDefinition, ...] = (
        ExternalIntegrationDefinition(
            integration_id="codingagent_external",
            label="CodingAgent External",
            category="assistant",
            role="External coding sidecar. Not Blackfong.",
            project_name="CodingAgent-main",
            relative_path="CodingAgent-main",
            summary="Offline Ollama assistant that can run in cli, tui, or gui mode as a distinct external coding specialist.",
            launch_modes=("cli", "tui", "gui"),
            launch_supported=True,
        ),
        ExternalIntegrationDefinition(
            integration_id="redamon_mode",
            label="RedAmon",
            category="security",
            role="Operator-approved security workflow mode.",
            project_name="redamon",
            relative_path="redamon",
            summary="Heavyweight dockerized security platform that must remain gated behind explicit launch and review approval.",
            launch_modes=("review", "launch"),
            launch_supported=True,
        ),
        ExternalIntegrationDefinition(
            integration_id="ai_monitor",
            label="AI Monitor",
            category="telemetry",
            role="Optional native telemetry provider for local Linux/NVIDIA systems.",
            project_name="ai-monitor",
            relative_path="ai-monitor",
            summary="PyQt6 and pyqtgraph NVIDIA telemetry dashboard suited for rich native monitoring panes.",
            launch_modes=("gui",),
            launch_supported=True,
        ),
        ExternalIntegrationDefinition(
            integration_id="dreamgraph_reference",
            label="DreamGraph Reference",
            category="visual_reference",
            role="Visual inspiration only. Never cognition authority.",
            project_name="dreamgraph-main",
            relative_path="dreamgraph-main",
            summary="Graph-first explorer whose visual language can inspire Thoughtglass styling without replacing OBEOS cognition.",
            visual_reference_only=True,
        ),
    )

    def __init__(self, *, workspace_root: Optional[Path] = None, tacklebox_root: Optional[Path] = None) -> None:
        self.workspace_root = Path(workspace_root or Path(__file__).resolve().parents[1])
        self.tacklebox_root = Path(tacklebox_root or _default_tacklebox_root())

    def list_integrations(self) -> Dict[str, Any]:
        integrations = [self.describe(definition.integration_id) for definition in self.DEFINITIONS]
        return {
            "schema_version": "1.0",
            "tacklebox_root": str(self.tacklebox_root),
            "integrations": integrations,
            "counts": {
                "available": sum(1 for item in integrations if item.get("available")),
                "launch_supported": sum(1 for item in integrations if item.get("launch_supported")),
            },
        }

    def describe(self, integration_id: str) -> Dict[str, Any]:
        definition = self._definition(integration_id)
        project_path = definition.resolve_path(self.tacklebox_root)
        exists = project_path.exists()
        required_files = self._required_files(definition)
        detected_files = [item for item in required_files if (project_path / item).exists()]
        supported_environment = self._supported_environment(definition)
        return {
            "schema_version": "1.0",
            "integration_id": definition.integration_id,
            "label": definition.label,
            "category": definition.category,
            "role": definition.role,
            "summary": definition.summary,
            "project_name": definition.project_name,
            "project_path": str(project_path),
            "available": exists,
            "launch_supported": definition.launch_supported and exists,
            "launch_modes": list(definition.launch_modes),
            "visual_reference_only": definition.visual_reference_only,
            "supported_environment": supported_environment,
            "obeos_policy": self._obeos_policy(definition),
            "detected_files": detected_files,
            "missing_files": [item for item in required_files if item not in detected_files],
            "launch_contract": self.launch_contract(definition.integration_id, execute=False) if exists and definition.launch_supported else None,
            "visual_borrow_ideas": self._visual_borrow_ideas(definition),
        }

    def launch_contract(
        self,
        integration_id: str,
        *,
        execute: bool = False,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        definition = self._definition(integration_id)
        project_path = definition.resolve_path(self.tacklebox_root)
        if not project_path.exists():
            return {
                "success": False,
                "status": "unavailable",
                "integration_id": integration_id,
                "error": f"{definition.project_name} was not found at {project_path}",
            }

        if definition.integration_id == "codingagent_external":
            launch_mode = (mode or "gui").strip().lower()
            if launch_mode not in {"cli", "tui", "gui"}:
                launch_mode = "gui"
            command_line = [_venv_python(project_path), "main.py", "--mode", launch_mode]
        elif definition.integration_id == "redamon_mode":
            launch_mode = "review"
            command_line = [
                "docker",
                "compose",
                "up",
                "-d",
                "postgres",
                "neo4j",
                "recon-orchestrator",
                "kali-sandbox",
                "agent",
                "webapp",
            ]
        elif definition.integration_id == "ai_monitor":
            launch_mode = "gui"
            command_line = [_venv_python(project_path), "ai_monitor.py"]
        else:
            return {
                "success": False,
                "status": "not_launchable",
                "integration_id": integration_id,
                "error": "This integration is reference-only and should not be launched by OBEOS.",
            }

        contract = {
            "success": True,
            "status": "planned",
            "integration_id": integration_id,
            "label": definition.label,
            "mode": launch_mode,
            "project_path": str(project_path),
            "command_line": command_line,
            "working_directory": str(project_path),
            "execute": execute,
            "launched_at": None,
        }
        if not execute:
            return contract

        try:
            subprocess.Popen(command_line, cwd=str(project_path))
        except OSError as exc:
            contract.update(
                {
                    "success": False,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            return contract
        contract["status"] = "launched"
        contract["launched_at"] = time.time()
        return contract

    def discoverable_ids(self) -> List[str]:
        return [definition.integration_id for definition in self.DEFINITIONS]

    def _definition(self, integration_id: str) -> ExternalIntegrationDefinition:
        normalized = str(integration_id or "").strip().lower()
        for definition in self.DEFINITIONS:
            if definition.integration_id == normalized:
                return definition
        raise KeyError(f"Unknown integration: {integration_id}")

    @staticmethod
    def _required_files(definition: ExternalIntegrationDefinition) -> List[str]:
        if definition.integration_id == "codingagent_external":
            return ["README.md", "main.py", "pyproject.toml"]
        if definition.integration_id == "redamon_mode":
            return ["README.md", "docker-compose.yml", "agentic/api.py"]
        if definition.integration_id == "ai_monitor":
            return ["README.md", "ai_monitor.py", "pyproject.toml"]
        if definition.integration_id == "dreamgraph_reference":
            return ["README.md"]
        return ["README.md"]

    @staticmethod
    def _supported_environment(definition: ExternalIntegrationDefinition) -> Dict[str, Any]:
        if definition.integration_id == "ai_monitor":
            return {
                "preferred_os": "Linux",
                "requires_gpu_vendor": "NVIDIA",
                "preferred_surface": "PySide6 workspace",
                "vm_default": False,
            }
        if definition.integration_id == "redamon_mode":
            return {
                "preferred_os": "Linux",
                "requires": ["docker", "docker compose"],
                "preferred_surface": "security workflow mode",
                "vm_default": False,
            }
        return {
            "preferred_os": "Windows/Linux",
            "preferred_surface": "workspace and HUD",
            "vm_default": True,
        }

    @staticmethod
    def _obeos_policy(definition: ExternalIntegrationDefinition) -> Dict[str, Any]:
        if definition.integration_id == "codingagent_external":
            return {
                "authority": "external_sidecar",
                "identity_rule": "Never present this assistant as Blackfong or the native CodingAgent.",
                "operator_mode": "launch_or_consult",
            }
        if definition.integration_id == "redamon_mode":
            return {
                "authority": "operator_approved_only",
                "identity_rule": "RedAmon is a separate security workflow mode, never ambient D.A.I.S.E authority.",
                "operator_mode": "launch_and_review",
            }
        if definition.integration_id == "dreamgraph_reference":
            return {
                "authority": "visual_reference_only",
                "identity_rule": "DreamGraph visuals may inspire Thoughtglass, but OBEOS cognition stays authoritative.",
                "operator_mode": "reference",
            }
        return {
            "authority": "optional_provider",
            "identity_rule": "Optional native provider; do not make it a required baseline dependency.",
            "operator_mode": "optional_launch",
        }

    @staticmethod
    def _visual_borrow_ideas(definition: ExternalIntegrationDefinition) -> List[str]:
        if definition.integration_id != "dreamgraph_reference":
            return []
        return [
            "active node halo and event pulse signaling",
            "clearer graph clustering and hierarchy spacing",
            "persistent side inspector cards for selected nodes",
            "minimap or overview cues for dense brain views",
            "more deliberate semantic coloring for edges and state transitions",
        ]

