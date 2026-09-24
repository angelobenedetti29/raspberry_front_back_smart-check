"""Renderizado de íconos SVG tintados, sin depender de assets pre-coloreados."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

logger = logging.getLogger(__name__)

DIR_ICONOS = Path(__file__).resolve().parents[1] / "assets" / "iconos"
_CACHE: dict[tuple[str, str | None, int], QIcon] = {}


def icono(clave: str, color: str | None = None, px: int = 24) -> QIcon:
    """Devuelve un `QIcon` a partir del SVG `clave`, tintado con `color` (hex).

    El SVG se dibuja como máscara y luego se rellena con `color`, así el mismo
    archivo sirve para todos los estados (normal, seleccionado, atenuado).
    """
    cache_key = (clave, color, px)
    cacheado = _CACHE.get(cache_key)
    if cacheado is not None:
        return cacheado

    ruta = DIR_ICONOS / f"{clave}.svg"
    if not ruta.is_file():
        logger.warning("Ícono no encontrado: %s", ruta)
        return QIcon()

    renderer = QSvgRenderer(str(ruta))
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    if color is not None:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(color))
    painter.end()

    resultado = QIcon(pixmap)
    _CACHE[cache_key] = resultado
    return resultado
