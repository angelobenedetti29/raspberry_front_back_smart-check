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
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from backend.inference.detector import (
    ErrorCargaModelo,
    MotorInferencia,
    ResultadoDeteccion,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# El modelo es una exportación de Ultralytics, con salida (1, 4 + clases, N).
_ATRIBUTOS_BASE = 4


def _leer_nombres(path: Path) -> tuple[str, ...]:
    """Lee un archivo `.names`: una clase por línea, ignorando líneas vacías."""
    contenido = path.read_text(encoding="utf-8")
    return tuple(linea.strip() for linea in contenido.splitlines() if linea.strip())


class DetectorYoloOpenCV:
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
        modelo = Path(modelo_path)
        nombres = Path(nombres_path)

        if not modelo.is_file():
            raise ErrorCargaModelo(f"No se encontró el modelo: {modelo}")
        if not nombres.is_file():
            # No se cae a nombres por defecto a propósito: rotular todas las
            # clases con nombres ajenos al modelo es peor que no arrancar.
            raise ErrorCargaModelo(
                f"No se encontró el archivo de nombres de clase: {nombres}"
            )

        etiquetas = _leer_nombres(nombres)
        if not etiquetas:
            raise ErrorCargaModelo(f"El archivo de nombres está vacío: {nombres}")

        try:
            net = cv2.dnn.readNet(str(modelo))
        except cv2.error as exc:
            raise ErrorCargaModelo(f"No se pudo cargar el modelo {modelo}: {exc}") from exc

        self.modelo_path = modelo
        self.nombres = etiquetas
        self.image_size = int(image_size)
        self.confidence_threshold = float(confidence_threshold)
        self.nms_threshold = float(nms_threshold)
        self.class_thresholds: Mapping[str, float] = dict(class_thresholds)
        self._net: cv2.dnn.Net | None = net

        logger.info(
            "Detector ONNX cargado: %s (%d clases, entrada %dx%d)",
            modelo.name,
            len(etiquetas),
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
        # (1, 4 + clases, N) -> (1, N, 4 + clases)
        preds = preds.transpose((0, 2, 1))

        factor_x = ancho / self.image_size
        factor_y = alto / self.image_size

        cajas: list[list[int]] = []
        confianzas: list[float] = []
        clases: list[int] = []

        for fila in preds[0]:
            scores = fila[_ATRIBUTOS_BASE:]
            # np.argmax y no cv2.minMaxLoc: en OpenCV 5 el índice del pico cambia
            # de eje según cómo se convierta el array a Mat. El detector de
            # referencia usaba minMaxLoc y terminaba etiquetando toda tostada
            # como quemada.
            clase_id = int(np.argmax(scores))
            confianza = float(scores[clase_id])
            if confianza <= self._umbral(clase_id):
                continue

            # La salida trae (centro_x, centro_y, ancho, alto) en el espacio del
            # modelo, sin normalizar.
            centro_x, centro_y, caja_ancho, caja_alto = (float(v) for v in fila[:_ATRIBUTOS_BASE])
            cajas.append(
                [
                    int((centro_x - 0.5 * caja_ancho) * factor_x),
                    int((centro_y - 0.5 * caja_alto) * factor_y),
                    int(caja_ancho * factor_x),
                    int(caja_alto * factor_y),
                ]
            )
            confianzas.append(confianza)
            clases.append(clase_id)

        if not cajas:
            return []

        indices = cv2.dnn.NMSBoxes(
            cajas, confianzas, self._umbral_minimo(), self.nms_threshold
        )
        return self._armar(indices, cajas, confianzas, clases)

    def liberar(self) -> None:
        """Suelta la red. Idempotente; tras liberar, `detectar_frame` devuelve []."""
        self._net = None

    # --- internos ---

    def _etiqueta(self, clase_id: int) -> str:
        """Nombre de la clase, con reserva explícita si el `.names` no la cubre."""
        if 0 <= clase_id < len(self.nombres):
            return self.nombres[clase_id]
        return f"class_{clase_id}"

    def _umbral(self, clase_id: int) -> float:
        """Umbral de la clase; las claves de config están en minúscula."""
        clave = self._etiqueta(clase_id).lower()
        return self.class_thresholds.get(clave, self.confidence_threshold)

    def _umbral_minimo(self) -> float:
        """Menor umbral por clase.

        El pre-filtro de NMS usa este valor y no el global: filtrar con un umbral
        más alto descartaría cajas válidas antes de que el NMS pueda decidir.
        """
        if not self.class_thresholds:
            return self.confidence_threshold
        return min(self.class_thresholds.values())

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
