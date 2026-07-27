from datetime import datetime, timezone
from pathlib import Path
import re

from jinja2 import Environment, FileSystemLoader

from blackforge.core.result import GenerationResult
from blackforge.core.templates import get_templates_dir, resolve_template_name


class GenerationError(Exception):
    """Raised when project generation cannot proceed."""


def _to_package_name(project_name: str) -> str:
    package = re.sub(r"[^a-zA-Z0-9]+", "_", project_name).strip("_").lower()
    if not package:
        raise GenerationError("Project name must contain letters or numbers.")
    return package


def _resolve_destination(destination_root: Path, project_name: str) -> Path:
    if any(separator in project_name for separator in ("/", "\\")):
        raise GenerationError("Project name must not contain path separators.")
    if project_name.strip() in {"", ".", ".."}:
        raise GenerationError("Project name must be a local folder name.")
    root = destination_root.expanduser().resolve()
    destination = (root / project_name).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise GenerationError("Destination must stay inside the approved output directory.") from exc
    return destination


def generate_project(
    template_name: str,
    project_name: str,
    output_dir: Path | None = None,
    force: bool = False,
    preview: bool = False,
) -> GenerationResult:
    destination_root = output_dir or Path.cwd()
    destination = _resolve_destination(destination_root, project_name)
    template_key = resolve_template_name(template_name)
    template_dir = get_templates_dir() / template_key

    if destination.exists() and not force and not preview:
        raise GenerationError(
            f"Destination '{destination}' already exists. Use --force to overwrite."
        )

    if not preview:
        destination.mkdir(parents=True, exist_ok=True)

    context = {
        "project_name": project_name,
        "package_name": _to_package_name(project_name),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
    }

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
        autoescape=False,
    )

    created_files: list[Path] = []
    for source in sorted(template_dir.rglob("*")):
        relative_path = source.relative_to(template_dir).as_posix()
        rendered_relative = env.from_string(relative_path).render(**context)
        target = (destination / rendered_relative).resolve()
        try:
            target.relative_to(destination)
        except ValueError as exc:
            raise GenerationError(f"Template path escapes destination: {rendered_relative}") from exc

        if source.is_dir():
            if not preview:
                target.mkdir(parents=True, exist_ok=True)
            continue

        if not preview:
            target.parent.mkdir(parents=True, exist_ok=True)
        rendered_content = env.get_template(relative_path).render(**context)
        if not preview:
            target.write_text(rendered_content, encoding="utf-8")
        created_files.append(target.relative_to(destination))

    return GenerationResult(
        template_name=template_name,
        project_name=project_name,
        destination=destination,
        created_files=created_files,
        preview=preview,
    )
