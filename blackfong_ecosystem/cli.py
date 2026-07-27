from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import click

from .config import hud_settings, paths
from .daise import DAISE
from .events import EVENT_SOURCES, Event
from .mobray import Mobray
from .storage import EventStore, ChromaVectorStore
from .capsules import CapsuleImporter
from .knowledge import ingest_file, ingest_directory


@click.group()
def cli() -> None:
    """Blackcomputer / D.A.I.S.E. command-line interface."""


@cli.command("init")
def init_command() -> None:
    """
    Initialize Blackcomputer storage only (directories, event DB).
    This does not warm the orchestrator, the workspace shell, or managed subsystems.
    """
    paths.data_root.mkdir(parents=True, exist_ok=True)
    paths.artifacts_root.mkdir(parents=True, exist_ok=True)
    paths.capsules_root.mkdir(parents=True, exist_ok=True)
    paths.text_inbox_path.mkdir(parents=True, exist_ok=True)
    paths.vector_db_path.mkdir(parents=True, exist_ok=True)
    paths.knowledge_root.mkdir(parents=True, exist_ok=True)
    paths.knowledge_cleaned_path.mkdir(parents=True, exist_ok=True)
    paths.knowledge_chunks_path.mkdir(parents=True, exist_ok=True)
    paths.knowledge_intel_path.mkdir(parents=True, exist_ok=True)

    store = EventStore()
    store.init_db()

    click.echo(f"Initialized data directories under: {paths.data_root}")
    click.echo(f"Event DB: {paths.events_db}")


@cli.command("event-log")
@click.option("--source", type=click.Choice(list(EVENT_SOURCES)), required=True)
@click.option("--type", "event_type", required=True, help="Canonical event type string.")
@click.option(
    "--payload",
    type=str,
    default="{}",
    help="JSON payload string for the event.",
)
@click.option(
    "--payload-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a JSON file containing the event payload.",
)
@click.option(
    "--text",
    "payload_text",
    type=str,
    help="Convenience text field to merge into the payload as {'text': ...}.",
)
@click.option(
    "--confidence",
    type=float,
    default=1.0,
    show_default=True,
    help="Confidence score for the event.",
)
@click.option("--session-id", type=str, help="Optional session id for the event.")
@click.option("--correlation-id", type=str, help="Optional correlation id for the event.")
@click.option("--request-id", type=str, help="Optional request id for trace-aware events.")
@click.option("--tag", "tags", multiple=True, help="Optional repeatable tag for event metadata.")
def event_log_command(
    source: str,
    event_type: str,
    payload: str,
    payload_file: Optional[Path],
    payload_text: Optional[str],
    confidence: float,
    session_id: Optional[str],
    correlation_id: Optional[str],
    request_id: Optional[str],
    tags: tuple[str, ...],
) -> None:
    """
    Manually log a normalized event into the Event DB.
    Useful for testing the pipeline before Black Box / Project 3000 integration.
    """
    if payload_file is not None:
        payload = payload_file.read_text(encoding="utf-8")
    try:
        payload_dict = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON for payload: {exc}") from exc
    if not isinstance(payload_dict, dict):
        raise click.ClickException("Payload JSON must decode to an object/dictionary.")
    if payload_text is not None:
        payload_dict["text"] = payload_text

    event = Event.new(
        source=source,  # type: ignore[arg-type]
        type=event_type,
        payload=payload_dict,
        confidence=confidence,
        session_id=session_id,
        correlation_id=correlation_id,
        request_id=request_id,
        tags=list(tags) or None,
    )
    store = EventStore()
    store.add_event(event)
    click.echo(f"Logged event {event.id} ({event.type})")


@cli.command("daise-session")
@click.argument("task", type=str)
@click.option("--export", type=click.Path(), help="Write full session result JSON to file.")
def daise_session_command(task: str, export: Optional[str]) -> None:
    """
    Run a simple DAISE v0 reasoning session for a given task.
    Stores a session_summary event and prints the result.
    """
    daise = DAISE.create_default()
    daise.init_storage()
    result = daise.run_session(task=task)

    hypothesis_data = result.get("hypothesis_data", {})
    hypothesis = hypothesis_data.get("hypothesis")
    confidence = hypothesis_data.get("confidence")
    if confidence is None:
        confidence = 0.5
    context = result.get("context", {})

    # print provenance counts
    used_ev = hypothesis_data.get("used_events", [])
    used_sem = hypothesis_data.get("used_semantic", [])
    click.echo(f"Events used: {len(used_ev)}, Semantic hits: {len(used_sem)}")

    if hypothesis:
        mobray = Mobray()
        interrogation = mobray.interrogate(hypothesis, context, confidence)

        # Build and log event
        import hashlib
        hypothesis_hash = hashlib.sha256(hypothesis.encode("utf-8")).hexdigest()
        payload = {
            "schema_version": 1,
            "hypothesis_hash": hypothesis_hash,
            "confidence": interrogation.confidence,
            "tier": interrogation.tier,
            "escalated": interrogation.escalated,
            "flag": interrogation.flag,
            "reason_count": len(interrogation.reasons),
            "question_count": len(interrogation.questions),
            "instability_score": interrogation.instability_score,
            "rule_ids_triggered": interrogation.rule_ids_triggered,
        }
        if result.get("summary_event_id"):
            payload["source_event_id"] = result["summary_event_id"]
        event = Event.new(
            source="daise",
            type="mobray_interrogation",
            payload=payload,
            confidence=interrogation.confidence,
        )
        daise.event_store.add_event(event)

        # Rendering
        click.echo(f"Hypothesis:\n-----------\n{hypothesis}\n")
        click.echo(f"Confidence: {confidence:.2f} ({interrogation.tier})\n")
        click.echo(f"Mobray Escalation State: {'ESCALATED' if interrogation.escalated else 'STABLE'}")
        if interrogation.flag:
            click.echo(f"Flag: {interrogation.flag}")
        click.echo(f"Instability Score: {interrogation.instability_score:.1f}\n")
        if interrogation.questions:
            click.echo("Questions:")
            for i, q in enumerate(interrogation.questions, 1):
                click.echo(f"{i}. {q}")
            click.echo()
        if interrogation.escalated:
            click.echo("=" * 50)
            click.echo("⚠ CRITICAL INTERROGATION LEVEL")
            click.echo("=" * 50)
    else:
        click.echo("No hypothesis generated; skipping Mobray interrogation.")

    # export if requested
    if export:
        Path(export).write_text(json.dumps(result, indent=2))
    click.echo(json.dumps(result, indent=2))


@cli.command("event-api")
@click.option("--host", type=str, default="localhost", show_default=True, help="API server host")
@click.option("--port", type=int, default=8000, show_default=True, help="API server port")
@click.option("--debug", is_flag=True, help="Run in debug mode")
def event_api_command(host: str, port: int, debug: bool) -> None:
    """
    Start the Event API server (control plane backbone).
    
    This starts the REST API for:
    - Publishing events (POST /events/publish)
    - Querying events (GET /events/query)
    - Getting event by ID (GET /events/{id})
    - Registering services (POST /services/register)
    - Listing capabilities (GET /services/capabilities)
    
    Access the API at http://{host}:{port}
    """
    from .event_api import create_app
    
    click.echo("=" * 60)
    click.echo("🧠 BLACKFONG EVENT API SERVER")
    click.echo("=" * 60)
    click.echo(f"Starting on http://{host}:{port}")
    click.echo("Endpoints:")
    click.echo("  POST   /events/publish          - Publish an event")
    click.echo("  GET    /events/query            - Query events")
    click.echo("  GET    /events/{id}             - Get event by ID")
    click.echo("  POST   /services/register       - Register a service")
    click.echo("  GET    /services/capabilities   - List services")
    click.echo("  GET    /health                  - Health check")
    click.echo("=" * 60)
    click.echo()
    
    app = create_app()
    app.run(host=host, port=port, debug=debug)


@cli.command("capsule-import")
@click.argument("date", type=str)
def capsule_import_command(date: str) -> None:
    """
    Import a Black Box daily capsule by date (YYYY-MM-DD).
    Expects `{date}.raw` under the capsules directory.
    """
    store = EventStore()
    importer = CapsuleImporter(store=store)
    try:
        imported, summary_id = importer.import_date(date)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        f"Imported {imported} events from capsule {date}. "
        f"Summary event id: {summary_id}"
    )


