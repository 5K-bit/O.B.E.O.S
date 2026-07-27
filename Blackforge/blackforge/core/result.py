from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class GenerationResult:
    template_name: str
    project_name: str
    destination: Path
    created_files: list[Path] = field(default_factory=list)
    preview: bool = False
