"""Tests de las fórmulas de tiempo de la sección Horno y cinta."""

from __future__ import annotations

from frontend.secciones.horno_cinta import _segundos_restantes


def test_countdown_usa_el_ultimo_poll_real() -> None:
    # Poll hace 3 s de un intervalo de 5 s: quedan 2 s.
    assert _segundos_restantes(103.0, 100.0, 5.0) == 2.0


def test_countdown_sin_poll_previo_asume_poll_vencido() -> None:
    # Sin poll registrado se asume que ya toca consultar: restante 0.
    assert _segundos_restantes(103.0, None, 5.0) == 0.0


def test_countdown_nunca_es_negativo() -> None:
    assert _segundos_restantes(120.0, 100.0, 5.0) == 0.0
