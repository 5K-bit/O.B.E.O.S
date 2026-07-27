from __future__ import annotations

# Backwards-compatible re-exports.
# Prefer importing `get_settings()` from `nightwatch.config`.

from nightwatch.config import get_settings

_s = get_settings()

DATA_DIR = _s.data_dir
DB_PATH = _s.db_path
BACKUPS_DIR = _s.backups_dir
HOST = _s.host
PORT = _s.port

