"""Tests unitarios de LoteRequest: from_payload/to_payload y validación estricta."""

from datetime import datetime, timezone

import pytest

from backend.domain.entities.lote_request import LoteRequest
from backend.tests.fakes import make_lote_payload


def test_from_payload_to_payload_roundtrip():
    payload = make_lote_payload()
    assert LoteRequest.from_payload(payload).to_payload() == payload


def test_from_payload_rejects_bool_for_int_fields():
    with pytest.raises(ValueError, match="'correctos' debe ser un entero"):
        LoteRequest.from_payload(make_lote_payload(correctos=True, quemados=0, totalUnidades=1))


def test_from_payload_rejects_fractional_float_for_int_fields():
    with pytest.raises(ValueError, match="'totalUnidades' debe ser un entero"):
        LoteRequest.from_payload(make_lote_payload(totalUnidades=3.9))


def test_from_payload_accepts_integral_float_for_int_fields():
    lote = LoteRequest.from_payload(
        make_lote_payload(totalUnidades=3.0, correctos=2.0, quemados=1.0)
    )
    assert lote.totalUnidades == 3 and isinstance(lote.totalUnidades, int)
    assert lote.correctos == 2 and isinstance(lote.correctos, int)


def test_from_payload_rejects_string_numbers():
    with pytest.raises(ValueError, match="'velocidadCinta' debe ser un número"):
        LoteRequest.from_payload(make_lote_payload(velocidadCinta="1.1"))


def test_from_payload_rejects_nan_and_inf():
    with pytest.raises(ValueError, match="finito"):
        LoteRequest.from_payload(make_lote_payload(tempHorno1=float("nan")))
    with pytest.raises(ValueError, match="finito"):
        LoteRequest.from_payload(make_lote_payload(tempHorno2=float("inf")))


def test_from_payload_rejects_bool_for_float_fields():
    with pytest.raises(ValueError, match="'correctosKg' debe ser un número"):
        LoteRequest.from_payload(make_lote_payload(correctosKg=True))


def test_from_payload_rejects_non_string_turno():
    with pytest.raises(ValueError, match="'turno' debe ser un texto"):
        LoteRequest.from_payload(make_lote_payload(turno=5))


def test_from_payload_rejects_invalid_uuid_and_date_types():
    with pytest.raises(ValueError, match="Formato UUID inválido"):
        LoteRequest.from_payload(make_lote_payload(productoId=123))
    with pytest.raises(ValueError, match="Formato de fecha inválido"):
        LoteRequest.from_payload(make_lote_payload(finAt=["x"]))


def test_missing_fields_keep_declaration_order():
    payload = make_lote_payload()
    payload.pop("turno")
    with pytest.raises(ValueError, match="Faltan campos obligatorios: turno"):
        LoteRequest.from_payload(payload)


def test_to_payload_normalizes_uuid_and_offset_datetime():
    lote = LoteRequest.from_payload(
        make_lote_payload(
            productoId="A1B2C3D4-5678-90AB-CDEF-1234567890AB",
            inicioAt="2024-01-01T05:00:00-03:00",
        )
    )
    out = lote.to_payload()
    assert out["productoId"] == "a1b2c3d4-5678-90ab-cdef-1234567890ab"
    assert out["inicioAt"] == "2024-01-01T08:00:00Z"


def utc_expected(value):
    return value.astimezone().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_to_payload_naive_datetime_is_interpreted_as_local():
    naive = datetime(2024, 1, 1, 8, 0, 0)
    naive_fin = datetime(2024, 1, 1, 9, 0, 0)
    lote = LoteRequest.from_payload(
        make_lote_payload(inicioAt=naive, finAt=naive_fin)
    )
    assert lote.to_payload()["inicioAt"] == utc_expected(naive)


def test_from_payload_rejects_total_mismatch():
    with pytest.raises(ValueError, match="total de unidades"):
        LoteRequest.from_payload(make_lote_payload(totalUnidades=99))
