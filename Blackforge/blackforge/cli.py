from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from blackforge.core.generator import GenerationError, generate_project
from blackforge.core.templates import list_templates
from blackforge.ui.console import console, error, info, success

app = typer.Typer(help="BlackForge: local-first project scaffolding.")
new_app = typer.Typer(help="Create a new project from a template.")
app.add_typer(new_app, name="new")


@app.command("list-templates")
def list_templates_command() -> None:
    """List all available built-in templates."""
    available = list_templates()
    if not available:
        info("No templates found.")
        return

    table = Table(title="Available Templates")
    table.add_column("Template", style="magenta")
    for template_name in available:
        table.add_row(template_name)
    console.print(table)


def _create(template_name: str, project_name: str, force: bool, preview: bool) -> None:
    try:
        result = generate_project(
            template_name=template_name,
            project_name=project_name,
            output_dir=Path.cwd(),
            force=force,
            preview=preview,
        )
    except (GenerationError, ValueError) as exc:
        error(str(exc))
        raise typer.Exit(code=1) from exc

    action = "Previewed" if result.preview else "Created"
    success(
        f"{action} '{result.project_name}' from template '{template_name}' "
        f"at {result.destination}"
    )
    info(f"{'Would generate' if result.preview else 'Generated'} {len(result.created_files)} files.")


@new_app.command("cli")
def new_cli(
    project_name: str,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Overwrite destination if it exists.")
    ] = False,
    preview: Annotated[
        bool, typer.Option("--preview", help="Show the generation plan without writing files.")
    ] = False,
) -> None:
    _create("cli", project_name, force, preview)


@new_app.command("fastapi")
def new_fastapi(
    project_name: str,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Overwrite destination if it exists.")
    ] = False,
    preview: Annotated[
        bool, typer.Option("--preview", help="Show the generation plan without writing files.")
    ] = False,
) -> None:
    _create("fastapi", project_name, force, preview)


@new_app.command("python-package")
def new_python_package(
    project_name: str,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Overwrite destination if it exists.")
    ] = False,
    preview: Annotated[
        bool, typer.Option("--preview", help="Show the generation plan without writing files.")
    ] = False,
) -> None:
    _create("python-package", project_name, force, preview)


if __name__ == "__main__":
    app()
