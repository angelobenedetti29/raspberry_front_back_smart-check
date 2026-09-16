"""Allowlisted logging for the generic HTTP client (no payloads/headers/bodies)."""

import requests

from backend.infrastructure.http.requests_client import RequestsHttpClient
from backend.tests.fakes import FakeResponse


def test_success_logs_only_allowlisted_metadata(monkeypatch, caplog):
    import backend.infrastructure.http.requests_client as module

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse(text="RESPONSE_SECRET")

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
        raise requests.ConnectionError("leaked http://user:pass@host?token=SECRET")

    monkeypatch.setattr(module.requests, "post", fake_post)
    client = RequestsHttpClient()

    with caplog.at_level("WARNING", logger="backend.infrastructure.http.requests_client"):
        ok = client.post("https://host.example/notify", {"x": 1})

    assert ok is False
    assert client.last_error == "ConnectionError"
    assert "SECRET" not in caplog.text
    assert "user:pass" not in caplog.text
    assert "error=ConnectionError" in caplog.text


def test_timeout_is_reported_as_uncertain(monkeypatch, caplog):
    import backend.infrastructure.http.requests_client as module

    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests.Timeout("leaked http://user:pass@host?token=SECRET")

    monkeypatch.setattr(module.requests, "post", fake_post)
    client = RequestsHttpClient()

    with caplog.at_level("WARNING", logger="backend.infrastructure.http.requests_client"):
        ok = client.post("https://host.example/notify", {"x": 1})

    assert ok is False
    assert client.last_error == module.TIMEOUT_UNCERTAIN
    assert "SECRET" not in caplog.text
    assert "user:pass" not in caplog.text
    assert "status=uncertain" in caplog.text


def test_default_timeout_exceeds_backend_signed_timeout():
    import backend.infrastructure.http.requests_client as module

    # El transporte firmado del backend usa 10 s; el cliente debe esperar más
    # para no declarar fallo mientras el central aún puede registrar el lote.
    assert module.DEFAULT_TIMEOUT_SECONDS >= 20
    assert RequestsHttpClient().timeout == module.DEFAULT_TIMEOUT_SECONDS


def test_non_2xx_exposes_safe_http_code_not_response_body(monkeypatch, caplog):
    import backend.infrastructure.http.requests_client as module

    monkeypatch.setattr(module.requests, "post", lambda *a, **k: FakeResponse(500, "INTERNAL_SECRET"))
    client = RequestsHttpClient()

    with caplog.at_level("INFO", logger="backend.infrastructure.http.requests_client"):
        ok = client.post("https://host.example/notify", {})

    assert ok is False
    assert client.last_error == "http_500"
    assert "INTERNAL_SECRET" not in caplog.text
