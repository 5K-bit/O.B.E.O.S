from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path


def _backup_filename(today: date) -> str:
    return f"nightwatch-{today.isoformat()}.db"


def ensure_daily_backup(db_path: Path, backups_dir: Path) -> Path | None:
    """
    Create one SQLite backup per day.
    Returns the backup path if created, else None.
    """
    backups_dir.mkdir(parents=True, exist_ok=True)
    out = backups_dir / _backup_filename(date.today())
    if out.exists():
        return None

    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(out)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    return out

