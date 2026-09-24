"""Detección y lectura de utilización de aceleradores de IA (device-agnostic).

El monitor no asume que el dispositivo sea una Raspberry Pi: pregunta si hay
un procesador de IA compatible disponible y, si lo hay, lee su utilización.
Hoy sólo hay un adaptador concreto (Hailo), pero el puerto FuenteAceleradorIA
permite sumar otros sin tocar el recolector.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# Ruta donde HailoRT deja la utilización del NNC cuando HAILO_MONITOR=1.
# Es un único archivo global que se reescribe cada ciclo (float + '\n').
_ARCHIVO_UTILIZACION_HAILO = Path("/tmp/nnc_utilization/nnc_utilization")


class FuenteAceleradorIA(Protocol):
    """Puerto de un acelerador de IA capaz de reportar su utilización."""

    nombre: str

    def disponible(self) -> bool:
        """True si el acelerador está presente y utilizable ahora."""
        ...

    def porcentaje_utilizacion(self) -> float | None:
        """Porcentaje de utilización [0,100], o None si no se puede leer."""
        ...


class FuenteHailo:
    """Adaptador para la NPU Hailo (p. ej. AI HAT+ en Raspberry Pi 5).

    Detección: el paquete `hailo_platform` instalado *y* `Device.scan()` con al
    menos un dispositivo. Nunca se crea un VDevice: es global del proceso y
    costoso (lo administra el motor de inferencia).

    Utilización: Hailo-8/8L no exponen la métrica por la API Python; se lee el
    archivo que deja HailoRT con `HAILO_MONITOR=1` (ver `habilitar_monitor_hailo`).
    """

    nombre = "Hailo"

    def __init__(self, archivo_utilizacion: Path = _ARCHIVO_UTILIZACION_HAILO) -> None:
        self._archivo = archivo_utilizacion
        self._presente: bool | None = None

    def disponible(self) -> bool:
        if self._presente is None:
            self._presente = _hailo_presente()
        return self._presente

    def porcentaje_utilizacion(self) -> float | None:
        if not self.disponible():
            return None
        try:
            return float(self._archivo.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None


def _hailo_presente() -> bool:
    """True sólo si el SDK está instalado y enumera algún dispositivo Hailo."""
    if importlib.util.find_spec("hailo_platform") is None:
        return False
    try:
        from hailo_platform import Device  # type: ignore[import-not-found]

        return len(Device.scan()) > 0
    except Exception:  # noqa: BLE001 - la detección nunca debe romper el monitor
        logger.debug("acelerador: falló la detección de Hailo", exc_info=True)
        return False


def habilitar_monitor_hailo() -> None:
    """Pide a HailoRT que escriba la utilización del NNC en /tmp.

    Debe llamarse *antes* de que el proceso cree el VDevice (el motor de
    inferencia lo hace en la primera inferencia). Es inofensivo si Hailo no
    está instalado. Sólo tiene efecto en Linux.
    """
    os.environ.setdefault("HAILO_MONITOR", "1")
    os.environ.setdefault("HAILO_MONITOR_TIME_INTERVAL", "1000")


def detectar_acelerador() -> FuenteAceleradorIA | None:
    """Devuelve la primera fuente de acelerador de IA disponible, o None.

    Punto de extensión: sumar acá otros adaptadores (p. ej. otro NPU) sin que
    el recolector sepa de hardware concreto.
    """
    candidatos: tuple[FuenteAceleradorIA, ...] = (FuenteHailo(),)
    for fuente in candidatos:
        if fuente.disponible():
            return fuente
    return None
