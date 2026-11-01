"""Tests de la URL de publicación derivada del nombre y el tipo."""

from __future__ import annotations

import pytest

from backend.device.tipos import TipoDispositivo
from backend.streaming.url import construir_url_publicacion, slug_nombre


@pytest.mark.parametrize(
    ("nombre", "esperado"),
    [
        ("rb-1", "rb-1"),
        ("RB-1", "rb-1"),
        ("Cámara Niño", "camara-nino"),
        ("  rb  1 -- horno  ", "rb-1-horno"),
        ("///", "dispositivo"),
        ("", "dispositivo"),
    ],
)
def test_slug_nombre(nombre: str, esperado: str) -> None:
    assert slug_nombre(nombre) == esperado


def test_sufijo_stream_por_tipo() -> None:
    assert TipoDispositivo.ENTRADA_HORNO.sufijo_stream == "entrada"
    assert TipoDispositivo.SALIDA_HORNO.sufijo_stream == "salida"


def test_url_entrada_horno() -> None:
    url = construir_url_publicacion(
        "rtsp://smartcheck.duckdns.org:8554",
        "rb-1",
        TipoDispositivo.ENTRADA_HORNO,
    )
    assert url == "rtsp://smartcheck.duckdns.org:8554/rb-1-entrada"


def test_url_salida_horno_con_barra_final() -> None:
    url = construir_url_publicacion(
        "rtsp://smartcheck.duckdns.org:8554/",
        "rb-1",
        TipoDispositivo.SALIDA_HORNO,
    )
    assert url == "rtsp://smartcheck.duckdns.org:8554/rb-1-salida"
