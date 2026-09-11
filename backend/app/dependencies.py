from backend.app.config import get_settings
from backend.infrastructure.ai.yolo_detector import YoloDetector
from backend.infrastructure.http.requests_client import RequestsHttpClient
from backend.infrastructure.iot.mock_controller import MockIoTController
from backend.use_cases.control_device import ControlDeviceUseCase
from backend.use_cases.detect_and_notify import DetectAndNotifyUseCase
from backend.use_cases.finalize_lote import FinalizeLoteUseCase
from backend.use_cases.send_lote_request import SendLoteRequestUseCase


class LazyYoloDetector:
    """Lazy Detector proxy to avoid locking the Hailo NPU on startup."""

    def __init__(self):
        self._detector = None

    @property
    def detector(self):
        if self._detector is None:
            try:
                self._detector = YoloDetector()
                print("[Backend] Detector YOLO NPU/ONNX cargado perezosamente con éxito.")
            except Exception as e:
                print(f"[Backend] Error al cargar YOLO en inicialización diferida: {e}")

                class MockDetector:
                    def detect_frame(self, frame):
                        return []

                    def get_class_names(self):
                        return ['Tostada Quemada', 'tostadas ok']

                    @property
                    def model_path(self):
                        return "Mocked (Error)"

                self._detector = MockDetector()
        return self._detector

    def detect_frame(self, frame):
        return self.detector.detect_frame(frame)

    def get_class_names(self):
        return self.detector.get_class_names()

    def release_hailo(self):
        if self._detector is not None and hasattr(self._detector, "release_hailo"):
            self._detector.release_hailo()

    @property
    def model_path(self):
        return getattr(self.detector, "model_path", "Mocked")


_settings = get_settings()

_detector = LazyYoloDetector()
_iot_controller = MockIoTController()
_http_client = RequestsHttpClient()
_detect_use_case = DetectAndNotifyUseCase(_detector, _iot_controller, _http_client)
_control_device_use_case = ControlDeviceUseCase(_iot_controller)
_send_lote_use_case = SendLoteRequestUseCase(
    _http_client,
    _settings.central_lotes_base_url,
    _settings.central_lotes_api_key,
)
_finalize_lote_use_case = FinalizeLoteUseCase(_send_lote_use_case, _settings.central_lotes_base_url)


def get_detector():
    return _detector


def get_iot_controller():
    return _iot_controller


def get_detect_use_case():
    return _detect_use_case


def get_control_device_use_case():
    return _control_device_use_case


def get_finalize_lote_use_case():
    return _finalize_lote_use_case
