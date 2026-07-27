from __future__ import annotations

import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import Config
from .db import get_item_by_path, set_path_by_old_path, upsert_file
from .scanner import scan_once


class _Handler(FileSystemEventHandler):
    def __init__(self, con, cfg: Config, lock: threading.Lock):
        self._con = con
        self._cfg = cfg
        self._lock = lock

    def on_created(self, event):
        if event.is_directory:
            return
        self._intake(Path(event.src_path))

    def on_moved(self, event):
        if event.is_directory:
            return
        src = str(event.src_path)
        dest = Path(event.dest_path)
        with self._lock:
            existing = get_item_by_path(self._con, src)
            if existing:
                set_path_by_old_path(self._con, src, str(dest), dest.name)
                return
        self._intake(dest)

    def on_modified(self, event):
        # best-effort metadata refresh; skip noisy writes by settle_seconds in scan loop
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.exists() and p.is_file():
            try:
                st = p.stat()
            except FileNotFoundError:
                return
            with self._lock:
                upsert_file(
                    self._con,
                    path=str(p),
                    filename=p.name,
                    size=int(st.st_size),
                    mtime=float(st.st_mtime),
                )

    def _intake(self, p: Path) -> None:
        try:
            st = p.stat()
        except FileNotFoundError:
            return
        if not p.is_file():
            return
        # If it's "too new", let the polling scan catch it on the next pass.
        now = time.time()
        if self._cfg.settle_seconds and (now - st.st_mtime) < self._cfg.settle_seconds:
            return
        with self._lock:
            upsert_file(
                self._con,
                path=str(p),
                filename=p.name,
                size=int(st.st_size),
                mtime=float(st.st_mtime),
            )


def start_observer(con, cfg: Config) -> Observer:
    lock = threading.Lock()
    handler = _Handler(con, cfg, lock)
    obs = Observer()
    for folder in cfg.watched_folders:
        obs.schedule(handler, str(folder), recursive=cfg.recursive)
    obs.start()
    return obs


def run_forever(con, cfg: Config, *, stop_event: threading.Event | None = None) -> None:
    """
    Run watchdog + polling scan loop.
    """
    stop_event = stop_event or threading.Event()
    lock = threading.Lock()
    handler = _Handler(con, cfg, lock)
    obs = Observer() if cfg.watched_folders else None
    if obs:
        for folder in cfg.watched_folders:
            obs.schedule(handler, str(folder), recursive=cfg.recursive)
        obs.start()
    try:
        # Initial scan so the queue reflects reality.
        with lock:
            scan_once(con, cfg)
        while not stop_event.is_set():
            with lock:
                scan_once(con, cfg)
            stop_event.wait(cfg.poll_seconds)
    finally:
        if obs:
            obs.stop()
            obs.join(timeout=5)

