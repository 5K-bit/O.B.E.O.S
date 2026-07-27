import importlib.util

import numpy as np
import pytest

CV2_AVAILABLE = importlib.util.find_spec("cv2") is not None
pytestmark = pytest.mark.skipif(not CV2_AVAILABLE, reason="cv2 unavailable")

if CV2_AVAILABLE:
    from three_thousand.core.motion import MotionDetector


def test_first_frame_does_not_trigger_motion() -> None:
    detector = MotionDetector(area_threshold_ratio=0.01)
    frame = np.zeros((120, 120, 3), dtype=np.uint8)

    result = detector.detect(frame)

    assert result.detected is False
    assert result.confidence == 0.0


def test_motion_detected_on_synthetic_difference() -> None:
    detector = MotionDetector(area_threshold_ratio=0.01)
    frame_a = np.zeros((120, 120, 3), dtype=np.uint8)
    frame_b = frame_a.copy()
    frame_b[20:90, 20:90] = 255

    detector.detect(frame_a)
    result = detector.detect(frame_b)

    assert result.detected is True
    assert result.confidence > 0.01
