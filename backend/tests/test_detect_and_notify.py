"""Tests for DetectAndNotifyUseCase (no model load, no network)."""

import numpy as np

from backend.domain.entities.detection import DetectionResult
from backend.use_cases.detect_and_notify import DetectAndNotifyUseCase

FRAME = np.zeros((16, 16, 3), dtype=np.uint8)


class FakeDetector:
    """Returns a fixed detection for every frame; no model is ever loaded."""

    def __init__(self, label="TCQ", confidence=0.91, bbox=(4, 4, 12, 12)):
        self.label = label
        self.confidence = confidence
        self.bbox = bbox

    def detect_frame(self, _frame):
        return [DetectionResult(self.label, self.confidence, self.bbox)]


def _use_case(label="TCQ"):
    return DetectAndNotifyUseCase(FakeDetector(label=label))


def test_execute_returns_the_visible_toasts():
    use_case = _use_case()

    toasts = use_case.execute(FRAME)

    assert len(toasts) == 1
    toast = toasts[0]
    assert toast.id == 1
    assert toast.label == "TCOK"
    assert toast.state == "ok"


def test_burn_is_only_confirmed_after_consecutive_frames():
    use_case = _use_case()

    # The tracker confirms a burn after 3 consecutive burnt frames.
    assert use_case.execute(FRAME)[0].state == "ok"
    assert use_case.execute(FRAME)[0].state == "ok"

    burned = use_case.execute(FRAME)
    assert burned[0].state == "burnt"
    assert burned[0].label == "TCQ"


def test_normalized_burn_label_is_treated_as_burnt():
    # "quemada" is normalized by the tracker just like "TCQ".
    use_case = _use_case(label="quemada")

    for _ in range(2):
        use_case.execute(FRAME)

    assert use_case.execute(FRAME)[0].state == "burnt"


def test_reset_tracker_clears_the_session():
    use_case = _use_case()
    for _ in range(3):
        use_case.execute(FRAME)
    assert use_case.execute(FRAME)[0].state == "burnt"

    use_case.reset_tracker()

    fresh = use_case.execute(FRAME)
    assert fresh[0].state == "ok"
    assert fresh[0].id == 1
