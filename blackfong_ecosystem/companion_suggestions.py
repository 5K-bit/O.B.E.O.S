"""Evidence-linked, presentation-only companion prompts with durable rate limiting."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

PROMPTS = {
    "knowledge_ingested": "New knowledge was ingested. Would you like to explore how it connects to your current work?",
    "knowledge_phase1_completed": "Knowledge analysis completed. Would you like to review its coverage and gaps?",
    "knowledge_gaps_detected": "The knowledge pipeline reported gaps. Would you like to inspect the supporting evidence?",
    "tinyprogrammer_program_created": "TinyProgrammer recorded a new creation. Would you like to review it together?",
    "file_change_applied": "A file change was recorded. Would you like to review its validation results?",
    "spatial_observation_batch": "Earth received spatial records. Would you like to examine their source, age and coverage?",
}


class CompanionSuggestions:
    MIN_INTERVAL = 300.0

    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS shown (event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, shown_at REAL NOT NULL)")

    def _connection(self):
        return sqlite3.connect(self.path, timeout=1)

    def offer(self, events, *, busy=False):
        if busy:
            return None
        now = self.clock()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            last = db.execute("SELECT MAX(shown_at) FROM shown").fetchone()[0]
            if last is not None and now - last < self.MIN_INTERVAL:
                return None
            for event in list(events)[:100]:
                kind = event.get("type") or event.get("event_type")
                identity = str(event.get("id") or event.get("event_id") or "")
                if kind not in PROMPTS or not identity:
                    continue
                if db.execute("SELECT 1 FROM shown WHERE event_id=?", (identity,)).fetchone():
                    continue
                # High-frequency feeds do not create a new prompt every five minutes.
                recent_kind = db.execute("SELECT MAX(shown_at) FROM shown WHERE kind=?", (kind,)).fetchone()[0]
                if recent_kind is not None and now - recent_kind < 86400:
                    continue
                db.execute("INSERT INTO shown VALUES (?, ?, ?)", (identity, kind, now))
                db.execute("DELETE FROM shown WHERE event_id NOT IN (SELECT event_id FROM shown ORDER BY shown_at DESC LIMIT 2048)")
                return {"text": PROMPTS[kind], "evidence_event_id": identity, "event_type": kind,
                        "observed_at": event.get("timestamp"), "command": None, "camera_action": None}
        return None
