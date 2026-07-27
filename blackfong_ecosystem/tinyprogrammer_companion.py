from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None

from .config import paths, tinyprogrammer_settings
from .conversation_memory import ConversationMemoryService, conversation_memory_service
from .notification_service import NotificationService, notification_service
from .policy_engine import PolicyEngine
from .storage import EventStore


COMPANION_BRIEF_SCOPE = {
    "schema_version": "1.0",
    "allowed": [
        "creative_idea_generation",
        "summaries",
        "mood_prompts",
        "themes",
        "prototype_concepts",
        "reflections",
        "feedback",
    ],
    "denied": [
        "file_edits",
        "command_execution",
        "governance_decisions",
        "blackfong_bypass",
        "direct_sensor_control",
    ],
}

BRIEF_DENY_PATTERNS = {
    "file_edits": ["edit file", "write file", "modify file", "delete file", "apply diff", "patch file"],
    "command_execution": ["run command", "execute command", "shell", "powershell", "cmd.exe", "subprocess"],
    "governance_decisions": ["override policy", "decide governance", "bypass policy", "ignore blackfong"],
    "blackfong_bypass": ["bypass blackfong", "skip blackfong", "without blackfong"],
    "direct_sensor_control": ["turn on camera", "activate camera", "turn on microphone", "record audio", "start webcam"],
}


