from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import chat_settings, paths
from .conversation_memory import ConversationMemoryService, conversation_memory_service
from .events import Event
from .spine_contract import (
    ADAPTER_CONTRACT_VERSION,
    DEFAULT_RESOURCE_BUDGETS,
    SPINE_MODE_AGENTS,
    AdapterHealthCheck,
    AdapterRequest,
    AdapterResponse,
    AgentAdmissionChecklist,
    AgentAdmissionDecision,
    ResourceBudget,
    ResourceUsage,
    SpineMode,
    normalize_spine_mode,
    validate_adapter_response,
)
from .storage import EventStore


PROJECTS_ROOT = paths.root.parent


class SpineAdapter(ABC):
    """Versioned boundary between D.A.I.S.E and a local project."""

    name: str
    source_project: str
    supported_actions: tuple[str, ...] = ("status",)
    event_types: tuple[str, ...] = ("adapter_health_observation",)
    trust_score: float = 0.75
    tests_exist: bool = True
    failure_handling_exists: bool = True

    def __init__(self, project_path: Path, *, resource_budget: Optional[ResourceBudget] = None) -> None:
        self.project_path = project_path
        self.resource_budget = resource_budget or DEFAULT_RESOURCE_BUDGETS[self.name]

    def health_check(self) -> AdapterHealthCheck:
        checks = [
            {
                "name": "project_path",
                "ok": self.project_path.exists(),
                "detail": str(self.project_path),
            },
            {
                "name": "supported_actions",
                "ok": bool(self.supported_actions),
                "detail": ", ".join(self.supported_actions),
            },
            {
                "name": "event_mapping",
                "ok": bool(self.event_types),
                "detail": ", ".join(self.event_types),
            },
        ]
        ok = all(check["ok"] for check in checks)
        return AdapterHealthCheck(
            agent=self.name,
            source_project=self.source_project,
            ok=ok,
            status="healthy" if ok else "unavailable",
            checks=checks,
            resource_budget=self.resource_budget.to_dict(),
        )

    def admission_checklist(self, health: Optional[AdapterHealthCheck] = None) -> AgentAdmissionChecklist:
        health = health or self.health_check()
        return AgentAdmissionChecklist(
            agent=self.name,
            adapter_exists=True,
            event_mapping_exists=bool(self.event_types),
            trust_scoring_exists=self.trust_score > 0.0,
            health_checks_exist=bool(health.checks),
            failure_handling_exists=self.failure_handling_exists,
            resource_budget_exists=self.resource_budget is not None,
            tests_exist=self.tests_exist,
        )

    def admit(self, *, event_store: Optional[EventStore] = None) -> AgentAdmissionDecision:
        health = self.health_check()
        checklist = self.admission_checklist(health)
        admitted = health.ok and checklist.admitted
        reason = "admitted" if admitted else "failed admission checklist or health check"
        decision = AgentAdmissionDecision(
            agent=self.name,
            admitted=admitted,
            checklist=checklist,
            health=health,
            reason=reason,
        )
        if event_store is not None:
            event_store.init_db()
            event_store.add_event(
                Event.new(
                    source="daise",
                    type="agent_admission_decision",
                    payload=decision.to_dict(),
                    confidence=1.0 if admitted else 0.65,
                    tags=["spine", "adapter", "admission", self.name],
                )
            )
        return decision

    def run(self, request: AdapterRequest) -> AdapterResponse:
        start = time.time()
        if request.adapter_contract_version != ADAPTER_CONTRACT_VERSION:
            return self._response(
                request.action,
                False,
                f"Unsupported adapter contract version: {request.adapter_contract_version}",
                start,
                error="unsupported_contract_version",
            )
        if request.action not in self.supported_actions:
            return self._response(
                request.action,
                False,
                f"{self.name} does not support action {request.action!r}.",
                start,
                error="unsupported_action",
            )
        try:
            return self._run_supported(request, start)
        except Exception as exc:
            return self._response(
                request.action,
                False,
                f"{self.name} adapter failed: {exc}",
                start,
                error=str(exc),
            )

    @abstractmethod
    def _run_supported(self, request: AdapterRequest, start: float) -> AdapterResponse:
        raise NotImplementedError

    def _response(
        self,
        action: str,
        success: bool,
        response: str,
        start: float,
        *,
        observations: Optional[List[Dict[str, Any]]] = None,
        proposals: Optional[List[Dict[str, Any]]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        resource_usage: Optional[ResourceUsage] = None,
    ) -> AdapterResponse:
        usage = resource_usage or ResourceUsage(latency_ms=(time.time() - start) * 1000)
        return AdapterResponse(
            agent=self.name,
            source_project=self.source_project,
            action=action,
            success=success,
            response=response,
            observations=observations or [],
            proposals=proposals or [],
            metrics=metrics or {},
            trust_score=self.trust_score if success else min(self.trust_score, 0.35),
            resource_usage=usage.to_dict(),
            error=error,
        )


class JarvisSpineAdapter(SpineAdapter):
    name = "jarvis"
    source_project = "O.B.E.O.S Jarvis persona"
    supported_actions = ("status", "respond", "help")
    event_types = ("adapter_health_observation", "agent_query", "agent_response")
    trust_score = 0.82

    def _run_supported(self, request: AdapterRequest, start: float) -> AdapterResponse:
        prompt = str(request.payload.get("prompt") or request.payload.get("text") or "").strip()
        if request.action in {"status", "help"}:
            text = (
                "Jarvis is the O.B.E.O.S operator persona. D.A.I.S.E remains the authority spine; "
                "Jarvis handles user-facing control-center responses and routes work through admitted adapters."
            )
        elif any(token in prompt.lower() for token in ("status", "spine", "agent", "what can you do", "help")):
            text = (
                "O.B.E.O.S spine is active. I can report spine modes, agent health, learner status, "
                "TinyProgrammer state, and vision tool readiness through D.A.I.S.E."
            )
        else:
            text = (
                "Jarvis is merged into the O.B.E.O.S spine as a local control-center persona. "
                "Ask for spine status, agent status, learning status, TinyProgrammer, or vision tools."
            )
        return self._response(
            request.action,
            True,
            text,
            start,
            observations=[
                {"key": "brain", "value": "daise", "authority": "event_memory"},
                {"key": "persona", "value": "jarvis", "authority": "live_adapter_response"},
                {"key": "model", "value": chat_settings.model_for_agent("jarvis")},
            ],
        )


class LiloSpineAdapter(SpineAdapter):
    name = "lilo"
    source_project = "lilo"
    supported_actions = ("status", "pcep_status")
    event_types = ("adapter_health_observation", "learning_attempt", "learner_profile_update")
    trust_score = 0.78

    def _run_supported(self, request: AdapterRequest, start: float) -> AdapterResponse:
        question_count = _count_python_constructor(self.project_path / "curriculum" / "questions_bank.py", "Question")
        pcep_count = _count_json_items(PROJECTS_ROOT / "lilo-pcep" / "question_bank")
        db_stats = _sqlite_table_counts(self.project_path / "db" / "lilo.db")
        response = (
            f"Lilo is available with {question_count} fallback certification questions "
            f"and {pcep_count} PCEP questions available for fold-in."
        )
        proposals = []
        if pcep_count:
            proposals.append(
                {
                    "proposal": "fold_lilo_pcep",
                    "summary": "Import lilo-pcep sections and question banks as a PCEP path inside Lilo.",
                    "source": "lilo-pcep",
                    "target": "lilo",
                    "question_count": pcep_count,
                }
            )
        return self._response(
            request.action,
            True,
            response,
            start,
            observations=[
                {"key": "fallback_question_count", "value": question_count},
                {"key": "pcep_question_count", "value": pcep_count},
                {"key": "db_tables", "value": db_stats},
            ],
            proposals=proposals,
            metrics={"question_count": question_count, "pcep_question_count": pcep_count},
        )


class StitchSpineAdapter(SpineAdapter):
    name = "stitch"
    source_project = "stitch"
    supported_actions = ("status", "exercise")
    event_types = ("adapter_health_observation", "learning_attempt", "learner_profile_update")
    trust_score = 0.78

    def _run_supported(self, request: AdapterRequest, start: float) -> AdapterResponse:
        exercises_path = self.project_path / "curriculum" / "exercises_bank.py"
        exercise_count = _count_python_constructor(exercises_path, "Exercise")
        titles = re.findall(r'title="([^"]+)"', _read_text(exercises_path))[:5]
        response = f"Stitch is available with {exercise_count} curated fallback Python exercises."
        if request.action == "exercise" and titles:
            response += f" Suggested starter exercise: {titles[0]}."
        return self._response(
            request.action,
            True,
            response,
            start,
            observations=[
                {"key": "exercise_count", "value": exercise_count},
                {"key": "sample_titles", "value": titles},
            ],
            proposals=[
                {
                    "proposal": "deepen_python_curriculum",
                    "summary": "Expand exercises into project tracks, debugging drills, tests, and hints.",
                    "priority": "high",
                }
            ],
            metrics={"exercise_count": exercise_count},
        )


class SecurityPlusSpineAdapter(SpineAdapter):
    name = "securityplus"
    source_project = "comptTIA/securityplus-bot"
    supported_actions = ("status", "question_bank")
    event_types = ("adapter_health_observation", "learning_attempt", "learner_profile_update")
    trust_score = 0.8

    def _run_supported(self, request: AdapterRequest, start: float) -> AdapterResponse:
        db_stats = _sqlite_table_counts(self.project_path / "data" / "securityplus_bot.sqlite3")
        bundled_question_count = _count_json_items(self.project_path / "question_bank")
        question_count = max(bundled_question_count, int(db_stats.get("question_bank", 0)))
        bundled_objective_count = _count_json_items(self.project_path / "objectives")
        objective_count = max(bundled_objective_count, int(db_stats.get("objectives", 0)))
        proposals = []
        if question_count < 150:
            proposals.append(
                {
                    "proposal": "expand_securityplus_bank",
                    "summary": "Grow Security+ teaching material beyond the small seed bank.",
                    "target_question_count": 300,
                }
            )
        return self._response(
            request.action,
            True,
            f"Security+ bot has {question_count} bundled questions and {objective_count} objective records.",
            start,
            observations=[
                {"key": "securityplus_question_count", "value": question_count},
                {"key": "objective_count", "value": objective_count},
                {"key": "bundled_seed_question_count", "value": bundled_question_count},
                {"key": "db_tables", "value": db_stats},
            ],
            proposals=proposals,
            metrics={"question_count": question_count, "objective_count": objective_count},
        )


class ThirtyDaysSpineAdapter(SpineAdapter):
    name = "thirty_days"
    source_project = "30-Days-Of-Python"
    supported_actions = ("status", "curriculum")
    event_types = ("adapter_health_observation", "learner_profile_update")
    trust_score = 0.72

    def _run_supported(self, request: AdapterRequest, start: float) -> AdapterResponse:
        day_dirs = sorted(path.name for path in self.project_path.glob("*_Day_*") if path.is_dir())
        return self._response(
            request.action,
            True,
            f"30 Days has {len(day_dirs)} lesson directories ready to become an interactive tracker.",
            start,
            observations=[
                {"key": "lesson_count", "value": len(day_dirs)},
                {"key": "lessons", "value": day_dirs[:10]},
            ],
            proposals=[
                {
                    "proposal": "interactive_30_days_tracker",
                    "summary": "Track completion, tests, scores, and delegate practice to Stitch.",
                    "priority": "high",
                }
            ],
            metrics={"lesson_count": len(day_dirs)},
        )


class TinyProgrammerSpineAdapter(SpineAdapter):
    name = "tinyprogrammer"
    source_project = "TinyProgrammer"
    supported_actions = ("status", "brief", "recent")
    event_types = (
        "adapter_health_observation",
        "tinyprogrammer_status",
        "tinyprogrammer_brief_applied",
        "tinyprogrammer_program_created",
    )
    trust_score = 0.76

    def _run_supported(self, request: AdapterRequest, start: float) -> AdapterResponse:
        if request.action == "brief":
            from .tinyprogrammer_companion import TinyProgrammerCompanion

            brief = str(request.payload.get("brief") or "").strip()
            if not brief:
                return self._response(request.action, False, "Brief text is required.", start, error="missing_brief")
            result = TinyProgrammerCompanion(project_path=self.project_path).send_brief(
                brief,
                metadata={"adapter_contract_version": ADAPTER_CONTRACT_VERSION, "source": "spine_adapter"},
            )
            return self._response(
                request.action,
                bool(result.get("success")),
                f"TinyProgrammer brief applied in {result.get('mode', 'unknown')} mode.",
                start,
                observations=[{"key": "brief_result", "value": result}],
                metrics={"mode": result.get("mode")},
            )

        index_path = self.project_path / "programs" / "index.json"
        program_count = len(_load_json_list(index_path))
        response = (
            f"TinyProgrammer is present with {program_count} archived program records. "
            "It should run as D.A.I.S.E's creative companion using local models."
        )
        return self._response(
            request.action,
            True,
            response,
            start,
            observations=[
                {"key": "program_count", "value": program_count},
                {"key": "preferred_model", "value": "deepseek-coder:latest"},
            ],
            proposals=[
                {
                    "proposal": "windows_simulation_backend",
                    "summary": "Enable Windows simulation and DAISE/Ollama generation backend.",
                    "priority": "high",
                }
            ],
            metrics={"program_count": program_count},
        )


class VisionSpineAdapter(SpineAdapter):
    name = "vision"
    source_project = "vision/yolov5/opencv/cocostuff"
    supported_actions = ("status", "smoke", "detect")
    event_types = ("adapter_health_observation", "vision_tool_result")
    trust_score = 0.74

    def __init__(self, project_path: Path, *, resource_budget: Optional[ResourceBudget] = None) -> None:
        super().__init__(project_path, resource_budget=resource_budget)
        self.vision_root = PROJECTS_ROOT / "vision-main" / "vision-main"
        self.yolo_root = PROJECTS_ROOT / "yolov5-master" / "yolov5-master"
        self.opencv_root = PROJECTS_ROOT / "opencv-4.x" / "opencv-4.x"
        self.cocostuff_root = PROJECTS_ROOT / "cocostuff-master" / "cocostuff-master"

    def health_check(self) -> AdapterHealthCheck:
        checks = [
            {"name": "torchvision_source", "ok": self.vision_root.exists(), "detail": str(self.vision_root)},
            {"name": "yolov5_source", "ok": self.yolo_root.exists(), "detail": str(self.yolo_root)},
            {"name": "opencv_source", "ok": self.opencv_root.exists(), "detail": str(self.opencv_root)},
            {"name": "cocostuff_reference", "ok": self.cocostuff_root.exists(), "detail": str(self.cocostuff_root)},
        ]
        ok = all(check["ok"] for check in checks)
        return AdapterHealthCheck(
            agent=self.name,
            source_project=self.source_project,
            ok=ok,
            status="healthy" if ok else "partial",
            checks=checks,
            resource_budget=self.resource_budget.to_dict(),
        )

    def _run_supported(self, request: AdapterRequest, start: float) -> AdapterResponse:
        execute = bool(request.payload.get("execute"))
        observations = [
            {"key": "torchvision_root", "value": str(self.vision_root)},
            {"key": "yolov5_root", "value": str(self.yolo_root)},
            {"key": "opencv_root", "value": str(self.opencv_root)},
            {"key": "cocostuff_root", "value": str(self.cocostuff_root)},
        ]
        if request.action == "smoke" and execute:
            script = PROJECTS_ROOT / "run-vision-smoke.ps1"
            result = _run_powershell(script, timeout_seconds=240)
            observations.append({"key": "smoke_result", "value": result})
            return self._response(
                request.action,
                result["returncode"] == 0,
                "Vision smoke command completed." if result["returncode"] == 0 else "Vision smoke command failed.",
                start,
                observations=observations,
                metrics={"returncode": result["returncode"]},
                error=None if result["returncode"] == 0 else result["stderr"][:500],
            )
        return self._response(
            request.action,
            True,
            "Vision knowledge/tools are registered. Use --execute on smoke/detect commands to run bounded local checks.",
            start,
            observations=observations,
            proposals=[
                {
                    "proposal": "ingest_cv_knowledge",
                    "summary": "Ingest Torchvision, YOLOv5, OpenCV, and COCO-Stuff docs/labels into D.A.I.S.E knowledge.",
                    "priority": "medium",
                }
            ],
        )


def build_default_adapters() -> Dict[str, SpineAdapter]:
    return {
        "jarvis": JarvisSpineAdapter(paths.root),
        "lilo": LiloSpineAdapter(PROJECTS_ROOT / "lilo"),
        "stitch": StitchSpineAdapter(PROJECTS_ROOT / "stitch"),
        "securityplus": SecurityPlusSpineAdapter(PROJECTS_ROOT / "comptTIA" / "securityplus-bot"),
        "thirty_days": ThirtyDaysSpineAdapter(PROJECTS_ROOT / "30-Days-Of-Python-master" / "30-Days-Of-Python-master"),
        "tinyprogrammer": TinyProgrammerSpineAdapter(PROJECTS_ROOT / "TinyProgrammer-main"),
        "vision": VisionSpineAdapter(PROJECTS_ROOT),
    }


class SpineAdapterRegistry:
    def __init__(
        self,
        *,
        adapters: Optional[Dict[str, SpineAdapter]] = None,
        event_store: Optional[EventStore] = None,
        memory_service: Optional[ConversationMemoryService] = None,
    ) -> None:
        self.adapters = adapters or build_default_adapters()
        self.event_store = event_store or EventStore()
        self.memory_service = memory_service or conversation_memory_service

    def list_agents(self, mode: Optional[str | SpineMode] = None) -> List[str]:
        if mode is None:
            return sorted(self.adapters)
        selected_mode = normalize_spine_mode(mode)
        return [agent for agent in SPINE_MODE_AGENTS[selected_mode] if agent in self.adapters]

    def status(self, mode: Optional[str | SpineMode] = None, *, log_events: bool = False) -> Dict[str, Any]:
        agents = self.list_agents(mode)
        health = {agent: self.adapters[agent].health_check().to_dict() for agent in agents}
        if log_events:
            self.event_store.init_db()
            for agent, payload in health.items():
                self.event_store.add_event(
                    Event.new(
                        source="daise",
                        type="adapter_health_observation",
                        payload=payload,
                        confidence=1.0 if payload.get("ok") else 0.5,
                        tags=["spine", "adapter", "health", agent],
                    )
                )
        return {
            "adapter_contract_version": ADAPTER_CONTRACT_VERSION,
            "mode": normalize_spine_mode(mode).value if mode else "all",
            "agents": agents,
            "health": health,
        }

    def admit(self, agent: str) -> AgentAdmissionDecision:
        adapter = self.get(agent)
        return adapter.admit(event_store=self.event_store)

    def run(self, agent: str, action: str, payload: Optional[Dict[str, Any]] = None) -> AdapterResponse:
        adapter = self.get(agent)
        response = adapter.run(AdapterRequest(agent=agent, action=action, payload=payload or {}))
        validation_errors = validate_adapter_response(response.to_dict())
        if validation_errors:
            return AdapterResponse(
                agent=agent,
                source_project=adapter.source_project,
                action=action,
                success=False,
                response="Adapter response failed contract validation.",
                observations=response.observations,
                proposals=response.proposals,
                metrics={"validation_errors": validation_errors},
                trust_score=0.0,
                resource_usage=response.resource_usage,
                error="; ".join(validation_errors),
            )
        return response

    def get(self, agent: str) -> SpineAdapter:
        normalized = agent.strip().lower()
        if normalized not in self.adapters:
            raise KeyError(f"Unknown spine adapter: {agent}")
        return self.adapters[normalized]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _count_python_constructor(path: Path, constructor_name: str) -> int:
    return len(re.findall(rf"\b{re.escape(constructor_name)}\s*\(", _read_text(path)))


def _load_json_list(path: Path) -> List[Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _count_json_items(path: Path) -> int:
    if path.is_file():
        data = _load_json_list(path)
        if data:
            return len(data)
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        if isinstance(parsed, dict):
            return len(parsed)
        return 0
    if path.is_dir():
        return sum(_count_json_items(item) for item in path.glob("*.json"))
    return 0


def _sqlite_table_counts(db_path: Path) -> Dict[str, int]:
    if not db_path.exists():
        return {}
    result: Dict[str, int] = {}
    try:
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            for (table_name,) in rows:
                if str(table_name).startswith("sqlite_"):
                    continue
                count = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
                result[str(table_name)] = int(count)
    except sqlite3.Error:
        return {}
    return result


def _run_powershell(script: Path, *, timeout_seconds: int) -> Dict[str, Any]:
    if not script.exists():
        return {"returncode": 127, "stdout": "", "stderr": f"Script not found: {script}"}
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=str(PROJECTS_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }
