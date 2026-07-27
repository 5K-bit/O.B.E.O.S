from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np


def _utc_suffix() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def is_camera_available(camera_index: int = 0) -> bool:
    try:
        capture = cv2.VideoCapture(camera_index)
    except Exception:
        return False

    try:
        if not capture or not capture.isOpened():
            return False
        ok, _ = capture.read()
        return bool(ok)
    except Exception:
        return False
    finally:
        if capture:
            capture.release()


def capture_frame(camera_index: int = 0) -> np.ndarray | None:
    try:
        capture = cv2.VideoCapture(camera_index)
    except Exception:
        return None

    try:
        if not capture or not capture.isOpened():
            return None
        ok, frame = capture.read()
        if not ok:
            return None
        return frame
    except Exception:
        return None
    finally:
        if capture:
            capture.release()


def save_frame(frame: np.ndarray, snapshots_dir: Path) -> Path:
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshots_dir / f"{_utc_suffix()}.jpg"
    if not cv2.imwrite(str(snapshot_path), frame):
        raise RuntimeError("Failed to write snapshot image to disk.")
    return snapshot_path
