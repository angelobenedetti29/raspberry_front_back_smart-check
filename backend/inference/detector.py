"""Puerto de detección de objetos y tipos asociados.

Este módulo define el contrato que consumen el pipeline de streaming y la
persistencia. No importa ``cv2`` ni ``numpy`` en runtime: las implementaciones
concretas viven en módulos aparte, de modo que importar el puerto no arrastre
dependencias pesadas ni la NPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np


class MotorInferencia(str, Enum):
    """Motor que ejecuta la inferencia.

    El valor es el identificador estable (lo usa el estado y la config); la
    etiqueta es el rótulo corto que la UI muestra como badge de motor.
    """

    OPENCV_DNN = "opencv-dnn"
    HAILO = "hailo"

    @property
    def etiqueta(self) -> str:
        """Rótulo corto del motor para la interfaz."""
        return _ETIQUETAS_MOTOR[self]


_ETIQUETAS_MOTOR: dict[MotorInferencia, str] = {
    MotorInferencia.OPENCV_DNN: "CPU",
    MotorInferencia.HAILO: "NPU",
}


class ErrorCargaModelo(Exception):
    """Un detector no pudo cargarse.

    Cubre archivo ausente, motor no disponible en la máquina o modelo inválido.
    Es un error de dominio a propósito: el pipeline lo captura y lo expone en el
    estado visible en vez de degradar en silencio a "no detecta nada".
    """


@dataclass(frozen=True, slots=True)
class ResultadoDeteccion:
    """Una detección individual.

    ``bbox`` está en píxeles del frame de entrada, con el origen en la esquina
    superior izquierda, en el orden ``(left, top, width, height)``.
    """

    label: str
    confianza: float
    bbox: tuple[int, int, int, int]

    @property
    def etiqueta(self) -> str:
        """Texto listo para dibujar sobre el frame."""
        return f"{self.label} {self.confianza:.2f}"


@runtime_checkable
class Detector(Protocol):
    """Contrato de un detector de objetos sobre frames BGR en memoria.

    Una misma instancia no es segura para uso concurrente: el dueño del detector
    serializa las llamadas (ver el hilo de inferencia del pipeline).
    """

    nombres: tuple[str, ...]
    """Etiquetas de clase, en el orden en que el modelo devuelve los índices."""

    motor: MotorInferencia
    """Motor que efectivamente ejecuta la inferencia."""

    modelo_path: Path
    """Archivo de modelo realmente cargado.

    Puede no coincidir con el archivo pedido: si hay una NPU disponible y existe
    el ``.hef`` hermano de un ``.onnx``, se carga el ``.hef``.
    """

    def detectar_frame(self, frame: np.ndarray) -> list[ResultadoDeteccion]:
        """Detecta objetos en un frame BGR ``(alto, ancho, 3)`` uint8.

        Devuelve las detecciones que superan su umbral, con la caja en píxeles
        del frame de entrada. Un frame inválido o un detector ya liberado
        devuelven una lista vacía.
        """
        ...

    def liberar(self) -> None:
        """Libera los recursos del detector.

        Es idempotente y tolera una construcción parcial: se puede llamar aunque
        ``__init__`` haya fallado a mitad de camino, y aunque ya se haya llamado
        antes.
        """
        ...
