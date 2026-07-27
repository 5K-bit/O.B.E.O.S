from __future__ import annotations

import os
from pathlib import Path


def _xdg_dir(env_key: str, default_suffix: str) -> Path:
    val = os.environ.get(env_key)
    if val:
        return Path(val).expanduser()
    return Path.home() / default_suffix


def config_path() -> Path:
    override = os.environ.get("QUIETDROP_CONFIG")
    if override:
        return Path(override).expanduser()
    return _xdg_dir("XDG_CONFIG_HOME", ".config") / "quietdrop" / "config.toml"


def data_dir() -> Path:
    override = os.environ.get("QUIETDROP_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return _xdg_dir("XDG_DATA_HOME", ".local/share") / "quietdrop"


def db_path() -> Path:
    override = os.environ.get("QUIETDROP_DB")
    if override:
        return Path(override).expanduser()
    return data_dir() / "quietdrop.db"


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

