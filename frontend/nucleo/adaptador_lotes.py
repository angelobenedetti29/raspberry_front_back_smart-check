"""Adaptador entre el servicio de lotes y el puerto de la interfaz.

Es el único módulo del frontend que importa `backend.lote`, igual que
`adaptador_dispositivo.py` lo es para `backend.device`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from frontend.nucleo.controlador_dispositivo import TipoDispositivoUI
from frontend.nucleo.controlador_lotes import (
    ConteosUI,
    EstadoLoteUI,
    EstadoLotesUI,
    LoteUI,
    ProductoUI,
)

if TYPE_CHECKING:
    from backend.lote import LoteService
    from backend.lote.tipos import Conteos, DispositivoSector, Lote, Sector


def _tipo_a_ui(valor: object) -> TipoDispositivoUI | None:
    """Mapea el tipo del backend al enum de la interfaz, por valor.

    Un valor desconocido o ausente se traduce a `None` para no romper la UI.
    """
    if valor is None:
        return None
    if isinstance(valor, TipoDispositivoUI):
        return valor
    crudo = getattr(valor, "value", valor)
    try:
        return TipoDispositivoUI(crudo)
    except ValueError:
        return None


def _conteos(conteos: Conteos) -> ConteosUI:
    return ConteosUI(
        ok=conteos.ok,
        crudo=conteos.crudo,
        quemado=conteos.quemado,
        total=conteos.total,
    )


def _descripcion(dispositivo: DispositivoSector) -> str:
    """Formatea un dispositivo como "hostname (rol)"."""
    rol = _tipo_a_ui(dispositivo.tipo)
    return (
        f"{dispositivo.hostname} ({rol.etiqueta})"
        if rol is not None
        else dispositivo.hostname
    )


def _lote(lote: Lote | None) -> LoteUI | None:
    if lote is None:
        return None
    abierto_por = ""
    degradado = False
    if lote.abierto_por is not None:
        abierto_por = _descripcion(lote.abierto_por)
        degradado = (
            _tipo_a_ui(lote.abierto_por.tipo) is not TipoDispositivoUI.ENTRADA_HORNO
        )
    return LoteUI(
        id=lote.id,
        estado=EstadoLoteUI(lote.estado.value),
        producto_nombre=lote.producto_nombre or "",
        abierto_en=lote.abierto_en,
        abierto_por=abierto_por,
        degradado=degradado,
        conteos=_conteos(lote.conteos),
        cerrado_en=lote.cerrado_en or "",
        motivo_cierre=lote.motivo_cierre or "",
    )


class AdaptadorLotes:
    """Implementa `ControladorLotes` sobre un `LoteService`."""

    def __init__(self, servicio: LoteService) -> None:
        self._servicio = servicio

    def estado(self) -> EstadoLotesUI:
        """Traduce la instantánea del backend a tipos de la interfaz."""
        estado = self._servicio.estado()
        sector: Sector | None = estado.sector
        return EstadoLotesUI(
            habilitado=estado.habilitado,
            registrado=estado.registrado,
            rol=_tipo_a_ui(estado.tipo),
            sector_nombre=sector.nombre if sector is not None else "",
            companeros=(
                tuple(_descripcion(companero) for companero in sector.companeros)
                if sector is not None
                else ()
            ),
            productos=tuple(
                ProductoUI(id=producto.id, nombre=producto.nombre)
                for producto in estado.productos
                if producto.activo
            ),
            lote_activo=_lote(estado.lote_activo),
            segundos_para_cierre=estado.segundos_para_cierre,
            pendientes=estado.pendientes,
            descartados=estado.descartados,
            rechazados=estado.rechazados,
            backend_ok=estado.backend_ok,
            mensaje=estado.mensaje,
            historial=tuple(
                ui
                for ui in (_lote(lote) for lote in estado.historial)
                if ui is not None
            ),
        )

    def finalizar_lote(self) -> None:
        """Pide cerrar el lote abierto (motivo manual). No bloquea."""
        self._servicio.finalizar_lote()

    def refrescar(self) -> None:
        """Pide re-consultar catálogo, sector y lote abierto. No bloquea."""
        self._servicio.refrescar()
