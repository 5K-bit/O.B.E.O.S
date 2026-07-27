from __future__ import annotations
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from . import paths
from .actions import archive, mark_reviewed, reject, rename
from .config import Config, load as load_config, write as write_config, write_default
from .db import add_tags, connect, counts_by_status, get_item, list_items
from .scanner import scan_once
from .watcher import run_forever

app = typer.Typer(add_completion=False, help="QuietDrop: local-first file intake + staging.")
console = Console()


def _ensure_config() -> Path:
    cfg_path = paths.config_path()
    if not cfg_path.exists():
        write_default(cfg_path)
    return cfg_path


def _load_cfg() -> Config:
    cfg_path = _ensure_config()
    return load_config(cfg_path)


def _con():
    con = connect(paths.db_path())
    return con


@app.command()
def init(
    watch: list[Path] = typer.Option(
        [],
        "--watch",
        "-w",
        help="Folder to watch (repeatable).",
    ),
    archive_folder: Optional[Path] = typer.Option(
        None,
        "--archive",
        "-a",
        help="Archive destination folder.",
    ),
    recursive: bool = typer.Option(False, help="Watch subfolders too."),
    poll_seconds: float = typer.Option(2.0, help="Polling interval seconds."),
    settle_seconds: float = typer.Option(2.0, help="Ignore files newer than this."),
):
    """
    Create config + database (idempotent).
    """
    cfg_path = paths.config_path()
    cfg = Config(
        watched_folders=[p.expanduser().resolve() for p in watch],
        archive_folder=(archive_folder or Path("~/QuietDropArchive")).expanduser().resolve(),
        poll_seconds=poll_seconds,
        recursive=recursive,
        settle_seconds=settle_seconds,
    )
    write_config(cfg_path, cfg)
    cfg.archive_folder.mkdir(parents=True, exist_ok=True)

    con = _con()
    con.close()

    console.print(f"[bold]config[/bold]: {cfg_path}")
    console.print(f"[bold]db[/bold]: {paths.db_path()}")
    console.print(f"[bold]archive[/bold]: {cfg.archive_folder}")
    if cfg.watched_folders:
        for p in cfg.watched_folders:
            console.print(f"[bold]watch[/bold]: {p}")
    else:
        console.print("[yellow]No watched folders configured yet.[/yellow] Edit config or rerun init with --watch.")


@app.command()
def status():
    """
    Show counts by status.
    """
    con = _con()
    try:
        c = counts_by_status(con)
    finally:
        con.close()

    table = Table(title="QuietDrop status", show_header=True, header_style="bold")
    table.add_column("new", justify="right")
    table.add_column("reviewed", justify="right")
    table.add_column("archived", justify="right")
    table.add_column("rejected", justify="right")
    table.add_row(str(c["new"]), str(c["reviewed"]), str(c["archived"]), str(c["rejected"]))
    console.print(table)


@app.command("list")
def list_cmd(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max items to show."),
):
    """
    List items in the queue.
    """
    con = _con()
    try:
        items = list_items(con, status=status, limit=limit)  # type: ignore[arg-type]
    finally:
        con.close()

    table = Table(show_header=True, header_style="bold")
    table.add_column("id", style="cyan", justify="right")
    table.add_column("status", style="magenta")
    table.add_column("filename")
    table.add_column("size", justify="right")
    table.add_column("path")
    for i in items:
        table.add_row(str(i.id), i.status, i.filename, str(i.size), i.path)
    console.print(table)


@app.command()
def scan():
    """
    One-shot scan of watched folders.
    """
    cfg = _load_cfg()
    con = _con()
    try:
        n = scan_once(con, cfg)
    finally:
        con.close()
    console.print(f"scanned: {n} files (stable)")


@app.command()
def run():
    """
    Run watcher + polling loop forever (service mode).
    """
    cfg = _load_cfg()
    con = _con()
    try:
        run_forever(con, cfg)
    finally:
        con.close()


@app.command()
def review(item_id: int):
    """
    Mark an item as reviewed.
    """
    con = _con()
    try:
        item = mark_reviewed(con, item_id)
    finally:
        con.close()
    console.print(f"reviewed: {item.id} {item.filename}")


@app.command("archive")
def archive_cmd(item_id: int):
    """
    Move an item to the archive folder and mark archived.
    """
    cfg = _load_cfg()
    con = _con()
    try:
        item = archive(con, cfg, item_id)
    finally:
        con.close()
    console.print(f"archived: {item.id} -> {item.path}")


@app.command("reject")
def reject_cmd(item_id: int):
    """
    Mark an item rejected (no move).
    """
    con = _con()
    try:
        item = reject(con, item_id)
    finally:
        con.close()
    console.print(f"rejected: {item.id} {item.filename}")


@app.command("rename")
def rename_cmd(item_id: int, new_name: str):
    """
    Rename a file on disk and update the DB path.
    """
    con = _con()
    try:
        item = rename(con, item_id, new_name)
    finally:
        con.close()
    console.print(f"renamed: {item.id} -> {item.path}")


@app.command()
def tag(item_id: int, tags: list[str] = typer.Argument(..., help="One or more tags.")):
    """
    Add tags to an item.
    """
    con = _con()
    try:
        if not get_item(con, item_id):
            raise typer.Exit(code=2)
        add_tags(con, item_id, tags)
        item = get_item(con, item_id)
    finally:
        con.close()
    assert item is not None
    console.print(f"tags: {item.id} -> {item.tags}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8844, help="Bind port."),
    watch: bool = typer.Option(True, "--watch/--no-watch", help="Run watcher in background."),
):
    """
    Start the QuietDrop web UI + API.
    """
    from .web.app import create_app

    # Ensure config exists so the server has something to load.
    _ensure_config()

    app_ = create_app(start_watcher=watch)
    uvicorn.run(app_, host=host, port=port, log_level="info")

