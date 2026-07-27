from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional


Status = Literal["new", "reviewed", "archived", "rejected"]


@dataclass(frozen=True)
class Item:
    id: int
    path: str
    filename: str
    size: int
    mtime: float
    first_seen: float
    status: Status
    reviewed_at: Optional[float]
    archived_at: Optional[float]
    rejected_at: Optional[float]
    tags: list[str]


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  first_seen REAL NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('new','reviewed','archived','rejected')),
  reviewed_at REAL,
  archived_at REAL,
  rejected_at REAL,
  tags TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_items_status_first_seen
  ON items(status, first_seen DESC);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _row_to_item(r: sqlite3.Row) -> Item:
    return Item(
        id=int(r["id"]),
        path=str(r["path"]),
        filename=str(r["filename"]),
        size=int(r["size"]),
        mtime=float(r["mtime"]),
        first_seen=float(r["first_seen"]),
        status=r["status"],
        reviewed_at=r["reviewed_at"],
        archived_at=r["archived_at"],
        rejected_at=r["rejected_at"],
        tags=list(json.loads(r["tags"] or "[]")),
    )


def upsert_file(
    con: sqlite3.Connection,
    *,
    path: str,
    filename: str,
    size: int,
    mtime: float,
) -> int:
    now = time.time()
    cur = con.execute(
        """
        INSERT INTO items(path, filename, size, mtime, first_seen, status)
        VALUES(?, ?, ?, ?, ?, 'new')
        ON CONFLICT(path) DO UPDATE SET
          filename=excluded.filename,
          size=excluded.size,
          mtime=excluded.mtime
        """,
        (path, filename, size, mtime, now),
    )
    con.commit()
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = con.execute("SELECT id FROM items WHERE path=?", (path,)).fetchone()
    return int(row["id"])


def get_item(con: sqlite3.Connection, item_id: int) -> Item | None:
    row = con.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    return _row_to_item(row) if row else None


def get_item_by_path(con: sqlite3.Connection, path: str) -> Item | None:
    row = con.execute("SELECT * FROM items WHERE path=?", (path,)).fetchone()
    return _row_to_item(row) if row else None


def list_items(
    con: sqlite3.Connection,
    *,
    status: Status | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Item]:
    if status is None:
        rows = con.execute(
            "SELECT * FROM items ORDER BY first_seen DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM items WHERE status=? ORDER BY first_seen DESC LIMIT ? OFFSET ?",
            (status, limit, offset),
        ).fetchall()
    return [_row_to_item(r) for r in rows]


def counts_by_status(con: sqlite3.Connection) -> dict[str, int]:
    rows = con.execute(
        "SELECT status, COUNT(*) AS c FROM items GROUP BY status"
    ).fetchall()
    out = {"new": 0, "reviewed": 0, "archived": 0, "rejected": 0}
    for r in rows:
        out[str(r["status"])] = int(r["c"])
    return out


def set_status(con: sqlite3.Connection, item_id: int, status: Status) -> None:
    now = time.time()
    reviewed_at = now if status == "reviewed" else None
    archived_at = now if status == "archived" else None
    rejected_at = now if status == "rejected" else None

    con.execute(
        """
        UPDATE items
        SET status=?,
            reviewed_at=COALESCE(reviewed_at, ?),
            archived_at=COALESCE(archived_at, ?),
            rejected_at=COALESCE(rejected_at, ?)
        WHERE id=?
        """,
        (status, reviewed_at, archived_at, rejected_at, item_id),
    )
    con.commit()


def set_path(con: sqlite3.Connection, item_id: int, new_path: str, new_filename: str) -> None:
    con.execute(
        "UPDATE items SET path=?, filename=? WHERE id=?",
        (new_path, new_filename, item_id),
    )
    con.commit()


def set_path_by_old_path(
    con: sqlite3.Connection, old_path: str, new_path: str, new_filename: str
) -> None:
    con.execute(
        "UPDATE items SET path=?, filename=? WHERE path=?",
        (new_path, new_filename, old_path),
    )
    con.commit()


def add_tags(con: sqlite3.Connection, item_id: int, tags: Iterable[str]) -> None:
    item = get_item(con, item_id)
    if not item:
        return
    existing = set(item.tags)
    for t in tags:
        t = t.strip()
        if t:
            existing.add(t)
    con.execute(
        "UPDATE items SET tags=? WHERE id=?",
        (json.dumps(sorted(existing)), item_id),
    )
    con.commit()

