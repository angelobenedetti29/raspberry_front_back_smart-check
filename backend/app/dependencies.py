import logging
import threading
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

logger = logging.getLogger(__name__)


class LazyYoloDetector(IImageDetector):
    """Proxy perezoso para no reservar la NPU Hailo durante el arranque.

    La carga real del modelo (y con ella el bloqueo de la NPU) se pospone hasta
    la primera detección. Si esa carga falla, se usa un ``FallbackDetector`` para
    no tumbar el servicio.
    """

    def __init__(self):
        """Inicializa el proxy sin construir el detector real."""
        self._detector = None
        # Evita que dos requests concurrentes (el endpoint corre en el
        # threadpool) construyan el detector dos veces y reserven la NPU de más.
        self._lock = threading.Lock()

    @property
    def detector(self):
        """Devuelve el detector real, construyéndolo en la primera llamada.

        Usa double-checked locking: el lock solo se toma si aún no está resuelto.
        """
        if self._detector is None:
            with self._lock:
                if self._detector is None:
                    try:
                        self._detector = YoloDetector()
                        logger.info("Detector YOLO NPU/ONNX cargado perezosamente con éxito.")
                    except Exception as e:
                        logger.error("Error al cargar YOLO en inicialización diferida: %s", e)
                        self._detector = FallbackDetector(e)
        return self._detector

    def detect(self, image_path: str):
        """Delega en el detector subyacente la inferencia sobre una imagen."""
        return self.detector.detect(image_path)

    def detect_frame(self, frame):
        """Delega en el detector subyacente la inferencia sobre un frame."""
        return self.detector.detect_frame(frame)

    def get_class_names(self):
        """Devuelve los nombres de clase del modelo subyacente."""
        return self.detector.get_class_names()

    def release_hailo(self):
        """Libera la NPU Hailo solo si el detector ya se había cargado."""
        # No se fuerza la carga perezosa solo para liberar la NPU.
        if self._detector is not None:
            self._detector.release_hailo()

    @property
    def loaded(self) -> bool:
        """True si la carga perezosa ya se resolvió (modelo real o fallback).

        No dispara la carga del modelo.
        """
        return self._detector is not None

    @property
    def model_path(self) -> str | None:
        """Ruta del modelo cargado, o None si todavía no se cargó.

        No dispara la carga perezosa (a diferencia de la version anterior).
        """
        if self._detector is None:
            return None
        return getattr(self._detector, "model_path", None)

    @property
    def error(self) -> str:
        """Motivo de degradación si la carga derivó en FallbackDetector."""
        if self._detector is None:
            return ""
        return str(getattr(self._detector, "error", "") or "")


# Composición de dependencias del proceso: se resuelven una sola vez al
# importar el módulo y los routers las consumen vía ``Depends``.
_settings = get_settings()

_detector = LazyYoloDetector()

# Carga la identidad enrolada persistida (si existe) una sola vez al arranque.
# La telemetría y todos los emisores firmados solo se habilitan para una
# identidad enrolada; la antigua API key compartida ya no existe.
_identity_store = IdentityStore(Path(_settings.device_identity_dir))
_enrolled_identity = _identity_store.try_load_enrolled()


def _identity_provider():
    """Devuelve la identidad enrolada leída al arranque (o ``None``)."""
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
    """Dependencia FastAPI: proxy perezoso del detector YOLO."""
    return _detector


def get_detect_use_case():
    """Dependencia FastAPI: caso de uso de detección y notificación."""
    return _detect_use_case


def get_finalize_lote_use_case():
    """Dependencia FastAPI: caso de uso de finalización de lote."""
    return _finalize_lote_use_case


def get_send_lote_inicio_use_case():
    """Dependencia FastAPI: caso de uso de inicio de lote."""
    return _send_lote_inicio_use_case


def get_telemetry_loop():
    """Dependencia FastAPI: hilo de telemetría periódica."""
    return _telemetry_loop
