"""Tests de la convención de etiquetas ``<producto>_<estado>`` (backend/etiquetas.py).

Cubre el parseo del sufijo de estado, la normalización de nombres contra el
catálogo del backend (tildes/espacios → ``_``), los alias transitorios de los
modelos actuales, la clasificación a ``EstadoProducto`` (backend/lote/estado.py)
y los dos consumidores de la convención: el latch de quemado del estabilizador
y el color por estado del anotador.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from backend.etiquetas import (
    ESTADOS_VALIDOS,
    ResolutorEtiquetas,
    normalizar_clave,
    separar_etiqueta,
)
from backend.inference import ResultadoDeteccion
from backend.lote.estado import clasificar_estado
from backend.lote.tipos import EstadoProducto
from backend.streaming.anotador import (
    _AMARILLO_MEDIO,
    _PALETA,
    _ROJO_QUEMADO,
    _VERDE_OK,
    _color,
)
from backend.streaming.estabilizador import EstabilizadorDetecciones


@pytest.mark.parametrize(
    ("label", "esperado"),
    [
        # Convención: prefijo + sufijo de estado tras el último "_".
        ("tostada_comun_quemado", ("tostada_comun", "quemado")),
        ("fajita_crudo", ("fajita", "crudo")),
        ("tostada_integral_ok", ("tostada_integral", "ok")),
        # Alias transitorios de los modelos actuales (borrar cuando llegue el
        # modelo reentrenado): resuelven al producto real del Go.
        ("TCQ", ("tostada_comun", "quemado")),
        ("TCOK", ("tostada_comun", "ok")),
        ("Tostada Quemada", ("tostada_comun", "quemado")),
        ("tostadas ok", ("tostada_comun", "ok")),
        # Sin sufijo válido: el label entero es el producto y no hay estado.
        ("person", ("person", None)),
        ("hot dog", ("hot_dog", None)),
        ("caja_ok_cruda", ("caja_ok_cruda", None)),
        ("", ("", None)),
        ("ok", ("ok", None)),
    ],
)
def test_separar_etiqueta(label: str, esperado: tuple[str, str | None]) -> None:
    assert separar_etiqueta(label) == esperado


RESOLUTOR = ResolutorEtiquetas(
    {
        "tc": ("tostada_comun", None),
        "tcok": ("tostada_comun", "ok"),
        "tcq": ("tostada_comun", "quemado"),
        "cha": ("chalitas", None),
        "fajita": ("chalitas", None),
        "chaok": ("chalitas", "ok"),
        "chaq": ("chalitas", "quemado"),
    }
)


@pytest.mark.parametrize(
    ("label", "esperado"),
    [
        # Códigos cortos del mapa configurable.
        ("TC", ("tostada_comun", None)),
        ("TCQ", ("tostada_comun", "quemado")),
        ("chaok", ("chalitas", "ok")),
        ("Fajita", ("chalitas", None)),
        ("Tostada Común", ("tostada_comun", None)),
        # Fuera del mapa: cae a la convención <producto>_<estado>.
        ("tostada_comun_quemado", ("tostada_comun", "quemado")),
        ("person", ("person", None)),
    ],
)
def test_resolutor_configurable(label: str, esperado: tuple[str, str | None]) -> None:
    assert separar_etiqueta(label, RESOLUTOR) == esperado


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Tostada Común", "tostada_comun"),
        ("  FAJITA  ", "fajita"),
        ("Tostada-Integral", "tostada_integral"),
        ("Tostada  doble   espacio", "tostada_doble_espacio"),
        ("Fajita Íntegra", "fajita_integra"),
        ("", ""),
    ],
)
def test_normalizar_clave(texto: str, esperado: str) -> None:
    assert normalizar_clave(texto) == esperado


def test_estados_validos_coinciden_con_estado_producto() -> None:
    """Guarda el acoplamiento 1:1: clasificar_estado hace EstadoProducto(estado)."""
    assert set(ESTADOS_VALIDOS) == {estado.value for estado in EstadoProducto}


@pytest.mark.parametrize(
    ("label", "esperado"),
    [
        ("tostada_comun_quemado", EstadoProducto.QUEMADO),
        ("fajita_crudo", EstadoProducto.CRUDO),
        ("tostada_integral_ok", EstadoProducto.OK),
        ("TCQ", EstadoProducto.QUEMADO),
        ("tostadas ok", EstadoProducto.OK),
        ("person", None),
        ("caja_ok_cruda", None),
    ],
)
def test_clasificar_estado(label: str, esperado: EstadoProducto | None) -> None:
    assert clasificar_estado(label) == esperado


@pytest.mark.parametrize(
    ("label", "esperado"),
    [
        ("tostada_comun_quemado", _ROJO_QUEMADO),
        ("fajita_crudo", _AMARILLO_MEDIO),
        ("tostada_integral_ok", _VERDE_OK),
        ("TCQ", _ROJO_QUEMADO),
        ("tostadas ok", _VERDE_OK),
    ],
)
def test_color_por_estado(label: str, esperado: tuple[int, int, int]) -> None:
    assert _color(label) == esperado


def test_color_fallback_paleta_para_labels_sin_estado() -> None:
    """Sin sufijo válido (p. ej. clases COCO) cae a la paleta por hash."""
    assert _color("person") in _PALETA


def _eventos_estables(
    label: str,
    frames: int = 3,
    separar: Callable[[str], tuple[str, str | None]] | None = None,
) -> list[tuple[str, str]]:
    """Corre `frames` detecciones del mismo label y acumula (tipo, label)."""
    estabilizador = EstabilizadorDetecciones(separar=separar)
    eventos: list[tuple[str, str]] = []
    deteccion = ResultadoDeteccion(label=label, confianza=0.9, bbox=(100, 100, 200, 200))
    for _ in range(frames):
        estabilizador.estabilizar([deteccion])
        eventos += [(evento.tipo, evento.label) for evento in estabilizador.eventos()]
    return eventos


def test_estabilizador_confirma_quemado_por_sufijo() -> None:
    eventos = _eventos_estables("tostada_comun_quemado")
    assert ("alta", "tostada_comun_quemado") in eventos
    assert ("quemada", "tostada_comun_quemado") in eventos


def test_estabilizador_ok_no_emite_quemada() -> None:
    eventos = _eventos_estables("tostada_integral_ok")
    assert ("alta", "tostada_integral_ok") in eventos
    assert all(tipo != "quemada" for tipo, _ in eventos)


def test_estabilizador_alias_tcq_confirma_quemado() -> None:
    eventos = _eventos_estables("TCQ")
    assert ("quemada", "TCQ") in eventos


def test_estabilizador_resolutor_configurable_confirma_quemada() -> None:
    """Con el mapa del config, CHAQ (chalitas quemado) confirma la quemada."""
    eventos = _eventos_estables("CHAQ", separar=RESOLUTOR.separar)
    assert ("quemada", "CHAQ") in eventos


def test_color_por_resolutor_configurable() -> None:
    assert _color("CHAQ", RESOLUTOR) == _ROJO_QUEMADO
