"""Dibujo de detecciones sobre un frame BGR.

El pipeline decide *cuándo* anotar; este módulo decide *cómo* se ven las cajas.
Es el único lugar del backend que conoce el aspecto visual de una detección.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import TYPE_CHECKING

import cv2

if TYPE_CHECKING:
    import numpy as np

    from backend.inference import ResultadoDeteccion

_FUENTE = cv2.FONT_HERSHEY_SIMPLEX

# Paleta en BGR, elegida para distinguirse sobre video de planta. Es el fallback
# para las clases que no tienen color semántico asignado.
_PALETA: tuple[tuple[int, int, int], ...] = (
    (0, 200, 255),
    (80, 220, 80),
    (60, 90, 255),
    (230, 160, 40),
    (200, 80, 200),
    (200, 200, 60),
)

# BGR de los tokens del tema: verde = ok, rojo = defecto.
_VERDE_OK = (122, 224, 63)
_ROJO_DEFECTO = (95, 90, 255)

# Clases con color semántico, indexadas por etiqueta ya normalizada.
_COLORES_SEMANTICOS: dict[str, tuple[int, int, int]] = {
    "tcq": _ROJO_DEFECTO,
    "tostada quemada": _ROJO_DEFECTO,
    "tcok": _VERDE_OK,
    "tostadas ok": _VERDE_OK,
}


def _color(label: str) -> tuple[int, int, int]:
    """Color por etiqueta, con precedencia semántica y hash como fallback.

    Primero se normaliza la etiqueta (`strip().lower()`) y se busca en el mapa
    semántico: las clases de defecto/ok usan los tokens del tema (rojo/verde)
    para que el operador lea el estado de un vistazo. Si no hay color semántico,
    se deriva del hash de la clave normalizada y no de `hash()` a propósito:
    `hash()` de un str está aleatorizado por proceso, así que el mismo producto
    cambiaría de color entre arranques. Tampoco se usa el índice de clase, que
    depende del archivo `.names` y haría bailar los colores si se reordena.
    """
    clave = label.strip().lower()
    semantico = _COLORES_SEMANTICOS.get(clave)
    if semantico is not None:
        return semantico
    digest = hashlib.md5(clave.encode("utf-8")).digest()
    return _PALETA[digest[0] % len(_PALETA)]


def _metricas(alto: int) -> tuple[int, float, int]:
    """Grosor, escala de fuente y margen proporcionales al alto del frame.

    Calibrado para que en 720p dé los mismos valores que usaba el script de
    referencia (2 px, 0.6), pero sin quedar atado a esa resolución.
    """
    alto = max(1, alto)
    grosor = max(1, round(alto / 360))
    escala = max(0.4, alto / 1200)
    margen = max(2, grosor * 3)
    return grosor, escala, margen


def _recortar(
    bbox: tuple[int, int, int, int], ancho: int, alto: int
) -> tuple[int, int, int, int]:
    """Recorta la caja al rectángulo del frame."""
    left, top, caja_ancho, caja_alto = bbox
    left = max(0, min(left, ancho - 1))
    top = max(0, min(top, alto - 1))
    caja_ancho = max(0, min(caja_ancho, ancho - left))
    caja_alto = max(0, min(caja_alto, alto - top))
    return left, top, caja_ancho, caja_alto


def _rotular(
    frame_bgr: np.ndarray,
    texto: str,
    left: int,
    top: int,
    color: tuple[int, int, int],
    escala: float,
    grosor: int,
    margen: int,
) -> None:
    """Escribe la etiqueta sobre el borde superior de la caja.

    Si no hay lugar arriba, la baja adentro de la caja: el script de referencia
    la dibujaba siempre en `top - 10`, que en una caja pegada al borde superior
    dejaba el texto fuera del frame.
    """
    alto, _, _ = frame_bgr.shape
    (ancho_texto, alto_texto), _ = cv2.getTextSize(texto, _FUENTE, escala, grosor)

    izquierda = max(0, min(left, frame_bgr.shape[1] - ancho_texto))
    linea_base = top - margen
    if linea_base - alto_texto < 0:
        linea_base = min(alto - margen, top + alto_texto + margen)

    cv2.putText(
        frame_bgr,
        texto,
        (izquierda, linea_base),
        _FUENTE,
        escala,
        color,
        grosor,
        cv2.LINE_AA,
    )


def dibujar(frame_bgr: np.ndarray, detecciones: Sequence[ResultadoDeteccion]) -> None:
    """Dibuja cajas y etiquetas sobre `frame_bgr`, modificándolo in place.

    Se modifica in place para no sumar una copia por frame en la Pi: quien llame
    y necesite el frame limpio debe pasar una copia.
    """
    alto, ancho = frame_bgr.shape[:2]
    grosor, escala, margen = _metricas(alto)

    for deteccion in detecciones:
        left, top, caja_ancho, caja_alto = _recortar(deteccion.bbox, ancho, alto)
        if caja_ancho <= 0 or caja_alto <= 0:
            continue

        color = _color(deteccion.label)
        cv2.rectangle(
            frame_bgr, (left, top), (left + caja_ancho, top + caja_alto), color, grosor
        )
        _rotular(
            frame_bgr,
            deteccion.etiqueta,
            left,
            top,
            color,
            escala,
            grosor,
            margen,
        )
