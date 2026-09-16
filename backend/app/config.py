import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from device_enrollment.errors import ConfigurationError
from device_enrollment.urls import normalize_api_base_url

DEFAULT_IDENTITY_DIR = "/var/lib/smart-check/device"


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# Load backend/.env as early as possible so every consumer sees the values.
load_env_file(Path(__file__).resolve().parents[1] / ".env")


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
    """Normaliza la URL base del dispositivo; vacía si no está configurada."""
    if not raw:
        return ""
    try:
        return normalize_api_base_url(raw)
    except ConfigurationError:
        # Tolerate legacy scheme-less values (a bare host), but never downgrade
        # an http(s) URL that failed policy — e.g. plaintext http:// to a
        # non-loopback host — into a usable base URL.
        if raw.strip().lower().startswith(("http://", "https://")):
            raise
        return raw.strip().rstrip("/")


@dataclass(frozen=True)
class Settings:
    device_api_base_url: str = ""
    device_auth_audience: str = ""
    device_identity_dir: str = DEFAULT_IDENTITY_DIR
    horno_id: str = ""
    default_producto_id: str = ""
    ping_interval_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings(
        device_api_base_url=_normalize_base_url(os.getenv("DEVICE_API_BASE_URL")),
        device_auth_audience=os.getenv("DEVICE_AUTH_AUDIENCE") or "",
        device_identity_dir=os.getenv("DEVICE_IDENTITY_DIR") or DEFAULT_IDENTITY_DIR,
        horno_id=os.getenv("HORNO_ID") or os.getenv("CENTRAL_HORNO_ID") or "",
        default_producto_id=(
            os.getenv("PRODUCTO_ID") or os.getenv("CENTRAL_PRODUCTO_ID") or ""
        ),
        ping_interval_seconds=_parse_ping_interval(os.getenv("PING_INTERVAL_SECONDS")),
    )
