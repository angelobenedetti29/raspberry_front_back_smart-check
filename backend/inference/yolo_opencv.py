"""Detector YOLO sobre un modelo ONNX, ejecutado en CPU con `cv2.dnn`.

Es el motor de desarrollo: permite validar todo el pipeline de inferencia sin
depender de la NPU, que sólo existe en la Raspberry.

Portado de la rama CPU del detector de referencia. Preserva su preprocesado
(`blobFromImage` con `1/255`, `swapRB=True`) y su decodificación de cajas, y
corrige una cosa que arrastraba: los umbrales por clase salen de la
configuración en lugar de un mapa hardcodeado.

El NMS es class-agnostic a propósito, como en la referencia: para tostadas un
objeto es una sola clase, así que suprimir entre clases evita que la misma
tostada salga dos veces con etiquetas contradictorias (p. ej. TCQ y TCOK).

El postprocesado resuelve el tensor de salida completo con numpy y no fila por
fila: recorrer en Python las ~8400 predicciones de un YOLO a 640 era el costo
dominante del paso y retenía el GIL, compitiendo con el lazo de producción.

El preprocesado usa letterbox (ver `preprocesado.py`) en vez de deformar a
cuadrado, para que el modelo vea la misma geometría que en el entrenamiento.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

import cv2
import numpy as np

from backend.inference.base import DetectorYoloBase
from backend.inference.detector import (
    ErrorCargaModelo,
    MotorInferencia,
    ResultadoDeteccion,
)
from backend.inference.postprocesado import decodificar_salida
from backend.inference.preprocesado import letterbox

logger = logging.getLogger(__name__)


class DetectorYoloOpenCV(DetectorYoloBase):
    """Detector YOLO sobre ONNX en CPU.

    No es seguro para uso concurrente; el dueño del detector (el hilo de
    inferencia del pipeline) serializa las llamadas y también la liberación.
    """

    motor = MotorInferencia.OPENCV_DNN

    def __init__(
        self,
        modelo_path: str | Path,
        nombres_path: str | Path,
        *,
        image_size: int,
        confidence_threshold: float,
        nms_threshold: float,
        class_thresholds: Mapping[str, float],
    ) -> None:
        super().__init__(
            modelo_path,
            nombres_path,
            image_size=image_size,
            confidence_threshold=confidence_threshold,
            class_thresholds=class_thresholds,
        )

        try:
            net = cv2.dnn.readNet(str(self.modelo_path))
        except cv2.error as exc:
            raise ErrorCargaModelo(
                f"No se pudo cargar el modelo {self.modelo_path}: {exc}"
            ) from exc

        self.nms_threshold = float(nms_threshold)
        # Tabla de umbrales por índice de clase, para resolver el filtro de todo
        # el tensor de una vez. Sin clases declaradas no hay tabla y se usa el
        # global para todas.
        self._tabla_umbrales = (
            np.asarray(self._umbrales_por_clase, dtype=np.float32)
            if self._umbrales_por_clase
            else None
        )
        self._net: cv2.dnn.Net | None = net

        logger.info(
            "Detector ONNX cargado: %s (%d clases, entrada %dx%d)",
            self.modelo_path.name,
            len(self.nombres),
            self.image_size,
            self.image_size,
        )

    def detectar_frame(self, frame: np.ndarray) -> list[ResultadoDeteccion]:
        """Detecta objetos en un frame BGR. Devuelve [] si el detector fue liberado."""
        net = self._net
        if net is None or frame is None or frame.size == 0:
            return []

        lienzo, escala, pad_x, pad_y = letterbox(frame, self.image_size)
        blob = cv2.dnn.blobFromImage(
            lienzo,
            1 / 255.0,
            (self.image_size, self.image_size),
            swapRB=True,
            crop=False,
        )
        net.setInput(blob)
        return decodificar_salida(
            net.forward(),
            escala=escala,
            pad_x=pad_x,
            pad_y=pad_y,
            tabla_umbrales=self._tabla_umbrales,
            umbral_global=self.confidence_threshold,
            umbral_minimo=self._umbral_minimo(),
            nms_threshold=self.nms_threshold,
            etiqueta=self._etiqueta,
        )

    def liberar(self) -> None:
        """Suelta la red. Idempotente; tras liberar, `detectar_frame` devuelve []."""
        self._net = None
