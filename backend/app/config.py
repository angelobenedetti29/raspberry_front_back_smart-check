import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from device_enrollment import DEFAULT_IDENTITY_DIR
from device_enrollment.envfile import load_env_values
from device_enrollment.errors import ConfigurationError
from device_enrollment.urls import normalize_api_base_url

logger = logging.getLogger(__name__)

# Ruta del .env del backend. Se resuelve en cada llamada a load_env_file para
# que los tests puedan redirigirla con monkeypatch.
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_env_file(env_path: Path | None = None) -> None:
    """Aplica las asignaciones de ``env_path`` a ``os.environ``.

    Usa el parser compartido con la CLI (``load_env_values``) y nunca pisa una
    variable ya definida: el entorno explícito (systemd, shell) tiene prioridad
    sobre ``backend/.env``. No hace nada si el archivo no existe; si no se puede
    leer, loguea un warning y continúa.
    """
    path = ENV_FILE if env_path is None else env_path
    if path is None or not path.exists():
        return

    try:
        values = load_env_values(path)
    except OSError as exc:
        logger.warning("No se pudo leer %s: %s", path, exc)
        return

    for key, value in values.items():
        os.environ.setdefault(key, value)


def _parse_ping_interval(raw: str | None, default: float = 10.0) -> float:
    """Parsea PING_INTERVAL_SECONDS; usa el default si es inválido o <= 0."""
    try:
        value = float(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return value


def _normalize_base_url(raw: str | None) -> str:
    """Normaliza DEVICE_API_BASE_URL; devuelve "" si está ausente.

    Lanza ``ConfigurationError`` si el valor está presente pero no es una URL
    http(s) válida. Los valores sin esquema (p. ej. "central.example.com") ya no
    se toleran: antes se devolvían tal cual y fallaban en runtime con
    ``MissingSchema``. ``get_settings`` captura el error y degrada.
    """
    if not raw:
        return ""
    return normalize_api_base_url(raw)


@dataclass(frozen=True)
class Settings:
    """Configuración inmutable del backend, resuelta una vez por proceso."""

    device_api_base_url: str = ""
    device_auth_audience: str = ""
    device_identity_dir: str = DEFAULT_IDENTITY_DIR
    horno_id: str = ""
    default_producto_id: str = ""
    ping_interval_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    """Construye los Settings una vez por proceso (``lru_cache``).

    Carga ``backend/.env`` (sin pisar el entorno) y degrada a "" si la URL base
    está presente pero es inválida: la app arranca con transporte firmado y
    telemetría deshabilitados en lugar de fallar durante el import.
    """
    load_env_file(ENV_FILE)

    try:
        base_url = _normalize_base_url(os.getenv("DEVICE_API_BASE_URL"))
    except ConfigurationError as exc:
        logger.warning(
            "DEVICE_API_BASE_URL inválida: %s. "
            "El envío firmado y la telemetría quedan deshabilitados.",
            exc,
        )
        base_url = ""

    return Settings(
        device_api_base_url=base_url,
        device_auth_audience=os.getenv("DEVICE_AUTH_AUDIENCE") or "",
        device_identity_dir=os.getenv("DEVICE_IDENTITY_DIR") or DEFAULT_IDENTITY_DIR,
        horno_id=os.getenv("HORNO_ID") or "",
        default_producto_id=os.getenv("PRODUCTO_ID") or "",
        ping_interval_seconds=_parse_ping_interval(os.getenv("PING_INTERVAL_SECONDS")),
    )
