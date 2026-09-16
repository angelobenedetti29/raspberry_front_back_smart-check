"""Tests for SendPingRequestUseCase using the signed transport (network-free)."""

import pytest

from backend.use_cases.send_ping_request import SendPingRequestUseCase

PING_PAYLOAD = {
    "dispositivoId": "a1b2c3d4-5678-90ab-cdef-1234567890ab",
    "cpuPct": 12.5,
}


class FakeTransport:
    def __init__(self, result=True):
        self.result = result
        self.calls = []
        self.last_error = "last error"
        self.last_status_code = 503
        self.last_response_text = "service unavailable"

    def post(self, path, payload, **kwargs):
        self.calls.append((path, payload, kwargs))
        return self.result


class BareTransport:
    def post(self, path, payload, **kwargs):
        return True


def test_url_strips_trailing_slash_and_appends_endpoint():
    transport = FakeTransport()
    use_case = SendPingRequestUseCase(transport, "http://central:9000/api/v1/")

    assert use_case.execute(PING_PAYLOAD) is True

    path, payload, _ = transport.calls[0]
    assert path == "/dispositivos/ping"
    assert payload == PING_PAYLOAD
    assert use_case.target == "http://central:9000/api/v1/dispositivos/ping"


def test_url_without_trailing_slash_and_failure_result():
    transport = FakeTransport(result=False)
    use_case = SendPingRequestUseCase(transport, "http://central:9000/api/v1")

    assert use_case.execute(PING_PAYLOAD) is False
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


def test_get_last_error_proxies_through():
    transport = FakeTransport()
    use_case = SendPingRequestUseCase(transport, "http://central:9000/api/v1")

    assert use_case.get_last_error() == "last error"


def test_get_last_error_defaults_to_none_when_missing():
    use_case = SendPingRequestUseCase(BareTransport(), "http://central:9000/api/v1")

    assert use_case.get_last_error() is None
