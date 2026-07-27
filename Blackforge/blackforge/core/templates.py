from pathlib import Path


def get_templates_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "templates"


def list_templates() -> list[str]:
    templates_dir = get_templates_dir()
    if not templates_dir.exists():
        return []

    names: list[str] = []
    for child in templates_dir.iterdir():
        if child.is_dir():
            names.append(child.name.replace("_", "-"))
    return sorted(names)


def resolve_template_name(template_name: str) -> str:
    normalized = template_name.strip().replace("-", "_")
    template_path = get_templates_dir() / normalized
    if not template_path.is_dir():
        raise ValueError(f"Template '{template_name}' does not exist.")
    return normalized
