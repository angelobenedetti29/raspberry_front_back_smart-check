"""Detección de plataforma y helpers del catálogo de modelos."""

import os
from dataclasses import dataclass

from backend.infrastructure.ai.yolo_detector import HAILO_AVAILABLE
from frontend.config import (
    DEFAULT_MODEL_INDEX,
    MODEL_CATALOG,
    NPU_MODEL_INDEX,
)

# Ruta donde el kernel expone el modelo de la placa. Se lee para distinguir una
# Raspberry Pi (donde puede usarse la NPU Hailo) de un PC o un simulador.
RASPBERRY_PI_MODEL_FILE = "/sys/firmware/devicetree/base/model"


def is_raspberry_pi() -> bool:
    """Devuelve True si la aplicación corre sobre una Raspberry Pi."""
    is_pi = False
    try:
        if os.path.exists(RASPBERRY_PI_MODEL_FILE):
            with open(RASPBERRY_PI_MODEL_FILE, "r") as f:
                is_pi = "raspberry pi" in f.read().lower()
    except Exception:
        pass
    return is_pi


@dataclass(frozen=True)
class PlatformInfo:
    """Plataforma detectada: si es Raspberry Pi y si hay NPU Hailo disponible."""

    is_raspberry_pi: bool
    is_npu: bool


def detect_platform() -> PlatformInfo:
    """Detecta la plataforma y registra el modelo por defecto resultante."""
    pi = is_raspberry_pi()
    npu = pi and HAILO_AVAILABLE

    if npu:
        print("[INFO] Raspberry Pi con NPU detectada. Usando YOLOv8s HEF por defecto.")
    else:
        print("[INFO] PC o simulador detectado. Usando YOLOv11 ONNX por defecto.")

    return PlatformInfo(is_raspberry_pi=pi, is_npu=npu)


def default_model_index(is_npu: bool) -> int:
    """Índice del catálogo que se selecciona al arrancar según la plataforma."""
    return NPU_MODEL_INDEX if is_npu else DEFAULT_MODEL_INDEX


def model_for_index(index: int) -> tuple[str, str]:
    """Devuelve ``(ruta del modelo, ruta de las etiquetas)`` para un índice.

    Un índice inválido (no entero, o fuera de rango) cae explícitamente en
    ``DEFAULT_MODEL_INDEX``; no se usa la indexación negativa de Python.
    """
    if not isinstance(index, int) or not 0 <= index < len(MODEL_CATALOG):
        index = DEFAULT_MODEL_INDEX
    entry = MODEL_CATALOG[index]
    return entry.model_path, entry.names_path
