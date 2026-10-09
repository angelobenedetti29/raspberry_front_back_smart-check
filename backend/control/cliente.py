"""Cliente HTTP del subsistema de control contra el backend Go (SCA-400).

Endpoints consumidos:
  - POST /api/v1/lotes/{id}/metricas-horno        (Bearer dispositivo)
  - GET  /api/v1/lotes/{id}/consigna-propuesta    (Bearer dispositivo)
  - POST /api/v1/lotes/{id}/aplicar-consigna      (Bearer dispositivo)

Las URLs se arman desde la plantilla del endpoint y el id del lote, igual
que en backend.lote.cliente y backend.device.cliente. La capa HTTP
compartida (backend.api) se ocupa de serializar y mapear errores.
"""

from __future__ import annotations

from backend.api import (
    ClienteHTTP,
    ErrorConexion,
    ErrorFormato,
    ErrorHttp,
    desenvolver,
)
from backend.control.tipos import ErrorControl, ModoControl, SetpointPropuesto


class ClienteControl:
    """Habla contra el backend para telemetría y consignas del control."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 5.0,
    ) -> None:
        self._http = ClienteHTTP(base_url, timeout)

    def publicar_telemetria(
        self,
        secret: str,
        lote_id: str,
        endpoint_plantilla: str,
        payload: dict,
    ) -> None:
        """POST /api/v1/lotes/{id}/metricas-horno con las lecturas del horno.

        El endpoint se resuelve desde la plantilla del config (`{id}` → lote_id).
        Un 409 (lote no abierto) se traduce a ErrorControl sin reintento:
        típicamente la Pi ya abrió el lote pero el operador lo cerró.
        """
        if not secret:
            raise ErrorControl("No hay secret de dispositivo para autenticar")
        path = endpoint_plantilla.format(id=lote_id)
        try:
            self._http.peticion("POST", path, payload=payload, token=secret, parsear=False)
        except ErrorHttp as exc:
            raise ErrorControl(
                f"El backend rechazó la telemetría ({exc.codigo}): {exc.detalle}",
                codigo=exc.codigo,
            ) from exc
        except ErrorConexion as exc:
            raise ErrorControl(
                f"No se pudo publicar telemetría al backend: {exc.detalle}"
            ) from exc
        except ErrorFormato as exc:
            raise ErrorControl(str(exc)) from exc

    def obtener_propuesta(
        self,
        secret: str,
        lote_id: str,
        endpoint_plantilla: str,
    ) -> SetpointPropuesto | None:
        """GET /api/v1/lotes/{id}/consigna-propuesta.

        Devuelve None cuando el backend responde "no hay propuesta pendiente"
        (204 o payload con propuesta=null). En modo MANUAL nunca hay
        propuestas.
        """
        if not secret:
            raise ErrorControl("No hay secret de dispositivo para autenticar")
        path = endpoint_plantilla.format(id=lote_id)
        try:
            data = self._http.peticion("GET", path, token=secret)
        except ErrorHttp as exc:
            if exc.codigo == 204:
                return None
            raise ErrorControl(
                f"El backend rechazó la consulta de propuesta ({exc.codigo}): {exc.detalle}",
                codigo=exc.codigo,
            ) from exc
        except ErrorConexion as exc:
            raise ErrorControl(
                f"No se pudo consultar propuesta al backend: {exc.detalle}"
            ) from exc
        except ErrorFormato as exc:
            raise ErrorControl(str(exc)) from exc
        if data is None:
            return None
        try:
            payload = desenvolver(data)
        except ErrorFormato as exc:
            raise ErrorControl(str(exc)) from exc
        if not isinstance(payload, dict):
            return None
        # El backend puede devolver {propuesta: null} cuando no hay nada.
        propuesta = payload.get("propuesta")
        if not isinstance(propuesta, dict):
            return None
        try:
            return _a_setpoint(propuesta)
        except (KeyError, TypeError, ValueError) as exc:
            raise ErrorControl(f"Propuesta mal formada: {exc}") from exc

    def aplicar_consigna(
        self,
        secret: str,
        lote_id: str,
        endpoint_plantilla: str,
        body: dict,
    ) -> None:
        """POST /api/v1/lotes/{id}/aplicar-consigna (ack del despacho)."""
        if not secret:
            raise ErrorControl("No hay secret de dispositivo para autenticar")
        path = endpoint_plantilla.format(id=lote_id)
        try:
            self._http.peticion(
                "POST", path, payload=body, token=secret, parsear=False
            )
        except ErrorHttp as exc:
            raise ErrorControl(
                f"El backend rechazó la aplicación de consigna ({exc.codigo}): {exc.detalle}",
                codigo=exc.codigo,
            ) from exc
        except ErrorConexion as exc:
            raise ErrorControl(
                f"No se pudo aplicar la consigna al backend: {exc.detalle}"
            ) from exc
        except ErrorFormato as exc:
            raise ErrorControl(str(exc)) from exc

    def cambiar_modo(
        self,
        secret: str,
        lote_id: str,
        endpoint_plantilla: str,
        modo: ModoControl,
    ) -> None:
        """PUT /api/v1/lotes/{id}/modo — sincroniza el modo del lote con el backend.

        El operador cambia el modo desde la UI de Horno y Cinta; la Pi lo
        empuja al backend Go para que otras Pis del sector lo vean en su
        próximo /lotes/abierto. Si falla, el caller hace log y mantiene el
        cambio local; el backend reconcilia después.
        """
        if not secret:
            raise ErrorControl("No hay secret de dispositivo para autenticar")
        path = endpoint_plantilla.format(id=lote_id)
        try:
            self._http.peticion(
                "PUT",
                path,
                payload={"modo": modo.value},
                token=secret,
                parsear=False,
            )
        except ErrorHttp as exc:
            raise ErrorControl(
                f"El backend rechazó el cambio de modo ({exc.codigo}): {exc.detalle}",
                codigo=exc.codigo,
            ) from exc
        except ErrorConexion as exc:
            raise ErrorControl(
                f"No se pudo enviar cambio de modo al backend: {exc.detalle}"
            ) from exc
        except ErrorFormato as exc:
            raise ErrorControl(str(exc)) from exc


def _a_setpoint(payload: dict) -> SetpointPropuesto:
    """Construye un SetpointPropuesto desde el dict que devuelve el backend.

    El backend expone los campos con nombres en camelCase; acá se mantienen
    en snake_case para no contaminar el resto del código Python con camel.
    """
    t1 = _float(payload, "temperaturaObjetivo")
    vel = _float(payload, "velocidadCintaObjetivo")
    t2 = payload.get("tempHorno2Objetivo")
    aplicado_por = str(payload.get("aplicadoPor", "MPC"))
    ventana = payload.get("ventana")
    decision_id_raw = payload.get("decisionId")
    decision_id = str(decision_id_raw) if decision_id_raw is not None else None
    return SetpointPropuesto(
        temp_horno_1=t1,
        temp_horno_2=float(t2) if t2 is not None else None,
        velocidad_cinta=vel,
        aplicado_por=aplicado_por,
        ventana=str(ventana) if ventana is not None else None,
        decision_id=decision_id,
    )


def _float(payload: dict, clave: str) -> float:
    valor = payload.get(clave)
    if valor is None:
        raise KeyError(f"falta {clave}")
    return float(valor)