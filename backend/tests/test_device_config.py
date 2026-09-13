"""Config normalization and legacy-alias tests (no API key fallback)."""

import pytest

from device_enrollment.errors import ConfigurationError
from device_enrollment.urls import normalize_api_base_url


def test_normalize_api_base_url_appends_api_suffix():
    assert normalize_api_base_url("https://h.example") == "https://h.example/api/v1"
    assert normalize_api_base_url("https://h.example/") == "https://h.example/api/v1"
    assert normalize_api_base_url("https://h.example/api/v1/") == "https://h.example/api/v1"
    assert normalize_api_base_url("http://localhost:9000/prefix") == (
        "http://localhost:9000/prefix/api/v1"
    )


def test_normalize_api_base_url_rejects_empty_and_non_http():
    with pytest.raises(ConfigurationError):
        normalize_api_base_url("")
    with pytest.raises(ConfigurationError):
        normalize_api_base_url("api.example.test")


def test_normalize_api_base_url_allows_https_anywhere():
    assert normalize_api_base_url("https://central.example.com/api/v1") == (
        "https://central.example.com/api/v1"
    )
    assert normalize_api_base_url("HTTPS://Central.Example.COM") == (
        "HTTPS://Central.Example.COM/api/v1"
    )


def test_normalize_api_base_url_allows_http_only_on_literal_loopback():
    assert normalize_api_base_url("http://127.0.0.1:8000") == (
        "http://127.0.0.1:8000/api/v1"
    )
    assert normalize_api_base_url("http://localhost:9000/prefix") == (
        "http://localhost:9000/prefix/api/v1"
    )
    assert normalize_api_base_url("http://[::1]:8000") == "http://[::1]:8000/api/v1"


def test_normalize_api_base_url_rejects_cleartext_http_to_non_loopback():
    for value in (
        "http://central.example.com",
        "http://192.168.1.10:8080",
        "http://127.0.0.1.evil.example",
        "http://localhost.evil.example",
        "http://0.0.0.0:8000",
    ):
        with pytest.raises(ConfigurationError):
            normalize_api_base_url(value)


def test_settings_normalizer_does_not_downgrade_insecure_http():
    from backend.app import config

    with pytest.raises(ConfigurationError):
        config._normalize_base_url("http://central.example.com")
    # Loopback http remains the development escape hatch.
    assert config._normalize_base_url("http://localhost:9000") == (
        "http://localhost:9000/api/v1"
    )
    # Scheme-less legacy values keep their pre-existing tolerance.
    assert config._normalize_base_url("central.example.com") == "central.example.com"



def test_settings_accept_legacy_central_base_url(monkeypatch):
    from backend.app import config

    for name in (
        "DEVICE_API_BASE_URL",
        "DEVICE_AUTH_AUDIENCE",
        "DEVICE_IDENTITY_DIR",
        "DISPOSITIVO_ID",
        "CENTRAL_DISPOSITIVO_ID",
        "CENTRAL_BASE_URL",
        "CENTRAL_LOTE_BASE_URL",
        "CENTRAL_LOTES_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CENTRAL_BASE_URL", "https://legacy.example")
    monkeypatch.setenv("DEVICE_AUTH_AUDIENCE", "https://legacy.example/api/v1")
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.device_api_base_url == "https://legacy.example/api/v1"
        assert settings.central_base_url == "https://legacy.example/api/v1"
        assert settings.device_auth_audience == "https://legacy.example/api/v1"
    finally:
        config.get_settings.cache_clear()


def test_settings_have_no_shared_api_key_field(monkeypatch):
    from backend.app import config

    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert not hasattr(settings, "central_api_key")
        assert not hasattr(settings, "central_lotes_api_key")
    finally:
        config.get_settings.cache_clear()
