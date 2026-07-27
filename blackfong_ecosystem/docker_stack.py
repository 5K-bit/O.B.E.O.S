from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


class DockerStackService:
    """Manage the bounded Blackfong Docker service stack."""

    SERVICE_NAME = "blackfong-hud"
    HOST_RULE = "Host authority stays native."
    STACK_RULE = "Docker services are workers, not owners."

    def __init__(self, *, workspace_root: Optional[Path] = None) -> None:
        self.workspace_root = Path(workspace_root or Path(__file__).resolve().parents[1])
        self.compose_file = self.workspace_root / "compose.yaml"
        self.dockerfile = self.workspace_root / "Dockerfile"
        self.data_volume = "blackfong-hud-data"
        self.workspace_mount = self.workspace_root

    def status(self) -> Dict[str, Any]:
        docker_path = shutil.which("docker")
        result: Dict[str, Any] = {
            "status": "ok",
            "stack_rule": self.STACK_RULE,
            "host_authority_rule": self.HOST_RULE,
            "docker_available": bool(docker_path),
            "docker_path": docker_path,
            "compose_file": str(self.compose_file),
            "dockerfile": str(self.dockerfile),
            "service_name": self.SERVICE_NAME,
            "service_url": "http://127.0.0.1:8080",
            "mounted_roots": [
                {
                    "volume": self.data_volume,
                    "container_path": "/app/data",
                    "mode": "rw",
                    "purpose": "container-local worker state",
                },
                {"host_path": str(self.workspace_mount), "container_path": "/workspace", "mode": "ro"},
            ],
            "authority": {
                "host": "native",
                "docker": "service_worker",
            },
        }
        if not docker_path:
            result.update(
                {
                    "status": "degraded",
                    "reason_code": "docker_unavailable",
                    "human_reason": "Docker is not installed or not available on PATH in this environment.",
                }
            )
            return result
        if not self.compose_file.exists() or not self.dockerfile.exists():
            result.update(
                {
                    "status": "degraded",
                    "reason_code": "stack_assets_missing",
                    "human_reason": "Docker stack assets are missing from the workspace root.",
                }
            )
            return result
        version = self._capture(self._compose_cmd() + ["version", "--short"])
        ps = self._capture(self._compose_cmd() + ["ps", "--all", "--format", "json"])
        result["compose_version"] = version
        result["containers"] = self._parse_compose_ps(ps)
        result["active"] = any(container.get("State") == "running" for container in result["containers"])
        return result

    def up(self, *, execute: bool = True, build: bool = False) -> Dict[str, Any]:
        command = self._compose_cmd() + ["up", "-d"]
        if build:
            command.append("--build")
        planned = self._planned_payload("up", command)
        if not execute:
            return planned
        return self._run_action("up", command)

    def down(self, *, execute: bool = True, remove_orphans: bool = True) -> Dict[str, Any]:
        command = self._compose_cmd() + ["down"]
        if remove_orphans:
            command.append("--remove-orphans")
        planned = self._planned_payload("down", command)
        if not execute:
            return planned
        return self._run_action("down", command)

    def logs(self, *, execute: bool = True, tail: int = 120) -> Dict[str, Any]:
        command = self._compose_cmd() + ["logs", "--tail", str(max(1, int(tail))), self.SERVICE_NAME]
        planned = self._planned_payload("logs", command)
        if not execute:
            return planned
        return self._run_action("logs", command)

    def _compose_cmd(self) -> List[str]:
        return ["docker", "compose", "-f", str(self.compose_file)]

    def _planned_payload(self, action: str, command: List[str]) -> Dict[str, Any]:
        return {
            "status": "planned",
            "action": action,
            "command_line": command,
            "stack_rule": self.STACK_RULE,
            "host_authority_rule": self.HOST_RULE,
            "compose_file": str(self.compose_file),
            "service_name": self.SERVICE_NAME,
        }

    def _run_action(self, action: str, command: List[str]) -> Dict[str, Any]:
        docker_path = shutil.which("docker")
        if not docker_path:
            return {
                "status": "error",
                "action": action,
                "reason_code": "docker_unavailable",
                "human_reason": "Docker is not installed or not available on PATH in this environment.",
                "command_line": command,
            }
        if not self.compose_file.exists() or not self.dockerfile.exists():
            return {
                "status": "error",
                "action": action,
                "reason_code": "stack_assets_missing",
                "human_reason": "Docker stack assets are missing from the workspace root.",
                "command_line": command,
            }
        try:
            completed = subprocess.run(
                command,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as exc:
            return {
                "status": "error",
                "action": action,
                "reason_code": "docker_exec_failed",
                "human_reason": str(exc),
                "command_line": command,
            }
        payload = {
            "status": "ok" if completed.returncode == 0 else "error",
            "action": action,
            "command_line": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "stack_rule": self.STACK_RULE,
            "host_authority_rule": self.HOST_RULE,
        }
        if completed.returncode != 0:
            payload["reason_code"] = "docker_command_failed"
            payload["human_reason"] = completed.stderr.strip() or completed.stdout.strip() or "Docker command failed."
        return payload

    def _capture(self, command: List[str]) -> str:
        try:
            completed = subprocess.run(
                command,
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError:
            return ""
        if completed.returncode != 0:
            return ""
        return completed.stdout.strip()

    @staticmethod
    def _parse_compose_ps(raw: str) -> List[Dict[str, Any]]:
        text = (raw or "").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            entries: List[Dict[str, Any]] = []
            for line in lines:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    entries.append(item)
            return entries
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [parsed]
        return []