@cli.command("ingest-text")
@click.argument("path", type=click.Path(path_type=Path, exists=True), required=False)
def ingest_text_command(path: Optional[Path]) -> None:
    """
    Ingest text as knowledge artifacts into the vector store.

    If PATH is provided, ingest that file. Otherwise ingest entire text_inbox_path.
    Emits knowledge_ingested event(s). CLI only.
    """
    event_store = EventStore()
    event_store.init_db()
    vector_store = ChromaVectorStore(paths.vector_db_path)

    def do_ingest(p: Path):
        if p.is_file():
            count, eid = ingest_file(p, vector_store, event_store)
            click.echo(f"Added {count} chunks from {p}. Event id: {eid}")
        else:
            total, eids = ingest_directory(p, vector_store, event_store)
            click.echo(f"Added {total} chunks from {p}. Event ids: {eids}")

    if path is not None:
        do_ingest(path)
    else:
        paths.text_inbox_path.mkdir(parents=True, exist_ok=True)
        do_ingest(paths.text_inbox_path)

@cli.command("memory-query")
@click.option("--keyword", type=str, required=True, help="Keyword to search in payloads.")
def memory_query_command(keyword: str) -> None:
    """Print events whose payload contains KEYWORD in chronological order."""
    store = EventStore()
    store.init_db()
    evs = store.search_payload(keyword, limit=100)
    # reverse for chronological
    for e in reversed(evs):
        click.echo(f"{e.timestamp} {e.source}/{e.type} {e.payload}")

@cli.command("instability-stats")
@click.option("--last", type=int, default=100, help="Number of recent mobray events to include.")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv")
@click.option("--output", type=click.Path(), help="File to write output to (otherwise stdout)")
def instability_stats_command(last: int, fmt: str, output: Optional[str]) -> None:
    """Export recent mobray_interrogation events as CSV or JSON with sparkline."""
    store = EventStore()
    store.init_db()
    evs = store.list_events_by_type("mobray_interrogation", limit=last)
    data = []
    scores = []
    for e in evs:
        score = e.payload.get("instability_score", 0.0)
        scores.append(score)
        data.append({
            "timestamp": e.timestamp,
            "instability_score": score,
            "tier": e.payload.get("tier"),
            "escalated": e.payload.get("escalated"),
        })
    # helper sparkline
    def sparkline(vals):
        ticks = "▁▂▃▄▅▆▇█"
        if not vals:
            return ""
        mn, mx = min(vals), max(vals)
        rng = mx - mn if mx > mn else 1
        return "".join(ticks[int((v - mn) / rng * (len(ticks) - 1))] for v in vals)

    if fmt == "csv":
        import csv, sys
        f = open(output, "w") if output else sys.stdout
        writer = csv.DictWriter(f, fieldnames=["timestamp", "instability_score", "tier", "escalated"])
        writer.writeheader()
        writer.writerows(data)
        if output:
            f.close()
    else:
        import json, sys
        s = json.dumps(data, indent=2)
        if output:
            Path(output).write_text(s)
        else:
            click.echo(s)
    # always show sparkline to stdout
    if scores:
        click.echo("Instability trend: " + sparkline(scores))

@cli.command("ingest-knowledge")
@click.argument("path", type=click.Path(path_type=Path, exists=True), required=False)
def ingest_knowledge_command(path: Optional[Path]) -> None:
    """
    Alias for ingest-text with type validation (txt, md, jsonl).
    """
    allowed = {".txt", ".md", ".jsonl"}
    def valid(p: Path) -> bool:
        return p.is_dir() or p.suffix.lower() in allowed

    if path is not None and not valid(path):
        raise click.ClickException(f"Unsupported file type for knowledge: {path.suffix}")
    ingest_text_command(path)


@cli.command("tui")
def tui_command() -> None:
    """
    Start the Blackfong TUI console.
    """
    try:
        from .ui.blackcomputer_tui import run_tui
    except ImportError as exc:
        raise click.ClickException(
            "Blackfong TUI startup failed due to a Textual/import mismatch: "
            f"{exc}. Activate the project virtual environment or reinstall the TUI dependencies."
        ) from exc

    run_tui()


@cli.command("hud")
@click.option("--host", type=str, default=hud_settings.host, show_default=True, help="HUD host")
@click.option("--port", type=int, default=hud_settings.port, show_default=True, help="HUD port")
@click.option("--open-browser/--no-open-browser", default=hud_settings.open_browser, show_default=True, help="Open the browser automatically")
def hud_command(host: str, port: int, open_browser: bool) -> None:
    """
    Start the Thoughtglass HUD secondary cockpit.
    """
    from .operational_interfaces import AdminControls, SessionExplorer, WebDashboard

    dashboard = WebDashboard(host=host, port=port)
    SessionExplorer(dashboard)
    AdminControls(dashboard)

    click.echo("=" * 60)
    click.echo("THOUGHTGLASS HUD")
    click.echo("=" * 60)
    click.echo(f"Starting HUD on http://{host}:{port}")
    click.echo("Role: secondary browser cockpit for O.B.E.O.S alongside the native Blackfong Workspace.")
    click.echo("Surfaces:")
    click.echo("  /            - Browser cockpit")
    click.echo("  /api/chat     - Chat endpoint")
    click.echo("  /api/sessions - Persisted session history")
    click.echo("  /api/commands - Slash command catalog")
    click.echo("  /api/awareness - Canonical diagnostics, sensors, policy, and provenance")
    click.echo("  /api/diagnose  - Bottleneck/risk/recommendation summary")
    click.echo("=" * 60)
    click.echo()

    try:
        dashboard.start_dashboard(open_browser=open_browser)
    except KeyboardInterrupt:
        click.echo("\nShutting down Thoughtglass HUD...")
    finally:
        dashboard.stop_dashboard(
            interactive=bool(sys.stdin and sys.stdin.isatty()),
            prompt=lambda: click.confirm("Stop managed TinyProgrammer before Blackfong exits?", default=False),
        )


@cli.group("spine")
def spine_group() -> None:
    """Inspect and control the O.B.E.O.S authority spine."""


@spine_group.command("status")
@click.option("--mode", type=click.Choice(["minimal", "learning", "creative", "full"]), help="Filter by spine mode.")
@click.option("--log-events", is_flag=True, help="Persist adapter health observations.")
def spine_status_command(mode: Optional[str], log_events: bool) -> None:
    from .spine_adapters import SpineAdapterRegistry
    from .spine_contract import AUTHORITY_ORDER, SPINE_MODE_AGENTS

    registry = SpineAdapterRegistry()
    status = registry.status(mode, log_events=log_events)
    status["authority_order"] = [source.value for source in AUTHORITY_ORDER]
    status["available_modes"] = {
        spine_mode.value: agents for spine_mode, agents in SPINE_MODE_AGENTS.items()
    }
    click.echo(json.dumps(status, indent=2))


@spine_group.command("mode")
@click.argument("mode", type=click.Choice(["minimal", "learning", "creative", "full"]), required=False)
def spine_mode_command(mode: Optional[str]) -> None:
    from .events import Event
    from .spine_adapters import SpineAdapterRegistry
    from .spine_contract import SpineMode

    selected = mode or SpineMode.MINIMAL.value
    registry = SpineAdapterRegistry()
    agents = registry.list_agents(selected)
    store = EventStore()
    store.init_db()
    event = Event.new(
        source="daise",
        type="spine_mode_changed",
        payload={
            "adapter_contract_version": "1.0",
            "mode": selected,
            "enabled_agents": agents,
            "explicit": mode is not None,
        },
        confidence=1.0,
        tags=["spine", "mode", selected],
    )
    store.add_event(event)
    click.echo(json.dumps({"mode": selected, "enabled_agents": agents, "event_id": event.id}, indent=2))


@cli.group("agent")
def agent_group() -> None:
    """Inspect, admit, and run versioned spine agents."""


