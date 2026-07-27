from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(slots=True)
class MotionResult:
    detected: bool
    confidence: float
    metadata: dict[str, Any]


class MotionDetector:
    def __init__(self, area_threshold_ratio: float = 0.02) -> None:
        self._previous_gray: np.ndarray | None = None
        self._area_threshold_ratio = area_threshold_ratio

    def detect(self, frame: np.ndarray) -> MotionResult:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self._previous_gray is None:
            self._previous_gray = gray
            return MotionResult(detected=False, confidence=0.0, metadata={"max_area": 0.0})

        frame_delta = cv2.absdiff(self._previous_gray, gray)
        thresholded = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)[1]
        thresholded = cv2.dilate(thresholded, None, iterations=2)
        contours, _ = cv2.findContours(thresholded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        max_area = 0.0
        for contour in contours:
            area = float(cv2.contourArea(contour))
            max_area = max(max_area, area)

        frame_area = float(frame.shape[0] * frame.shape[1]) or 1.0
        confidence = min(max_area / frame_area, 1.0)
        detected = confidence >= self._area_threshold_ratio
        metadata = {"max_area": max_area, "frame_area": frame_area}
        self._previous_gray = gray
        return MotionResult(detected=detected, confidence=confidence, metadata=metadata)
