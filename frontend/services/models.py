"""Platform detection and model catalog helpers."""

import os
from dataclasses import dataclass

from backend.infrastructure.ai.yolo_detector import HAILO_AVAILABLE
from frontend.config import (
    DEFAULT_MODEL_INDEX,
    MODEL_CATALOG,
    NPU_MODEL_INDEX,
)

RASPBERRY_PI_MODEL_FILE = "/sys/firmware/devicetree/base/model"


def is_raspberry_pi() -> bool:
    """Return True when running on a Raspberry Pi single-board computer."""
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
    """Detect the host platform and log the default model that will be used."""
    pi = is_raspberry_pi()
    npu = pi and HAILO_AVAILABLE

    if npu:
        print("[INFO] Raspberry Pi con NPU detectada. Usando YOLOv8s HEF por defecto.")
    else:
        print("[INFO] PC o simulador detectado. Usando YOLOv11 ONNX por defecto.")

    return PlatformInfo(is_raspberry_pi=pi, is_npu=npu)


def default_model_index(is_npu: bool) -> int:
    """Index of the model selected by default for the given platform."""
    return NPU_MODEL_INDEX if is_npu else DEFAULT_MODEL_INDEX


def model_for_index(index: int) -> tuple[str, str]:
    """Return (model_path, names_path) for a catalog index.

    Negative or out-of-range indices fall back to ``DEFAULT_MODEL_INDEX``
    explicitly (Python's negative indexing is intentionally not used).
    """
    if not isinstance(index, int) or not 0 <= index < len(MODEL_CATALOG):
        index = DEFAULT_MODEL_INDEX
    entry = MODEL_CATALOG[index]
    return entry[1], entry[2]