@agent_group.command("list")
@click.option("--mode", type=click.Choice(["minimal", "learning", "creative", "full"]), help="Filter by spine mode.")
def agent_list_command(mode: Optional[str]) -> None:
    from .spine_adapters import SpineAdapterRegistry

    registry = SpineAdapterRegistry()
    click.echo(json.dumps(registry.status(mode), indent=2))


@agent_group.command("status")
@click.argument("agent", required=False)
def agent_status_command(agent: Optional[str]) -> None:
    from .spine_adapters import SpineAdapterRegistry

    registry = SpineAdapterRegistry()
    if agent:
        payload = registry.get(agent).health_check().to_dict()
    else:
        payload = registry.status()
    click.echo(json.dumps(payload, indent=2))


@agent_group.command("admit")
@click.argument("agent")
def agent_admit_command(agent: str) -> None:
    from .spine_adapters import SpineAdapterRegistry

    registry = SpineAdapterRegistry()
    click.echo(json.dumps(registry.admit(agent).to_dict(), indent=2))


@agent_group.command("run")
@click.argument("agent")
@click.argument("action_arg", required=False)
@click.option("--action", "action_option", type=str, help="Adapter action to run. Defaults to status.")
@click.option("--payload", type=str, default="{}", help="JSON payload for the adapter request.")
def agent_run_command(agent: str, action_arg: Optional[str], action_option: Optional[str], payload: str) -> None:
    from .spine_adapters import SpineAdapterRegistry

    if action_arg and action_option and action_arg != action_option:
        raise click.ClickException("Use either positional ACTION or --action, not both with different values.")
    action = action_option or action_arg or "status"
    try:
        payload_dict = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON payload: {exc}") from exc
    if not isinstance(payload_dict, dict):
        raise click.ClickException("Payload must decode to a JSON object.")
    registry = SpineAdapterRegistry()
    click.echo(json.dumps(registry.run(agent, action, payload_dict).to_dict(), indent=2))


@cli.group("learn")
def learn_group() -> None:
    """Federated learning profile commands."""


@learn_group.command("status")
def learn_status_command() -> None:
    from .learner_profile import LearnerProfileService

    click.echo(json.dumps(LearnerProfileService().synthesize().to_dict(), indent=2))


@learn_group.command("recommend")
def learn_recommend_command() -> None:
    from .learner_profile import LearnerProfileService

    profile = LearnerProfileService().synthesize()
    click.echo(json.dumps({"recommendations": profile.recommendations, "weaknesses": profile.weaknesses}, indent=2))


@learn_group.command("sync")
def learn_sync_command() -> None:
    from .learner_profile import LearnerProfileService

    profile = LearnerProfileService().synthesize(persist=True)
    click.echo(json.dumps(profile.to_dict(), indent=2))


@cli.group("tiny")
def tiny_group() -> None:
    """TinyProgrammer companion commands."""


@tiny_group.command("status")
def tiny_status_command() -> None:
    from .spine_adapters import SpineAdapterRegistry

    click.echo(json.dumps(SpineAdapterRegistry().run("tinyprogrammer", "status").to_dict(), indent=2))


@tiny_group.command("brief")
@click.argument("brief")
def tiny_brief_command(brief: str) -> None:
    from .spine_adapters import SpineAdapterRegistry

    payload = {"brief": brief}
    click.echo(json.dumps(SpineAdapterRegistry().run("tinyprogrammer", "brief", payload).to_dict(), indent=2))


@tiny_group.command("control")
@click.argument("action")
@click.argument("value", required=False)
def tiny_control_command(action: str, value: Optional[str]) -> None:
    from .tinyprogrammer_companion import TinyProgrammerCompanion

    result = TinyProgrammerCompanion().send_control(action, value)
    click.echo(json.dumps(result, indent=2))


@tiny_group.command("recent")
@click.option("--limit", type=int, default=10, show_default=True)
def tiny_recent_command(limit: int) -> None:
    from .tinyprogrammer_companion import TinyProgrammerCompanion

    click.echo(json.dumps(TinyProgrammerCompanion().get_recent(limit=limit), indent=2))


@cli.group("dream")
def dream_group() -> None:
    """Generate and apply D.A.I.S.E creative influence profiles."""


@dream_group.command("profile")
@click.option("--apply", "apply_profile", is_flag=True, help="Apply the generated profile to TinyProgrammer.")
@click.option("--mood", type=str, help="Override preferred TinyProgrammer mood.")
@click.option("--theme", type=str, help="Override preferred TinyProgrammer theme.")
@click.option("--tempo", type=str, help="Override preferred TinyProgrammer tempo.")
def dream_profile_command(apply_profile: bool, mood: Optional[str], theme: Optional[str], tempo: Optional[str]) -> None:
    from .cognitive_expansion import DAISEStyleProfileService, DreamLayerService
    from .tinyprogrammer_companion import TinyProgrammerCompanion

    overrides = {key: value for key, value in {"mood": mood, "theme": theme, "tempo": tempo}.items() if value}
    store = EventStore()
    profile = DAISEStyleProfileService(store).generate_profile(overrides=overrides or None, persist=True)
    payload = {"style_profile": profile}
    if apply_profile:
        payload["tinyprogrammer"] = DreamLayerService(store, tinyprogrammer=TinyProgrammerCompanion()).apply_to_tinyprogrammer(
            profile["style_profile"]
        )
    click.echo(json.dumps(payload, indent=2))


@cli.group("archive")
def archive_group() -> None:
    """Inspect and change Black Box archive capture profiles."""


@archive_group.command("profile")
@click.argument("profile", required=False)
@click.option("--approved", is_flag=True, help="Approve the requested profile change.")
@click.option("--level", type=int, default=1, show_default=True, help="Consent level for high-risk profiles.")
@click.option("--reason", type=str, default="", help="Operator reason for the profile change.")
def archive_profile_command(profile: Optional[str], approved: bool, level: int, reason: str) -> None:
    from .blackbox_archive import ArchiveCaptureProfileManager

    store = EventStore()
    manager = ArchiveCaptureProfileManager(store)
    if profile:
        result = manager.set_profile(profile, consent={"approved": approved, "level": level}, reason=reason)
    else:
        result = manager.current_profile()
    click.echo(json.dumps(result, indent=2))


@cli.group("legion")
def legion_group() -> None:
    """Manage the local Legion fullscreen cockpit."""


@legion_group.command("token")
@click.option("--rotate", is_flag=True, help="Generate a new local pairing token.")
def legion_token_command(rotate: bool) -> None:
    from .device_registry import DeviceRegistryService, LegionAuthService

    auth = LegionAuthService(DeviceRegistryService(event_store=EventStore()))
    token = auth.current_pairing_token(create=True, rotate=rotate)
    click.echo(json.dumps({"schema_version": "1.0", "pairing_token": token, "rotated": rotate}, indent=2))


@legion_group.command("status")
def legion_status_command() -> None:
    from .device_registry import DeviceRegistryService, LegionAuthService

    registry = DeviceRegistryService(event_store=EventStore())
    auth = LegionAuthService(registry)
    payload = {
        "schema_version": "1.0",
        "pairing_token_configured": bool(auth.current_pairing_token(create=True)),
        "devices": registry.list_devices(include_history=False),
        "paired_clients": registry.active_paired_clients(),
    }
    click.echo(json.dumps(payload, indent=2))


@legion_group.command("paired")
def legion_paired_command() -> None:
    from .device_registry import DeviceRegistryService

    registry = DeviceRegistryService(event_store=EventStore())
    click.echo(json.dumps({"schema_version": "1.0", "paired_clients": registry.active_paired_clients()}, indent=2))


@cli.group("sentinel")
def sentinel_group() -> None:
    """Emit bounded Sentinel environmental observations."""


@sentinel_group.command("detect")
@click.option("--modality", required=True, type=str, help="camera, audio, presence, screen, or environment.")
@click.option("--detector", required=True, type=str, help="Detector or collection method.")
@click.option("--label", required=True, type=str, help="Detection label.")
@click.option("--confidence", type=float, default=0.8, show_default=True)
@click.option("--raw-artifact", type=click.Path(path_type=Path), help="Optional raw artifact path to reference.")
def sentinel_detect_command(modality: str, detector: str, label: str, confidence: float, raw_artifact: Optional[Path]) -> None:
    from .sentinel import SentinelDetectionService

    raw = {"path": str(raw_artifact), "media_type": modality} if raw_artifact else None
    result = SentinelDetectionService(EventStore()).emit_detection(
        modality=modality,
        detector=detector,
        detection={"label": label},
        confidence=confidence,
        raw_artifact=raw,
    )
    click.echo(json.dumps(result, indent=2))


