from __future__ import annotations

import os
import time
from pathlib import Path

from .config import Config
from .db import upsert_file


def iter_files(root: Path, *, recursive: bool) -> list[Path]:
    if not root.exists():
        return []
    if recursive:
        out: list[Path] = []
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                out.append(Path(dirpath) / fn)
        return out
    return [p for p in root.iterdir() if p.is_file()]


def scan_once(con, cfg: Config) -> int:
    """
    Scan watched folders and insert/update any stable files.
    Returns number of paths processed (best-effort).
    """
    processed = 0
    now = time.time()

    for folder in cfg.watched_folders:
        for p in iter_files(folder, recursive=cfg.recursive):
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            if not p.is_file():
                continue
            # Skip "too new" files to reduce half-copied ingest noise.
            if cfg.settle_seconds and (now - st.st_mtime) < cfg.settle_seconds:
                continue

            upsert_file(
                con,
                path=str(p),
                filename=p.name,
                size=int(st.st_size),
                mtime=float(st.st_mtime),
            )
            processed += 1

    return processed

