from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def _load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = path.read_bytes()
    try:
        import tomllib  # py>=3.11

        return tomllib.loads(raw.decode("utf-8"))
    except ModuleNotFoundError:
        import tomli  # py<3.11

        return tomli.loads(raw.decode("utf-8"))


def _default_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "nightwatch"
    return Path.home() / ".local" / "share" / "nightwatch"


def _default_config_path() -> Path | None:
    # precedence: env -> cwd -> XDG config
    env = os.environ.get("NIGHTWATCH_CONFIG")
    if env:
        return Path(env).expanduser()

    cwd = Path.cwd() / "nightwatch.toml"
    if cwd.exists():
        return cwd

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else (Path.home() / ".config")
    p = base / "nightwatch" / "nightwatch.toml"
    return p if p.exists() else None


def _writable_backups_dir(preferred: Path, fallback: Path) -> Path:
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".writecheck"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return preferred
    except Exception:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    backups_dir: Path
    host: str
    port: int
    config_path: Path | None


_CACHED: Settings | None = None


def get_settings() -> Settings:
    global _CACHED
    if _CACHED is not None:
        return _CACHED

    config_path = _default_config_path()
    cfg = _load_toml(config_path) if config_path else {}
    nw = cfg.get("nightwatch", cfg)  # allow either [nightwatch] or top-level

    data_dir = Path(os.environ.get("NIGHTWATCH_DATA_DIR", nw.get("data_dir", str(_default_data_dir())))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)

    db_path = Path(os.environ.get("NIGHTWATCH_DB_PATH", nw.get("db_path", str(data_dir / "nightwatch.db")))).expanduser()

    preferred_backups = Path(
        os.environ.get("NIGHTWATCH_BACKUPS_DIR", nw.get("backups_dir", "/backups"))
    ).expanduser()
    backups_dir = _writable_backups_dir(preferred_backups, data_dir / "backups")

    host = os.environ.get("NIGHTWATCH_HOST", nw.get("host", "127.0.0.1"))
    port = int(os.environ.get("NIGHTWATCH_PORT", str(nw.get("port", 8037))))

    _CACHED = Settings(
        data_dir=data_dir,
        db_path=db_path,
        backups_dir=backups_dir,
        host=host,
        port=port,
        config_path=config_path,
    )
    return _CACHED

