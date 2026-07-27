"""Typer CLI entrypoint for MaxPayne."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path

from rich.panel import Panel
import typer

from maxpayne.core.runner import CheckRunner
from maxpayne.core.system import detect_platform
from maxpayne.explain import explain_file
from maxpayne.heal import apply_default_heal, heal_dependency, heal_env_files, heal_git_config, heal_port
from maxpayne.ui.console import console, render_results_table, render_summary, summary_counts

app = typer.Typer(help="MaxPayne - local developer environment doctor.")
doctor_app = typer.Typer(help="Run a focused check group.")
heal_app = typer.Typer(help="Apply targeted environment fixes.", invoke_without_command=True)

app.add_typer(doctor_app, name="doctor")
app.add_typer(heal_app, name="heal")

logger = logging.getLogger(__name__)


@app.callback()
def app_callback(
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging output."),
) -> None:
    """Configure global CLI options."""
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(levelname)s: %(message)s",
        force=True,
    )
    logger.debug("Debug logging enabled")


@app.command()
def diagnose() -> None:
    """Run all diagnostics."""
    runner = CheckRunner()
    results = runner.run_all()
    render_results_table(results, title="MaxPayne Diagnose")
    render_summary(results)


@app.command()
def ports() -> None:
    """Inspect common local development ports."""
    runner = CheckRunner()
    results = runner.run_group("ports")
    render_results_table(results, title="MaxPayne Ports")
    render_summary(results)


@app.command()
def report(
    output: Path = typer.Option(
        Path("maxpayne-report.json"),
        "--output",
        "-o",
        help="Path for exported JSON report.",
    ),
) -> None:
    """Run all checks and export a JSON diagnostic report."""
    runner = CheckRunner()
    results = runner.run_all()
    pass_count, warn_count, fail_count = summary_counts(results)
    platform_name, is_wsl = detect_platform()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": {"system": platform_name, "is_wsl": is_wsl},
        "summary": {"pass": pass_count, "warn": warn_count, "fail": fail_count},
        "results": [{**asdict(result), "status": result.status.lower()} for result in results],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    render_results_table(results, title="MaxPayne Report")
    render_summary(results)
    typer.echo(f"Report written to {output}")


@app.command()
def explain(
    log_file: Path = typer.Argument(..., help="Path to crash log or traceback file."),
    model: str = typer.Option("llama3.2", "--model", help="Local Ollama model to query."),
) -> None:
    """Explain a crash log in plain English using local Ollama when available."""
    if not log_file.exists():
        raise typer.BadParameter(f"File not found: {log_file}")

    explanation = explain_file(log_file, model=model)
    console.print(
        Panel(
            explanation.explanation,
            title=f"MaxPayne Explain ({explanation.source})",
            border_style="cyan",
        )
    )


@doctor_app.command("python")
def doctor_python() -> None:
    """Run Python-specific checks."""
    _run_single_group("python", "MaxPayne Doctor: Python")


@doctor_app.command("git")
def doctor_git() -> None:
    """Run Git-specific checks."""
    _run_single_group("git", "MaxPayne Doctor: Git")


@doctor_app.command("docker")
def doctor_docker() -> None:
    """Run Docker-specific checks."""
    _run_single_group("docker", "MaxPayne Doctor: Docker")


@doctor_app.command("ollama")
def doctor_ollama() -> None:
    """Run Ollama-specific checks."""
    _run_single_group("ollama", "MaxPayne Doctor: Ollama")


@doctor_app.command("windows")
def doctor_windows() -> None:
    """Run Windows-specific checks."""
    _run_single_group("windows", "MaxPayne Doctor: Windows")


@heal_app.callback(invoke_without_command=True)
def heal_default(
    ctx: typer.Context,
    interactive: bool = typer.Option(False, "--interactive", help="Prompt before modifying settings."),
) -> None:
    """Apply safe default healing actions."""
    if ctx.invoked_subcommand is not None:
        return

    results = apply_default_heal(interactive=interactive)
    render_results_table(results, title="MaxPayne Heal")
    render_summary(results)


@heal_app.command("git")
def heal_git(
    interactive: bool = typer.Option(False, "--interactive", help="Prompt before setting missing values."),
) -> None:
    """Heal missing global git identity settings."""
    result = heal_git_config(interactive=interactive)
    render_results_table([result], title="MaxPayne Heal: Git")
    render_summary([result])


@heal_app.command("env")
def heal_env() -> None:
    """Heal `.env` / `.env.example` mismatch."""
    result = heal_env_files()
    render_results_table([result], title="MaxPayne Heal: Env")
    render_summary([result])


@heal_app.command("port")
def heal_port_command(
    port: int = typer.Argument(..., help="Port to free from listening processes."),
    interactive: bool = typer.Option(False, "--interactive", help="Prompt before terminating processes."),
    approve: bool = typer.Option(False, "--approve", help="Actually terminate matching processes without prompts."),
) -> None:
    """Free a busy local port."""
    result = heal_port(port=port, interactive=interactive, dry_run=not approve and not interactive)
    render_results_table([result], title=f"MaxPayne Heal: Port {port}")
    render_summary([result])


@heal_app.command("dependency")
def heal_dependency_command(
    package: str = typer.Argument(..., help="Dependency name to install into current Python environment."),
) -> None:
    """Install a missing Python dependency."""
    result = heal_dependency(package)
    render_results_table([result], title=f"MaxPayne Heal: Dependency {package}")
    render_summary([result])


def _run_single_group(group_name: str, title: str) -> None:
    runner = CheckRunner()
    results = runner.run_group(group_name)
    render_results_table(results, title=title)
    render_summary(results)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
