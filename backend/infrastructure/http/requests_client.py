import logging
import time
from typing import Any, Dict
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

# Timeout por defecto holgado: debe superar el del transporte firmado del
# backend (10 s en ``device_enrollment/transport.py``) para no declarar fallo
# mientras el central todavía puede estar registrando el lote.
DEFAULT_TIMEOUT_SECONDS = 20
# Marca de ``last_error`` para timeouts: el resultado es incierto, no un fallo
# confirmado, porque el lote pudo haberse registrado en el servidor.
TIMEOUT_UNCERTAIN = "timeout_sin_confirmar"


def _safe_path(url: str) -> str:
    """Ruta sin query para logs; nunca expone credenciales de la URL."""
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return "/"


class RequestsHttpClient:
    """Cliente HTTP genérico con logging allowlisted (sin payloads ni headers).

    No es thread-safe: el estado ``last_error`` es mutable y compartido por todas
    las llamadas de la instancia. La responsabilidad de serializar los ``post``
    recae en el llamador (en el frontend, ``frontend/app.py`` usa un
    ``QThreadPool`` con un solo hilo para los POST de lote). Si se llamara a
    ``post`` de forma concurrente, cada request podría leer o pisar el
    ``last_error`` de otro.
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        """Inicializa el cliente con un timeout en segundos para cada request.

        El valor por defecto supera el timeout firmado del backend para evitar
        falsos negativos. ``last_error`` guarda el motivo del último fallo
        (código ``http_*``, ``TIMEOUT_UNCERTAIN`` o el tipo de excepción) para
        que el llamador pueda consultarlo, inmediatamente después de su propio
        ``post`` y siempre desde el mismo hilo que lo ejecutó.
        """
        self.timeout = timeout
        self.last_error = None

    def post(self, url: str, payload: Dict[str, Any], headers: Dict[str, str] = None) -> bool:
        """Envía ``payload`` como JSON por POST y devuelve si fue exitoso.

        Se consideran exitosos los estados 200, 201, 202 y 204. Ante un estado de
        error guarda ``http_<código>`` en ``last_error``. Un timeout se reporta
        como ``TIMEOUT_UNCERTAIN`` (resultado incierto, el lote pudo registrarse)
        y cualquier otra excepción de red guarda el nombre del tipo. Nunca lanza:
        devuelve ``False``.
        """
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
        except requests.Timeout:
            # El central pudo haber procesado el request antes de agotar el
            # timeout: no es un fallo confirmado.
            self.last_error = TIMEOUT_UNCERTAIN
            logger.warning(
                "http_post path=%s status=uncertain error=%s",
                _safe_path(url),
                self.last_error,
            )
            return False
        except requests.RequestException as exc:
            # Solo el tipo de excepción: nunca el mensaje, que puede contener
            # URLs con credenciales o fragmentos del payload.
            self.last_error = type(exc).__name__
            logger.warning("http_post path=%s status=error error=%s", _safe_path(url), self.last_error)
            return False
