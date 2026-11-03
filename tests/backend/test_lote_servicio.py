"""Tests de `LoteService._attach_o_abrir` y sus dos wrappers."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from backend.config import load_config
from backend.lote import LoteService
from backend.lote.tipos import Conteos, ErrorLotes, EstadoLote, Lote


def _lote(id_: str = "L-1", *, modo: str = "MANUAL", manual: bool = False) -> Lote:
    return Lote(
        id=id_,
        sector_id="S-1",
        estado=EstadoLote.ABIERTO,
        producto_id="P-1",
        producto_nombre="Producto",
        abierto_en="2026-01-01T00:00:00",
        abierto_por=None,
        conteos=Conteos(ok=0, crudo=0, quemado=0, total=0),
        inactividad_segundos=0.0,
        modo=modo,
        iniciado_manualmente=manual,
    )


@pytest.fixture
def servicio() -> LoteService:
    svc = LoteService(load_config(), MagicMock())
    svc._cliente = MagicMock()
    return svc


def test_asegurar_lote_attach_a_lote_existente(servicio: LoteService) -> None:
    """Con lote abierto en el sector, se attacha y no abre ni marca ok."""
    abierto = _lote("L-9", modo="SUPERVISION")
    servicio._cliente.lote_abierto.return_value = abierto
    servicio._on_lote_abierto = MagicMock()

    with patch.object(servicio, "_marcar_ok") as marcar_ok:
        assert servicio._asegurar_lote("s", "P-1") == "L-9"

    servicio._cliente.abrir_lote.assert_not_called()
    marcar_ok.assert_not_called()
    assert servicio._lote is abierto
    servicio._on_lote_abierto.assert_called_once()


def test_asegurar_lote_abre_lote_nuevo_degradado(
    servicio: LoteService, caplog: pytest.LogCaptureFixture
) -> None:
    """Sin lote abierto, abre con el producto y loguea el alta degradada."""
    nuevo = _lote("L-3")
    servicio._cliente.lote_abierto.return_value = None
    servicio._cliente.abrir_lote.return_value = (nuevo, True)

    with (
        patch.object(servicio, "_marcar_ok") as marcar_ok,
        caplog.at_level(logging.INFO, logger="backend.lote"),
    ):
        assert servicio._asegurar_lote("s", "P-7") == "L-3"

    marcar_ok.assert_not_called()
    kwargs = servicio._cliente.abrir_lote.call_args.kwargs
    assert kwargs["producto_id"] == "P-7"
    assert kwargs["idempotency_key"]
    assert kwargs["momento"]
    assert kwargs["iniciado_manualmente"] is False
    assert kwargs["estado_inicial"] is None
    assert servicio._lote is nuevo
    assert "abierto degradado por la salida L-3" in caplog.text


def test_asegurar_lote_sin_producto_marca_error(servicio: LoteService) -> None:
    """Sin lote abierto ni producto resuelto, no abre y reporta el error."""
    servicio._cliente.lote_abierto.return_value = None

    with patch.object(servicio, "_marcar_error") as marcar_error:
        assert servicio._asegurar_lote("s", None) is None

    servicio._cliente.abrir_lote.assert_not_called()
    marcar_error.assert_called_once_with("Producto sin resolver para abrir el lote (ver log)")


def test_abrir_lote_attach_no_abre_ni_marca_ok(servicio: LoteService) -> None:
    """El alta de ENTRADA se attacha a un lote preexistente sin marcarlo ok."""
    abierto = _lote("L-5", manual=True)
    servicio._secret = "s"
    servicio._cliente.lote_abierto.return_value = abierto

    with patch.object(servicio, "_marcar_ok") as marcar_ok:
        servicio._abrir_lote("P-1")

    servicio._cliente.abrir_lote.assert_not_called()
    marcar_ok.assert_not_called()
    assert servicio._lote is abierto


def test_abrir_lote_abre_y_marca_ok(servicio: LoteService) -> None:
    """Sin lote abierto, ENTRADA abre uno propio y marca el backend ok."""
    nuevo = _lote("L-6")
    servicio._secret = "s"
    servicio._cliente.lote_abierto.return_value = None
    servicio._cliente.abrir_lote.return_value = (nuevo, True)

    with patch.object(servicio, "_marcar_ok") as marcar_ok:
        servicio._abrir_lote("P-1")

    servicio._cliente.abrir_lote.assert_called_once()
    marcar_ok.assert_called_once()
    assert servicio._lote is nuevo


@pytest.mark.parametrize("falla", ["lote_abierto", "abrir_lote"])
def test_error_de_lotes_aplica_error_y_no_rompe(servicio: LoteService, falla: str) -> None:
    """Un ErrorLotes en cualquiera de las llamadas se aplica sin propagarse."""
    servicio._secret = "s"
    servicio._cliente.lote_abierto.return_value = None
    servicio._cliente.abrir_lote.return_value = (_lote(), True)
    if falla == "lote_abierto":
        servicio._cliente.lote_abierto.side_effect = ErrorLotes(None, "red")
    else:
        servicio._cliente.abrir_lote.side_effect = ErrorLotes(500, "boom")

    with patch.object(servicio, "_aplicar_error") as aplicar_error:
        servicio._abrir_lote("P-1")
        assert servicio._asegurar_lote("s", "P-1") is None

    aplicar_error.assert_called()
    assert servicio._lote is None
