"""Cliente HTTP para enviar métricas al backend (sólo stdlib: urllib)."""

from __future__ import annotations

import json
from urllib import error, request

from backend.monitor.metricas import MetricasSistema


class ErrorMetricas(Exception):
    """Error de red, HTTP o de payload al enviar métricas al backend."""

    def __init__(self, mensaje: str, codigo: int | None = None) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


class ClienteMetricas:
    """Envía la telemetría del dispositivo al endpoint de ping del backend.

    La identidad se resuelve con el secret del dispositivo (el backend deriva
    el device_id a partir de él), enviado como `Authorization: Bearer`.
    """

    def __init__(self, base_url: str, endpoint: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._endpoint = "/" + endpoint.strip("/")
        self._timeout = timeout

    def enviar(self, secret: str, metricas: MetricasSistema) -> None:
        """POST de las métricas. Lanza ErrorMetricas si el backend no acepta."""
        if not secret:
            raise ErrorMetricas(
                "No hay secret de dispositivo para autenticar el envío"
            )
        cuerpo = json.dumps(metricas.a_payload()).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{self._base_url}{self._endpoint}"
        req = request.Request(url, data=cuerpo, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self._timeout) as resp:
                resp.read()
        except error.HTTPError as exc:
            raise ErrorMetricas(
                f"El backend respondió {exc.code} al enviar métricas: "
                f"{_leer_detalle(exc)}",
                codigo=exc.code,
            ) from exc
        except error.URLError as exc:
            raise ErrorMetricas(
                f"No se pudo contactar el backend al enviar métricas: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise ErrorMetricas(f"Error de red al enviar métricas: {exc}") from exc


def _leer_detalle(exc: error.HTTPError) -> str:
    """Intenta leer el cuerpo del error HTTP para dar contexto."""
    try:
        raw = exc.read()
        return raw.decode("utf-8", errors="replace")[:200] if raw else "(sin cuerpo)"
    except Exception:
        return "(cuerpo ilegible)"
