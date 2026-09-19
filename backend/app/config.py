"""Snapshot inmutable de configuración del runtime del backend.

La fuente de verdad es ``config.json`` (raíz del repositorio), cargado por
``smartcheck_config``. Este módulo solo traduce ese esquema unificado (más los
secretos resueltos desde ``.env``) al ``Settings`` plano que consumen los
routers y el resto del backend. No lee variables de entorno para *valores* de
configuración: la única variable relevante es la ruta de bootstrap, resuelta
por ``smartcheck_config.paths``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from device_enrollment import DEFAULT_IDENTITY_DIR
from device_enrollment.errors import ConfigurationError
from device_enrollment.urls import normalize_api_base_url
from smartcheck_config import AppConfig, Secrets

logger = logging.getLogger(__name__)


def _normalize_base_url(raw: str | None) -> str:
    """Normaliza ``device.api_base_url``; devuelve "" si está ausente.

    Lanza ``ConfigurationError`` si el valor está presente pero no es una URL
    http(s) válida. ``settings_from_config`` captura el error y degrada, de modo
    que un valor inválido deja el envío firmado y la telemetría deshabilitados en
    lugar de romper el arranque.
    """
    if not raw:
        return ""
    return normalize_api_base_url(raw)


@dataclass(frozen=True)
class Settings:
    """Configuración inmutable del backend, resuelta desde ``config.json``."""

    device_api_base_url: str = ""
    device_auth_audience: str = ""
    device_identity_dir: str = DEFAULT_IDENTITY_DIR
    horno_id: str = ""
    default_producto_id: str = ""
    ping_interval_seconds: float = 10.0

    inference_enabled: bool = False
    require_hailo: bool = False
    inference_model_path: str | None = None
    inference_labels_path: str | None = None
    inference_confidence_threshold: float = 0.6

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    config_revision: int = 1


def settings_from_config(app_config: AppConfig, secrets: Secrets) -> Settings:
    """Traduce ``AppConfig`` + ``Secrets`` al ``Settings`` plano del backend.

    ``identity_dir`` sale exclusivamente de ``secrets``; el esquema unificado no
    lo contiene. Una ``api_base_url`` inválida se degrada a "" con un warning.
    """
    try:
        base_url = _normalize_base_url(app_config.device.api_base_url)
    except ConfigurationError as exc:
        logger.warning(
            "device.api_base_url inválida: %s. "
            "El envío firmado y la telemetría quedan deshabilitados.",
            exc,
        )
        base_url = ""

    inference = app_config.stream.inference
    return Settings(
        device_api_base_url=base_url,
        device_auth_audience=app_config.device.auth_audience or "",
        device_identity_dir=secrets.identity_dir or DEFAULT_IDENTITY_DIR,
        horno_id=app_config.device.horno_id,
        default_producto_id=app_config.device.producto_id,
        ping_interval_seconds=app_config.device.ping_interval_seconds,
        inference_enabled=inference.enabled,
        require_hailo=inference.require_hailo,
        inference_model_path=inference.model_path,
        inference_labels_path=inference.labels_path,
        inference_confidence_threshold=inference.confidence_threshold,
        api_host=app_config.api.host,
        api_port=app_config.api.port,
        config_revision=app_config.revision,
    )


# ---------------------------------------------------------------------------
# Punto de acceso al snapshot vivo del runtime
# ---------------------------------------------------------------------------

_settings_provider: Callable[[], Settings] | None = None
_fallback_store = None


def set_settings_provider(provider: Callable[[], Settings]) -> None:
    """Registra el proveedor del snapshot actual (lo inyecta ``dependencies``)."""
    global _settings_provider
    _settings_provider = provider


def get_settings() -> Settings:
    """Devuelve el snapshot actual del runtime.

    Se usa como dependencia FastAPI (los tests lo sobreescriben por nombre) y
    también para llamadas directas. Delega en el contenedor de dependencias; si
    el contenedor aún no se importó, cae a un ``ConfigStore`` perezoso para no
    romper consumidores tempranos.
    """
    if _settings_provider is not None:
        return _settings_provider()

    global _fallback_store
    if _fallback_store is None:
        from backend.app.config_store import ConfigStore

        _fallback_store = ConfigStore()
    return _fallback_store.get()
