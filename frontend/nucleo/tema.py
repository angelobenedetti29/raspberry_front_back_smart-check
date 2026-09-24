"""Carga del tema visual (hoja QSS) y tabla de colores del producto."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QApplication, QStyleFactory

logger = logging.getLogger(__name__)

# Paleta "grafito de planta" para panel industrial. Los nombres son semánticos
# y coinciden con los tokens usados en la hoja de estilos.
TOKENS: dict[str, str] = {
    "bg": "#101418",
    "panel": "#171C22",
    "panel_alto": "#1F262E",
    "borde": "#2A323B",
    "texto": "#ECEFF3",
    "texto_muted": "#94A1AE",
    "accent": "#3DD6D0",
    "accent_soft": "#123B3A",
    "ok": "#3FE07A",
    "alerta": "#FFB020",
    "error": "#FF5A5F",
    "inactivo": "#66727E",
}

DIR_ASSETS = Path(__file__).resolve().parents[1] / "assets"
RUTA_QSS = DIR_ASSETS / "estilos.qss"


def color(token: str) -> str:
    """Devuelve el hex asociado a un token del tema."""
    try:
        return TOKENS[token]
    except KeyError as exc:
        raise KeyError(f"Token de color desconocido: {token!r}") from exc


def cargar_tema(app: QApplication) -> None:
    """Aplica la hoja de estilos global.

    La ruta se deriva de `__file__`, nunca del directorio de trabajo. Si el
    archivo no está, registra un aviso y continúa con el estilo por defecto.
    """
    # Fijar Fusion antes de la hoja: el estilo del sistema (p. ej. Kvantum) inyecta
    # transparencia en el popup del QComboBox. Con estilo base fijo el resultado es
    # el mismo en cualquier máquina.
    app.setStyle(QStyleFactory.create("Fusion"))
    try:
        hoja = RUTA_QSS.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("No se pudo leer la hoja de estilos %s: %s", RUTA_QSS, exc)
        return
    app.setStyleSheet(hoja)
    logger.info("Tema aplicado desde %s", RUTA_QSS)
