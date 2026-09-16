"""Tests for SendPingRequestUseCase using the signed transport (network-free)."""

import pytest

from backend.tests.fakes import FakeTransport
from backend.use_cases.send_ping_request import SendPingRequestUseCase
from device_enrollment.transport import SendResult

PING_PAYLOAD = {
    "dispositivoId": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
    "cpuPct": 12.5,
}


def test_url_strips_trailing_slash_and_appends_endpoint():
    transport = FakeTransport()
    use_case = SendPingRequestUseCase(transport, "http://central:9000/api/v1/")

    result = use_case.execute(PING_PAYLOAD)

    assert result.ok is True
    path, payload, _ = transport.calls[0]
    assert path == "/dispositivos/ping"
    assert payload == PING_PAYLOAD
    assert use_case.target == "http://central:9000/api/v1/dispositivos/ping"


def test_url_without_trailing_slash_and_failure_result():
    transport = FakeTransport(SendResult(ok=False, error="Timeout"))
    use_case = SendPingRequestUseCase(transport, "http://central:9000/api/v1")

    result = use_case.execute(PING_PAYLOAD)

    assert result.ok is False
    assert result.error == "Timeout"
    assert transport.calls[0][0] == "/dispositivos/ping"


def test_no_shared_api_key_header_is_used():
    transport = FakeTransport()
    use_case = SendPingRequestUseCase(transport, "http://central:9000/api/v1")
    use_case.execute(PING_PAYLOAD)

    _, _, kwargs = transport.calls[0]
    assert "X-API-Key" not in kwargs
    assert "headers" not in kwargs


def test_target_property():
    use_case = SendPingRequestUseCase(FakeTransport(), "http://central:9000/api/v1/")
    assert use_case.target == "http://central:9000/api/v1/dispositivos/ping"


def test_missing_dispositivo_id_raises_value_error():
    transport = FakeTransport()
    use_case = SendPingRequestUseCase(transport, "http://central:9000/api/v1")

    with pytest.raises(ValueError):
        use_case.execute({"cpuPct": 1.0})
    assert transport.calls == []


def test_execute_returns_the_transport_result_unchanged():
    result = SendResult(ok=False, error="device_not_enrolled")
    transport = FakeTransport(result)
    use_case = SendPingRequestUseCase(transport, "http://central:9000/api/v1")

    assert use_case.execute(PING_PAYLOAD) is result


def test_execute_surfaces_error_detail_from_transport():
    transport = FakeTransport(SendResult(ok=False, status_code=403, error="unexpected_redirect"))
    use_case = SendPingRequestUseCase(transport, "http://central:9000/api/v1")

    result = use_case.execute(PING_PAYLOAD)

    assert result.ok is False
    assert result.status_code == 403
    assert result.error == "unexpected_redirect"
