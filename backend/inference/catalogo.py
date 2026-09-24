"""Resolución del catálogo de modelos a motores concretos.

Traduce una entrada del catálogo en un detector. La decisión de motor es
implícita y visible: un `.hef` siempre es NPU, un `.onnx` es CPU salvo que haya
NPU disponible y exista el `.hef` hermano, caso en el que se redirige.

La resolución no carga nada: sólo mira extensiones, disponibilidad de librerías
y existencia de archivos. Así la interfaz puede listar el catálogo entero
mostrando qué se puede usar y qué no, sin arrastrar `cv2` ni `hailo_platform`.
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from backend.inference.detector import (
    Detector,
    ErrorCargaModelo,
    MotorInferencia,
)

if TYPE_CHECKING:
    from backend.config import InferenceConfig, ModelEntry

logger = logging.getLogger(__name__)

_MOTORES_POR_EXTENSION: dict[str, MotorInferencia] = {
    ".onnx": MotorInferencia.OPENCV_DNN,
    ".hef": MotorInferencia.HAILO,
}


@dataclass(frozen=True, slots=True)
class ResolucionModelo:
    """Qué se cargaría realmente para una entrada del catálogo."""

    model_id: str
    label: str
    # Motor efectivo. None cuando ninguna extensión conocida lo cubre.
    motor: MotorInferencia | None
    # Archivo efectivo: puede ser el `.hef` hermano y no el `.onnx` pedido.
    modelo_path: Path
    disponible: bool
    # Motivo por el que no está disponible. Vacío cuando sí lo está.
    motivo: str


def hailo_disponible() -> bool:
    """True si la NPU se puede usar en esta máquina.

    Se consulta con `find_spec` en vez de importar: preguntar por disponibilidad
    no debe traer la librería al proceso ni fallar cuando no está.
    """
    return (
        importlib.util.find_spec("hailo_platform") is not None
        and importlib.util.find_spec("backend.inference.yolo_hailo") is not None
    )


def opencv_disponible() -> bool:
    """True si `cv2.dnn` se puede usar en esta máquina."""
    return importlib.util.find_spec("cv2") is not None


def _motor_disponible(motor: MotorInferencia) -> bool:
    if motor is MotorInferencia.HAILO:
        return hailo_disponible()
    return opencv_disponible()


def resolver_modelo(entrada: ModelEntry) -> ResolucionModelo:
    """Resuelve motor y archivo efectivos, sin cargar el modelo.

    Aplica el auto-redirect `.onnx` -> `.hef`: si la entrada apunta a un ONNX, la
    NPU está disponible y existe el `.hef` hermano, se resuelve a Hailo. El motor
    y la ruta devueltos son los **efectivos**, para que la interfaz nunca muestre
    un motor distinto del que realmente corre.

    No levanta excepciones: un modelo inutilizable se reporta en `disponible` y
    `motivo`.
    """
    modelo_path = entrada.model_path
    motor = _MOTORES_POR_EXTENSION.get(modelo_path.suffix.lower())

    if motor is MotorInferencia.OPENCV_DNN and hailo_disponible():
        hermano = modelo_path.with_suffix(".hef")
        if hermano.is_file():
            logger.info(
                "Auto-redirect NPU: %s -> %s", modelo_path.name, hermano.name
            )
            modelo_path = hermano
            motor = MotorInferencia.HAILO

    if motor is None:
        extension = entrada.model_path.suffix or "(sin extensión)"
        motivo = f"Extensión de modelo no soportada: {extension}"
    elif not _motor_disponible(motor):
        motivo = f"El motor {motor.etiqueta} no está disponible en esta máquina"
    elif not modelo_path.is_file():
        motivo = f"No se encontró el archivo del modelo: {modelo_path}"
    elif not entrada.names_path.is_file():
        motivo = f"No se encontró el archivo de nombres de clase: {entrada.names_path}"
    else:
        motivo = ""

    return ResolucionModelo(
        model_id=entrada.model_id,
        label=entrada.label,
        motor=motor,
        modelo_path=modelo_path,
        disponible=not motivo,
        motivo=motivo,
    )


def catalogo_resuelto(catalogo: Sequence[ModelEntry]) -> tuple[ResolucionModelo, ...]:
    """Resuelve el catálogo completo, para que la interfaz lo liste con su motor."""
    return tuple(resolver_modelo(entrada) for entrada in catalogo)


def construir_detector(entrada: ModelEntry, inferencia: InferenceConfig) -> Detector:
    """Construye el detector del modelo indicado.

    Levanta `ErrorCargaModelo` si el motor no está disponible, falta algún
    archivo o el modelo no se puede cargar. No hay degradación silenciosa: el
    pipeline captura el error y lo expone en el estado.
    """
    resolucion = resolver_modelo(entrada)
    if not resolucion.disponible or resolucion.motor is None:
        raise ErrorCargaModelo(resolucion.motivo)

    if resolucion.motor is MotorInferencia.HAILO:
        # Import perezoso: `hailo_platform` sólo existe en la Raspberry.
        from backend.inference.yolo_hailo import DetectorYoloHailo

        return DetectorYoloHailo(
            resolucion.modelo_path,
            entrada.names_path,
            image_size=inferencia.image_size,
            confidence_threshold=inferencia.confidence_threshold,
            class_thresholds=entrada.class_thresholds,
        )

    from backend.inference.yolo_opencv import DetectorYoloOpenCV

    return DetectorYoloOpenCV(
        resolucion.modelo_path,
        entrada.names_path,
        image_size=inferencia.image_size,
        confidence_threshold=inferencia.confidence_threshold,
        nms_threshold=inferencia.nms_threshold,
        class_thresholds=entrada.class_thresholds,
    )
