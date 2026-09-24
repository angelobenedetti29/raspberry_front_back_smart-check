"""Modelo de estado del sistema que consumen las secciones de la interfaz."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class NivelEstado(Enum):
    """Nivel de salud de un ítem de estado."""

    ACTIVO = "activo"
    ATENCION = "atencion"
    INACTIVO = "inactivo"
    SIN_DATO = "sin_dato"


@dataclass(frozen=True, slots=True)
class EstadoItem:
    """Una fila del estado del sistema."""

    clave: str
    etiqueta: str
    nivel: NivelEstado
    detalle: str


@dataclass(frozen=True, slots=True)
class SnapshotSistema:
    """Fotografía inmutable del estado, lista para renderizar."""

    items: tuple[EstadoItem, ...]
    modelo_activo: str
    resumen: str
