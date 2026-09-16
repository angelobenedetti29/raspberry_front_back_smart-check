import logging
import time
from typing import Any, Dict
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)


def _safe_path(url: str) -> str:
    """Ruta sin query para logs; nunca expone credenciales de la URL."""
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return "/"


class RequestsHttpClient:
    """Cliente HTTP genérico con logging allowlisted (sin payloads ni headers)."""

    def __init__(self, timeout: int = 5):
        self.timeout = timeout
        self.last_error = None

    def post(self, url: str, payload: Dict[str, Any], headers: Dict[str, str] = None) -> bool:
        try:
            self.last_error = None
            started = time.monotonic()
            response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "http_post path=%s status=%s duration_ms=%d",
                _safe_path(url),
                response.status_code,
                elapsed_ms,
            )
            if response.status_code in [200, 201, 202, 204]:
                return True

            self.last_error = f"http_{response.status_code}"
            return False
        except requests.RequestException as exc:
            # Solo el tipo de excepción: nunca el mensaje, que puede contener
            # URLs con credenciales o fragmentos del payload.
            self.last_error = type(exc).__name__
            logger.warning("http_post path=%s status=error error=%s", _safe_path(url), self.last_error)
            return False