@sentinel_group.command("agent")
@click.option("--hub", required=True, type=str, help="Blackcomputer HUD base URL, for example http://blackcomputer:8080.")
@click.option("--device-id", default="sentinel", show_default=True)
@click.option("--label", default="Sentinel", show_default=True)
@click.option("--token", default="", help="Sentinel agent token; defaults to BLACKFONG_SENTINEL_AGENT_TOKEN if set.")
@click.option("--interval", type=float, default=10.0, show_default=True, help="Heartbeat interval in seconds.")
@click.option("--once", is_flag=True, help="Send one report and exit.")
def sentinel_agent_command(hub: str, device_id: str, label: str, token: str, interval: float, once: bool) -> None:
    from .config import legion_settings
    from .sentinel import run_sentinel_agent

    resolved_token = token or legion_settings.sentinel_agent_token
    if not resolved_token:
        raise click.ClickException("Set BLACKFONG_SENTINEL_AGENT_TOKEN or pass --token from `blackfong legion token`.")
    click.echo(f"Starting Sentinel agent for {device_id} -> {hub}")
    try:
        run_sentinel_agent(
            hub=hub,
            device_id=device_id,
            label=label,
            token=resolved_token,
            interval_seconds=interval,
            once=once,
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    if once:
        click.echo("Sentinel report delivered.")


@sentinel_group.command("status")
@click.option("--local", "local_snapshot", is_flag=True, help="Show this machine's Sentinel agent payload instead of Blackcomputer registry state.")
def sentinel_status_command(local_snapshot: bool) -> None:
    if local_snapshot:
        from .sentinel import build_sentinel_agent_report

        click.echo(json.dumps(build_sentinel_agent_report(), indent=2, default=str))
        return
    from .device_registry import DeviceRegistryService

    registry = DeviceRegistryService(event_store=EventStore())
    click.echo(json.dumps(registry.get_device("sentinel", include_history=True), indent=2))


@cli.group("frame")
def frame_group() -> None:
    """Run and inspect the Brilliant Frame wearable bridge."""


@frame_group.command("agent")
@click.option("--hub", default="http://127.0.0.1:8080", show_default=True, help="Blackcomputer HUD base URL.")
@click.option("--device-id", default="frame", show_default=True)
@click.option("--label", default="Brilliant Frame", show_default=True)
@click.option("--token", default="", help="Frame agent token; defaults to BLACKFONG_SENTINEL_AGENT_TOKEN or `blackfong legion token`.")
@click.option("--name", "frame_name", default=None, help='Exact BLE name, for example "Frame 4F".')
@click.option("--once", is_flag=True, help="Connect, report once, then exit.")
@click.option("--capture-artifacts", is_flag=True, help="Persist bounded captured media artifacts in OBEOS data/artifacts/frame.")
def frame_agent_command(
    hub: str,
    device_id: str,
    label: str,
    token: str,
    frame_name: Optional[str],
    once: bool,
    capture_artifacts: bool,
) -> None:
    import asyncio

    from .config import legion_settings
    from .device_registry import DeviceRegistryService, LegionAuthService
    from .frame_bridge import run_frame_agent

    resolved_token = token or legion_settings.sentinel_agent_token
    if not resolved_token:
        resolved_token = LegionAuthService(DeviceRegistryService(event_store=EventStore())).current_pairing_token(create=True)
    click.echo(f"Starting Brilliant Frame bridge for {device_id} -> {hub}")
    try:
        asyncio.run(
            run_frame_agent(
                hub=hub,
                token=resolved_token,
                device_id=device_id,
                label=label,
                frame_name=frame_name,
                once=once,
                capture_artifacts=capture_artifacts,
            )
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@frame_group.command("status")
def frame_status_command() -> None:
    from .device_registry import DeviceRegistryService

    registry = DeviceRegistryService(event_store=EventStore())
    click.echo(json.dumps(registry.get_device("frame", include_history=True), indent=2))


@frame_group.command("send-text")
@click.argument("text", type=str)
@click.option("--name", "frame_name", default=None, help='Exact BLE name, for example "Frame 4F".')
def frame_send_text_command(text: str, frame_name: Optional[str]) -> None:
    import asyncio

    from .frame_bridge import BrilliantFrameTransport, frame_lua_app_path

    async def _send() -> None:
        transport = BrilliantFrameTransport()
        try:
            await transport.connect(name=frame_name)
            await transport.bootstrap_app(frame_lua_app_path())
            await transport.send_text(text)
        finally:
            await transport.disconnect()

    try:
        asyncio.run(_send())
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("Frame text sent.")


@frame_group.command("capture")
@click.option("--mode", default="photo", type=click.Choice(["photo"]), show_default=True)
@click.option("--output", type=click.Path(path_type=Path), help="Output JPEG path. Defaults to data/artifacts/frame/latest_photo.jpg.")
@click.option("--name", "frame_name", default=None, help='Exact BLE name, for example "Frame 4F".')
def frame_capture_command(mode: str, output: Optional[Path], frame_name: Optional[str]) -> None:
    import asyncio

    from .frame_bridge import BrilliantFrameTransport, frame_lua_app_path

    target = output or (paths.artifacts_root / "frame" / "latest_photo.jpg")

    async def _capture() -> bytes:
        transport = BrilliantFrameTransport()
        try:
            await transport.connect(name=frame_name)
            await transport.bootstrap_app(frame_lua_app_path())
            return await transport.request_photo(resolution=720)
        finally:
            await transport.disconnect()

    try:
        photo = asyncio.run(_capture())
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(photo)
    click.echo(json.dumps({"mode": mode, "path": str(target), "byte_count": len(photo)}, indent=2))


@frame_group.command("gesture")
@click.option("--hub", default="http://127.0.0.1:8080", show_default=True)
@click.option("--action", required=True, type=str, help="Thoughtglass gesture action, e.g. switch_layer, zoom, rotate, select.")
@click.option("--layer", type=str, help="Layer for switch_layer, e.g. sensors.")
@click.option("--node-id", type=str, help="Node id for select/open_module.")
@click.option("--amount", type=float, default=0.0, show_default=True, help="Delta amount for zoom/rotate.")
def frame_gesture_command(hub: str, action: str, layer: Optional[str], node_id: Optional[str], amount: float) -> None:
    import asyncio

    from .frame_bridge import HttpFrameHubClient

    target: Dict[str, Any] = {}
    if layer:
        target["layer"] = layer
    if node_id:
        target["node_id"] = node_id
    gesture = {
        "action": action,
        "target": target,
        "delta": {"amount": amount} if amount else {},
        "layer": layer if action == "switch_layer" else None,
        "mode": "frame",
    }
    try:
        result = asyncio.run(HttpFrameHubClient(hub_url=hub).post_gesture(gesture))
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, indent=2))


@cli.group("vision")
def vision_group() -> None:
    """Vision knowledge and tool commands."""


@vision_group.command("smoke")
@click.option("--execute", is_flag=True, help="Actually run the bounded local Torchvision smoke command.")
def vision_smoke_command(execute: bool) -> None:
    from .spine_adapters import SpineAdapterRegistry

    click.echo(json.dumps(SpineAdapterRegistry().run("vision", "smoke", {"execute": execute}).to_dict(), indent=2))


@vision_group.command("detect")
@click.option("--execute", is_flag=True, help="Reserved for bounded YOLO execution.")
def vision_detect_command(execute: bool) -> None:
    from .spine_adapters import SpineAdapterRegistry

    click.echo(json.dumps(SpineAdapterRegistry().run("vision", "detect", {"execute": execute}).to_dict(), indent=2))


