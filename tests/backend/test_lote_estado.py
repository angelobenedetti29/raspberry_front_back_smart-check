"""Tests del estado local del lote: muere con el lote que cerró el backend."""

from __future__ import annotations

from unittest.mock import MagicMock

from backend.config import load_config
from backend.lote import LoteService
from backend.lote.tipos import ErrorLotes, EstadoLote, EstadoProducto


def _servicio_con_lote() -> LoteService:
    """Servicio enganchado a un lote con conteos y dedupe cargados."""
    servicio = LoteService(load_config(), MagicMock())
    servicio._lote = MagicMock(id="L-1", estado=EstadoLote.ABIERTO)
    servicio._reportadas.add(("m1", 1))
    servicio._conteos_locales[EstadoProducto.OK] = 7
    servicio._cierre_manual = True
    servicio._ultimo_modo_emitido = MagicMock()
    return servicio


def test_404_olvida_conteos_dedupe_y_cierre_manual() -> None:
    """Un reporte 404 (lote ya cerrado) no deja estado para el lote siguiente."""
    servicio = _servicio_con_lote()

    servicio._aplicar_error(ErrorLotes(404, "lote inexistente"), [])

    assert servicio._lote is None
    assert not servicio._reportadas
    assert not servicio._conteos_locales
    assert not servicio._cierre_manual
    assert servicio._ultimo_modo_emitido is None
    # El cierre del próximo lote arranca de cero, no con los 7 del anterior.
    assert servicio._conteos_finales().total == 0


def test_lote_cerrado_desde_el_backend_tambien_limpia_el_estado() -> None:
    """Si el operario cierra el lote en el frontend, la Pi no arrastra conteos."""
    servicio = _servicio_con_lote()
    servicio._secret = "s"
    servicio._cliente.lote_abierto = MagicMock(return_value=None)

    assert servicio._inactividad_sector() is None

    assert servicio._lote is None
    assert not servicio._reportadas
    assert not servicio._conteos_locales
    assert servicio._conteos_finales().total == 0
