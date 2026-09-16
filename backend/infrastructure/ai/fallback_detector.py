"""Detector de reemplazo para cuando el modelo real no se puede cargar."""

from typing import List

from backend.domain.entities.detection import DetectionResult
from backend.domain.interfaces.image_detector import IImageDetector


class FallbackDetector(IImageDetector):
    """Detector que no detecta nada, para que la aplicación arranque sin modelo.

    Implementa la misma interfaz que ``YoloDetector`` para poder sustituirlo sin
    condiciones en los casos de uso, y guarda el motivo del fallo para que la UI
    pueda mostrarlo. Es el único detector simulado del repositorio: lo comparten
    el backend (carga perezosa fallida) y el frontend.
    """

    def __init__(self, error=None):
        """Guarda el motivo del fallo y expone los atributos de ``YoloDetector``.

        ``error`` se convierte a texto y queda vacío si es ``None``. Los atributos
        ``use_hailo`` y ``model_path`` permiten que el resto del código trate a este
        detector igual que al real.
        """
        self.error = "" if error is None else str(error)
        self.use_hailo = False
        self.model_path = "Mocked (Error)"

    def detect(self, image_path: str) -> List[DetectionResult]:
        """Devuelve siempre una lista vacía, sin leer la imagen."""
        return []

    def detect_frame(self, frame) -> List[DetectionResult]:
        """Devuelve siempre una lista vacía, ignorando el frame."""
        return []

    def get_class_names(self) -> List[str]:
        """Devuelve las dos clases por defecto que muestra la UI sin modelo."""
        return ["Tostada Quemada", "tostadas ok"]

    def release_hailo(self) -> None:
        """No-op: el detector simulado no ocupa la NPU."""
        return None
