from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Migration:
    version: int
    path: Path


def _migrations_dir() -> Path:
    return Path(__file__).parent / "migrations"


def list_migrations() -> list[Migration]:
    migs: list[Migration] = []
    for p in sorted(_migrations_dir().glob("*.sql")):
        # expects "0001_name.sql"
        stem = p.stem.split("_", 1)[0]
        try:
            version = int(stem)
        except ValueError:
            continue
        migs.append(Migration(version=version, path=p))
    migs.sort(key=lambda m: m.version)
    return migs


def current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        if row and row[0] is not None:
            return int(row[0])
        return 0
    except sqlite3.OperationalError:
        # schema_version doesn't exist yet
        return 0


def apply_migrations(db_path: Path) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        cur = current_version(conn)
        applied = 0
        for m in list_migrations():
            if m.version <= cur:
                continue
            sql = m.path.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.commit()
            applied += 1
            cur = m.version
        return applied
    finally:
        conn.close()

