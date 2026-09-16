"""Config normalization tests (no API key fallback)."""

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


def test_settings_normalizer_rejects_insecure_and_schemeless():
    from backend.app import config

    with pytest.raises(ConfigurationError):
        config._normalize_base_url("http://central.example.com")
    # Loopback http remains the development escape hatch.
    assert config._normalize_base_url("http://localhost:9000") == (
        "http://localhost:9000/api/v1"
    )
    # Scheme-less values are invalid: no legacy tolerance.
    with pytest.raises(ConfigurationError):
        config._normalize_base_url("central.example.com")



def test_settings_use_device_api_base_url_and_ignore_legacy_alias(monkeypatch):
    from backend.app import config

    for name in (
        "DEVICE_API_BASE_URL",
        "DEVICE_AUTH_AUDIENCE",
        "DEVICE_IDENTITY_DIR",
        "CENTRAL_BASE_URL",
        "CENTRAL_LOTE_BASE_URL",
        "CENTRAL_LOTES_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEVICE_API_BASE_URL", "https://central.example")
    # El alias legacy ya no debe influir en la URL del dispositivo.
    monkeypatch.setenv("CENTRAL_BASE_URL", "https://legacy.example")
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.device_api_base_url == "https://central.example/api/v1"
        assert not hasattr(settings, "central_base_url")
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


@pytest.mark.parametrize("invalid_url", ["central.example.com", "http://central.example.com"])
def test_settings_degrade_invalid_base_url_to_empty(monkeypatch, caplog, invalid_url):
    from backend.app import config

    monkeypatch.setattr(config, "ENV_FILE", None)  # hermético: no leer un .env real
    monkeypatch.setenv("DEVICE_API_BASE_URL", invalid_url)
    config.get_settings.cache_clear()
    try:
        with caplog.at_level("WARNING", logger="backend.app.config"):
            settings = config.get_settings()
        assert settings.device_api_base_url == ""
        assert "DEVICE_API_BASE_URL inválida" in caplog.text
    finally:
        config.get_settings.cache_clear()


def test_settings_missing_env_file_is_noop(monkeypatch, tmp_path):
    from backend.app import config

    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.delenv("DEVICE_API_BASE_URL", raising=False)
    config.get_settings.cache_clear()
    try:
        assert config.get_settings().device_api_base_url == ""
    finally:
        config.get_settings.cache_clear()


def test_settings_read_env_file_with_shared_parser(monkeypatch, tmp_path):
    from backend.app import config

    env_file = tmp_path / "backend.env"
    env_file.write_text(
        "# comentario\n"
        "export DEVICE_API_BASE_URL=https://env.example/api/v1\n"
        "HORNO_ID=horno-env\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.delenv("DEVICE_API_BASE_URL", raising=False)
    monkeypatch.delenv("HORNO_ID", raising=False)
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.device_api_base_url == "https://env.example/api/v1"
        assert settings.horno_id == "horno-env"
    finally:
        config.get_settings.cache_clear()


def test_explicit_env_wins_over_env_file(monkeypatch, tmp_path):
    from backend.app import config

    env_file = tmp_path / "backend.env"
    env_file.write_text(
        "DEVICE_API_BASE_URL=https://file.example/api/v1\n", encoding="utf-8"
    )
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.setenv("DEVICE_API_BASE_URL", "https://process.example/api/v1")
    config.get_settings.cache_clear()
    try:
        assert (
            config.get_settings().device_api_base_url
            == "https://process.example/api/v1"
        )
    finally:
        config.get_settings.cache_clear()


def test_unreadable_env_file_degrades_with_warning(monkeypatch, caplog, tmp_path):
    from backend.app import config

    env_file = tmp_path / "backend.env"
    env_file.write_text(
        "DEVICE_API_BASE_URL=https://env.example/api/v1\n", encoding="utf-8"
    )
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.delenv("DEVICE_API_BASE_URL", raising=False)

    def boom(_path):
        raise OSError("permiso denegado")

    monkeypatch.setattr(config, "load_env_values", boom)
    config.get_settings.cache_clear()
    try:
        with caplog.at_level("WARNING", logger="backend.app.config"):
            settings = config.get_settings()
        assert settings.device_api_base_url == ""
        assert "No se pudo leer" in caplog.text
    finally:
        config.get_settings.cache_clear()
