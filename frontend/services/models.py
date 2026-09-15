"""Platform detection and model catalog helpers."""

import os
from dataclasses import dataclass

from backend.infrastructure.ai.yolo_detector import HAILO_AVAILABLE
from frontend.config import (
    DEFAULT_MODEL_INDEX,
    MODEL_CATALOG,
    NPU_MODEL_INDEX,
)

""" Path para detectar si estamos corriendo en una Raspberry Pi.
 Se utiliza para determinar si se debe usar el modelo YOLOv8s HEF 
 (para Raspberry Pi con NPU) o el modelo YOLOv11 ONNX (para PC o simulador). """
RASPBERRY_PI_MODEL_FILE = "/sys/firmware/devicetree/base/model"


def is_raspberry_pi() -> bool:
    """Revisa el path y determina si estamos corriendo en una Raspberry Pi."""
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
    is_raspberry_pi: bool
    is_npu: bool


def detect_platform() -> PlatformInfo:
    """Detecta la plataforma en la que se está ejecutando la aplicación y devuelve un objeto PlatformInfo."""
    pi = is_raspberry_pi()
    npu = pi and HAILO_AVAILABLE

    if npu:
        print("[INFO] Raspberry Pi con NPU detectada. Usando YOLOv8s HEF por defecto.")
    else:
        print("[INFO] PC o simulador detectado. Usando YOLOv11 ONNX por defecto.")

    return PlatformInfo(is_raspberry_pi=pi, is_npu=npu)


def default_model_index(is_npu: bool) -> int:
    """Devuelve el índice del modelo por defecto según la plataforma detectada."""
    return NPU_MODEL_INDEX if is_npu else DEFAULT_MODEL_INDEX


def model_for_index(index: int) -> tuple[str, str]:
    """Devuelve la ruta del modelo y la ruta de los nombres de clases para un índice dado en el catálogo de modelos.
    """
    if not isinstance(index, int) or not 0 <= index < len(MODEL_CATALOG):
        index = DEFAULT_MODEL_INDEX
    entry = MODEL_CATALOG[index]
    return entry[1], entry[2]
