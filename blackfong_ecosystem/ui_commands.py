"""Shared command registry for OBEOS Desktop, TUI, and Web HUD."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from blackfong_ecosystem.identity_semantics import identity_registry
from blackfong_ecosystem.service_registry import UIServiceContainer, get_ui_services

logger = logging.getLogger(__name__)

CommandResult = Dict[str, Any]
CommandHandler = Callable[["CommandContext", str], CommandResult]


@dataclass
class CommandDefinition:
    command: str
    description: str
    category: str = "General"
    usage: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    autocomplete: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "description": self.description,
            "category": self.category,
            "usage": self.usage or self.command,
            "aliases": list(self.aliases),
            "autocomplete": list(self.autocomplete),
        }


@dataclass
class CommandContext:
    services: UIServiceContainer = field(default_factory=get_ui_services)
    source: str = "shared"
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    orchestrator: Any = None
    ensure_orchestrator: Optional[Callable[[], bool]] = None
    voice_status_provider: Optional[Callable[[], Dict[str, Any]]] = None
    metrics_provider: Optional[Callable[[], Dict[str, Any]]] = None
    awareness_snapshot_provider: Optional[Callable[[bool], Dict[str, Any]]] = None
    cognition_snapshot_provider: Optional[Callable[[Optional[str]], Dict[str, Any]]] = None
    set_cognition_layer: Optional[Callable[[str], Dict[str, Any]]] = None
    sensor_mode_setter: Optional[Callable[[str], Dict[str, Any]]] = None

    def voice_status(self) -> Dict[str, Any]:
        if self.voice_status_provider is not None:
            return self.voice_status_provider()
        if self.orchestrator is None:
            return {"active": False, "message": "Voice loop unavailable"}
        return self.orchestrator.get_voice_loop_status()

    def awareness_snapshot(self, *, emit: bool = False) -> Dict[str, Any]:
        if self.awareness_snapshot_provider is not None:
            return self.awareness_snapshot_provider(emit)
        return self.services.awareness_service.build_snapshot(voice_status=self.voice_status(), emit=emit)

    def cognition_snapshot(self, *, layer: Optional[str] = None) -> Dict[str, Any]:
        if self.set_cognition_layer is not None and layer:
            self.set_cognition_layer(layer)
        if self.cognition_snapshot_provider is not None:
            return self.cognition_snapshot_provider(layer)
        return self.services.cognition_service.build_snapshot(
            session_id=self.session_id,
            request_id=self.request_id,
            layer=layer or "live_brain",
            focus={"layer": layer or "live_brain", "mode": "thoughtglass"},
            request_states={},
            request_threads={},
            backend_bootstrap={"status": "idle", "started_at": None, "completed_at": None, "error": None},
            sensor_mode="off",
            voice_status=self.voice_status(),
        )


class CommandRegistry:
    """Registry-backed dispatcher for UI commands."""

    def __init__(self) -> None:
        self._definitions: List[CommandDefinition] = []
        self._handlers: Dict[str, CommandHandler] = {}
        self._aliases: Dict[str, str] = {}

    def register(self, definition: CommandDefinition, handler: CommandHandler, *, family: Optional[str] = None) -> None:
        self._definitions.append(definition)
        key = family or _primary(definition.command)
        self._handlers[key] = handler
        for alias in definition.aliases:
            self._aliases[_normalize(alias)] = key

    def execute(self, command_line: str, context: Optional[CommandContext] = None) -> CommandResult:
        command = _normalize(command_line)
        context = context or CommandContext()
        if not command:
            return _error(command, "Command is required")
        key = self._aliases.get(command) or _primary(command)
        handler = self._handlers.get(key)
        if handler is None:
            return _error(command, "Unknown command")
        try:
            result = handler(context, command)
        except Exception as exc:
            logger.exception("Command failed: %s", command)
            result = _error(command, str(exc))
        if "command" not in result:
            result["command"] = command
        status = str(result.get("status") or "")
        context.services.event_bus.publish(
            "command_executed",
            {
                "command": command,
                "status": status or ("error" if result.get("error") else "ok"),
                "source": context.source,
                "session_id": context.session_id,
                "request_id": context.request_id,
            },
        )
        return result

    def catalog(self) -> List[Dict[str, Any]]:
        return [definition.to_dict() for definition in self._definitions]

    def completions(self, prefix: str = "") -> List[str]:
        normalized = _normalize(prefix)
        commands = [definition.command for definition in self._definitions]
        if not normalized:
            return commands
        return [command for command in commands if command.startswith(normalized)]


def _normalize(command: str) -> str:
    return " ".join(str(command or "").strip().split())


def _primary(command: str) -> str:
    return _normalize(command).split(" ", 1)[0]


def _parts(command: str) -> List[str]:
    return _normalize(command).split(" ")


def _error(command: str, message: str, *, status: str = "error") -> CommandResult:
    return {"status": status, "command": command, "error": message}


def _with_command(command: str, result: Mapping[str, Any]) -> CommandResult:
    merged = dict(result)
    merged["command"] = command
    return merged


def _int_payload(context: CommandContext, name: str, default: int, *, minimum: int = 1, maximum: int = 500) -> int:
    try:
        value = int(context.payload.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _safe_path(raw_path: str, *, command: str, usage: str) -> tuple[Optional[str], Optional[CommandResult]]:
    path = str(raw_path or "").strip()
    if not path:
        return None, _error(command, usage)
    if "\x00" in path:
        return None, _error(command, "Path contains invalid characters")
    return path, None


def _status(context: CommandContext, command: str) -> CommandResult:
    memory_stats = {}
    try:
        memory_stats = context.services.memory_service.get_memory_stats()
    except Exception:
        logger.exception("Unable to load memory stats")
    metrics = context.metrics_provider() if context.metrics_provider is not None else {}
    return {"status": "ok", "command": command, "metrics": metrics, "memory": memory_stats, "voice": context.voice_status()}


def _sessions(context: CommandContext, command: str) -> CommandResult:
    return {"status": "ok", "command": command, "sessions": context.services.memory_service.list_sessions(limit=_int_payload(context, "limit", 100))}


def _memory(context: CommandContext, command: str) -> CommandResult:
    return {
        "status": "ok",
        "command": command,
        "memory": context.services.memory_service.list_global_memories(
            query=str(context.payload.get("query") or ""),
            limit=_int_payload(context, "limit", 12),
            agent_name=context.payload.get("agent"),
            session_id=context.session_id,
        ),
    }


def _notifications(context: CommandContext, command: str) -> CommandResult:
    if command == "/notifications read-all":
        return {"status": "ok", "command": command, **context.services.notification_service.mark_all_read()}
    return {"status": "ok", "command": command, **context.services.notification_service.list_notifications(limit=_int_payload(context, "limit", 50))}


def _what_changed(context: CommandContext, command: str) -> CommandResult:
    return {
        "status": "ok",
        "command": command,
        "changes": context.services.operator_actions.summarize_changes(
            session_id=context.session_id,
            since_timestamp=context.payload.get("since_timestamp"),
        ),
    }


def _cognition(context: CommandContext, command: str) -> CommandResult:
    requested_layer = command.split(" ", 1)[1].strip().lower().replace(" ", "_") if " " in command else "live_brain"
    snapshot = context.cognition_snapshot(layer=requested_layer)
    return {
        "status": "ok",
        "command": command,
        "focus": snapshot.get("focus", {}),
        "cognition": snapshot,
        "ascii_summary": snapshot.get("ascii_summary", ""),
    }


def _trace(context: CommandContext, command: str) -> CommandResult:
    request_id = str(context.request_id or context.payload.get("request_id") or "")
    if not request_id:
        return _error(command, "request_id is required")
    return {"status": "ok", "command": command, "trace": context.services.memory_service.get_request_trace(request_id)}


def _pin(context: CommandContext, command: str) -> CommandResult:
    if not context.session_id:
        return _error(command, "session_id is required")
    event = context.services.memory_service.pin_session_memory(
        context.session_id,
        text=context.payload.get("text"),
        title=context.payload.get("title"),
        event_id=context.payload.get("event_id"),
        tags=context.payload.get("tags") or [],
    )
    return {"status": "ok", "command": command, "event_id": event.id}


def _desktop(context: CommandContext, command: str) -> CommandResult:
    subcommand = _parts(command)[1].lower() if len(_parts(command)) > 1 else "status"
    platform = context.services.platform_service
    if subcommand == "status":
        return {"status": "ok", "command": command, "desktop": platform.desktop_status()}
    if subcommand == "start":
        return _with_command(command, platform.desktop_start(execute=True))
    if subcommand == "stop":
        return _with_command(command, platform.desktop_stop(execute=True))
    if subcommand == "home":
        return _with_command(command, platform.home(execute=True))
    if subcommand == "workspace":
        return _with_command(command, platform.launch_workspace(execute=True))
    return _error(command, "Use /desktop status, /desktop start, /desktop stop, /desktop home, or /desktop workspace")


def _home(context: CommandContext, command: str) -> CommandResult:
    return _with_command(command, context.services.platform_service.home(execute=True))


def _docker(context: CommandContext, command: str) -> CommandResult:
    subcommand = _parts(command)[1].lower() if len(_parts(command)) > 1 else "status"
    docker = context.services.docker_stack
    if subcommand == "status":
        return {"status": "ok", "command": command, "docker": docker.status()}
    if subcommand == "up":
        return _with_command(command, docker.up(execute=True))
    if subcommand == "down":
        return _with_command(command, docker.down(execute=True))
    if subcommand == "logs":
        return _with_command(command, docker.logs(execute=True))
    return _error(command, "Use /docker status, /docker up, /docker down, or /docker logs")


def _obeos(context: CommandContext, command: str) -> CommandResult:
    subcommand = command.split(" ", 1)[1].strip().lower() if " " in command else "manifest"
    platform = context.services.platform_service
    if subcommand == "installer-status":
        from blackfong_ecosystem.installer_plan import InstallerPlanService

        return {"status": "ok", "command": command, "installer_status": InstallerPlanService().current_status()}
    if subcommand == "manifest":
        return {"status": "ok", "command": command, "obeos": platform.manifest()}
    if subcommand == "shell-compat":
        return {"status": "ok", "command": command, "shell_compatibility": platform.shell_compatibility_status()}
    if subcommand == "vm-checklist":
        return {"status": "ok", "command": command, "vm_checklist": platform.vm_checklist_markdown()}
    if subcommand == "vm-acceptance-snapshot":
        return {"status": "ok", "command": command, "vm_acceptance_snapshot": platform.vm_acceptance_snapshot()}
    if subcommand == "activate-home":
        return _with_command(command, platform.activate_home_boot(execute=True))
    return _error(command, "Use /obeos installer-status, /obeos manifest, /obeos shell-compat, /obeos vm-checklist, /obeos vm-acceptance-snapshot, or /obeos activate-home")


def _summary(context: CommandContext, command: str) -> CommandResult:
    return {
        "status": "ok",
        "command": command,
        "summary": context.services.operator_summary.build_summary(
            session_id=context.session_id,
            request_id=context.request_id,
            include_awareness=True,
        ),
    }


def _files(context: CommandContext, command: str) -> CommandResult:
    parts = command.split(" ", 2)
    subcommand = parts[1].strip().lower() if len(parts) > 1 else "policy"
    remainder = parts[2] if len(parts) > 2 else ""
    file_workflow = context.services.file_workflow
    file_scope = context.services.file_scope_service
    if subcommand == "policy":
        policy = file_workflow.workspace_policy()
        return {"status": "ok", "command": command, "files": policy, "policy": policy}
    if subcommand == "proposals":
        return {"status": "ok", "command": command, **file_workflow.list_proposals()}
    if subcommand == "roots":
        return {"status": "ok", "command": command, "files": file_scope.list_roots(session_id=context.session_id)}
    if subcommand == "approve-root":
        path, error = _safe_path(remainder, command=command, usage="Use /files approve-root <path>")
        if error:
            return error
        return {"status": "ok", "command": command, "files": file_scope.approve_root(path, session_id=context.session_id, request_id=context.request_id)}
    if subcommand == "map":
        path, error = _safe_path(remainder, command=command, usage="Use /files map <path>")
        if error:
            return error
        return {"status": "ok", "command": command, "files": file_scope.map_path(path, session_id=context.session_id, request_id=context.request_id)}
    if subcommand == "scan":
        path = remainder.strip() or "."
        if "\x00" in path:
            return _error(command, "Path contains invalid characters")
        return {"status": "ok", "command": command, **file_workflow.scan(path)}
    if subcommand == "read":
        path, error = _safe_path(remainder, command=command, usage="Use /files read <path>")
        if error:
            return error
        return {"status": "ok", "command": command, "files": file_scope.read_path(path, session_id=context.session_id, request_id=context.request_id)}
    return _error(command, "Use /files policy, /files proposals, /files roots, /files approve-root <path>, /files map <path>, /files scan <path>, or /files read <path>")


def _awareness_family(context: CommandContext, command: str) -> CommandResult:
    primary = _primary(command)
    if command == "/sensors snapshot":
        if context.sensor_mode_setter is not None:
            context.sensor_mode_setter("snapshot")
        snapshot = context.awareness_snapshot(emit=True)
        return {"status": "ok", "command": command, "sensor_mode": "snapshot", "sensors": snapshot.get("sensors", {})}
    if command == "/sensors live stop":
        if context.sensor_mode_setter is not None:
            context.sensor_mode_setter("off")
        snapshot = context.awareness_snapshot(emit=True)
        return {"status": "ok", "command": command, "sensor_mode": "off", "sensors": snapshot.get("sensors", {})}
    if command == "/sensors live start":
        consent_decision = context.services.awareness_service.policy_engine.evaluate_consent(
            action="start_live_sensor_mode",
            consent=context.payload.get("consent"),
            target="live_perception_mode",
        )
        if not consent_decision.get("approved"):
            return {"status": "requires_consent", "command": command, "consent": consent_decision}
        if context.sensor_mode_setter is not None:
            context.sensor_mode_setter("live_vision")
        snapshot = context.awareness_snapshot(emit=True)
        return {"status": "ok", "command": command, "sensor_mode": "live_vision", "consent": consent_decision, "sensors": snapshot.get("sensors", {})}

    snapshot = context.awareness_snapshot(emit=primary in {"/awareness", "/diagnose"})
    awareness = context.services.awareness_service
    if primary == "/awareness":
        return {"status": "ok", "command": command, "awareness": snapshot}
    if primary == "/performance":
        return {"status": "ok", "command": command, "performance": awareness.performance_slice(snapshot)}
    if primary == "/backend":
        return {"status": "ok", "command": command, "backend": snapshot.get("observed", {}).get("backend", {})}
    if primary == "/environment":
        return {"status": "ok", "command": command, "environment": awareness.environment_slice(snapshot)}
    if primary == "/sensors":
        return {"status": "ok", "command": command, "sensors": snapshot.get("sensors", {})}
    if primary == "/diagnose":
        return {
            "status": "ok",
            "command": command,
            "diagnostic_summary": snapshot.get("diagnostic_summary", {}),
            "health": snapshot.get("health", {}),
            "inferred": snapshot.get("inferred", {}),
        }
    if primary == "/capabilities":
        return {"status": "ok", "command": command, "capabilities": snapshot.get("observed", {}).get("capabilities", {})}
    if primary == "/limits":
        return {
            "status": "ok",
            "command": command,
            "policy": snapshot.get("policy", {}),
            "capture_policy": snapshot.get("sensors", {}).get("capture_policy", {}),
            "workspace_policy": context.services.file_workflow.workspace_policy(),
        }
    topic = command.split(" ", 1)[1] if " " in command else str(context.payload.get("topic") or "last decision")
    return {"status": "ok", "command": command, "why": awareness.explain_why(topic, request_id=context.request_id, snapshot=snapshot)}


def _identity(context: CommandContext, command: str) -> CommandResult:
    name = command.split(" ", 1)[1] if " " in command else str(context.payload.get("name") or "")
    resolution = identity_registry.resolve(name)
    event = identity_registry.emit_resolution_event(resolution, memory_service=context.services.memory_service, session_id=context.session_id)
    return {"status": "ok", "command": command, "resolution": resolution.to_dict(), "event_id": event.id}


def _voice(context: CommandContext, command: str) -> CommandResult:
    if context.ensure_orchestrator is not None and not context.ensure_orchestrator():
        return _error(command, "Voice loop unavailable")
    if context.orchestrator is None:
        return _error(command, "Voice loop unavailable")
    if command == "/voice-start":
        started = context.orchestrator.start_voice_loop()
        return {"status": "ok" if started else "unchanged", "command": command, "voice": context.voice_status()}
    stopped = context.orchestrator.stop_voice_loop()
    return {"status": "ok" if stopped else "unchanged", "command": command, "voice": context.voice_status()}


def _tiny(context: CommandContext, command: str) -> CommandResult:
    parts = command.split(" ", 2)
    tiny = context.services.tinyprogrammer
    subcommand = parts[1] if len(parts) > 1 else "status"
    remainder = parts[2] if len(parts) > 2 else ""
    if subcommand == "status":
        return {"status": "ok", "command": command, "tinyprogrammer": tiny.get_status(require_stream=True)}
    if subcommand in {"launch", "native"}:
        result = tiny.ensure_running(
            require_stream=True,
            adopt_external=True,
            open_browser=False,
            consent={"approved": True, "level": 1},
        )
        return {"command": command, "tinyprogrammer": result, **result}
    if subcommand == "open":
        result = tiny.open(open_browser=True)
        return {"command": command, "tinyprogrammer": result, **result}
    if subcommand == "live":
        result = tiny.open_live(consent={"approved": True, "level": 1}, open_browser=True)
        return {"command": command, "tinyprogrammer": result, **result}
    if subcommand == "recent":
        return {"status": "ok", "command": command, "tinyprogrammer": tiny.get_recent(limit=12)}
    if subcommand == "bbs":
        return {"status": "ok", "command": command, "tinyprogrammer": tiny.get_bbs(limit=12)}
    if subcommand == "log":
        return {"status": "ok", "command": command, "tinyprogrammer": tiny.get_log_tail(lines=40)}
    if subcommand == "brief":
        if not remainder.strip():
            return _error(command, "Use /tiny brief <text>")
        return {"status": "ok", "command": command, "tinyprogrammer": tiny.send_brief(remainder.strip())}
    if subcommand == "restart":
        return {"status": "ok", "command": command, "tinyprogrammer": tiny.send_control("restart")}
    if subcommand == "screensaver":
        normalized = remainder.strip().lower()
        if normalized not in {"on", "off"}:
            return _error(command, "Use /tiny screensaver <on|off>")
        return {"status": "ok", "command": command, "tinyprogrammer": tiny.send_control("screensaver", normalized)}
    if subcommand in {"mood", "theme", "model", "tempo", "nudge"}:
        return {"status": "ok", "command": command, "tinyprogrammer": tiny.send_control(subcommand, remainder.strip())}
    if subcommand in {"rate", "feedback"}:
        rating_parts = remainder.split(" ", 1)
        try:
            rating = int((rating_parts[0] or "0").strip())
        except ValueError:
            return _error(command, f"Use /tiny {subcommand} <1-5> [feedback]")
        feedback = rating_parts[1] if len(rating_parts) > 1 else ""
        return {"status": "ok", "command": command, "tinyprogrammer": tiny.rate_last_creation(rating, feedback=feedback)}
    return _error(command, "Unknown TinyProgrammer command")


def _dream(context: CommandContext, command: str) -> CommandResult:
    from blackfong_ecosystem.cognitive_expansion import DAISEStyleProfileService, DreamLayerService

    parts = command.split(" ", 2)
    subcommand = parts[1].strip().lower() if len(parts) > 1 else "profile"
    remainder = parts[2].strip() if len(parts) > 2 else ""
    profile_service = DAISEStyleProfileService(context.services.event_store)
    if subcommand == "profile":
        profile = profile_service.generate_profile(persist=True)
        return {"status": "ok", "command": command, **profile}
    if subcommand == "apply":
        overrides = {"theme": remainder} if remainder else None
        profile = profile_service.generate_profile(overrides=overrides, persist=True)["style_profile"]
        result = DreamLayerService(context.services.event_store, tinyprogrammer=context.services.tinyprogrammer).apply_to_tinyprogrammer(profile)
        return {"status": "ok" if result.get("success") else "partial", "command": command, "dream_layer": result}
    return _error(command, "Use /dream profile or /dream apply [theme]")


def _archive(context: CommandContext, command: str) -> CommandResult:
    from blackfong_ecosystem.blackbox_archive import ArchiveCaptureProfileManager

    parts = command.split(" ", 2)
    subcommand = parts[1].strip().lower() if len(parts) > 1 else "profile"
    remainder = parts[2].strip() if len(parts) > 2 else ""
    manager = ArchiveCaptureProfileManager(context.services.event_store)
    if subcommand == "profile" and not remainder:
        return {"status": "ok", "command": command, "archive": manager.current_profile()}
    if subcommand == "profile":
        consent = context.payload.get("consent") if isinstance(context.payload.get("consent"), dict) else {}
        result = manager.set_profile(remainder, consent=consent, reason=str(context.payload.get("reason") or "ui_command"))
        return {"status": "ok" if result.get("activated") else "requires_consent", "command": command, "archive": result}
    return _error(command, "Use /archive profile [bounded_raw|metadata_only|continuous_raw]")


def _verify(context: CommandContext, command: str) -> CommandResult:
    from blackfong_ecosystem.verification import VerificationService

    service = VerificationService(workspace_root=context.services.platform_service.workspace_root, services=context.services)
    parts = command.split(" ", 1)
    subcommand = parts[1].strip().lower() if len(parts) > 1 else "full"
    if subcommand == "automated":
        return {"status": "ok", "command": command, "verification": service.automated()}
    if subcommand == "surfaces":
        return {"status": "ok", "command": command, "verification": service.surfaces()}
    if subcommand == "integrations":
        return {"status": "ok", "command": command, "verification": service.integrations()}
    if subcommand == "full":
        return {"status": "ok", "command": command, "verification": service.full()}
    if subcommand == "bundle":
        return {"status": "ok", "command": command, "verification": service.bundle()}
    return _error(command, "Use /verify automated, /verify surfaces, /verify integrations, /verify bundle, or /verify full")


def _integrations(context: CommandContext, command: str) -> CommandResult:
    registry = context.services.integration_registry
    parts = command.split(" ", 2)
    if len(parts) == 1:
        return {"status": "ok", "command": command, "integrations": registry.list_integrations()}
    subcommand = parts[1].strip().lower()
    remainder = parts[2].strip() if len(parts) > 2 else ""
    if subcommand == "status":
        return {
            "status": "ok",
            "command": command,
            "integrations": registry.describe(remainder) if remainder else registry.list_integrations(),
        }
    if subcommand == "launch":
        if not remainder:
            return _error(command, "Use /integrations launch <integration_id>")
        mode = context.payload.get("mode")
        return {
            "status": "ok",
            "command": command,
            "integrations": registry.launch_contract(remainder, execute=False, mode=str(mode) if mode else None),
        }
    return _error(command, "Use /integrations, /integrations status [integration_id], or /integrations launch <integration_id>")


def _redamon(context: CommandContext, command: str) -> CommandResult:
    workflow = context.services.redamon_workflow
    parts = command.split(" ", 2)
    subcommand = parts[1].strip().lower() if len(parts) > 1 else "status"
    remainder = parts[2].strip() if len(parts) > 2 else ""
    if subcommand == "status":
        return {"status": "ok", "command": command, "redamon": workflow.status(session_id=context.session_id)}
    if subcommand == "enter":
        return {
            "status": "ok",
            "command": command,
            "redamon": workflow.enter_mode(
                session_id=context.session_id,
                request_id=context.request_id,
                objective=remainder or str(context.payload.get("objective") or ""),
            ),
        }
    if subcommand == "propose":
        target = str(context.payload.get("target") or "")
        return {
            "status": "ok",
            "command": command,
            "redamon": workflow.propose_launch(
                session_id=context.session_id,
                request_id=context.request_id,
                objective=remainder or str(context.payload.get("objective") or ""),
                target=target,
            ),
        }
    if subcommand == "launch":
        consent = context.payload.get("consent") if isinstance(context.payload.get("consent"), dict) else {"approved": True, "level": 2}
        return {
            "status": "ok",
            "command": command,
            "redamon": workflow.launch(
                session_id=context.session_id,
                request_id=context.request_id,
                intent_id=str(context.payload.get("intent_id") or "") or None,
                consent=consent,
                execute=False,
            ),
        }
    return _error(command, "Use /redamon status, /redamon enter [objective], /redamon propose [objective], or /redamon launch")


def _catalog_defs() -> Iterable[CommandDefinition]:
    rows = [
        ("/status", "System health and voice status", "Core"),
        ("/sessions", "List persisted sessions", "Core"),
        ("/memory", "Recall pinned memory and summaries", "Core"),
        ("/pin", "Promote the latest session reply to durable memory", "Core"),
        ("/trace", "Show the current request operator trace", "Core"),
        ("/whatchanged", "Summarize state changes since the last operator checkpoint", "Core"),
        ("/cognition", "Show the current Thoughtglass cognition snapshot", "Thoughtglass"),
        ("/cognition memory", "Focus Thoughtglass on the Memory layer", "Thoughtglass"),
        ("/cognition tasks", "Focus Thoughtglass on the Tasks layer", "Thoughtglass"),
        ("/cognition sensors", "Focus Thoughtglass on the Sensors layer", "Thoughtglass"),
        ("/cognition faults", "Focus Thoughtglass on the Faults layer", "Thoughtglass"),
        ("/cognition forge", "Focus Thoughtglass on the Forge layer", "Thoughtglass"),
        ("/awareness", "Show canonical D.A.I.S.E awareness snapshot", "System"),
        ("/performance", "Show latency, backend, memory, and queue pressure", "System"),
        ("/backend", "Show local model backend health", "System"),
        ("/environment", "Show local host and service readiness", "System"),
        ("/sensors", "Show speak/hear/see capability without active capture", "System"),
        ("/sensors snapshot", "Refresh safe sensor capability observations without live capture", "System"),
        ("/sensors live start", "Request explicit live perception mode with consent", "System"),
        ("/sensors live stop", "Stop live perception mode", "System"),
        ("/diagnose", "Summarize bottlenecks, risks, and recommendations", "System"),
        ("/capabilities", "Show the canonical capability registry", "System"),
        ("/limits", "Show policy limits and safety gates", "System"),
        ("/identity <name>", "Resolve a legacy or canonical identity name", "System"),
        ("/desktop status", "Show the Kubuntu/Plasma desktop session contract and current target state", "Desktop"),
        ("/desktop start", "Request a transition to graphical.target on a Linux/systemd OBEOS install", "Desktop"),
        ("/desktop stop", "Return session authority to the OBEOS TUI home target on tty1", "Desktop"),
        ("/desktop home", "Return session authority to the OBEOS TUI home target on tty1", "Desktop"),
        ("/desktop workspace", "Launch the managed Blackfong Workspace shell inside the current desktop session", "Desktop"),
        ("/home", "Return session authority to the OBEOS TUI home surface", "Desktop"),
        ("/docker status", "Show the bounded Blackfong Docker service stack status", "Docker"),
        ("/docker up", "Start the bounded Blackfong Docker HUD/API service stack", "Docker"),
        ("/docker down", "Stop the bounded Blackfong Docker HUD/API service stack", "Docker"),
        ("/docker logs", "Show Docker service stack logs for the HUD/API worker", "Docker"),
        ("/obeos manifest", "Show the Kubuntu-first OBEOS remaster manifest", "OBEOS"),
        ("/obeos installer-status", "Show whether the external installer is shipping truth or migration-only input", "OBEOS"),
        ("/obeos shell-compat", "Show the Ubuntu-compatible shell command contract", "OBEOS"),
        ("/obeos vm-checklist", "Show the canonical Kubuntu VM-first install and validation checklist", "OBEOS"),
        ("/obeos vm-acceptance-snapshot", "Capture the current Kubuntu VM acceptance snapshot or blocked prerequisites", "OBEOS"),
        ("/obeos activate-home", "Activate TUI-home boot after the safe Plasma bootstrap checks pass", "OBEOS"),
        ("/summary", "Show the shared operator summary across session, Tiny, notifications, and file scopes", "Core"),
        ("/files policy", "Show safe file workflow workspace policy", "Files"),
        ("/files proposals", "List governed file proposals and their current states", "Files"),
        ("/files roots", "List governed roots, approved read-map scopes, and candidate machine roots", "Files"),
        ("/files approve-root <path>", "Approve a machine path for D.A.I.S.E read-map access outside governed roots", "Files"),
        ("/files map <path>", "Browse an approved machine path as a bounded tree map", "Files"),
        ("/files read <path>", "Read text content from an approved machine path or governed root", "Files"),
        ("/files scan <path>", "Scan a governed workspace path", "Files"),
        ("/why", "Explain the latest decision or a requested topic with provenance", "System"),
        ("/voice-start", "Start backend voice loop", "Voice"),
        ("/voice-stop", "Stop backend voice loop", "Voice"),
        ("/notifications", "Show notification inbox", "Notifications"),
        ("/notifications read-all", "Mark all notifications as read", "Notifications"),
        ("/tiny status", "Show TinyProgrammer companion status", "TinyProgrammer"),
        ("/tiny launch", "Ensure TinyProgrammer is running with stream available", "TinyProgrammer"),
        ("/tiny native", "Ensure TinyProgrammer native companion window is available", "TinyProgrammer"),
        ("/tiny open", "Open TinyProgrammer browser dashboard", "TinyProgrammer"),
        ("/tiny recent", "Show recent TinyProgrammer creations", "TinyProgrammer"),
        ("/tiny bbs", "Show TinyProgrammer BBS activity or reason-coded availability", "TinyProgrammer"),
        ("/tiny live", "Open TinyProgrammer live stream", "TinyProgrammer"),
        ("/tiny log", "Show the latest managed TinyProgrammer log tail", "TinyProgrammer"),
        ("/tiny brief <text>", "Send a creative brief to TinyProgrammer", "TinyProgrammer"),
        ("/tiny mood <value>", "Set TinyProgrammer preferred mood", "TinyProgrammer"),
        ("/tiny theme <value>", "Set TinyProgrammer creative theme", "TinyProgrammer"),
        ("/tiny model <value>", "Set TinyProgrammer model override", "TinyProgrammer"),
        ("/tiny restart", "Restart TinyProgrammer loop", "TinyProgrammer"),
        ("/tiny screensaver <on|off>", "Toggle TinyProgrammer screensaver", "TinyProgrammer"),
        ("/tiny nudge <text>", "Apply a short creative brief to TinyProgrammer", "TinyProgrammer"),
        ("/tiny rate <1-5> [feedback]", "Rate the latest TinyProgrammer creation", "TinyProgrammer"),
        ("/dream profile", "Generate an auditable D.A.I.S.E style profile for TinyProgrammer", "Dream Layer"),
        ("/dream apply", "Apply D.A.I.S.E Dream Layer style controls to TinyProgrammer", "Dream Layer"),
        ("/archive profile", "Show the active Black Box archive capture profile", "Black Box"),
        ("/archive profile <profile>", "Request bounded_raw, metadata_only, or consent-gated continuous_raw archive mode", "Black Box"),
        ("/verify automated", "Run the automated OBEOS pytest validation harness", "Verification"),
        ("/verify surfaces", "Check HUD, workspace, TinyProgrammer, and Thoughtglass availability", "Verification"),
        ("/verify integrations", "Check optional tacklebox integration readiness", "Verification"),
        ("/verify full", "Generate the canonical full OBEOS verification report", "Verification"),
        ("/verify bundle", "Export diagnostics zip with surfaces, session, and logs", "Verification"),
        ("/integrations", "List optional tacklebox integrations and their current availability", "Integrations"),
        ("/integrations status <id>", "Show a single external integration profile", "Integrations"),
        ("/integrations launch <id>", "Show the external integration launch contract", "Integrations"),
        ("/redamon status", "Show the RedAmon security workflow status", "Security"),
        ("/redamon enter [objective]", "Enter RedAmon review mode without launching it", "Security"),
        ("/redamon propose [objective]", "Create a RedAmon launch review item for operator approval", "Security"),
        ("/redamon launch", "Show the approved RedAmon launch contract", "Security"),
    ]
    for command, description, category in rows:
        yield CommandDefinition(command=command, description=description, category=category, autocomplete=[command])


_registry: Optional[CommandRegistry] = None


def get_command_registry() -> CommandRegistry:
    global _registry
    if _registry is not None:
        return _registry
    registry = CommandRegistry()
    handlers: Dict[str, CommandHandler] = {
        "/status": _status,
        "/sessions": _sessions,
        "/memory": _memory,
        "/notifications": _notifications,
        "/whatchanged": _what_changed,
        "/cognition": _cognition,
        "/trace": _trace,
        "/pin": _pin,
        "/desktop": _desktop,
        "/home": _home,
        "/docker": _docker,
        "/obeos": _obeos,
        "/summary": _summary,
        "/files": _files,
        "/awareness": _awareness_family,
        "/performance": _awareness_family,
        "/backend": _awareness_family,
        "/environment": _awareness_family,
        "/sensors": _awareness_family,
        "/diagnose": _awareness_family,
        "/capabilities": _awareness_family,
        "/limits": _awareness_family,
        "/why": _awareness_family,
        "/identity": _identity,
        "/voice-start": _voice,
        "/voice-stop": _voice,
        "/tiny": _tiny,
        "/dream": _dream,
        "/archive": _archive,
        "/verify": _verify,
        "/integrations": _integrations,
        "/redamon": _redamon,
    }
    for definition in _catalog_defs():
        registry.register(definition, handlers.get(_primary(definition.command), _awareness_family))
    _registry = registry
    return registry


def reset_command_registry_for_tests() -> None:
    global _registry
    _registry = None
