import numpy as np
from typing import List
from backend.domain.interfaces.image_detector import IImageDetector
from backend.use_cases.toast_tracker import ToastTracker, TrackedToast

class DetectAndNotifyUseCase:
    def __init__(self, detector: IImageDetector):
        self.detector = detector
        self.tracker = ToastTracker()

    def reset_tracker(self):
        """Resets the state of the internal toast tracker."""
        self.tracker.reset()

    def execute(self, frame: np.ndarray) -> List[TrackedToast]:
        """
        Executes the detection on a frame and updates the toast tracker.

        Returns every toast currently visible, which is what callers render and
        count.
        """
        detections = self.detector.detect_frame(frame)

        # El tracker devuelve además las tostadas recién confirmadas como
        # quemadas; ya no hay consumidor para ese segundo valor.
        active_toasts, _newly_burnt_toasts = self.tracker.update(detections)

        return active_toasts
