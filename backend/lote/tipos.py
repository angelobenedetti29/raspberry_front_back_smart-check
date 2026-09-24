"""Tipos de dominio del subsistema de lotes (sin HTTP ni hilos).

Espejo en Python de lo que expone el backend Go. Todo es inmutable (`frozen=True,
slots=True`),
coherente con el resto de la configuración.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.device.tipos import TipoDispositivo


class ErrorLote(Exception):
    """Error de dominio del lote (estado inconsistente, producto faltante)."""


class ErrorLotes(Exception):
    """Error de red o HTTP contra el backend de lotes.

    `codigo` es el status HTTP, o None si fue un fallo de red/serialización.
    """

    def __init__(self, codigo: int | None, detalle: str) -> None:
        texto = f"[{codigo}] {detalle}" if codigo is not None else detalle
        super().__init__(texto)
        self.codigo = codigo
        self.detalle = detalle

    @property
    def reintentable(self) -> bool:
        """True si conviene reintentar: error de red, 429 o 5xx."""
        return self.codigo is None or self.codigo == 429 or self.codigo >= 500


class EstadoLote(str, Enum):
    """Estado del lote en el backend."""

    ABIERTO = "ABIERTO"
    CERRADO = "CERRADO"


class MotivoCierre(str, Enum):
    """Motivo con el que se cierra un lote."""

    SIN_DETECCIONES = "sin_detecciones"
    MANUAL = "manual"
    APAGADO = "apagado"


class EstadoProducto(str, Enum):
    """Estado de calidad de un producto. Lo define el modelo."""

    OK = "ok"
    CRUDO = "crudo"
    QUEMADO = "quemado"


@dataclass(frozen=True, slots=True)
class Producto:
    """Entidad del catálogo del backend que un modelo detecta."""

    id: str
    nombre: str
    activo: bool


@dataclass(frozen=True, slots=True)
class DispositivoSector:
    """Dispositivo que comparte el sector (o el que abrió el lote)."""

    device_id: str
    hostname: str
    tipo: TipoDispositivo


@dataclass(frozen=True, slots=True)
class Sector:
    """Sector del dispositivo autenticado, con sus compañeros."""

    sector_id: str
    nombre: str
    tipo: TipoDispositivo
    companeros: tuple[DispositivoSector, ...]


@dataclass(frozen=True, slots=True)
class Conteos:
    """Conteos de un lote. Un estado que el modelo no produce viaja None."""

    ok: int | None
    crudo: int | None
    quemado: int | None
    total: int


@dataclass(frozen=True, slots=True)
class Lote:
    """Lote abierto o cerrado, tal como lo devuelve el backend."""

    id: str
    sector_id: str
    estado: EstadoLote
    producto_id: str | None
    producto_nombre: str | None
    abierto_en: str
    abierto_por: DispositivoSector | None
    conteos: Conteos
    ultimo_evento_en: str | None
    inactividad_segundos: float
    cerrado_en: str | None = None
    motivo_cierre: str | None = None


@dataclass(frozen=True, slots=True)
class EventoDeteccion:
    """Producto detectado, listo para reportar al backend."""

    evento_id: str
    producto_id: str | None
    estado: EstadoProducto
    confianza: float
    pista: int
    frame: int
    modelo_id: str | None
    momento: str


@dataclass(frozen=True, slots=True)
class EstadoLotes:
    """Instantánea del servicio para la UI."""

    habilitado: bool
    registrado: bool
    tipo: TipoDispositivo | None
    sector: Sector | None
    productos: tuple[Producto, ...]
    lote_activo: Lote | None
    segundos_para_cierre: float | None
    pendientes: int
    descartados: int
    rechazados: int
    backend_ok: bool
    mensaje: str
    historial: tuple[Lote, ...]
