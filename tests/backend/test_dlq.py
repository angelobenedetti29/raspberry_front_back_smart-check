"""Tests de la cola DLQ: los rotados se drenan y sólo se vacía lo enviado."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from backend.config import load_config
from backend.lote import LoteService
from backend.lote.dlq import ColaDLQ
from backend.lote.tipos import EstadoProducto, EventoDeteccion


def _evento(evento_id: str) -> EventoDeteccion:
    return EventoDeteccion(
        evento_id=evento_id,
        producto_id="p-1",
        estado=EstadoProducto.OK,
        confianza=0.9,
        pista=1,
        frame=1,
        modelo_id="m1",
        momento="2026-01-01T00:00:00-03:00",
    )


def _dlq(tmp_path: Path, max_bytes: int = 5_242_880) -> ColaDLQ:
    return ColaDLQ(tmp_path / "dlq.jsonl", max_bytes=max_bytes, max_files=3)


def test_drenar_incluye_rotados_del_mas_viejo_al_mas_nuevo(tmp_path: Path) -> None:
    """El batch que rotó a `dlq.jsonl.1` sigue llegando al drain."""
    # max_bytes=1 fuerza la rotación en la segunda escritura.
    dlq = _dlq(tmp_path, max_bytes=1)
    dlq.encolar([_evento("viejo")], codigo_http=503, detalle="x", intento=1)
    dlq.encolar([_evento("nuevo")], codigo_http=503, detalle="x", intento=2)

    ids = [batch[0]["evento_id"] for batch in dlq.drenar()]

    assert ids == ["viejo", "nuevo"]


def test_vaciar_borra_el_principal_y_los_rotados(tmp_path: Path) -> None:
    """Tras un reenvío exitoso no queda copia vieja que se vuelva a drenar."""
    dlq = _dlq(tmp_path, max_bytes=1)
    dlq.encolar([_evento("a")], codigo_http=503, detalle="x", intento=1)
    dlq.encolar([_evento("b")], codigo_http=503, detalle="x", intento=2)
    rotado = dlq.path.with_name("dlq.jsonl.1")
    assert rotado.exists()

    dlq.vaciar()

    assert not dlq.path.exists()
    assert not rotado.exists()


def test_vaciar_no_borra_si_quedan_pendientes(tmp_path: Path) -> None:
    """Con la outbox no vacía, el DLQ sigue siendo la copia ante un corte."""
    servicio = LoteService(load_config(), MagicMock())
    servicio._dlq = _dlq(tmp_path)
    servicio._dlq.encolar([_evento("a")], codigo_http=503, detalle="x", intento=1)
    servicio._pendientes.append(_evento("b"))

    servicio._vaciar_dlq_si_ok()

    assert servicio._dlq.path.exists()


def test_vaciar_borra_cuando_no_quedan_pendientes(tmp_path: Path) -> None:
    """Con la outbox vacía y sin fallos, el DLQ ya cumplió su función."""
    servicio = LoteService(load_config(), MagicMock())
    servicio._dlq = _dlq(tmp_path)
    servicio._dlq.encolar([_evento("a")], codigo_http=503, detalle="x", intento=1)

    servicio._vaciar_dlq_si_ok()

    assert not servicio._dlq.path.exists()
