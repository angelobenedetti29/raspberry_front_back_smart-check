import pytest

from backend.use_cases.finalize_lote import FinalizeLoteUseCase, LoteDeliveryError
from backend.tests.fakes import FakeSendLoteUseCase, make_lote_payload


def make_use_case(success=True, target="http://central:9000/api/v1/lotes", **kwargs):
    send = FakeSendLoteUseCase(success=success, target=target, **kwargs)
    return FinalizeLoteUseCase(send), send


def test_missing_field_raises_value_error():
    use_case, _ = make_use_case()
    payload = make_lote_payload()
    payload.pop("turno")
    with pytest.raises(ValueError, match="Faltan campos obligatorios: turno"):
        use_case.execute(payload)


def test_invalid_uuid_raises_value_error():
    use_case, _ = make_use_case()
    with pytest.raises(ValueError):
        use_case.execute(make_lote_payload(productoId="not-a-uuid"))


def test_invalid_date_raises_value_error():
    use_case, _ = make_use_case()
    with pytest.raises(ValueError):
        use_case.execute(make_lote_payload(inicioAt="not-a-date"))


def test_total_mismatch_raises_value_error():
    use_case, _ = make_use_case()
    with pytest.raises(ValueError, match="total de unidades"):
        use_case.execute(make_lote_payload(totalUnidades=99))


def test_send_failure_raises_lote_delivery_error():
    use_case, _ = make_use_case(
        success=False, status_code=500, error="boom", response_text="server error"
    )
    with pytest.raises(LoteDeliveryError) as exc_info:
        use_case.execute(make_lote_payload())

    error = exc_info.value
    assert error.status_code == 500
    assert error.error == "boom"
    assert error.response_text == "server error"
    assert error.target == "http://central:9000/api/v1/lotes"


def test_success_returns_exact_payload_and_forwards():
    use_case, send = make_use_case()
    payload = make_lote_payload()

    result = use_case.execute(payload)

    assert result == {
        "status": "success",
        "message": "Lote enviado al servidor central.",
        "target": "http://central:9000/api/v1/lotes",
    }
    assert send.sent == [payload]


def test_target_delegates_to_send_use_case():
    use_case, _ = make_use_case(target="http://otro:1234/api/v1/lotes")
    assert use_case.target == "http://otro:1234/api/v1/lotes"
