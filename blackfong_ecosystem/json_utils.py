from __future__ import annotations

import json
from typing import Any


def make_json_compatible(value: Any) -> Any:
    """Recursively normalize data into a JSON-compatible structure.

    The desktop workspace and operator surfaces occasionally receive nested
    payloads with tuple keys or set values from diagnostics/state snapshots.
    Those structures are fine for Python, but they crash ``json.dumps``.
    """
    if isinstance(value, dict):
        return {str(key): make_json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_compatible(item) for item in value]
    if isinstance(value, set):
        return [make_json_compatible(item) for item in sorted(value, key=str)]
    return value


def dump_pretty_json(value: Any) -> str:
    """Serialize an arbitrary payload into readable JSON for UI surfaces."""
    return json.dumps(make_json_compatible(value), indent=2, default=str)
