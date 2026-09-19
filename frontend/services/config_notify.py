"""Notificación al backend local de que ``config.json`` cambió.

El frontend guarda el archivo y luego avisa al backend para que recargue su
configuración sin necesidad de reiniciarlo. Este módulo es puro (sin Qt): el
llamador decide en qué hilo ejecutarlo.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

__all__ = ["notify_backend_reload"]

# Endpoint que implementa la recarga en el backend.
RELOAD_PATH = "/api/config/reload"
# Timeout corto: el aviso es best-effort y no debe bloquear al llamador.
DEFAULT_TIMEOUT = 1.5


def notify_backend_reload(host: str, port: int, *, timeout: float = DEFAULT_TIMEOUT):
    """Pide al backend recargar ``config.json``.

    Returns:
        ``(ok, detail)``: ``ok`` es ``True`` si el backend respondió con éxito.
        Nunca lanza: cualquier fallo de red o HTTP se devuelve como
        ``(False, motivo)``.
    """
    url = f"http://{host}:{port}{RELOAD_PATH}"
    try:
        response = requests.post(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - contrato: nunca propagar
        logger.warning("[Config] No se pudo notificar la recarga a %s: %s", url, exc)
        return False, str(exc)

    if response.ok:
        logger.info("[Config] Backend notificado de la recarga en %s", url)
        return True, f"HTTP {response.status_code}"

    logger.warning(
        "[Config] El backend rechazó la recarga en %s: HTTP %s",
        url,
        response.status_code,
    )
    return False, f"HTTP {response.status_code}"
