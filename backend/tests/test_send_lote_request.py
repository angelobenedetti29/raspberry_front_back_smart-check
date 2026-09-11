"""Tests for SendLoteRequestUseCase (network-free)."""

from backend.domain.interfaces.http_client import IHttpClient
from backend.use_cases.send_lote_request import SendLoteRequestUseCase


class FakeHttpClient(IHttpClient):
    def __init__(self, result=True):
        self.result = result
        self.calls = []
        self.last_error = "last error"
        self.last_status_code = 503
        self.last_response_text = "service unavailable"

    def post(self, url, payload, headers=None):
        self.calls.append((url, payload, headers))
        return self.result


class BareHttpClient(IHttpClient):
    def post(self, url, payload, headers=None):
        return True


def test_url_strips_trailing_slash_and_appends_endpoint():
    client = FakeHttpClient()
    use_case = SendLoteRequestUseCase(client, "http://central:9000/", "key-123")

    assert use_case.execute({"totalUnidades": 1}) is True

    url, payload, headers = client.calls[0]
    assert url == "http://central:9000/api/v1/lotes"
    assert payload == {"totalUnidades": 1}
    assert headers == {
        "Content-Type": "application/json",
        "X-API-Key": "key-123",
    }


def test_url_without_trailing_slash_and_failure_result():
    client = FakeHttpClient(result=False)
    use_case = SendLoteRequestUseCase(client, "http://central:9000", "key-123")

    assert use_case.execute({}) is False
    assert client.calls[0][0] == "http://central:9000/api/v1/lotes"


def test_get_last_accessors_proxy_through():
    client = FakeHttpClient()
    use_case = SendLoteRequestUseCase(client, "http://central:9000", "key-123")

    assert use_case.get_last_error() == "last error"
    assert use_case.get_last_status_code() == 503
    assert use_case.get_last_response_text() == "service unavailable"


def test_get_last_accessors_default_to_none_when_missing():
    use_case = SendLoteRequestUseCase(BareHttpClient(), "http://central:9000", "key-123")

    assert use_case.get_last_error() is None
    assert use_case.get_last_status_code() is None
    assert use_case.get_last_response_text() is None
