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

logger = logging.getLogger(__name__)

# El modelo es una exportación de Ultralytics, con salida (1, 4 + clases, N).
_ATRIBUTOS_BASE = 4


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

        alto, ancho = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            frame,
            1 / 255.0,
            (self.image_size, self.image_size),
            swapRB=True,
            crop=False,
        )
        net.setInput(blob)
        preds = net.forward()
        # (1, 4 + clases, N) -> (N, 4 + clases)
        preds = preds[0].transpose()

        xywh = preds[:, :_ATRIBUTOS_BASE]
        scores = preds[:, _ATRIBUTOS_BASE:]
        # np.argmax y no cv2.minMaxLoc: en OpenCV 5 el índice del pico cambia de
        # eje según cómo se convierta el array a Mat. El detector de referencia
        # usaba minMaxLoc y terminaba etiquetando toda tostada como quemada.
        clases = scores.argmax(axis=1)
        confianzas = scores[np.arange(scores.shape[0]), clases]

        seleccion = confianzas > self._umbrales(clases)
        if not seleccion.any():
            return []

        xywh = xywh[seleccion]
        confianzas = confianzas[seleccion]
        clases = clases[seleccion]

        factor_x = ancho / self.image_size
        factor_y = alto / self.image_size

        # La salida trae (centro_x, centro_y, ancho, alto) en el espacio del
        # modelo, sin normalizar.
        cajas = np.column_stack(
            (
                (xywh[:, 0] - 0.5 * xywh[:, 2]) * factor_x,
                (xywh[:, 1] - 0.5 * xywh[:, 3]) * factor_y,
                xywh[:, 2] * factor_x,
                xywh[:, 3] * factor_y,
            )
        ).astype(int).tolist()

        indices = cv2.dnn.NMSBoxes(
            cajas, confianzas.tolist(), self._umbral_minimo(), self.nms_threshold
        )
        return self._armar(indices, cajas, confianzas.tolist(), clases.tolist())

    def liberar(self) -> None:
        """Suelta la red. Idempotente; tras liberar, `detectar_frame` devuelve []."""
        self._net = None

    # --- internos ---

    def _umbrales(self, clases: np.ndarray) -> np.ndarray:
        """Umbral efectivo de cada fila según su clase (vectorizado)."""
        umbrales = np.full(clases.shape, self.confidence_threshold, dtype=np.float32)
        tabla = self._tabla_umbrales
        if tabla is not None:
            dentro = clases < tabla.size
            umbrales[dentro] = tabla[clases[dentro]]
        return umbrales

    def _armar(
        self,
        indices: object,
        cajas: list[list[int]],
        confianzas: list[float],
        clases: list[int],
    ) -> list[ResultadoDeteccion]:
        """Traduce los índices que devuelve el NMS a resultados."""
        resultados: list[ResultadoDeteccion] = []
        for indice in indices:  # type: ignore[union-attr]
            # OpenCV 4.x devolvía índices anidados ([[i], [j]]) y 5.x planos.
            valor = indice[0] if isinstance(indice, (list, np.ndarray)) else indice
            i = int(valor)
            left, top, caja_ancho, caja_alto = cajas[i]
            resultados.append(
                ResultadoDeteccion(
                    label=self._etiqueta(clases[i]),
                    confianza=confianzas[i],
                    bbox=(left, top, caja_ancho, caja_alto),
                )
            )
        return resultados
