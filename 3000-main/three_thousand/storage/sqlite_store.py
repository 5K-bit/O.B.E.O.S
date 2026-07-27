from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from three_thousand.core.events import Event, sanitize_metadata, utc_timestamp


class SQLiteStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    snapshot_path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def add_event(
        self,
        event_type: str,
        confidence: float,
        snapshot_path: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        payload = json.dumps(sanitize_metadata(metadata))
        with sqlite3.connect(self.database_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO events (timestamp, event_type, confidence, snapshot_path, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (utc_timestamp(), event_type, confidence, snapshot_path, payload),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_events(self, limit: int = 20) -> list[Event]:
        with sqlite3.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, timestamp, event_type, confidence, snapshot_path, metadata_json
                FROM events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            Event(
                id=int(row["id"]),
                timestamp=str(row["timestamp"]),
                event_type=str(row["event_type"]),
                confidence=float(row["confidence"]),
                snapshot_path=str(row["snapshot_path"]),
                metadata_json=str(row["metadata_json"]),
            )
            for row in rows
        ]