@vision_group.command("ingest")
@click.argument("path", type=click.Path(path_type=Path, exists=True), required=False)
def vision_ingest_command(path: Optional[Path]) -> None:
    targets = []
    if path is not None:
        targets = [path]
    else:
        root = paths.root.parent
        targets = [
            root / "vision-main" / "vision-main" / "README.md",
            root / "yolov5-master" / "yolov5-master" / "README.md",
            root / "opencv-4.x" / "opencv-4.x" / "README.md",
            root / "cocostuff-master" / "cocostuff-master" / "README.md",
        ]

    event_store = EventStore()
    event_store.init_db()
    vector_store = ChromaVectorStore(paths.vector_db_path)
    results = []
    for target in targets:
        if not target.exists():
            results.append({"path": str(target), "ok": False, "error": "missing"})
            continue
        count, event_id = ingest_file(target, vector_store, event_store)
        results.append({"path": str(target), "ok": True, "chunks": count, "event_id": event_id})
    click.echo(json.dumps({"results": results}, indent=2))


@cli.group("install-plan")
def install_plan_group() -> None:
    """Generate, inspect, and validate non-destructive O.B.E.O.S. install plans against the current installer path."""


@install_plan_group.command("status")
def install_plan_status_command() -> None:
    from .installer_plan import InstallerPlanService

    click.echo(json.dumps(InstallerPlanService().current_status(), indent=2))


@install_plan_group.command("export")
@click.option("--profile", default="amd64-pc", show_default=True)
@click.option("--output", type=click.Path(path_type=Path), default=Path("data") / "blackfong-install-plan.json", show_default=True)
def install_plan_export_command(profile: str, output: Path) -> None:
    from .installer_plan import InstallerPlanService

    click.echo(json.dumps(InstallerPlanService().export_plan(profile, output), indent=2))


@install_plan_group.command("validate")
@click.option("--profile", default="amd64-pc", show_default=True)
def install_plan_validate_command(profile: str) -> None:
    from .installer_plan import InstallerPlanService

    service = InstallerPlanService()
    plan = service.build_plan(profile)
    click.echo(json.dumps(service.validate_plan(plan), indent=2))


@cli.group("desktop")
def desktop_group() -> None:
    """Manage the Kubuntu/Plasma desktop session contract for OBEOS."""


@desktop_group.command("status")
def desktop_status_command() -> None:
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(json.dumps(OBEOSLinuxPlatformService(workspace_root=paths.root).desktop_status(), indent=2))


@desktop_group.command("start")
@click.option("--dry-run", is_flag=True, help="Show the target transition without executing it.")
def desktop_start_command(dry_run: bool) -> None:
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(
        json.dumps(
            OBEOSLinuxPlatformService(workspace_root=paths.root).desktop_start(execute=not dry_run),
            indent=2,
        )
    )


@desktop_group.command("stop")
@click.option("--dry-run", is_flag=True, help="Show the target transition without executing it.")
def desktop_stop_command(dry_run: bool) -> None:
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(
        json.dumps(
            OBEOSLinuxPlatformService(workspace_root=paths.root).desktop_stop(execute=not dry_run),
            indent=2,
        )
    )


@desktop_group.command("workspace")
@click.option("--dry-run", is_flag=True, help="Show the workspace launch contract without executing it.")
def desktop_workspace_command(dry_run: bool) -> None:
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(
        json.dumps(
            OBEOSLinuxPlatformService(workspace_root=paths.root).launch_workspace(execute=not dry_run),
            indent=2,
        )
    )


@cli.command("home")
@click.option("--dry-run", is_flag=True, help="Show the return-to-OBEOS-home contract without executing it.")
def home_command(dry_run: bool) -> None:
    """Return session authority to the OBEOS TUI home surface."""
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(
        json.dumps(
            OBEOSLinuxPlatformService(workspace_root=paths.root).home(execute=not dry_run),
            indent=2,
        )
    )


@cli.group("docker")
def docker_group() -> None:
    """Manage the bounded Blackfong Docker service stack."""


@docker_group.command("status")
def docker_status_command() -> None:
    from .docker_stack import DockerStackService

    click.echo(json.dumps(DockerStackService(workspace_root=paths.root).status(), indent=2))


@docker_group.command("up")
@click.option("--build", is_flag=True, help="Build the service image before starting the stack.")
@click.option("--dry-run", is_flag=True, help="Show the compose command without executing it.")
def docker_up_command(build: bool, dry_run: bool) -> None:
    from .docker_stack import DockerStackService

    click.echo(json.dumps(DockerStackService(workspace_root=paths.root).up(execute=not dry_run, build=build), indent=2))


@docker_group.command("down")
@click.option("--dry-run", is_flag=True, help="Show the compose command without executing it.")
def docker_down_command(dry_run: bool) -> None:
    from .docker_stack import DockerStackService

    click.echo(json.dumps(DockerStackService(workspace_root=paths.root).down(execute=not dry_run), indent=2))


@docker_group.command("logs")
@click.option("--tail", type=int, default=120, show_default=True, help="Number of log lines to return.")
@click.option("--dry-run", is_flag=True, help="Show the compose command without executing it.")
def docker_logs_command(tail: int, dry_run: bool) -> None:
    from .docker_stack import DockerStackService

    click.echo(json.dumps(DockerStackService(workspace_root=paths.root).logs(execute=not dry_run, tail=tail), indent=2))


@cli.group("verify")
def verify_group() -> None:
    """Run structured OBEOS verification checks."""


@verify_group.command("automated")
def verify_automated_command() -> None:
    from .verification import VerificationService

    click.echo(json.dumps(VerificationService(workspace_root=paths.root).automated(), indent=2))


@verify_group.command("surfaces")
def verify_surfaces_command() -> None:
    from .verification import VerificationService

    click.echo(json.dumps(VerificationService(workspace_root=paths.root).surfaces(), indent=2))


@verify_group.command("integrations")
def verify_integrations_command() -> None:
    from .verification import VerificationService

    click.echo(json.dumps(VerificationService(workspace_root=paths.root).integrations(), indent=2))


@verify_group.command("full")
def verify_full_command() -> None:
    from .verification import VerificationService

    click.echo(json.dumps(VerificationService(workspace_root=paths.root).full(), indent=2))


@verify_group.command("bundle")
def verify_bundle_command() -> None:
    from .verification import VerificationService

    click.echo(json.dumps(VerificationService(workspace_root=paths.root).bundle(), indent=2))


@verify_group.command("evidence")
@click.option("--profile", type=str, default="amd64-pc", show_default=True, help="Installer profile to validate in the evidence snapshot.")
@click.option("--include-automated/--no-include-automated", default=False, show_default=True, help="Include the pytest-based automated verification slice.")
def verify_evidence_command(profile: str, include_automated: bool) -> None:
    from .verification import VerificationService

    click.echo(
        json.dumps(
            VerificationService(workspace_root=paths.root).release_evidence(
                profile=profile,
                include_automated=include_automated,
            ),
            indent=2,
        )
    )


@verify_group.command("shotset")
@click.option("--output-dir", type=click.Path(path_type=Path), help="Directory for the generated release screenshots.")
def verify_shotset_command(output_dir: Optional[Path]) -> None:
    from .verification import VerificationService

    click.echo(
        json.dumps(
            VerificationService(workspace_root=paths.root).shotset(output_dir=output_dir),
            indent=2,
        )
    )


@cli.group("integrations")
def integrations_group() -> None:
    """Inspect optional tacklebox integrations."""


@integrations_group.command("status")
@click.argument("integration_id", required=False)
def integrations_status_command(integration_id: Optional[str]) -> None:
    from .external_integrations import ExternalIntegrationRegistry

    registry = ExternalIntegrationRegistry(workspace_root=paths.root)
    payload = registry.describe(integration_id) if integration_id else registry.list_integrations()
    click.echo(json.dumps(payload, indent=2))


@integrations_group.command("launch")
@click.argument("integration_id")
@click.option("--mode", type=str, help="Optional launch mode for the external integration.")
@click.option("--dry-run", is_flag=True, help="Show the external launch contract without executing it.")
def integrations_launch_command(integration_id: str, mode: Optional[str], dry_run: bool) -> None:
    from .external_integrations import ExternalIntegrationRegistry

    payload = ExternalIntegrationRegistry(workspace_root=paths.root).launch_contract(
        integration_id,
        execute=not dry_run,
        mode=mode,
    )
    click.echo(json.dumps(payload, indent=2))


