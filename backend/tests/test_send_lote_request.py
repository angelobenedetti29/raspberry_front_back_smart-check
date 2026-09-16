"""Tests for SendLoteRequestUseCase using the signed transport (network-free)."""

from backend.tests.fakes import FakeTransport
from backend.use_cases.send_lote_request import SendLoteRequestUseCase
from device_enrollment.transport import SendResult


def test_url_strips_trailing_slash_and_appends_endpoint():
    transport = FakeTransport()
    use_case = SendLoteRequestUseCase(transport, "http://central:9000/api/v1/")

    result = use_case.execute({"totalUnidades": 1})

    assert result.ok is True
    path, payload, _ = transport.calls[0]
    assert path == "/lotes"
    assert payload == {"totalUnidades": 1}
    assert use_case.target == "http://central:9000/api/v1/lotes"


def test_url_without_trailing_slash_and_failure_result():
    transport = FakeTransport(
        SendResult(ok=False, status_code=503, error="service unavailable")
    )
    use_case = SendLoteRequestUseCase(transport, "http://central:9000/api/v1")

    result = use_case.execute({})

    assert result.ok is False
    assert result.status_code == 503
    assert result.error == "service unavailable"
    assert transport.calls[0][0] == "/lotes"


def test_no_shared_api_key_header_is_used():
    transport = FakeTransport()
    use_case = SendLoteRequestUseCase(transport, "http://central:9000/api/v1")
    use_case.execute({"totalUnidades": 1})

    _, _, kwargs = transport.calls[0]
    assert "X-API-Key" not in kwargs
    assert "headers" not in kwargs


def test_execute_returns_the_transport_result_unchanged():
    result = SendResult(ok=False, status_code=422, error="unprocessable", response_text="nope")
    transport = FakeTransport(result)
    use_case = SendLoteRequestUseCase(transport, "http://central:9000/api/v1")

    assert use_case.execute({"totalUnidades": 1}) is result


def test_execute_surfaces_success_detail_from_transport():
    transport = FakeTransport(SendResult(ok=True, status_code=201, response_text="created"))
    use_case = SendLoteRequestUseCase(transport, "http://central:9000/api/v1")

    result = use_case.execute({})

    assert result.ok is True
    assert result.status_code == 201
    assert result.response_text == "created"
