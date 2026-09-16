"""Tests for SendLoteRequestUseCase using the signed transport (network-free)."""

from backend.tests.fakes import BareTransport, FakeTransport
from backend.use_cases.send_lote_request import SendLoteRequestUseCase


def test_url_strips_trailing_slash_and_appends_endpoint():
    transport = FakeTransport()
    use_case = SendLoteRequestUseCase(transport, "http://central:9000/api/v1/")

    assert use_case.execute({"totalUnidades": 1}) is True

    path, payload, _ = transport.calls[0]
    assert path == "/lotes"
    assert payload == {"totalUnidades": 1}
    assert use_case.target == "http://central:9000/api/v1/lotes"


def test_url_without_trailing_slash_and_failure_result():
    transport = FakeTransport(result=False)
    use_case = SendLoteRequestUseCase(transport, "http://central:9000/api/v1")

    assert use_case.execute({}) is False
    assert transport.calls[0][0] == "/lotes"


def test_no_shared_api_key_header_is_used():
    transport = FakeTransport()
    use_case = SendLoteRequestUseCase(transport, "http://central:9000/api/v1")
    use_case.execute({"totalUnidades": 1})

    _, _, kwargs = transport.calls[0]
    assert "X-API-Key" not in kwargs
    assert "headers" not in kwargs


def test_get_last_accessors_proxy_through():
    transport = FakeTransport()
    use_case = SendLoteRequestUseCase(transport, "http://central:9000/api/v1")

    assert use_case.get_last_error() == "last error"
    assert use_case.get_last_status_code() == 503
    assert use_case.get_last_response_text() == "service unavailable"


def test_get_last_accessors_default_to_none_when_missing():
    use_case = SendLoteRequestUseCase(BareTransport(), "http://central:9000/api/v1")

    assert use_case.get_last_error() is None
    assert use_case.get_last_status_code() is None
    assert use_case.get_last_response_text() is None
