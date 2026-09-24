"""Inferencia de objetos, desacoplada del pipeline de streaming.

Expone el puerto `Detector` y los tipos que comparten sus implementaciones. Los
adaptadores concretos se importan por módulo explícito (no desde acá) para no
arrastrar `cv2` ni `hailo_platform` cuando no hacen falta.
"""

from backend.inference.detector import (
    Detector,
    ErrorCargaModelo,
    MotorInferencia,
    ResultadoDeteccion,
)

__all__ = [
    "Detector",
    "ErrorCargaModelo",
    "MotorInferencia",
    "ResultadoDeteccion",
]
