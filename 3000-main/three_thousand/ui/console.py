from __future__ import annotations

from rich.console import Console
from rich.table import Table

from three_thousand.core.events import Event

console = Console()


def print_status(available: bool) -> None:
    if available:
        console.print("[green]Camera is available.[/green]")
        return
    console.print("[yellow]Camera is unavailable.[/yellow]")


def print_snapshot_saved(snapshot_path: str) -> None:
    console.print(f"[green]Snapshot saved:[/green] {snapshot_path}")


def print_motion_alert(confidence: float, snapshot_path: str) -> None:
    console.print(
        f"[bold red]Motion detected[/bold red] "
        f"(confidence={confidence:.4f}) -> {snapshot_path}"
    )


def print_error(message: str) -> None:
    console.print(f"[red]{message}[/red]")


def print_events(events: list[Event]) -> None:
    if not events:
        console.print("[yellow]No events found.[/yellow]")
        return

    table = Table(title="Recent Motion Events")
    table.add_column("ID", justify="right")
    table.add_column("Timestamp")
    table.add_column("Type")
    table.add_column("Confidence", justify="right")
    table.add_column("Snapshot")

    for event in events:
        table.add_row(
            str(event.id),
            event.timestamp,
            event.event_type,
            f"{event.confidence:.4f}",
            event.snapshot_path,
        )
    console.print(table)
