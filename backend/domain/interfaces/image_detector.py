from abc import ABC, abstractmethod
from typing import List
from backend.domain.entities.detection import DetectionResult

class IImageDetector(ABC):
    """Contrato de los detectores de imágenes del backend.

    Define la inferencia sobre archivos o frames y la liberación explícita de la
    NPU. Cada implementación decide el modelo y el backend de cómputo concretos.
    """

    # Metadata de estado del detector. No forma parte de la inferencia y no
    # debe forzar la carga del modelo; los detectores perezosos la sobreescriben.
    loaded: bool = True
    error: str = ""

    @abstractmethod
    def detect(self, image_path: str) -> List[DetectionResult]:
        """Detecta objetos en un archivo de imagen a partir de su ruta."""
        pass

    @abstractmethod
    def detect_frame(self, frame) -> List[DetectionResult]:
        """Detecta objetos en un frame crudo de OpenCV (arreglo de numpy)."""
        pass

    @abstractmethod
    def get_class_names(self) -> List[str]:
        """Devuelve los nombres de todas las clases que el modelo puede detectar."""
        pass

    @abstractmethod
    def release_hailo(self) -> None:
        """Libera la NPU de Hailo si este detector la está reteniendo.

        Forma parte del contrato obligatorio porque quien llama libera el
        detector incondicionalmente. Los detectores que nunca toman la NPU lo
        implementan como no-op.
        """
        pass
