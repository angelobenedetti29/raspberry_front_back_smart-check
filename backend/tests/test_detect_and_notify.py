"""Tests for DetectAndNotifyUseCase (hardware/network-free, no model load)."""

import numpy as np

from backend.domain.entities.detection import DetectionResult
from backend.domain.interfaces.http_client import IHttpClient
from backend.infrastructure.iot.mock_controller import MockIoTController
from backend.use_cases.detect_and_notify import DetectAndNotifyUseCase

FRAME = np.zeros((16, 16, 3), dtype=np.uint8)
NOTIFY_URL = "http://central:9000/api/v1/notify"


class FakeDetector:
    """Returns a fixed detection for every frame; no model is ever loaded."""

    def __init__(self, label="TCQ", confidence=0.91, bbox=(4, 4, 12, 12)):
        self.label = label
        self.confidence = confidence
        self.bbox = bbox

    def detect_frame(self, _frame):
        return [DetectionResult(self.label, self.confidence, self.bbox)]


class SpyHttpClient(IHttpClient):
    def __init__(self, success=True):
        self.success = success
        self.calls = []

    def post(self, url, payload, headers=None):
        self.calls.append((url, payload, headers))
        return self.success


def _burn_use_case(label="TCQ"):
    iot = MockIoTController()
    http = SpyHttpClient()
    use_case = DetectAndNotifyUseCase(FakeDetector(label=label), iot, http)
    return use_case, iot, http


def test_burned_toast_turns_off_toaster_turns_on_alarm_and_notifies_once():
    use_case, iot, http = _burn_use_case()
    # Start with the toaster running so the "turn off" action is observable.
    iot.turn_on("rele_tostadora")

    # Burn confirmation uses the tracker's current hysteresis (3 consecutive
    # frames) even though the detector returns the same burnt detection.
    for _ in range(3):
        use_case.execute(FRAME, notification_url=NOTIFY_URL)

    assert iot.get_status("rele_tostadora") is False
    assert iot.get_status("alarma_buzzer") is True
    assert len(http.calls) == 1

    url, payload, _headers = http.calls[0]
    assert url == NOTIFY_URL
    assert payload["event"] == "burned_toast_detected"
    assert payload["details"][0]["label"] == "TCQ"


def test_burn_notification_fires_only_once_on_subsequent_frames():
    # "quemada" is normalized by the tracker just like "TCQ".
    use_case, _iot, http = _burn_use_case(label="quemada")

    for _ in range(10):
        use_case.execute(FRAME, notification_url=NOTIFY_URL)

    assert len(http.calls) == 1
    assert http.calls[0][0] == NOTIFY_URL


def test_burn_actions_run_without_notification_url():
    use_case, iot, http = _burn_use_case()

    for _ in range(4):
        use_case.execute(FRAME)

    assert iot.get_status("rele_tostadora") is False
    assert iot.get_status("alarma_buzzer") is True
    assert http.calls == []
