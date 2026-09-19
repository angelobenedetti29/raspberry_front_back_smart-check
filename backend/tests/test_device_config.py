"""Configuración del backend desde el esquema unificado (sin env ni lru_cache)."""

from __future__ import annotations

import pytest

from smartcheck_config import AppConfig, Secrets

from backend.app import config
from backend.app.config import Settings, get_settings, settings_from_config
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


def test_normalize_api_base_url_rejects_cleartext_http_to_non_loopback():
    for value in (
        "http://central.example.com",
        "http://192.168.1.10:8080",
        "http://0.0.0.0:8000",
    ):
        with pytest.raises(ConfigurationError):
            normalize_api_base_url(value)


def test_settings_normalizer_degrades_invalid_to_empty():
    assert config._normalize_base_url(None) == ""
    assert config._normalize_base_url("") == ""
    with pytest.raises(ConfigurationError):
        config._normalize_base_url("central.example.com")


def test_settings_from_config_maps_unified_schema():
    app_config = AppConfig.from_dict(
        {
            "revision": 7,
            "stream": {
                "inference": {
                    "enabled": True,
                    "model_path": "ai_training/models/m.hef",
                    "labels_path": "ai_training/models/m.names",
                    "confidence_threshold": 0.75,
                }
            },
            "device": {
                "api_base_url": "https://central.example.com",
                "auth_audience": "https://central.example.com/api/v1",
                "horno_id": "horno-1",
                "producto_id": "prod-1",
                "ping_interval_seconds": 5.0,
            },
            "api": {"host": "127.0.0.1", "port": 9001},
        }
    )
    secrets = Secrets(enrollment_code="secreto", identity_dir="/tmp/identidad")

    settings = settings_from_config(app_config, secrets)

    assert isinstance(settings, Settings)
    assert settings.device_api_base_url == "https://central.example.com/api/v1"
    assert settings.device_auth_audience == "https://central.example.com/api/v1"
    assert settings.device_identity_dir == "/tmp/identidad"
    assert settings.horno_id == "horno-1"
    assert settings.default_producto_id == "prod-1"
    assert settings.ping_interval_seconds == 5.0
    assert settings.inference_enabled is True
    assert settings.require_hailo is False
    assert settings.inference_model_path == "ai_training/models/m.hef"
    assert settings.inference_labels_path == "ai_training/models/m.names"
    assert settings.inference_confidence_threshold == 0.75
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 9001
    assert settings.config_revision == 7


def test_identity_dir_viene_de_secretos_y_no_de_config():
    app_config = AppConfig.from_dict({"device": {"horno_id": "h"}})
    settings = settings_from_config(
        app_config, Secrets(identity_dir="/var/lib/smart-check/device")
    )
    assert settings.device_identity_dir == "/var/lib/smart-check/device"
    assert not hasattr(app_config.device, "identity_dir")


def test_invalid_api_base_url_degrades_to_empty(caplog):
    app_config = AppConfig.from_dict(
        {"device": {"api_base_url": "central.example.com"}}
    )
    with caplog.at_level("WARNING", logger="backend.app.config"):
        settings = settings_from_config(app_config, Secrets())
    assert settings.device_api_base_url == ""
    assert "device.api_base_url inválida" in caplog.text


def test_api_host_default_es_loopback():
    """M4: el backend no debe escuchar en 0.0.0.0 por defecto."""
    assert Settings().api_host == "127.0.0.1"
    assert settings_from_config(AppConfig.from_dict({}), Secrets()).api_host == (
        "127.0.0.1"
    )


def test_get_settings_tiene_defaults_y_no_cachea():
    # El acceso directo sin runtime usa el fallback pero sigue siendo una
    # función plana, sin ``lru_cache``.
    assert not hasattr(get_settings, "cache_clear")
    defaults = Settings()
    assert defaults.device_api_base_url == ""
    assert defaults.inference_enabled is False
    assert defaults.config_revision == 1
