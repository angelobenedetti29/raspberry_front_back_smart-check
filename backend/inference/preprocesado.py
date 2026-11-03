"""Preprocesado geométrico compartido por los detectores YOLO.

Los modelos se entrenan y exportan con Ultralytics, que alimenta las imágenes con
*letterbox*: escala uniforme y relleno gris hasta el cuadrado de entrada. La
inferencia hacía *squash* a cuadrado, deformando la geometría respecto de lo que
vio el modelo; acá se replica el letterbox y se devuelven los parámetros para
des-escalar las cajas al frame original.
"""

from __future__ import annotations

import cv2
import numpy as np

# El mismo gris (114) que usa Ultralytics para rellenar.
_COLOR_RELLENO = 114


def letterbox(frame: np.ndarray, size: int) -> tuple[np.ndarray, float, int, int]:
    """Escala `frame` preservando relación de aspecto y lo centra en `size×size`.

    Devuelve el lienzo cuadrado junto con `escala`, `pad_x` y `pad_y`, que son lo
    necesario para traducir una caja del espacio del modelo al del frame:

        x_frame = (x_modelo - pad_x) / escala
    """
    alto, ancho = frame.shape[:2]
    escala = min(size / ancho, size / alto)
    nuevo_ancho = max(1, round(ancho * escala))
    nuevo_alto = max(1, round(alto * escala))

    redimensionado = cv2.resize(frame, (nuevo_ancho, nuevo_alto), interpolation=cv2.INTER_LINEAR)
    lienzo = np.full((size, size, 3), _COLOR_RELLENO, dtype=frame.dtype)

    pad_x = (size - nuevo_ancho) // 2
    pad_y = (size - nuevo_alto) // 2
    lienzo[pad_y : pad_y + nuevo_alto, pad_x : pad_x + nuevo_ancho] = redimensionado

    return lienzo, escala, pad_x, pad_y
