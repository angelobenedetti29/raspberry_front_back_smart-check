"""Cliente de alertas de calidad hacia el backend Go.

Cuando el `MonitorCalidad` detecta una racha de anomalías, la Pi reporta la
alerta al lote abierto con `POST /api/v1/lotes/{lote_id}/alertas-calidad`,
autenticada con el secret del dispositivo (el mismo que usa `lote/cliente.py`).
El backend decide a quién notificar y por qué canales: la Pi ya no se loguea ni
habla con los endpoints de administración de notificaciones.
"""

from __future__ import annotations

import logging
import threading

from backend.api import ClienteHTTP, ErrorConexion, ErrorFormato, ErrorHttp
from backend.config import ApiConfig, NotificacionesConfig

logger = logging.getLogger(__name__)

_TIMEOUT_HTTP = 10.0


class ClienteNotificaciones:
    """Reporta alertas de calidad del lote abierto al backend central."""

    def __init__(self, api: ApiConfig, config: NotificacionesConfig) -> None:
        self._api = api
        self._config = config
        self._http = ClienteHTTP(api.base_url, _TIMEOUT_HTTP)

    def disparar_alerta_asincrona(
        self,
        secret: str,
        lote_id: str,
        tipo_alerta: str,
        porcentaje: float,
    ) -> None:
        """Envía la alerta en un hilo daemon para no frenar el lazo del lote."""
        if not self._config.habilitado:
            return

        hilo = threading.Thread(
            target=self._enviar_seguro,
            args=(secret, lote_id, tipo_alerta, porcentaje),
            name=f"alerta-calidad-{tipo_alerta}",
            daemon=True,
        )
        hilo.start()

    def _enviar_seguro(
        self,
        secret: str,
        lote_id: str,
        tipo_alerta: str,
        porcentaje: float,
    ) -> None:
        """Envía la alerta sin dejar escapar excepciones del hilo."""
        try:
            self.enviar_alerta(secret, lote_id, tipo_alerta, porcentaje)
        except Exception:  # noqa: BLE001 - el hilo no debe morir ruidosamente
            logger.exception("Error al enviar alerta de calidad: %s", tipo_alerta)

    def enviar_alerta(
        self,
        secret: str,
        lote_id: str,
        tipo_alerta: str,
        porcentaje: float,
    ) -> bool:
        """POST /api/v1/lotes/{lote_id}/alertas-calidad. Devuelve True si el backend la aceptó.

        201 (alerta creada) y 200 con `creada=false` (ya había una activa) son
        ambos éxito. No reintenta: ante cualquier fallo loguea y descarta; la
        próxima alerta llega recién después del cooldown del monitor.
        """
        path = f"{self._api.lotes_endpoint}/{lote_id}/alertas-calidad"
        payload = {
            "tipo": tipo_alerta,
            "porcentaje": round(porcentaje, 2),
            # Enteros cuando son exactos: Python serializa 10.0 como "10.0" y un
            # decoder estricto de Go no lo acepta en un campo int (con 10 sirve
            # tanto para int como para float64).
            "umbral": _entero_si_exacto(self._config.umbral_anomalia),
            "ventana_segundos": _entero_si_exacto(self._config.ventana_segundos),
        }
        logger.info(
            "[HTTP REQUEST] POST %s | Tipo: %s | porcentaje: %.2f | umbral: %s | ventana: %ss",
            path,
            tipo_alerta,
            payload["porcentaje"],
            payload["umbral"],
            payload["ventana_segundos"],
        )
        try:
            data = self._http.peticion("POST", path, payload=payload, token=secret)
        except ErrorHttp as exc:
            logger.error("[HTTP ERROR] Status %s en POST %s | %s", exc.codigo, path, exc.detalle)
            return False
        except ErrorConexion as exc:
            logger.error("No se pudo enviar la alerta de calidad (%s): %s", path, exc.detalle)
            return False
        except ErrorFormato:
            # El backend respondió 2xx pero con un cuerpo que no es JSON: la
            # alerta se aceptó, sólo no podemos saber si fue nueva o repetida.
            logger.info("Alerta de calidad %s aceptada (respuesta sin JSON)", tipo_alerta)
            return True

        if _creada(data) is False:
            logger.info("Alerta de calidad %s: ya había una activa en el lote", tipo_alerta)
        else:
            logger.info("Alerta de calidad %s creada", tipo_alerta)
        return True


def _entero_si_exacto(valor: float) -> int | float:
    """Devuelve `valor` como int si no tiene parte decimal (10.0 -> 10)."""
    return int(valor) if float(valor).is_integer() else valor


def _creada(data: object) -> bool | None:
    """Lee `creada` de la respuesta (directo o dentro del envelope `data`)."""
    if not isinstance(data, dict):
        return None
    for fuente in (data, data.get("data")):
        if isinstance(fuente, dict) and isinstance(fuente.get("creada"), bool):
            return fuente["creada"]
    return None
