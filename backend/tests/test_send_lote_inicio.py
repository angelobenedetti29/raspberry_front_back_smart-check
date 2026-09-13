"""Tests for SendLoteInicioUseCase using the signed transport (network-free)."""

import pytest

from backend.use_cases.send_lote_inicio import SendLoteInicioUseCase


class FakeTransport:
    def __init__(self, result=True):
        self.result = result
        self.calls = []
        self.last_error = "last error"
        self.last_status_code = 422
        self.last_response_text = "unprocessable"

    def post(self, path, payload, **kwargs):
        self.calls.append((path, payload, kwargs))
        return self.result


class BareTransport:
    def post(self, path, payload, **kwargs):
        return True


def test_url_strips_trailing_slash_and_sends_payload():
    transport = FakeTransport()
    use_case = SendLoteInicioUseCase(transport, "http://central:9000/api/v1/")

    assert use_case.execute("horno-1", "producto-1") is True

    path, payload, _ = transport.calls[0]
    assert path == "/lotes/inicio"
    assert payload == {"hornoId": "horno-1", "productoId": "producto-1"}
    assert use_case.target == "http://central:9000/api/v1/lotes/inicio"


def test_url_without_trailing_slash_and_failure_result():
    transport = FakeTransport(result=False)
    use_case = SendLoteInicioUseCase(transport, "http://central:9000/api/v1")

    assert use_case.execute("horno-1", "producto-1") is False
    assert transport.calls[0][0] == "/lotes/inicio"


def test_no_shared_api_key_header_is_used():
    transport = FakeTransport()
    use_case = SendLoteInicioUseCase(transport, "http://central:9000/api/v1")
    use_case.execute("horno-1", "producto-1")

    _, _, kwargs = transport.calls[0]
    assert "X-API-Key" not in kwargs
    assert "headers" not in kwargs


def test_target_property():
    use_case = SendLoteInicioUseCase(FakeTransport(), "http://central:9000/api/v1/")
    assert use_case.target == "http://central:9000/api/v1/lotes/inicio"


@pytest.mark.parametrize(
    "horno_id, producto_id",
    [("", "producto-1"), ("horno-1", ""), (None, "producto-1"), ("horno-1", None)],
)
def test_empty_ids_raise_value_error(horno_id, producto_id):
    transport = FakeTransport()
    use_case = SendLoteInicioUseCase(transport, "http://central:9000/api/v1")

    with pytest.raises(ValueError):
        use_case.execute(horno_id, producto_id)
    assert transport.calls == []


def test_get_last_accessors_proxy_through():
    transport = FakeTransport()
    use_case = SendLoteInicioUseCase(transport, "http://central:9000/api/v1")

    assert use_case.get_last_error() == "last error"
    assert use_case.get_last_status_code() == 422
    assert use_case.get_last_response_text() == "unprocessable"


def test_get_last_accessors_default_to_none_when_missing():
    use_case = SendLoteInicioUseCase(BareTransport(), "http://central:9000/api/v1")

    assert use_case.get_last_error() is None
    assert use_case.get_last_status_code() is None
    assert use_case.get_last_response_text() is None
