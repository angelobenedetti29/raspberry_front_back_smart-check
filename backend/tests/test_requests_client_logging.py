"""Allowlisted logging for the generic HTTP client (no payloads/headers/bodies)."""

import requests

from backend.infrastructure.http.requests_client import RequestsHttpClient


class FakeResponse:
    def __init__(self, status_code=200, text="RESPONSE_SECRET"):
        self.status_code = status_code
        self.text = text


def test_success_logs_only_allowlisted_metadata(monkeypatch, caplog):
    import backend.infrastructure.http.requests_client as module

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr(module.requests, "post", fake_post)
    client = RequestsHttpClient()

    with caplog.at_level("INFO", logger="backend.infrastructure.http.requests_client"):
        ok = client.post(
            "https://host.example/notify?token=QUERY_SECRET",
            {"secret": "PAYLOAD_SECRET"},
            headers={"Authorization": "Bearer HEADER_SECRET"},
        )

    assert ok is True
    assert captured["json"] == {"secret": "PAYLOAD_SECRET"}
    assert "QUERY_SECRET" not in caplog.text
    assert "PAYLOAD_SECRET" not in caplog.text
    assert "HEADER_SECRET" not in caplog.text
    assert "RESPONSE_SECRET" not in caplog.text
    assert "Authorization" not in caplog.text
    assert "path=/notify" in caplog.text
    assert "status=200" in caplog.text


def test_error_logs_only_exception_type(monkeypatch, caplog):
    import backend.infrastructure.http.requests_client as module

    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.Timeout("leaked http://user:pass@host?token=SECRET")

    monkeypatch.setattr(module.requests, "post", fake_post)
    client = RequestsHttpClient()

    with caplog.at_level("WARNING", logger="backend.infrastructure.http.requests_client"):
        ok = client.post("https://host.example/notify", {"x": 1})

    assert ok is False
    assert client.last_error == "Timeout"
    assert "SECRET" not in caplog.text
    assert "user:pass" not in caplog.text
    assert "error=Timeout" in caplog.text


def test_non_2xx_exposes_safe_http_code_not_response_body(monkeypatch, caplog):
    import backend.infrastructure.http.requests_client as module

    monkeypatch.setattr(module.requests, "post", lambda *a, **k: FakeResponse(500, "INTERNAL_SECRET"))
    client = RequestsHttpClient()

    with caplog.at_level("INFO", logger="backend.infrastructure.http.requests_client"):
        ok = client.post("https://host.example/notify", {})

    assert ok is False
    assert client.last_error == "http_500"
    assert "INTERNAL_SECRET" not in caplog.text
