from pathlib import Path

from device_enrollment.identity import IdentityStore
from device_enrollment.transport import SignedTransport

from backend.app.config import get_settings
from backend.app.telemetry import TelemetryLoop
from backend.domain.interfaces.image_detector import IImageDetector
from backend.infrastructure.ai.fallback_detector import FallbackDetector
from backend.infrastructure.ai.yolo_detector import YoloDetector
from backend.infrastructure.system.system_metrics import LinuxSystemMetricsProvider
from backend.use_cases.detect_and_notify import DetectAndNotifyUseCase
from backend.use_cases.finalize_lote import FinalizeLoteUseCase
from backend.use_cases.send_lote_inicio import SendLoteInicioUseCase
from backend.use_cases.send_lote_request import SendLoteRequestUseCase
from backend.use_cases.send_ping_request import SendPingRequestUseCase


class LazyYoloDetector(IImageDetector):
    """Proxy perezoso para no reservar la NPU Hailo durante el arranque.

    La carga real del modelo (y con ella el bloqueo de la NPU) se pospone hasta
    la primera detección. Si esa carga falla, se usa un ``FallbackDetector`` para
    no tumbar el servicio.
    """

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
                self._detector = FallbackDetector(e)
        return self._detector

    def detect(self, image_path: str):
        return self.detector.detect(image_path)

    def detect_frame(self, frame):
        return self.detector.detect_frame(frame)

    def get_class_names(self):
        return self.detector.get_class_names()

    def release_hailo(self):
        # No se fuerza la carga perezosa solo para liberar la NPU.
        if self._detector is not None:
            self._detector.release_hailo()

    @property
    def model_path(self):
        return self.detector.model_path


_settings = get_settings()

_detector = LazyYoloDetector()

# Load the persisted enrolled identity (if any) once at startup. Telemetry and
# all signed senders are only enabled for an enrolled identity; the legacy
# shared API key is gone.
_identity_store = IdentityStore(Path(_settings.device_identity_dir))
_enrolled_identity = _identity_store.try_load_enrolled()


def _identity_provider():
    return _enrolled_identity


_transport = SignedTransport(
    _identity_provider,
    _settings.device_api_base_url,
    _settings.device_auth_audience,
)

_detect_use_case = DetectAndNotifyUseCase(_detector)
_send_lote_use_case = SendLoteRequestUseCase(_transport, _settings.device_api_base_url)
_finalize_lote_use_case = FinalizeLoteUseCase(_send_lote_use_case)

_system_metrics_provider = LinuxSystemMetricsProvider()
_send_ping_use_case = SendPingRequestUseCase(_transport, _settings.device_api_base_url)
_send_lote_inicio_use_case = SendLoteInicioUseCase(_transport, _settings.device_api_base_url)
_telemetry_loop = TelemetryLoop(
    _send_ping_use_case,
    _system_metrics_provider,
    _enrolled_identity.dispositivo_id if _enrolled_identity else "",
    _settings.ping_interval_seconds,
    enabled=bool(
        _enrolled_identity
        and _enrolled_identity.dispositivo_id
        and _settings.device_api_base_url
        and _settings.device_auth_audience
    ),
)


def get_detector():
    return _detector


def get_detect_use_case():
    return _detect_use_case


def get_finalize_lote_use_case():
    return _finalize_lote_use_case


def get_send_lote_inicio_use_case():
    return _send_lote_inicio_use_case


def get_telemetry_loop():
    return _telemetry_loop
