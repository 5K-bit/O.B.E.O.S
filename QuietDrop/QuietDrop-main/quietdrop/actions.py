from __future__ import annotations

import os
from pathlib import Path

from .config import Config
from .db import Item, get_item, set_path, set_status


def mark_reviewed(con, item_id: int) -> Item:
    item = get_item(con, item_id)
    if not item:
        raise KeyError(f"item {item_id} not found")
    set_status(con, item_id, "reviewed")
    item = get_item(con, item_id)
    assert item is not None
    return item


def reject(con, item_id: int) -> Item:
    item = get_item(con, item_id)
    if not item:
        raise KeyError(f"item {item_id} not found")
    set_status(con, item_id, "rejected")
    item = get_item(con, item_id)
    assert item is not None
    return item


def _unique_dest(dest_dir: Path, filename: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = Path(filename).stem
    ext = Path(filename).suffix
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    for i in range(1, 10_000):
        candidate = dest_dir / f"{base}-{i}{ext}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("could not find unique destination filename")


def archive(con, cfg: Config, item_id: int) -> Item:
    item = get_item(con, item_id)
    if not item:
        raise KeyError(f"item {item_id} not found")

    src = Path(item.path)
    if not src.exists():
        # Source of truth is filesystem; still allow status update if missing,
        # but don't invent a new path.
        set_status(con, item_id, "archived")
        item2 = get_item(con, item_id)
        assert item2 is not None
        return item2

    dest = _unique_dest(cfg.archive_folder, src.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dest)

    set_path(con, item_id, str(dest), dest.name)
    set_status(con, item_id, "archived")

    item2 = get_item(con, item_id)
    assert item2 is not None
    return item2


def rename(con, item_id: int, new_name: str) -> Item:
    item = get_item(con, item_id)
    if not item:
        raise KeyError(f"item {item_id} not found")

    src = Path(item.path)
    dest = src.with_name(new_name)
    if src.exists():
        os.replace(src, dest)

    set_path(con, item_id, str(dest), dest.name)
    item2 = get_item(con, item_id)
    assert item2 is not None
    return item2

