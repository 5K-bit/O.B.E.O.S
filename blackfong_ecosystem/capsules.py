from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from .config import paths
from .events import Event
from .storage import EventStore


@dataclass
class CapsuleImporter:
    """
    Imports Black Box daily capsules into the Event DB.

    v0 assumption:
    - `{date}.raw` is a JSONL file where each line is a canonical EventDict.
      (id, timestamp, source, type, payload, confidence, hash)
    - Encryption and integrity verification happen on the Black Box side
      or in a future decryption layer that reconstructs the raw file.
    """

    store: EventStore

    def import_date(self, date_str: str) -> Tuple[int, str]:
        """
        Import all events from `{capsules_root}/{date}.raw`.

        Returns:
            (number_of_events_imported, summary_event_id)
        """
        raw_path = paths.capsules_root / f"{date_str}.raw"
        if not raw_path.exists():
            raise FileNotFoundError(f"Raw capsule file not found: {raw_path}")

        imported = 0
        with raw_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                event = Event.from_dict(data)
                self.store.add_event(event)
                imported += 1

        summary_event = Event.new(
            source="blackbox",
            type="capsule_imported",
            payload={
                "date": date_str,
                "raw_path": str(raw_path),
                "imported_events": imported,
            },
            confidence=1.0,
        )
        self.store.add_event(summary_event)

        return imported, summary_event.id

