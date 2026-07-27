from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    data_dir: Path = Path("data")
    snapshots_subdir: str = "snapshots"
    database_name: str = "events.sqlite3"
    camera_index: int = 0
    motion_area_threshold: float = 0.02

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / self.snapshots_subdir

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_name

    def ensure_paths(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
