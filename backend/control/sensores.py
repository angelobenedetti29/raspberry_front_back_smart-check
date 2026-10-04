"""Interfaces abstractas para sensores del horno y aplicación de consignas.

La planta real usa PLC + variador; en desarrollo o sin hardware conectado,
se usan implementaciones stub que devuelven None o no hacen nada. El
ControlService depende sólo de estas interfaces; los detalles del hardware
se inyectan desde la composición (run.py o un factory).

Para no acoplar el control al resto de la app, los stubs viven acá mismo.
Cuando exista el driver real (Modbus, RS-485, etc.), se reemplaza el stub
sin tocar el ControlService.
"""

from __future__ import annotations

import logging
from typing import Protocol

from backend.control.tipos import LecturaHorno

logger = logging.getLogger(__name__)


class LectorSensores(Protocol):
    """Contrato mínimo para leer el estado actual del horno.

    La implementación real lee termocuplas/encoders/variador y devuelve una
    LecturaHorno. El stub devuelve todo None: la Pi publica un heartbeat
    con los campos en null y el backend igual lo acepta.
    """

    def leer(self) -> LecturaHorno: ...


class AplicadorConsigna(Protocol):
    """Contrato mínimo para aplicar un setpoint al horno.

    La implementación real escribe en el PLC/variador y devuelve la lectura
    efectiva (puede diferir si el controlador rechaza). El stub no hace nada
    y devuelve None en los campos afectados.
    """

    def aplicar(
        self,
        temp_objetivo: float,
        temp_objetivo_2: float | None,
        velocidad_objetivo: float,
    ) -> tuple[float | None, float | None, float | None]: ...


class LectorSensoresStub:
    """Lector vacío: la Pi sólo publica heartbeats con los campos en null."""

    def leer(self) -> LecturaHorno:
        return LecturaHorno(
            temp_horno_1=None,
            temp_horno_2=None,
            temp_comb_1=None,
            temp_comb_2=None,
            humedad_horno=None,
            temp_ambiente=None,
            humedad_ambiente=None,
            velocidad_cinta=None,
            temp_objetivo=None,
            vel_objetivo=None,
            confianza_deteccion=None,
        )


class AplicadorConsignaStub:
    """Aplicador vacío: registra el último setpoint recibido pero no hace nada."""

    def __init__(self) -> None:
        self._ultimo: tuple[float | None, float | None, float | None] | None = None

    def aplicar(
        self,
        temp_objetivo: float,
        temp_objetivo_2: float | None,
        velocidad_objetivo: float,
    ) -> tuple[float | None, float | None, float | None]:
        self._ultimo = (temp_objetivo, temp_objetivo_2, velocidad_objetivo)
        logger.info(
            "aplicador stub: T1=%.2f T2=%s V=%.3f",
            temp_objetivo,
            temp_objetivo_2,
            velocidad_objetivo,
        )
        return self._ultimo