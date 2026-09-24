"""Puerto del servicio de lotes hacia las secciones de la interfaz.

Igual que `controlador_dispositivo.py`, las secciones no importan `backend.*`:
este módulo expone tipos propios, listos para mostrar.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from frontend.nucleo.controlador_dispositivo import TipoDispositivoUI


class EstadoProductoUI(str, Enum):
    """Estado de calidad de un producto (lo define el modelo)."""

    OK = "ok"
    CRUDO = "crudo"
    QUEMADO = "quemado"

    @property
    def etiqueta(self) -> str:
        """Rótulo para la interfaz."""
        return {
            EstadoProductoUI.OK: "Ok",
            EstadoProductoUI.CRUDO: "Crudo",
            EstadoProductoUI.QUEMADO: "Quemado",
        }[self]


class EstadoLoteUI(str, Enum):
    """Estado del lote."""

    ABIERTO = "ABIERTO"
    CERRADO = "CERRADO"

    @property
    def etiqueta(self) -> str:
        """Rótulo para la interfaz."""
        return {
            EstadoLoteUI.ABIERTO: "En curso",
            EstadoLoteUI.CERRADO: "Cerrado",
        }[self]


@dataclass(frozen=True, slots=True)
class ProductoUI:
    """Producto del catálogo del backend, para el selector de Configuración."""

    id: str
    nombre: str


@dataclass(frozen=True, slots=True)
class ConteosUI:
    """Conteos de un lote. `None` = el modelo no produce ese estado."""

    ok: int | None
    crudo: int | None
    quemado: int | None
    total: int


@dataclass(frozen=True, slots=True)
class LoteUI:
    """Lote listo para mostrar."""

    id: str
    estado: EstadoLoteUI
    producto_nombre: str
    abierto_en: str
    abierto_por: str
    """Texto ya formateado, p. ej. "rbpi-01 (Entrada de horno)"."""

    degradado: bool
    """True si lo abrió la salida porque no había entrada registrada."""

    conteos: ConteosUI
    cerrado_en: str
    motivo_cierre: str


@dataclass(frozen=True, slots=True)
class EstadoLotesUI:
    """Estado del servicio de lotes, sin tipos del backend."""

    habilitado: bool
    registrado: bool
    rol: TipoDispositivoUI | None
    sector_nombre: str
    companeros: tuple[str, ...]
    productos: tuple[ProductoUI, ...]
    lote_activo: LoteUI | None
    segundos_para_cierre: float | None
    pendientes: int
    descartados: int
    rechazados: int
    backend_ok: bool
    mensaje: str
    historial: tuple[LoteUI, ...]


@runtime_checkable
class ControladorLotes(Protocol):
    """Lo que la sección Lotes le pide al servicio de lotes."""

    def estado(self) -> EstadoLotesUI: ...

    def finalizar_lote(self) -> None:
        """Pide cerrar el lote abierto (motivo manual). No bloquea."""
        ...

    def refrescar(self) -> None:
        """Pide re-consultar catálogo, sector y lote abierto. No bloquea."""
        ...
