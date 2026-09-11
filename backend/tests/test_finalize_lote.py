import pytest

from backend.use_cases.finalize_lote import FinalizeLoteUseCase, LoteDeliveryError


class FakeSendLoteUseCase:
    def __init__(self, success=True, status_code=None, error=None, response_text=None):
        self.success = success
        self.status_code = status_code
        self.error = error
        self.response_text = response_text
        self.sent = []

    def execute(self, payload):
        self.sent.append(payload)
        return self.success

    def get_last_status_code(self):
        return self.status_code

    def get_last_error(self):
        return self.error

    def get_last_response_text(self):
        return self.response_text


def make_payload(**overrides):
    payload = {
        "productoId": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
        "turno": "mañana",
        "inicioAt": "2024-01-01T08:00:00Z",
        "finAt": "2024-01-01T09:00:00Z",
        "totalUnidades": 3,
        "correctos": 2,
        "quemados": 1,
        "crudas": 0,
        "correctosKg": 0.05,
        "quemadosKg": 0.03,
        "crudosKg": 0.0,
        "tempHorno1": 220.0,
        "tempCombHorno1": 315.0,
        "tempHorno2": 218.0,
        "tempCombHorno2": 312.0,
        "velocidadCinta": 1.1,
    }
    payload.update(overrides)
    return payload


def make_use_case(success=True, base_url="http://central:9000", **kwargs):
    send = FakeSendLoteUseCase(success=success, **kwargs)
    return FinalizeLoteUseCase(send, base_url), send


def test_missing_field_raises_value_error():
    use_case, _ = make_use_case()
    payload = make_payload()
    payload.pop("turno")
    with pytest.raises(ValueError, match="Faltan campos obligatorios: turno"):
        use_case.execute(payload)


def test_invalid_uuid_raises_value_error():
    use_case, _ = make_use_case()
    with pytest.raises(ValueError):
        use_case.execute(make_payload(productoId="not-a-uuid"))


def test_invalid_date_raises_value_error():
    use_case, _ = make_use_case()
    with pytest.raises(ValueError):
        use_case.execute(make_payload(inicioAt="not-a-date"))


def test_total_mismatch_raises_value_error():
    use_case, _ = make_use_case()
    with pytest.raises(ValueError, match="total de unidades"):
        use_case.execute(make_payload(totalUnidades=99))


def test_send_failure_raises_lote_delivery_error():
    use_case, _ = make_use_case(
        success=False, status_code=500, error="boom", response_text="server error"
    )
    with pytest.raises(LoteDeliveryError) as exc_info:
        use_case.execute(make_payload())

    error = exc_info.value
    assert error.status_code == 500
    assert error.error == "boom"
    assert error.response_text == "server error"
    assert error.target == "http://central:9000/api/v1/lotes"


def test_success_returns_exact_payload_and_forwards():
    use_case, send = make_use_case(base_url="http://central:9000/")
    payload = make_payload()

    result = use_case.execute(payload)

    assert result == {
        "status": "success",
        "message": "Lote enviado al servidor central.",
        "target": "http://central:9000/api/v1/lotes",
    }
    assert send.sent == [payload]
