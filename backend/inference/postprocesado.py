"""Decodificación del tensor de salida YOLO, compartida por los detectores.

Los dos adaptadores (`yolo_opencv` y `yolo_hailo`) producen el mismo tensor
`(1, 4 + clases, N)` y lo resuelven igual: umbral por fila (global o por clase),
des-proyección del letterbox y NMS en CPU. Tenerlo en un solo lugar evita que un
motor y otro se desincronicen en el borde del umbral o en la geometría de la caja.
"""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np

from backend.inference.detector import ResultadoDeteccion

# El modelo es una exportación de Ultralytics, con salida (1, 4 + clases, N).
_ATRIBUTOS_BASE = 4


def decodificar_salida(
    preds: np.ndarray,
    *,
    escala: float,
    pad_x: int,
    pad_y: int,
    tabla_umbrales: np.ndarray | None,
    umbral_global: float,
    umbral_minimo: float,
    nms_threshold: float,
    etiqueta: Callable[[int], str],
) -> list[ResultadoDeteccion]:
    """Traduce el tensor crudo del modelo a detecciones en píxeles del frame.

    `tabla_umbrales` es el umbral efectivo por índice de clase; si es `None` se
    usa `umbral_global` para todas. `umbral_minimo` es el menor umbral efectivo
    del modelo y se usa como pre-filtro del NMS: uno más alto descartaría cajas
    válidas antes de que el NMS pueda decidir. `etiqueta` resuelve el nombre de
    la clase (incluida la reserva cuando el `.names` no la cubre).
    """
    # (1, 4 + clases, N) -> (N, 4 + clases)
    preds = np.asarray(preds)[0].transpose()
    xywh = preds[:, :_ATRIBUTOS_BASE]
    scores = preds[:, _ATRIBUTOS_BASE:]
    # np.argmax y no cv2.minMaxLoc: en OpenCV 5 el índice del pico cambia de eje
    # según cómo se convierta el array a Mat. El detector de referencia usaba
    # minMaxLoc y terminaba etiquetando toda tostada como quemada.
    clases = scores.argmax(axis=1)
    confianzas = scores[np.arange(scores.shape[0]), clases]

    # Umbral efectivo de cada fila según su clase (vectorizado). Una clase que
    # la tabla no cubre conserva el global.
    umbrales = np.full(clases.shape, umbral_global, dtype=np.float32)
    if tabla_umbrales is not None:
        dentro = clases < tabla_umbrales.size
        umbrales[dentro] = tabla_umbrales[clases[dentro]]

    seleccion = confianzas > umbrales
    if not seleccion.any():
        return []

    xywh = xywh[seleccion]
    confianzas = confianzas[seleccion]
    clases = clases[seleccion]

    # La salida trae (centro_x, centro_y, ancho, alto) en el espacio del modelo
    # letterboxeado: se quita el relleno y se divide por la escala uniforme para
    # volver a píxeles del frame original.
    cajas = (
        np.column_stack(
            (
                (xywh[:, 0] - 0.5 * xywh[:, 2] - pad_x) / escala,
                (xywh[:, 1] - 0.5 * xywh[:, 3] - pad_y) / escala,
                xywh[:, 2] / escala,
                xywh[:, 3] / escala,
            )
        )
        .astype(int)
        .tolist()
    )

    indices = cv2.dnn.NMSBoxes(cajas, confianzas.tolist(), umbral_minimo, nms_threshold)

    resultados: list[ResultadoDeteccion] = []
    for indice in indices:  # type: ignore[union-attr]
        # OpenCV 4.x devolvía índices anidados ([[i], [j]]) y 5.x planos.
        valor = indice[0] if isinstance(indice, (list, np.ndarray)) else indice
        i = int(valor)
        left, top, caja_ancho, caja_alto = cajas[i]
        resultados.append(
            ResultadoDeteccion(
                label=etiqueta(clases[i]),
                confianza=float(confianzas[i]),
                bbox=(left, top, caja_ancho, caja_alto),
            )
        )
    return resultados
