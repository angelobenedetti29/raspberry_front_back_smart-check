"""Cliente HTTP para enviar métricas al backend (usa la capa compartida backend.api)."""

from __future__ import annotations

from backend.api import ClienteHTTP, ErrorConexion, ErrorHttp
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
        self._http = ClienteHTTP(base_url, timeout)
        self._endpoint = "/" + endpoint.strip("/")

    def enviar(self, secret: str, metricas: MetricasSistema) -> None:
        """POST de las métricas. Lanza ErrorMetricas si el backend no acepta."""
        if not secret:
            raise ErrorMetricas(
                "No hay secret de dispositivo para autenticar el envío"
            )
        try:
            self._http.peticion(
                "POST",
                self._endpoint,
                payload=metricas.a_payload(),
                token=secret,
                parsear=False,
            )
        except ErrorHttp as exc:
            raise ErrorMetricas(
                f"El backend respondió {exc.codigo} al enviar métricas: {exc.detalle}",
                codigo=exc.codigo,
            ) from exc
        except ErrorConexion as exc:
            raise ErrorMetricas(
                f"No se pudo contactar el backend al enviar métricas: {exc.detalle}"
            ) from exc
