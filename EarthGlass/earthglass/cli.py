"""EarthGlass command-line interface."""

import typer
from rich.console import Console
from rich.table import Table

from earthglass.web.app import get_layers

app = typer.Typer(
    help="EarthGlass: a layered geospatial dashboard.",
    no_args_is_help=True,
)
cache_app = typer.Typer(help="Inspect EarthGlass cache state.", no_args_is_help=True)
app.add_typer(cache_app, name="cache")

console = Console()


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address for the server."),
    port: int = typer.Option(8000, help="Port to listen on."),
    reload: bool = typer.Option(False, help="Enable auto-reload for development."),
) -> None:
    """Run the EarthGlass web server."""
    import uvicorn

    console.print(
        f"[bold green]Starting EarthGlass[/] on [cyan]http://{host}:{port}[/]"
    )
    uvicorn.run("earthglass.web.app:app", host=host, port=port, reload=reload)


@app.command()
def layers() -> None:
    """List the map layers EarthGlass can render."""
    table = Table(title="EarthGlass Layers")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name", style="magenta")
    table.add_column("Type")
    table.add_column("Enabled")
    table.add_column("Description")

    for layer in get_layers():
        table.add_row(
            str(layer["id"]),
            str(layer["name"]),
            str(layer["type"]),
            "yes" if layer["enabled"] else "no",
            str(layer["description"]),
        )

    console.print(table)


@cache_app.command("status")
def cache_status() -> None:
    """Show the cache status for each layer."""
    console.print(
        "[yellow]Caching is not enabled in Milestone 1 \u2014 "
        "all layers report empty.[/]"
    )

    table = Table(title="Cache Status")
    table.add_column("Layer", style="cyan", no_wrap=True)
    table.add_column("Cached")
    table.add_column("Entries")

    for layer in get_layers():
        table.add_row(str(layer["id"]), "no", "0")

    console.print(table)


if __name__ == "__main__":
    app()