@cli.group("redamon")
def redamon_group() -> None:
    """Operator-approved RedAmon security workflow mode."""


@redamon_group.command("status")
@click.option("--session-id", type=str, help="Optional session scope for mode history.")
def redamon_status_command(session_id: Optional[str]) -> None:
    from .redamon_mode import RedAmonWorkflowService

    click.echo(json.dumps(RedAmonWorkflowService().status(session_id=session_id), indent=2))


@redamon_group.command("enter")
@click.option("--session-id", type=str, help="Optional session scope for RedAmon mode.")
@click.option("--request-id", type=str, help="Optional request id for RedAmon mode.")
@click.option("--objective", type=str, default="", help="Short objective for this RedAmon review session.")
def redamon_enter_command(session_id: Optional[str], request_id: Optional[str], objective: str) -> None:
    from .redamon_mode import RedAmonWorkflowService

    click.echo(
        json.dumps(
            RedAmonWorkflowService().enter_mode(session_id=session_id, request_id=request_id, objective=objective),
            indent=2,
        )
    )


@redamon_group.command("propose")
@click.option("--session-id", type=str, help="Optional session scope for RedAmon mode.")
@click.option("--request-id", type=str, help="Optional request id for RedAmon mode.")
@click.option("--objective", type=str, default="", help="Why RedAmon should be launched.")
@click.option("--target", type=str, default="", help="Target or project label for the security workflow.")
def redamon_propose_command(session_id: Optional[str], request_id: Optional[str], objective: str, target: str) -> None:
    from .redamon_mode import RedAmonWorkflowService

    click.echo(
        json.dumps(
            RedAmonWorkflowService().propose_launch(
                session_id=session_id,
                request_id=request_id,
                objective=objective,
                target=target,
            ),
            indent=2,
        )
    )


@redamon_group.command("launch")
@click.option("--session-id", type=str, help="Optional session scope for RedAmon mode.")
@click.option("--request-id", type=str, help="Optional request id for RedAmon mode.")
@click.option("--intent-id", type=str, help="Optional review intent id being approved.")
@click.option("--dry-run", is_flag=True, help="Show the RedAmon launch contract without executing it.")
def redamon_launch_command(session_id: Optional[str], request_id: Optional[str], intent_id: Optional[str], dry_run: bool) -> None:
    from .redamon_mode import RedAmonWorkflowService

    click.echo(
        json.dumps(
            RedAmonWorkflowService().launch(
                session_id=session_id,
                request_id=request_id,
                intent_id=intent_id,
                consent={"approved": True, "level": 2},
                execute=not dry_run,
            ),
            indent=2,
        )
    )


@cli.group("obeos")
def obeos_group() -> None:
    """Inspect and stage the Kubuntu-first OBEOS remaster bundle."""


@obeos_group.command("manifest")
def obeos_manifest_command() -> None:
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(json.dumps(OBEOSLinuxPlatformService(workspace_root=paths.root).manifest(), indent=2))


@obeos_group.command("shell-compat")
def obeos_shell_compat_command() -> None:
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(json.dumps(OBEOSLinuxPlatformService(workspace_root=paths.root).shell_compatibility_status(), indent=2))


@obeos_group.command("hardware-probe")
def obeos_hardware_probe_command() -> None:
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(json.dumps(OBEOSLinuxPlatformService(workspace_root=paths.root).hardware_probe(), indent=2))


@obeos_group.command("vm-checklist")
def obeos_vm_checklist_command() -> None:
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(OBEOSLinuxPlatformService(workspace_root=paths.root).vm_checklist_markdown())


@obeos_group.command("vm-acceptance-snapshot")
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional path to write vm_acceptance_snapshot.json.",
)
@click.option(
    "--base-iso",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional Kubuntu ISO path used to mark host-side VM readiness.",
)
def obeos_vm_acceptance_snapshot_command(output: Optional[Path], base_iso: Optional[Path]) -> None:
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(
        json.dumps(
            OBEOSLinuxPlatformService(workspace_root=paths.root).vm_acceptance_snapshot(
                output_path=output,
                base_iso_path=base_iso,
            ),
            indent=2,
        )
    )


@obeos_group.command("activate-home")
@click.option("--dry-run", is_flag=True, help="Show the TUI-home activation contract without executing it.")
def obeos_activate_home_command(dry_run: bool) -> None:
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(
        json.dumps(
            OBEOSLinuxPlatformService(workspace_root=paths.root).activate_home_boot(execute=not dry_run),
            indent=2,
        )
    )


@obeos_group.command("materialize")
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=Path("data") / "obeos-remaster",
    show_default=True,
    help="Directory where the Kubuntu remaster bundle will be generated.",
)
def obeos_materialize_command(output: Path) -> None:
    from .obeos_linux import OBEOSLinuxPlatformService

    click.echo(
        json.dumps(
            OBEOSLinuxPlatformService(workspace_root=paths.root).materialize_deployment_bundle(output),
            indent=2,
        )
    )


@cli.group("knowledge")
def knowledge_group() -> None:
    """Document ingestion pipeline (comics / corpora) into semantic memory."""


def _knowledge_registry():
    from .ingestion.source_registry import SourceRegistry

    paths.knowledge_root.mkdir(parents=True, exist_ok=True)
    return SourceRegistry(paths.source_registry_path)


def _collection_registry():
    from .knowledge_intel.collection_registry import CollectionRegistry

    paths.knowledge_root.mkdir(parents=True, exist_ok=True)
    return CollectionRegistry(paths.collection_registry_path)


def _resolve_ingest_domain(collection_slug: str, domain_opt: Optional[str]) -> str:
    """Domain to apply during ingestion: explicit --domain wins, else inherit from
    the collection taxonomy store (no hardcoded collection names)."""
    if domain_opt is not None:
        return domain_opt
    return _collection_registry().domain_for(collection_slug)


@knowledge_group.command("ingest-comics")
@click.option("--root", "root", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True, help="Directory containing source files (txt/epub/pdf/jp2).")
@click.option("--series", type=str, default="Spawn", show_default=True, help="Series name applied to discovered sources.")
@click.option("--collection", type=str, default=None, help="Collection slug for filtering/inventory (defaults to a slug of --series).")
@click.option("--domain", type=str, default=None, help="Domain label stored on chunks. If omitted, inherited from the collection taxonomy (set-domain).")
@click.option("--content-type", type=str, default="lore", show_default=True, help="Chunk metadata 'type' value.")
@click.option("--force", is_flag=True, default=False, help="Re-embed even if an unchanged canonical is already embedded.")
@click.option("--exclude", "exclude", multiple=True, help="Path segment(s) to skip during discovery (fnmatch). Repeatable. Vendored dirs like .venv/site-packages are always skipped.")
def knowledge_ingest_comics(root: Path, series: str, collection: Optional[str], domain: Optional[str], content_type: str, force: bool, exclude: tuple) -> None:
    """Run the full pipeline: register all, embed canonical text per group.

    Handles txt/epub/pdf/markdown sources. Markdown (.md/.markdown) files are each
    embedded as their own document.
    """
    from .ingestion.pipeline import ingest_series
    from .ingestion.source_registry import slugify_collection

    event_store = EventStore()
    event_store.init_db()
    vector_store = ChromaVectorStore(paths.vector_db_path)
    registry = _knowledge_registry()

    collection_slug = collection or slugify_collection(series)
    resolved_domain = _resolve_ingest_domain(collection_slug, domain)

    report = ingest_series(
        root,
        series,
        registry=registry,
        vector_store=vector_store,
        event_store=event_store,
        cleaned_dir=paths.knowledge_cleaned_path,
        chunks_dir=paths.knowledge_chunks_path,
        content_type=content_type,
        collection=collection or "",
        domain=resolved_domain,
        force=force,
        extra_ignores=tuple(exclude),
    )
    click.echo(
        f"[{report.series}] registered={report.registered} embedded={report.embedded} "
        f"reference_only={report.reference_only} pending_visual={report.pending_visual} "
        f"failed={report.failed}"
    )
    for outcome in report.outcomes:
        extra = f" reasons={outcome.reasons}" if outcome.reasons else ""
        click.echo(
            f"  {outcome.issue_range:>8} {outcome.source_type:<10} "
            f"{outcome.role:<10} {outcome.status:<16} chunks={outcome.chunk_count}{extra}"
        )


