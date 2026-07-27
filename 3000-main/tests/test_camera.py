import importlib.util

import numpy as np
import pytest

CV2_AVAILABLE = importlib.util.find_spec("cv2") is not None
pytestmark = pytest.mark.skipif(not CV2_AVAILABLE, reason="cv2 unavailable")

if CV2_AVAILABLE:
    from three_thousand.core import camera


class FakeCapture:
    def __init__(self, opened: bool, frame_ok: bool = True) -> None:
        self._opened = opened
        self._frame_ok = frame_ok
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802
        return self._opened

    def read(self):
        if not self._frame_ok:
            return False, None
        return True, np.zeros((10, 10, 3), dtype=np.uint8)

    def release(self) -> None:
        self.released = True


def test_camera_availability_uses_capture(monkeypatch) -> None:
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda _idx: FakeCapture(opened=True))
    assert camera.is_camera_available(0) is True


def test_capture_frame_returns_none_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda _idx: FakeCapture(opened=False))
    assert camera.capture_frame(0) is None