class TinyProgrammerCompanion:
    """Bridge TinyProgrammer into O.B.E.O.S as a companion node."""

    def __init__(
        self,
        *,
        memory_service: Optional[ConversationMemoryService] = None,
        base_url: Optional[str] = None,
        project_path: Optional[Path] = None,
        timeout: Optional[float] = None,
        notification_service_instance: Optional[NotificationService] = None,
        policy_engine: Optional[PolicyEngine] = None,
    ) -> None:
        self.memory_service = memory_service or conversation_memory_service
        self.event_store: EventStore = self.memory_service.event_store
        self.base_url = (base_url or tinyprogrammer_settings.base_url).rstrip("/")
        self.project_path = Path(project_path or tinyprogrammer_settings.project_path)
        self.timeout = timeout if timeout is not None else tinyprogrammer_settings.timeout
        self.python_executable = tinyprogrammer_settings.python_executable or sys.executable
        self.launch_timeout = tinyprogrammer_settings.launch_timeout
        self.autostart_enabled = tinyprogrammer_settings.auto_start
        self.auto_stream = tinyprogrammer_settings.auto_stream
        self.adopt_external = tinyprogrammer_settings.adopt_external
        self.shutdown_policy = tinyprogrammer_settings.shutdown_policy
        self.notification_service = notification_service_instance or notification_service
        self.policy_engine = policy_engine or PolicyEngine()
        self.overrides_path = self.project_path / "config_overrides.json"
        self.index_path = self.project_path / "programs" / "index.json"
        self.lessons_path = self.project_path / "lessons.md"
        self.log_path = paths.data_root / "logs" / "tinyprogrammer.log"
        self._last_state: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None
        self._process_log_handle: Optional[IO[str]] = None
        self.retry_delays_s = (2.0, 5.0, 10.0)
        self.max_startup_retries = len(self.retry_delays_s)
        self._retry_count = 0
        self._launch_attempts = 0
        self._last_error_code: Optional[str] = None
        self._last_error_summary: Optional[str] = None
        self._next_retry_at: Optional[float] = None

    def get_status(self, *, probe_live: bool = True, require_stream: Optional[bool] = None) -> Dict[str, Any]:
        status = self._fetch_live_status() if probe_live else None
        if status is None:
            status = self._build_offline_status()
        status = self._decorate_status(
            status,
            live=bool(status.get("online")),
            require_stream=self.auto_stream if require_stream is None else require_stream,
        )
        try:
            self._sync_status_event(status)
        except Exception:
            status["status_sync"] = "deferred"
            status["status_sync_reason"] = "event_store_temporarily_unavailable"
        return status

    def get_recent(self, limit: int = 10) -> Dict[str, Any]:
        creations = self._load_recent_programs(limit=limit)
        reflections = self._load_recent_lessons(limit=limit)
        self._sync_recent_events(creations, reflections)
        return {
            "online": self._fetch_live_status() is not None,
            "project_path": str(self.project_path),
            "programs": creations,
            "reflections": reflections,
        }

    def get_bbs(self, *, limit: int = 12) -> Dict[str, Any]:
        live_result = self._get_json(f"/api/bbs?limit={int(limit)}")
        if isinstance(live_result, dict):
            return live_result
        status = self.get_status()
        return {
            "success": False,
            "online": bool(status.get("online")),
            "bbs_enabled": bool(status.get("bbs_enabled")),
            "bbs_reason": status.get("bbs_reason", "unconfigured_bbs"),
            "human_reason": status.get("bbs_human_reason", "BBS is unavailable."),
            "posts": [],
            "stats": {},
        }

    def process_status(
        self,
        *,
        live: Optional[bool] = None,
        live_status: Optional[Dict[str, Any]] = None,
        require_stream: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if live_status is None and live is None:
            live_status = self._fetch_live_status()
        if live is None:
            live = live_status is not None
        stream_required = self.auto_stream if require_stream is None else require_stream
        pid = self._process.pid if self._process is not None else None
        return_code = self._process.poll() if self._process is not None else None
        launched_by_blackfong = self._process is not None
        stream_enabled = bool((live_status or {}).get("stream_enabled"))
        if launched_by_blackfong and return_code is None:
            ownership = "managed"
            state = "running"
        elif live:
            ownership = "external"
            state = "external_no_stream" if stream_required and not stream_enabled else "external_live"
        elif launched_by_blackfong:
            ownership = "managed"
            state = "stopped"
        else:
            ownership = "offline"
            state = "not_started"
        management_state = self._management_state(
            live=bool(live),
            ownership=ownership,
            launched_by_blackfong=launched_by_blackfong,
            return_code=return_code,
            stream_required=stream_required,
            stream_enabled=stream_enabled,
        )
        resource_metrics = self._resource_metrics(pid, active=management_state in {"running", "degraded", "launching"})
        return {
            "schema_version": "1.0",
            "state": state,
            "ownership": ownership,
            "management_state": management_state,
            "pid": pid,
            "return_code": return_code,
            "launched_by_blackfong": launched_by_blackfong,
            "online": live,
            "base_url": self.base_url,
            "stream_url": f"{self.base_url}/stream",
            "project_path": str(self.project_path),
            "log_path": str(self.log_path),
            "stream_required": stream_required,
            "stream_enabled": stream_enabled,
            "autostart_enabled": self.autostart_enabled,
            "shutdown_policy": self.shutdown_policy,
            "native_window_state": self._native_window_state(
                live=bool(live),
                ownership=ownership,
                management_state=management_state,
            ),
            "native_window_human_reason": self._native_window_human_reason(
                live=bool(live),
                ownership=ownership,
                management_state=management_state,
            ),
            **resource_metrics,
        }

    def get_log_tail(self, *, lines: int = 40, max_chars: int = 12_000) -> Dict[str, Any]:
        process = self.process_status(require_stream=self.auto_stream)
        if not self.log_path.exists():
            return {
                "success": False,
                "log_path": str(self.log_path),
                "human_reason": "TinyProgrammer has not written a managed Blackfong log yet.",
                "process": process,
                "content": "",
                "line_count": 0,
                "truncated": False,
            }
        try:
            raw_lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {
                "success": False,
                "log_path": str(self.log_path),
                "human_reason": str(exc),
                "process": process,
                "content": "",
                "line_count": 0,
                "truncated": False,
            }
        selected = raw_lines[-max(1, int(lines)) :]
        content = "\n".join(selected)
        truncated = len(content) > max_chars
        if truncated:
            content = content[-max_chars:]
        return {
            "success": True,
            "log_path": str(self.log_path),
            "process": process,
            "content": content,
            "line_count": len(selected),
            "truncated": truncated,
            "native_window_state": process.get("native_window_state"),
            "native_window_human_reason": process.get("native_window_human_reason"),
        }

    def ensure_running(
        self,
        *,
        require_stream: bool = True,
        adopt_external: Optional[bool] = None,
        open_browser: bool = False,
        consent: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        allow_external = self.adopt_external if adopt_external is None else adopt_external
        live_status = self._fetch_live_status()
        if live_status is not None:
            status = self._decorate_status(live_status, live=True, require_stream=require_stream)
            self.record_startup_success()
            process = status.get("process", {})
            ownership = str(process.get("ownership") or "external")
            if require_stream and not status.get("stream_available"):
                if ownership == "managed" and process.get("launched_by_blackfong"):
                    self.stop()
                    return self.launch(
                        consent=consent or {"approved": True, "level": 1},
                        open_browser=open_browser,
                        enable_stream=True,
                    )
                reason_code = "external_no_stream" if allow_external else "stream_required"
                return {
                    "success": False,
                    "status": "relaunch_required",
                    "reason_code": reason_code,
                    "human_reason": "TinyProgrammer is already online, but the current external instance does not have stream enabled.",
                    "process": process,
                    "tinyprogrammer": status,
                }
            if open_browser:
                target_url = f"{self.base_url}/stream" if require_stream and status.get("stream_available") else self.base_url
                webbrowser.open(target_url)
            return {
                "success": True,
                "status": "already_live",
                "reason_code": "external_adopted" if ownership == "external" else "already_running",
                "human_reason": "TinyProgrammer is already online and ready.",
                "process": process,
                "tinyprogrammer": status,
            }
        if self._process is not None and self._process.poll() is None:
            status = self.get_status(probe_live=False, require_stream=require_stream)
            if open_browser:
                webbrowser.open(self.base_url)
            return {
                "success": True,
                "status": "already_running",
                "reason_code": "launch_in_progress",
                "human_reason": "TinyProgrammer is already launching under Blackfong management.",
                "process": status.get("process", {}),
                "tinyprogrammer": status,
            }
        return self.launch(
            consent=consent,
            open_browser=open_browser,
            enable_stream=require_stream,
        )

    def launch(
        self,
        *,
        consent: Optional[Dict[str, Any]] = None,
        open_browser: bool = False,
        enable_stream: bool = True,
    ) -> Dict[str, Any]:
        self._launch_attempts += 1
        consent_decision = self.policy_engine.evaluate_consent(
            action="launch_tinyprogrammer",
            consent=consent or {"approved": True, "level": 1},
            target=str(self.project_path),
        )
        self._emit_operator_consent("launch_tinyprogrammer", str(self.project_path), consent_decision, consent)
        if not consent_decision.get("approved"):
            result = {"success": False, "status": "requires_consent", "consent": consent_decision}
            self._notify("TinyProgrammer launch needs consent", "Launch was blocked until level-1 consent is provided.", "warning")
            return result
        live_status = self._fetch_live_status()
        if live_status is not None:
            status = self._decorate_status(live_status, live=True, require_stream=enable_stream)
            self.record_startup_success()
            process = status.get("process", {})
            if enable_stream and not bool(live_status.get("stream_enabled")):
                if self._process is not None and self._process.poll() is None:
                    self.stop()
                else:
                    return {
                        "success": False,
                        "status": "relaunch_required",
                        "reason_code": "external_no_stream",
                        "human_reason": "TinyProgrammer is already running externally without web stream enabled.",
                        "process": process,
                        "tinyprogrammer": status,
                    }
            result = {"success": True, "status": "already_live", "process": process, "tinyprogrammer": status}
            if open_browser:
                target_url = f"{self.base_url}/stream" if enable_stream and status.get("stream_available") else self.base_url
                webbrowser.open(target_url)
            return result
        if self._process is not None and self._process.poll() is None:
            status = self.get_status(probe_live=False, require_stream=enable_stream)
            return {"success": True, "status": "already_running", "process": status.get("process", {}), "tinyprogrammer": status}
        if not self.project_path.exists() or not (self.project_path / "main.py").exists():
            result = {"success": False, "status": "unavailable", "error": f"TinyProgrammer project not found at {self.project_path}"}
            self.record_startup_failure(
                reason_code="not_started",
                summary=result["error"],
            )
            self._emit_process_state("unavailable", result)
            self._notify("TinyProgrammer launch failed", result["error"], "error", desktop=True)
            return result

        env = os.environ.copy()
        env.setdefault("WEB_ENABLED", "true")
        env.setdefault("TINY_HIDE_NATIVE_WINDOW", "true")
        if enable_stream:
            env["WEB_STREAM_ENABLED"] = "true"
        parsed = urlparse(self.base_url)
        if parsed.port:
            env.setdefault("WEB_PORT", str(parsed.port))
        if parsed.hostname:
            env.setdefault("WEB_HOST", parsed.hostname)
        try:
            log_handle = self._open_process_log()
            self._process_log_handle = log_handle
            self._process = subprocess.Popen(
                [self.python_executable, "main.py"],
                cwd=str(self.project_path),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            self._close_process_log()
            error_msg = f"TinyProgrammer launch failed: {exc}"
            # Log to file
            try:
                with self.log_path.open("a", encoding="utf-8") as log_file:
                    log_file.write(f"{datetime.now().isoformat()}: {error_msg}\n")
            except Exception:
                pass  # Ignore logging errors
            result = {"success": False, "status": "failed", "error": str(exc)}
            self.record_startup_failure(
                reason_code="managed_launch_failed",
                summary=str(exc),
            )
            self._emit_process_state("failed", result)
            self._notify("TinyProgrammer launch failed", str(exc), "error", desktop=True)
            return result

        deadline = time.time() + self.launch_timeout
        live = False
        while time.time() < deadline:
            if self._process.poll() is not None:
                break
            if self._fetch_live_status() is not None:
                live = True
                break
            time.sleep(0.5)
        state = "running" if live or self._process.poll() is None else "failed"
        result = {
            "success": state == "running",
            "status": "launched" if live else "process_started" if state == "running" else "failed",
            "process": self.process_status(live=live, require_stream=enable_stream),
            "live": live,
        }
        result["tinyprogrammer"] = self.get_status(probe_live=live, require_stream=enable_stream)
        if not result["success"]:
            self._close_process_log()
            # Log failure reason
            failure_reason = f"Launch failed: state={state}, live={live}, process_poll={self._process.poll() if self._process else 'no_process'}"
            try:
                with self.log_path.open("a", encoding="utf-8") as log_file:
                    log_file.write(f"{datetime.now().isoformat()}: {failure_reason}\n")
            except Exception:
                pass  # Ignore logging errors
            reason_code, summary = self.classify_recent_failure()
            self.record_startup_failure(
                reason_code=reason_code or "managed_launch_failed",
                summary=summary or failure_reason,
            )
        else:
            self.record_startup_success()
        self._emit_process_state(state, result)
        self._notify(
            "TinyProgrammer launched" if result["success"] else "TinyProgrammer launch failed",
            f"Process state: {result['status']}",
            "info" if result["success"] else "error",
            desktop=not result["success"],
        )
        if open_browser and result["success"]:
            webbrowser.open(self.base_url)
        return result

    def open(
        self,
        *,
        target: str = "dashboard",
        consent: Optional[Dict[str, Any]] = None,
        open_browser: bool = True,
    ) -> Dict[str, Any]:
        if str(target).lower() in {"live", "stream"}:
            return self.open_live(consent=consent, open_browser=open_browser)
        ensured = self.ensure_running(
            require_stream=False,
            adopt_external=True,
            open_browser=False,
            consent=consent or {"approved": True, "level": 1},
        )
        if not ensured.get("success"):
            return ensured
        url = f"{self.base_url}/stream" if str(target).lower() in {"live", "stream"} else self.base_url
        opened = webbrowser.open(url) if open_browser else True
        return {
            "success": bool(opened),
            "status": "opened" if opened else "failed",
            "url": url,
            "process": self.process_status(),
            "tinyprogrammer": self.get_status(),
        }

    def open_live(
        self,
        *,
        consent: Optional[Dict[str, Any]] = None,
        open_browser: bool = True,
    ) -> Dict[str, Any]:
        ensured = self.ensure_running(
            require_stream=True,
            adopt_external=True,
            open_browser=False,
            consent=consent or {"approved": True, "level": 1},
        )
        status = ensured.get("tinyprogrammer") or self.get_status(require_stream=True)
        if status.get("stream_available"):
            opened = webbrowser.open(f"{self.base_url}/stream") if open_browser else True
            return {
                "success": bool(opened),
                "status": "opened" if opened else "failed",
                "url": f"{self.base_url}/stream",
                "process": status.get("process", self.process_status()),
                "tinyprogrammer": status,
            }
        return {
            "success": False,
            "status": ensured.get("status") or "relaunch_required",
            "reason_code": status.get("stream_reason", "not_started_with_stream"),
            "human_reason": status.get("stream_human_reason", "TinyProgrammer needs to be restarted with stream enabled."),
            "process": status.get("process", {}),
            "tinyprogrammer": status,
        }

    def stop(self) -> Dict[str, Any]:
        if self._process is None:
            return {"success": False, "status": "not_launched_by_blackfong", "message": "Blackfong will not stop an external TinyProgrammer process."}
        if self._process.poll() is not None:
            self._close_process_log()
            result = {"success": True, "status": "already_stopped", "process": self.process_status()}
            self._emit_process_state("stopped", result)
            return result
        self._process.terminate()
        try:
            self._process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=3)
        self._close_process_log()
        result = {"success": True, "status": "stopped", "process": self.process_status()}
        self._emit_process_state("stopped", result)
        self._notify("TinyProgrammer stopped", "Blackfong stopped the TinyProgrammer process it launched.", "info")
        return result

    def record_startup_retry(self, *, retry_count: int, delay_seconds: float) -> None:
        self._retry_count = retry_count
        self._next_retry_at = time.time() + max(0.0, float(delay_seconds))

    def record_startup_failure(self, *, reason_code: str, summary: str) -> None:
        self._last_error_code = str(reason_code or "managed_launch_failed")
        self._last_error_summary = str(summary or "TinyProgrammer startup failed.")
        self._next_retry_at = None

    def record_startup_success(self) -> None:
        self._retry_count = 0
        self._next_retry_at = None
        self._last_error_code = None
        self._last_error_summary = None

    def classify_recent_failure(self) -> tuple[Optional[str], Optional[str]]:
        log = self.get_log_tail(lines=80, max_chars=4000)
        content = str(log.get("content") or "")
        lowered = content.lower()
        if "response ended prematurely" in lowered:
            return "stream_ended_prematurely", "TinyProgrammer lost its Ollama response stream before completion."
        if "ollama timed out" in lowered or "timed out" in lowered:
            return "ollama_timeout", "TinyProgrammer timed out while waiting for the local model backend."
        if "review" in lowered and "error" in lowered:
            return "review_loop_failed", "TinyProgrammer hit an error during its review/fix loop."
        if "launch failed" in lowered:
            return "managed_launch_failed", "TinyProgrammer failed during managed launch."
        if "not found" in lowered:
            return "not_started", "TinyProgrammer could not find its expected runtime or dependency."
        return None, None

    def _open_process_log(self) -> IO[str]:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.log_path.open("a", encoding="utf-8", errors="replace")
        handle.write(f"\n=== TinyProgrammer launch {datetime.now().isoformat(timespec='seconds')} ===\n")
        handle.flush()
        return handle

    def _close_process_log(self) -> None:
        if self._process_log_handle is None:
            return
        try:
            self._process_log_handle.flush()
            self._process_log_handle.close()
        except Exception:
            pass
        finally:
            self._process_log_handle = None

    def handle_managed_shutdown(
        self,
        *,
        interactive: bool = False,
        prompt: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        status = self.get_status(require_stream=self.auto_stream)
        process = status.get("process", {})
        if process.get("ownership") != "managed" or process.get("management_state") not in {"running", "launching", "degraded"}:
            return {"success": True, "status": "no_managed_companion", "tinyprogrammer": status}
        policy = str(self.shutdown_policy or "ask").lower()
        should_stop = False
        if policy == "stop":
            should_stop = True
        elif policy == "leave_running":
            should_stop = False
        elif interactive and prompt is not None:
            try:
                should_stop = bool(prompt())
            except Exception:
                should_stop = False
        if should_stop:
            return self.stop()
        result = {
            "success": True,
            "status": "left_running",
            "message": "TinyProgrammer was left running after Blackfong shutdown.",
            "tinyprogrammer": status,
        }
        self._emit_process_state("left_running", result)
        self._notify("TinyProgrammer left running", result["message"], "info")
        return result

    def send_brief(self, brief: str, *, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        scope_decision = self.evaluate_companion_brief_scope(brief, kind="brief")
        scope_event = self._emit_scope_decision(scope_decision, metadata=metadata)
        if scope_decision["decision_state"] != "approve":
            return {
                "success": False,
                "mode": "blocked_by_blackfong_scope",
                "brief": brief,
                "companion_scope_decision": scope_decision,
                "scope_event_id": scope_event.id,
            }

        payload = {"brief": brief, **(metadata or {})}
        live_result = self._post_json("/api/brief", payload)
        if live_result is None:
            self._update_local_overrides(
                {
                    "DAISE_ACTIVE_BRIEF": brief,
                    "DAISE_LAST_BRIEF_METADATA": metadata or {},
                }
            )
            live_result = {"success": True, "mode": "offline_override", "brief": brief}

        event = self.memory_service.persist_workflow_event(
            source="daise",
            event_type="tinyprogrammer_brief_applied",
            payload={
                "brief": brief,
                "metadata": metadata or {},
                "result": live_result,
                "companion_scope_decision": scope_decision,
            },
            confidence=1.0,
            tags=["tinyprogrammer", "brief"],
        )
        live_result["event_id"] = event.id
        live_result["scope_event_id"] = scope_event.id
        live_result["companion_scope_decision"] = scope_decision
        return live_result

    def send_control(self, action: str, value: Any = None) -> Dict[str, Any]:
        scope_decision = self.evaluate_companion_brief_scope(str(value or action), kind=f"control:{action}")
        scope_event = self._emit_scope_decision(scope_decision, metadata={"action": action, "value": value})
        if scope_decision["decision_state"] != "approve":
            return {
                "success": False,
                "mode": "blocked_by_blackfong_scope",
                "action": action,
                "value": value,
                "companion_scope_decision": scope_decision,
                "scope_event_id": scope_event.id,
            }
        payload = {"action": action, "value": value}
        live_result = self._post_live_control(action, value)
        if live_result is None:
            live_result = self._post_json("/api/control", payload)
        if live_result is None:
            live_result = self._apply_offline_control(action, value)
        live_result["companion_scope_decision"] = scope_decision
        live_result["scope_event_id"] = scope_event.id
        if not live_result.get("success", True):
            self._notify(
                "TinyProgrammer control failed",
                str(live_result.get("error") or live_result.get("message") or f"{action} unavailable"),
                "warning",
            )
        return live_result

    def rate_last_creation(self, rating: int, *, feedback: str = "") -> Dict[str, Any]:
        rating = max(1, min(5, int(rating)))
        payload = {"rating": rating, "feedback": feedback}
        live_result = self._post_json("/api/feedback", payload)
        if live_result is None:
            self._update_local_overrides({"DAISE_LAST_FEEDBACK": payload})
            live_result = {"success": True, "mode": "offline_override", **payload}

        event = self.memory_service.persist_workflow_event(
            source="daise",
            event_type="tinyprogrammer_feedback",
            payload=payload,
            confidence=rating / 5.0,
            tags=["tinyprogrammer", "feedback"],
        )
        live_result["event_id"] = event.id
        return live_result

    def _fetch_live_status(self) -> Optional[Dict[str, Any]]:
        response = self._get_json("/api/status")
        if response is None:
            return None
        response["mode"] = "live"
        response["online"] = True
        return response

    def _build_offline_status(self) -> Dict[str, Any]:
        overrides = self._load_overrides()
        recent = self._load_recent_programs(limit=1)
        latest = recent[0] if recent else {}
        return {
            "mode": "offline_snapshot",
            "online": False,
            "state": "offline",
            "mood": overrides.get("DAISE_PREFERRED_MOOD") or overrides.get("BRAIN_MOOD") or "unknown",
            "programs_written": len(self._load_recent_programs(limit=1000)),
            "current_program_type": latest.get("program_type"),
            "theme": overrides.get("DAISE_THEME", ""),
            "tempo": overrides.get("DAISE_TEMPO", ""),
            "model": overrides.get("LLM_MODEL", ""),
            "brief": overrides.get("DAISE_ACTIVE_BRIEF", ""),
            "bbs_enabled": bool(overrides.get("BBS_ENABLED", False)),
            "stream_enabled": False,
            "schedule_enabled": bool(overrides.get("SCHEDULE_ENABLED", False)),
            "is_clocked_in": None,
            "force_screensaver": False,
            "project_path": str(self.project_path),
            "base_url": self.base_url,
            "stream_url": f"{self.base_url}/stream",
        }

    def _sync_status_event(self, status: Dict[str, Any]) -> None:
        event = self.memory_service.persist_workflow_event(
            source="tinyprogrammer",
            event_type="tinyprogrammer_status",
            payload=status,
            confidence=1.0,
            tags=["tinyprogrammer", "status"],
        )
        current_state = str(status.get("state") or "")
        if current_state and current_state != self._last_state:
            self.memory_service.persist_workflow_event(
                source="tinyprogrammer",
                event_type="tinyprogrammer_state_transition",
                payload={"state": current_state, "status_event_id": event.id},
                confidence=1.0,
                tags=["tinyprogrammer", "state"],
            )
            self._notify(
                "TinyProgrammer state changed",
                f"TinyProgrammer is now {current_state}.",
                "info" if status.get("online") else "warning",
                desktop=False,
            )
            self._last_state = current_state

    def _decorate_status(
        self,
        status: Dict[str, Any],
        *,
        live: Optional[bool] = None,
        require_stream: Optional[bool] = None,
    ) -> Dict[str, Any]:
        status = dict(status)
        process = self.process_status(
            live=live,
            live_status=status if status.get("online") else None,
            require_stream=self.auto_stream if require_stream is None else require_stream,
        )
        status["process"] = process
        status["project_path"] = status.get("project_path") or str(self.project_path)
        status["base_url"] = status.get("base_url") or self.base_url
        status["stream_url"] = status.get("stream_url") or f"{self.base_url}/stream"
        status["resource_state"] = process.get("resource_state", "unknown")
        status["cpu_percent"] = process.get("cpu_percent")
        status["memory_mb"] = process.get("memory_mb")
        status["ownership"] = process.get("ownership")
        status["management_state"] = process.get("management_state")
        status["stream_required"] = process.get("stream_required")
        status["autostart_enabled"] = process.get("autostart_enabled")
        status["shutdown_policy"] = process.get("shutdown_policy")
        status["native_window_state"] = process.get("native_window_state")
        status["native_window_human_reason"] = process.get("native_window_human_reason")
        status["stream_load_state"] = self._stream_load_state(status, process)
        status["stream_available"] = bool(status.get("online")) and bool(status.get("stream_enabled"))
        status["stream_reason"] = self._stream_reason(status, process)
        status["stream_human_reason"] = self._stream_human_reason(status)
        status["bbs_reason"] = self._bbs_reason(status)
        status["bbs_human_reason"] = self._bbs_human_reason(status)
        status["schedule_reason"] = self._schedule_reason(status)
        status["schedule_human_reason"] = self._schedule_human_reason(status)
        status["live_control_reason"] = "available" if status.get("online") else "offline_unreachable"
        status["live_control_human_reason"] = (
            "TinyProgrammer live controls are available."
            if status.get("online")
            else "TinyProgrammer is offline or unreachable from O.B.E.O.S."
        )
        inferred_code, inferred_summary = self.classify_recent_failure()
        status["retry_count"] = self._retry_count
        status["max_retries"] = self.max_startup_retries
        status["last_error_code"] = self._last_error_code or inferred_code
        status["last_error_summary"] = self._last_error_summary or inferred_summary
        status["next_retry_at"] = self._next_retry_at
        status["launch_attempts"] = self._launch_attempts
        status["companion_views"] = {
            "native_window": {
                "state": status["native_window_state"],
                "human_reason": status["native_window_human_reason"],
            },
            "live_stream": {
                "state": "available" if status.get("stream_available") else "unavailable",
                "reason_code": status.get("stream_reason"),
                "human_reason": status.get("stream_human_reason"),
                "url": status.get("stream_url"),
            },
        }
        return status

    @staticmethod
    def _native_window_state(*, live: bool, ownership: str, management_state: str) -> str:
        if not live and management_state in {"offline_snapshot", "error"}:
            return "not_visible"
        if ownership in {"managed", "external"} and management_state in {"running", "degraded", "launching"}:
            return "visible_native_app"
        return "unknown"

    @staticmethod
    def _native_window_human_reason(*, live: bool, ownership: str, management_state: str) -> str:
        state = TinyProgrammerCompanion._native_window_state(
            live=live,
            ownership=ownership,
            management_state=management_state,
        )
        mapping = {
            "visible_native_app": "TinyProgrammer keeps its native companion window visible by default in this phase.",
            "not_visible": "TinyProgrammer is offline, so its native companion window is not expected to be visible.",
            "unknown": "TinyProgrammer native window visibility could not be confirmed from the current process state.",
        }
        return mapping.get(state, "TinyProgrammer native window visibility is unknown.")

    def _sync_recent_events(self, creations: List[Dict[str, Any]], reflections: List[Dict[str, Any]]) -> None:
        for creation in creations:
            external_id = creation.get("id") or creation.get("filename")
            if not external_id or self._workflow_event_exists("tinyprogrammer_program_archived", external_id):
                continue
            payload = {"external_id": external_id, **creation}
            self.memory_service.persist_workflow_event(
                source="tinyprogrammer",
                event_type="tinyprogrammer_program_created",
                payload=payload,
                confidence=1.0,
                tags=["tinyprogrammer", "program"],
            )
            self.memory_service.persist_workflow_event(
                source="tinyprogrammer",
                event_type="tinyprogrammer_program_archived",
                payload=payload,
                confidence=1.0,
                tags=["tinyprogrammer", "archive"],
            )
            self._notify(
                "TinyProgrammer created something",
                f"{creation.get('program_type', 'program')} - {creation.get('filename', external_id)}",
                "info",
            )

        for reflection in reflections:
            external_id = reflection.get("external_id")
            if not external_id or self._workflow_event_exists("tinyprogrammer_reflection", external_id):
                continue
            self.memory_service.persist_workflow_event(
                source="tinyprogrammer",
                event_type="tinyprogrammer_reflection",
                payload=reflection,
                confidence=1.0,
                tags=["tinyprogrammer", "reflection"],
            )

    def _resource_metrics(self, pid: Optional[int], *, active: bool) -> Dict[str, Any]:
        if not pid or not active:
            return {
                "cpu_percent": None,
                "memory_mb": None,
                "resource_state": "inactive",
            }
        if psutil is None:
            return {
                "cpu_percent": None,
                "memory_mb": None,
                "resource_state": "metrics_unavailable",
            }
        try:
            process = psutil.Process(pid)
            return {
                "cpu_percent": round(float(process.cpu_percent(interval=0.0)), 2),
                "memory_mb": round(float(process.memory_info().rss) / (1024 * 1024), 2),
                "resource_state": "available",
            }
        except Exception:
            return {
                "cpu_percent": None,
                "memory_mb": None,
                "resource_state": "unavailable",
            }

    @staticmethod
    def _stream_load_state(status: Dict[str, Any], process: Dict[str, Any]) -> str:
        if not status.get("online"):
            return "offline"
        if not status.get("stream_enabled"):
            return "disabled"
        if process.get("resource_state") == "available":
            return "ready"
        return "stream_enabled"

    def _stream_reason(self, status: Dict[str, Any], process: Dict[str, Any]) -> str:
        if status.get("stream_available"):
            return "available"
        if not status.get("online"):
            return "companion_offline"
        if process.get("ownership") == "external":
            return "external_no_stream"
        if process.get("launched_by_blackfong"):
            return "not_started_with_stream"
        return "feature_disabled"

    def _stream_human_reason(self, status: Dict[str, Any]) -> str:
        reason = self._stream_reason(status, status.get("process", {}))
        mapping = {
            "available": "TinyProgrammer web stream is available.",
            "companion_offline": "TinyProgrammer is offline, so the live stream is unavailable.",
            "external_no_stream": "TinyProgrammer is running externally without WEB_STREAM_ENABLED=true.",
            "not_started_with_stream": "TinyProgrammer was started without WEB_STREAM_ENABLED=true.",
            "feature_disabled": "TinyProgrammer stream is not enabled on the current instance.",
        }
        return mapping.get(reason, "TinyProgrammer stream state is unknown.")

    @staticmethod
    def _management_state(
        *,
        live: bool,
        ownership: str,
        launched_by_blackfong: bool,
        return_code: Optional[int],
        stream_required: bool,
        stream_enabled: bool,
    ) -> str:
        if live:
            if stream_required and not stream_enabled:
                return "degraded"
            return "running"
        if launched_by_blackfong and return_code is None:
            return "launching"
        if return_code not in {None, 0}:
            return "error"
        return "offline_snapshot"

    def _bbs_reason(self, status: Dict[str, Any]) -> str:
        if status.get("bbs_enabled"):
            return "available"
        overrides = self._load_overrides()
        if overrides.get("BBS_ENABLED") is False:
            return "feature_disabled"
        bbs_env = [
            os.getenv("BBS_SUPABASE_URL") or "",
            os.getenv("BBS_SUPABASE_ANON_KEY") or "",
            os.getenv("BBS_EDGE_FUNCTION_URL") or "",
        ]
        if any(bbs_env):
            return "bbs_client_unavailable"
        return "unconfigured_bbs"

    def _bbs_human_reason(self, status: Dict[str, Any]) -> str:
        reason = self._bbs_reason(status)
        mapping = {
            "available": "TinyProgrammer BBS access is available.",
            "feature_disabled": "BBS is disabled by TinyProgrammer configuration.",
            "bbs_client_unavailable": "BBS credentials exist, but TinyProgrammer did not initialize its BBS client.",
            "unconfigured_bbs": "BBS is not configured for this TinyProgrammer instance.",
        }
        return mapping.get(reason, "BBS status is unknown.")

    @staticmethod
    def _schedule_reason(status: Dict[str, Any]) -> str:
        if not status.get("schedule_enabled"):
            return "no_schedule_loaded"
        if status.get("is_clocked_in"):
            return "scheduled_active"
        return "outside_schedule"

    def _schedule_human_reason(self, status: Dict[str, Any]) -> str:
        reason = self._schedule_reason(status)
        mapping = {
            "no_schedule_loaded": "TinyProgrammer is not using a work schedule.",
            "scheduled_active": "TinyProgrammer is currently inside its work schedule.",
            "outside_schedule": "TinyProgrammer is outside its work schedule and may be sleeping or screensaving.",
        }
        return mapping.get(reason, "Schedule status is unknown.")

    def _workflow_event_exists(self, event_type: str, external_id: str) -> bool:
        events = self.event_store.list_events_by_type(event_type, limit=300)
        for event in events:
            if str(event.payload.get("external_id")) == str(external_id):
                return True
        return False

    def evaluate_companion_brief_scope(self, text: str, *, kind: str = "brief") -> Dict[str, Any]:
        normalized = str(text or "").strip().lower()
        kind_lower = kind.lower()
        allowed_controls = {
            "control:restart",
            "control:screensaver",
            "control:model",
            "control:mood",
            "control:theme",
            "control:tempo",
            "control:program_weights",
            "control:nudge",
            "control:brief",
        }
        denied_hits: List[Dict[str, Any]] = []
        for category, patterns in BRIEF_DENY_PATTERNS.items():
            for pattern in patterns:
                if pattern in normalized:
                    denied_hits.append({"category": category, "pattern": pattern})

        approved = not denied_hits
        if kind_lower.startswith("control:") and kind_lower not in allowed_controls:
            approved = False
            denied_hits.append({"category": "unsupported_control", "pattern": kind})

        return {
            "schema_version": "1.0",
            "kind": kind,
            "decision_state": "approve" if approved else "reject",
            "scope_status": "accepted" if approved else "rejected",
            "companion_brief_scope": COMPANION_BRIEF_SCOPE,
            "denied_hits": denied_hits,
            "reason": "within_companion_scope" if approved else "brief_exceeds_companion_scope",
            "provenance": {
                "source": "companion_brief_scope",
                "origin_service": "tinyprogrammer_companion",
                "derived_from": [],
                "collection_method": "scope_keyword_policy",
                "observed_at": time.time(),
                "schema_version": "1.0",
            },
        }

    def _emit_scope_decision(self, scope_decision: Dict[str, Any], *, metadata: Optional[Dict[str, Any]] = None):
        return self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="companion_brief_scope_decision",
            payload={**scope_decision, "metadata": metadata or {}},
            confidence=0.94 if scope_decision.get("decision_state") == "approve" else 0.82,
            tags=["tinyprogrammer", "scope"],
        )

    def _load_recent_programs(self, *, limit: int) -> List[Dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        recent = data[-limit:]
        enriched: List[Dict[str, Any]] = []
        for item in reversed([item for item in recent if isinstance(item, dict)]):
            filename = item.get("filename")
            if filename:
                program_path = self.project_path / "programs" / "programs" / str(filename)
                if not program_path.exists():
                    program_path = self.project_path / "programs" / str(filename)
                item = {**item, "path": str(program_path), "exists": program_path.exists()}
                if program_path.exists():
                    try:
                        item["code_preview"] = program_path.read_text(encoding="utf-8", errors="replace")[:1200]
                    except OSError:
                        item["code_preview"] = ""
            enriched.append(item)
        return enriched

    def _load_recent_lessons(self, *, limit: int) -> List[Dict[str, Any]]:
        if not self.lessons_path.exists():
            return []
        try:
            lines = self.lessons_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        lessons = []
        for index, line in enumerate(lines):
            if not line.startswith("- "):
                continue
            text = line[2:].strip()
            if not text:
                continue
            lessons.append(
                {
                    "external_id": f"lesson-{index}",
                    "text": text,
                }
            )
        return list(reversed(lessons[-limit:]))

    def _apply_offline_control(self, action: str, value: Any) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        normalized = str(action).strip().lower()
        if normalized == "restart":
            return {"success": False, "mode": "offline_override", "error": "restart requires live TinyProgrammer web API"}
        if normalized == "screensaver":
            return {"success": False, "mode": "offline_override", "error": "screensaver toggle requires live TinyProgrammer web API"}
        if normalized == "model":
            updates["LLM_MODEL"] = value
        elif normalized == "mood":
            updates["DAISE_PREFERRED_MOOD"] = value
        elif normalized == "theme":
            updates["DAISE_THEME"] = value
        elif normalized == "tempo":
            updates["DAISE_TEMPO"] = value
        elif normalized == "program_weights":
            updates["DAISE_PROGRAM_WEIGHTS"] = value
        elif normalized == "nudge":
            updates["DAISE_ACTIVE_BRIEF"] = value
        else:
            return {"success": False, "mode": "offline_override", "error": f"unsupported control action: {action}"}
        self._update_local_overrides(updates)
        return {"success": True, "mode": "offline_override", "action": normalized, "value": value, "updates": updates}

    def _post_live_control(self, action: str, value: Any) -> Optional[Dict[str, Any]]:
        normalized = str(action or "").strip().lower()
        if normalized == "restart":
            return self._post_json("/api/restart", {})
        if normalized == "screensaver":
            state = str(value or "").strip().lower()
            if state in {"on", "true", "1"}:
                return self._post_json("/api/screensaver/on", {})
            if state in {"off", "false", "0"}:
                return self._post_json("/api/screensaver/off", {})
        return None

    def _get_json(self, path: str) -> Optional[Dict[str, Any]]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.base_url}{path}")
                response.raise_for_status()
                payload = response.json()
            if isinstance(payload, dict):
                return payload
            return None
        except Exception:
            return None

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(f"{self.base_url}{path}", json=payload)
                response.raise_for_status()
                data = response.json()
            if isinstance(data, dict):
                return data
            return None
        except Exception:
            return None

    def _emit_process_state(self, state: str, payload: Dict[str, Any]) -> None:
        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="tinyprogrammer_process_state",
            payload={
                "schema_version": "1.0",
                "state": state,
                "process": self.process_status(),
                "result": payload,
                "provenance": {
                    "source": "local_process_manager",
                    "origin_service": "tinyprogrammer_companion",
                    "derived_from": [],
                    "collection_method": "process_state_probe",
                    "observed_at": time.time(),
                    "schema_version": "1.0",
                },
            },
            confidence=0.9,
            tags=["tinyprogrammer", "process", state],
        )

    def _emit_operator_consent(self, action: str, target: str, decision: Dict[str, Any], consent: Optional[Dict[str, Any]]) -> None:
        self.memory_service.persist_workflow_event(
            source="blackfong",
            event_type="operator_consent",
            payload={
                "schema_version": "1.0",
                "level": decision.get("provided_level"),
                "required_level": decision.get("required_level"),
                "action": action,
                "target": target,
                "approved": decision.get("approved"),
                "expires_at": (consent or {}).get("expires_at") or time.time() + 300,
                "policy_decision": decision,
            },
            confidence=0.95 if decision.get("approved") else 0.7,
            tags=["consent", action],
        )

    def _notify(self, title: str, message: str, level: str = "info", *, desktop: bool = False) -> None:
        try:
            self.notification_service.create(
                title=title,
                message=message,
                level=level,
                category="tinyprogrammer",
                desktop=desktop,
            )
        except Exception:
            return

    def _load_overrides(self) -> Dict[str, Any]:
        if not self.overrides_path.exists():
            return {}
        try:
            payload = json.loads(self.overrides_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _update_local_overrides(self, updates: Dict[str, Any]) -> None:
        overrides = self._load_overrides()
        overrides.update(updates)
        self.overrides_path.parent.mkdir(parents=True, exist_ok=True)
        self.overrides_path.write_text(json.dumps(overrides, indent=2), encoding="utf-8")


tinyprogrammer_companion = TinyProgrammerCompanion()