@knowledge_group.command("register")
@click.argument("path", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--series", type=str, required=True)
@click.option("--issue-range", "issue_range", type=str, required=True, help="e.g. 1-8")
@click.option(
    "--source-type",
    type=click.Choice(["djvu_text", "epub", "pdf_text", "visual_jp2"]),
    required=True,
)
@click.option("--collection", type=str, default=None, help="Collection slug (defaults to a slug of --series).")
@click.option("--domain", type=str, default=None, help="Domain label. If omitted, inherited from the collection taxonomy (set-domain).")
def knowledge_register(path: Path, series: str, issue_range: str, source_type: str, collection: Optional[str], domain: Optional[str]) -> None:
    """Register a single source (including jp2). Does not embed."""
    from .ingestion.source_registry import (
        ROLE_REFERENCE,
        SOURCE_TYPE_VISUAL,
        STATUS_PENDING_PROCESSING,
        STATUS_REGISTERED,
        slugify_collection,
    )

    event_store = EventStore()
    event_store.init_db()
    registry = _knowledge_registry()

    collection_slug = collection or slugify_collection(series)
    resolved_domain = _resolve_ingest_domain(collection_slug, domain)

    if source_type == SOURCE_TYPE_VISUAL:
        from .ingestion.visual_pipeline import register_visual_source

        record = register_visual_source(
            path,
            series=series,
            issue_range=issue_range,
            registry=registry,
            event_store=event_store,
            collection=collection or "",
            domain=resolved_domain,
        )
    else:
        record, changed = registry.register(
            source_type=source_type,
            path=path,
            series=series,
            issue_range=issue_range,
            role=ROLE_REFERENCE,
            processing_status=STATUS_REGISTERED,
            collection=collection or "",
            domain=resolved_domain,
        )
        if changed:
            ev = Event.new(
                source="daise",
                type="knowledge_source_added",
                payload={
                    "source_id": record.source_id,
                    "source_type": record.source_type,
                    "series": series,
                    "issue_range": issue_range,
                    "path": record.path,
                },
                tags=["knowledge"],
            )
            event_store.add_event(ev)

    registry.save()
    click.echo(
        f"Registered {record.source_id} ({record.source_type}) "
        f"status={record.processing_status}"
    )


@knowledge_group.command("status")
@click.option("--series", type=str, default=None, help="Filter by series.")
def knowledge_status(series: Optional[str]) -> None:
    """Summarize the source registry (embedded vs reference vs pending)."""
    registry = _knowledge_registry()
    records = registry.all()
    if series:
        records = [r for r in records if r.series == series]

    summary: dict[str, int] = {}
    for r in records:
        summary[r.processing_status] = summary.get(r.processing_status, 0) + 1
    click.echo(f"Total sources: {len(records)}")
    for status, count in sorted(summary.items()):
        click.echo(f"  {status}: {count}")


@knowledge_group.command("list")
@click.option("--series", type=str, default=None, help="Filter by series.")
@click.option("--status", "status_filter", type=str, default=None, help="Filter by processing_status.")
def knowledge_list(series: Optional[str], status_filter: Optional[str]) -> None:
    """List registered sources."""
    registry = _knowledge_registry()
    records = registry.all()
    if series:
        records = [r for r in records if r.series == series]
    if status_filter:
        records = [r for r in records if r.processing_status == status_filter]
    for r in sorted(records, key=lambda x: (x.series, x.issue_range, x.source_type)):
        click.echo(
            f"{r.source_id}  {r.series} {r.issue_range:<8} {r.source_type:<10} "
            f"{r.role:<10} {r.processing_status:<16} embedded={r.embedded} chunks={r.chunk_count}"
        )


@knowledge_group.command("reembed")
@click.argument("source_id", type=str)
@click.option("--content-type", type=str, default="lore", show_default=True)
def knowledge_reembed(source_id: str, content_type: str) -> None:
    """Force re-clean/chunk/embed of a single registered source."""
    from .ingestion.pipeline import IngestionPipeline

    event_store = EventStore()
    event_store.init_db()
    vector_store = ChromaVectorStore(paths.vector_db_path)
    registry = _knowledge_registry()

    pipeline = IngestionPipeline(
        registry=registry,
        vector_store=vector_store,
        event_store=event_store,
        cleaned_dir=paths.knowledge_cleaned_path,
        chunks_dir=paths.knowledge_chunks_path,
    )
    try:
        outcome = pipeline.reembed_source(source_id, content_type=content_type)
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    extra = f" reasons={outcome.reasons}" if outcome.reasons else ""
    click.echo(
        f"{outcome.source_id} status={outcome.status} chunks={outcome.chunk_count}{extra}"
    )


@knowledge_group.command("extract-all")
@click.option("--collection", type=str, required=True, help="Collection slug to enrich (e.g. spawn).")
def knowledge_extract_all(collection: str) -> None:
    """Phase 1 enrichment: inventory, coverage, timeline, provenance, graph updates."""
    from .knowledge_intel.orchestrator import run_phase1

    event_store = EventStore()
    event_store.init_db()
    registry = _knowledge_registry()
    paths.knowledge_intel_path.mkdir(parents=True, exist_ok=True)

    result = run_phase1(
        collection,
        registry=registry,
        event_store=event_store,
        chunks_dir=paths.knowledge_chunks_path,
        intel_root=paths.knowledge_intel_path,
        db_path=event_store.db_path,
        collection_registry=_collection_registry(),
    )
    click.echo(f"[{result.collection}] ok={result.ok} counts={result.counts}")
    for warning in result.warnings:
        click.echo(f"  warning: {warning}")
    for error in result.errors:
        click.echo(f"  error: {error['stage']}: {error['error']}")
    if not result.ok and not result.artifacts:
        raise click.ClickException(result.reason or "Phase 1 produced no outputs.")


@knowledge_group.command("set-domain")
@click.option("--collection", type=str, required=True, help="Collection slug (e.g. spawn, security_plus).")
@click.option("--domain", type=str, required=True, help="Domain label (e.g. comics, cybersecurity, programming).")
@click.option("--description", type=str, default=None, help="Human-readable description of the collection.")
@click.option("--subdomain", type=str, default=None, help="Optional finer-grained subdomain.")
@click.option("--category", type=str, default=None, help="Optional high-level category.")
def knowledge_set_domain(
    collection: str,
    domain: str,
    description: Optional[str],
    subdomain: Optional[str],
    category: Optional[str],
) -> None:
    """Declare a collection's taxonomy (stored separately from chunks/embeddings).

    Also syncs the domain onto existing registry records for that collection so
    registry-based services reflect it immediately. Chroma chunk metadata is
    updated by ``backfill-metadata``.
    """
    from .ingestion.source_registry import slugify_collection

    collection_registry = _collection_registry()
    meta = collection_registry.upsert(
        collection,
        domain=domain,
        description=description,
        subdomain=subdomain,
        category=category,
    )
    collection_registry.save()

    # Cheap, no-model sync of registry records for this collection.
    registry = _knowledge_registry()
    slug = slugify_collection(collection)
    synced = 0
    for record in registry.all():
        if record.resolved_collection() == slug and record.domain != domain:
            registry.update(record.source_id, domain=domain)
            synced += 1
    if synced:
        registry.save()

    click.echo(json.dumps({"collection": meta.collection, "domain": meta.domain, "records_synced": synced}, indent=2))


@knowledge_group.command("list-domains")
def knowledge_list_domains() -> None:
    """List declared collection taxonomy and flag untagged collections."""
    collection_registry = _collection_registry()
    registry = _knowledge_registry()
    declared = {m.collection: m for m in collection_registry.all()}

    from .ingestion.source_registry import slugify_collection

    seen_slugs = sorted({r.resolved_collection() for r in registry.all() if r.resolved_collection()})

    rows = []
    for slug in sorted(set(declared) | set(seen_slugs)):
        meta = declared.get(slug)
        rows.append(
            {
                "collection": slug,
                "domain": meta.domain if meta else "",
                "description": meta.description if meta else "",
                "tagged": meta is not None,
                "in_registry": slug in seen_slugs,
            }
        )
    click.echo(json.dumps(rows, indent=2))


@knowledge_group.command("backfill-metadata")
@click.option("--collection", type=str, default=None, help="Restrict backfill to one collection slug.")
@click.option("--force", is_flag=True, default=False, help="Re-tag even chunks that already have collection/domain.")
def knowledge_backfill_metadata(collection: Optional[str], force: bool) -> None:
    """Sync collection/domain onto registry records + Chroma chunks (rerunnable)."""
    from .knowledge_intel.backfill import backfill_collection_metadata

    registry = _knowledge_registry()
    vector_store = ChromaVectorStore(paths.vector_db_path)
    summary = backfill_collection_metadata(
        registry=registry,
        vector_store=vector_store,
        chunks_dir=paths.knowledge_chunks_path,
        collection_registry=_collection_registry(),
        only_collection=collection,
        force=force,
    )
    click.echo(json.dumps(summary, indent=2))


@knowledge_group.command("inventory")
@click.option("--collection", type=str, required=True)
def knowledge_inventory(collection: str) -> None:
    """Print the inventory for a collection."""
    from .knowledge_intel.collections import build_inventory

    registry = _knowledge_registry()
    inventory = build_inventory(
        registry, collection, chunks_dir=paths.knowledge_chunks_path, collection_registry=_collection_registry()
    )
    click.echo(json.dumps(inventory, indent=2))


@knowledge_group.command("coverage")
@click.option("--collection", type=str, required=True)
def knowledge_coverage(collection: str) -> None:
    """Print the coverage report for a collection."""
    from .knowledge_intel.coverage import build_coverage

    registry = _knowledge_registry()
    report = build_coverage(
        registry, collection, chunks_dir=paths.knowledge_chunks_path, collection_registry=_collection_registry()
    )
    click.echo(json.dumps(report, indent=2))


@knowledge_group.command("gaps")
@click.option("--collection", type=str, required=True)
@click.option("--explain", is_flag=True, default=False, help="Add a natural-language briefing (uses the local LLM when available; deterministic fallback otherwise).")
@click.option("--mode", type=click.Choice(["deterministic", "local_llm", "hybrid"]), default=None, help="Compute mode for --explain (defaults to BLACKFONG_KNOWLEDGE_COMPUTE_MODE / hybrid).")
def knowledge_gaps(collection: str, explain: bool, mode: Optional[str]) -> None:
    """Detect coverage gaps for a collection (deterministic)."""
    from .knowledge_intel.gaps import build_gaps, explain_gaps

    registry = _knowledge_registry()
    record = build_gaps(
        registry,
        collection,
        chunks_dir=paths.knowledge_chunks_path,
        collection_registry=_collection_registry(),
        db_path=paths.events_db,
    )
    if explain and record.get("found"):
        record["explanation"] = explain_gaps(
            record.get("gaps", []),
            collection=record.get("collection", collection),
            domain=record.get("domain", ""),
            mode=mode,
        )
    click.echo(json.dumps(record, indent=2))


@knowledge_group.command("summarize")
@click.option("--collection", type=str, required=True)
@click.option("--kind", type=click.Choice(["collection_summary", "source_summary"]), default="collection_summary", show_default=True)
@click.option("--source-id", "source_id", type=str, default=None, help="Required when --kind source_summary.")
@click.option("--mode", type=click.Choice(["deterministic", "local_llm", "hybrid"]), default=None, help="Compute mode (defaults to BLACKFONG_KNOWLEDGE_COMPUTE_MODE / hybrid).")
def knowledge_summarize(collection: str, kind: str, source_id: Optional[str], mode: Optional[str]) -> None:
    """Build a grounded summary of a collection or a single source."""
    from datetime import datetime, timezone

    from .knowledge_intel.summaries import build_summary
    from .ingestion.source_registry import slugify_collection

    registry = _knowledge_registry()
    record = build_summary(
        registry,
        collection,
        kind=kind,
        source_id=source_id,
        chunks_dir=paths.knowledge_chunks_path,
        collection_registry=_collection_registry(),
        mode=mode,
    )
    if record.get("found"):
        paths.knowledge_intel_path.mkdir(parents=True, exist_ok=True)
        out_dir = paths.knowledge_intel_path / slugify_collection(collection) / "summaries"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{kind}.json" if kind == "collection_summary" else f"source_{source_id}.json"
        artifact = out_dir / name
        artifact.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        record["artifact"] = str(artifact)

        event_store = EventStore()
        event_store.init_db()
        ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        event_store.add_event(
            Event.new(
                source="daise",
                type="knowledge_summary_created",
                payload={
                    "collection": record.get("collection"),
                    "kind": kind,
                    "summary_id": record.get("summary_id"),
                    "compute_used": record.get("compute_used"),
                    "source_ids": record.get("source_ids", []),
                    "artifact": str(artifact),
                },
                timestamp_ms=ts_ms,
                tags=["knowledge", "phase2", "summary"],
            )
        )
    click.echo(json.dumps(record, indent=2))


@knowledge_group.command("timeline")
@click.option("--collection", type=str, required=True)
@click.option("--limit", type=int, default=20, show_default=True, help="Max events to print.")
def knowledge_timeline(collection: str, limit: int) -> None:
    """Print the source-order timeline for a collection."""
    from .knowledge_intel.timeline import build_timeline

    registry = _knowledge_registry()
    timeline = build_timeline(registry, collection, chunks_dir=paths.knowledge_chunks_path)
    if not timeline.get("found"):
        click.echo(f"No timeline: {timeline.get('reason')}")
        return
    click.echo(f"[{timeline['collection']}] {timeline['event_count']} events ({timeline['ordering']})")
    for event in timeline["events"][:limit]:
        click.echo(
            f"  {event['sequence_index']:>4}. {event['title']:<24} "
            f"source={event['source_id']} chunk={event['chunk_id']}"
        )


@knowledge_group.command("ask")
@click.option("--question", "-q", type=str, required=True, help="Natural-language question for the librarian.")
@click.option("--collection", type=str, default=None, help="Restrict the answer to one collection (defaults to the whole knowledge base).")
@click.option("--top-k", "top_k", type=int, default=6, show_default=True, help="Number of passages to retrieve.")
@click.option("--mode", type=click.Choice(["deterministic", "local_llm", "hybrid"]), default=None, help="Compute mode (defaults to BLACKFONG_KNOWLEDGE_COMPUTE_MODE / hybrid).")
def knowledge_ask(question: str, collection: Optional[str], top_k: int, mode: Optional[str]) -> None:
    """Ask the grounded knowledge librarian a question (RAG + provenance)."""
    from .knowledge_intel.librarian import LibrarianAgent

    registry = _knowledge_registry()
    try:
        vector_store = ChromaVectorStore(paths.vector_db_path)
    except Exception:
        vector_store = None

    event_store = EventStore()
    event_store.init_db()

    agent = LibrarianAgent(
        registry=registry,
        vector_store=vector_store,
        chunks_dir=paths.knowledge_chunks_path,
        collection_registry=_collection_registry(),
        event_store=event_store,
    )
    record = agent.answer(question, collection=collection, top_k=top_k, mode=mode)
    click.echo(json.dumps(record, indent=2))


@knowledge_group.command("provenance")
@click.option("--chunk-id", "chunk_id", type=str, required=True)
def knowledge_provenance(chunk_id: str) -> None:
    """Show the provenance record for a single chunk id."""
    from .knowledge_intel.provenance import KnowledgeProvenanceService

    registry = _knowledge_registry()
    try:
        vector_store = ChromaVectorStore(paths.vector_db_path)
    except Exception:
        vector_store = None
    service = KnowledgeProvenanceService(
        registry=registry,
        chunks_dir=paths.knowledge_chunks_path,
        vector_store=vector_store,
        collection_registry=_collection_registry(),
    )
    click.echo(json.dumps(service.for_chunk(chunk_id), indent=2))


def main(argv: Optional[list[str]] = None) -> None:
    cli.main(args=argv, prog_name="blackfong")


if __name__ == "__main__":
    main()
