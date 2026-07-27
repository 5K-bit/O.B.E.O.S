from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib


@dataclass(frozen=True)
class Config:
    watched_folders: list[Path]
    archive_folder: Path
    poll_seconds: float = 2.0
    recursive: bool = False
    settle_seconds: float = 2.0


DEFAULT_TOML = """\
# QuietDrop configuration
#
# - watched_folders: directories QuietDrop scans/watches for incoming files
# - archive_folder: destination directory for "archive" action
# - poll_seconds: polling interval (fallback + safety net)
# - recursive: watch subfolders too (default false)
# - settle_seconds: ignore files newer than this (avoid half-copied files)

watched_folders = []
archive_folder = "~/QuietDropArchive"
poll_seconds = 2.0
recursive = false
settle_seconds = 2.0
"""


def _expand_path(p: str | Path) -> Path:
    return Path(p).expanduser().resolve()


def load(path: Path) -> Config:
    raw = path.read_bytes()
    data: dict[str, Any] = tomllib.loads(raw.decode("utf-8"))

    watched = [_expand_path(p) for p in data.get("watched_folders", [])]
    archive = _expand_path(data.get("archive_folder", "~/QuietDropArchive"))
    poll_seconds = float(data.get("poll_seconds", 2.0))
    recursive = bool(data.get("recursive", False))
    settle_seconds = float(data.get("settle_seconds", 2.0))

    return Config(
        watched_folders=watched,
        archive_folder=archive,
        poll_seconds=poll_seconds,
        recursive=recursive,
        settle_seconds=settle_seconds,
    )


def dump(cfg: Config) -> str:
    def _q(s: str) -> str:
        # minimal TOML string quoting
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    watched = ", ".join(_q(str(p)) for p in cfg.watched_folders)
    return (
        "# QuietDrop configuration\n"
        "watched_folders = [" + watched + "]\n"
        f"archive_folder = {_q(str(cfg.archive_folder))}\n"
        f"poll_seconds = {float(cfg.poll_seconds)}\n"
        f"recursive = {'true' if cfg.recursive else 'false'}\n"
        f"settle_seconds = {float(cfg.settle_seconds)}\n"
    )


def write(path: Path, cfg: Config) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(cfg), encoding="utf-8")


def write_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_TOML, encoding="utf-8")

