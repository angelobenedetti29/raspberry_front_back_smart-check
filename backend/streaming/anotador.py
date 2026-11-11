"""Dibujo de detecciones sobre un frame BGR.

El pipeline decide *cuándo* anotar; este módulo decide *cómo* se ven las cajas.
Es el único lugar del backend que conoce el aspecto visual de una detección.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import TYPE_CHECKING

import cv2

from backend.etiquetas import ResolutorEtiquetas, normalizar_clave, separar_etiqueta

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

# BGR de los tokens del tema.
_VERDE_OK = (122, 224, 63)
_ROJO_QUEMADO = (95, 90, 255)
_AMARILLO_MEDIO = (90, 195, 255)

# Color por estado de calidad (sufijo de la etiqueta, ver backend.etiquetas).
# Las etiquetas sin estado válido caen al fallback por hash.
_COLORES_ESTADO: dict[str, tuple[int, int, int]] = {
    "ok": _VERDE_OK,
    "crudo": _AMARILLO_MEDIO,
    "quemado": _ROJO_QUEMADO,
}


def _color(label: str, resolutor: ResolutorEtiquetas | None = None) -> tuple[int, int, int]:
    """Color por estado de calidad, con hash de la etiqueta como fallback.

    El estado sale del mapa ``config.etiquetas.clases`` o del sufijo de la
    etiqueta (``_ok``/``_crudo``/``_quemado``, ver ``backend.etiquetas``):
    ok/crudo/quemado usan los tokens del tema (verde/amarillo/rojo) para que el
    operador lea el estado de un vistazo, para cualquier producto. Si la etiqueta
    no tiene estado válido, se deriva del hash de la clave normalizada y no de
    `hash()` a propósito: `hash()` de un str está aleatorizado por proceso, así
    que el mismo producto cambiaría de color entre arranques. Tampoco se usa el
    índice de clase, que depende del archivo `.names` y haría bailar los colores
    si se reordena.
    """
    estado = separar_etiqueta(label, resolutor)[1]
    if estado is not None and estado in _COLORES_ESTADO:
        return _COLORES_ESTADO[estado]
    clave = normalizar_clave(label)
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


def _recortar(bbox: tuple[int, int, int, int], ancho: int, alto: int) -> tuple[int, int, int, int]:
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

    Si no hay lugar arriba, la baja adentro de la caja.
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


def _rotular_linea(
    frame_bgr: np.ndarray,
    texto: str,
    linea_y: int,
    color: tuple[int, int, int],
    escala: float,
    grosor: int,
    margen: int,
) -> None:
    """Escribe la etiqueta de la línea de conteo sobre el margen derecho."""
    (ancho_texto, alto_texto), _ = cv2.getTextSize(texto, _FUENTE, escala * 0.8, max(1, grosor - 1))
    izquierda = max(10, frame_bgr.shape[1] - ancho_texto - 20)
    linea_base = max(alto_texto + 4, linea_y - margen)
    cv2.putText(
        frame_bgr,
        texto,
        (izquierda, linea_base),
        _FUENTE,
        escala * 0.8,
        color,
        max(1, grosor - 1),
        cv2.LINE_AA,
    )


def dibujar(
    frame_bgr: np.ndarray,
    detecciones: Sequence[ResultadoDeteccion],
    *,
    linea_conteo_y: int | None = None,
    resolutor: ResolutorEtiquetas | None = None,
) -> None:
    """Dibuja cajas, etiquetas y la línea de conteo sobre `frame_bgr` in place.

    Se modifica in place para no sumar una copia por frame en la Pi: quien llame
    y necesite el frame limpio debe pasar una copia.
    """
    alto, ancho = frame_bgr.shape[:2]
    grosor, escala, margen = _metricas(alto)

    if linea_conteo_y is not None and 0 < linea_conteo_y < alto:
        color_linea = (230, 160, 40)
        cv2.line(
            frame_bgr,
            (0, linea_conteo_y),
            (ancho, linea_conteo_y),
            color_linea,
            max(1, grosor - 1),
            cv2.LINE_AA,
        )
        _rotular_linea(
            frame_bgr,
            "LÍNEA DE CONTEO",
            linea_conteo_y,
            color_linea,
            escala,
            grosor,
            margen,
        )

    for deteccion in detecciones:
        left, top, caja_ancho, caja_alto = _recortar(deteccion.bbox, ancho, alto)
        if caja_ancho <= 0 or caja_alto <= 0:
            continue

        color = _color(deteccion.label, resolutor)
        cv2.rectangle(frame_bgr, (left, top), (left + caja_ancho, top + caja_alto), color, grosor)
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
