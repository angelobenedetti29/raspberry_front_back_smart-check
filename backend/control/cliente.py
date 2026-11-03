"""Cliente HTTP del subsistema de control contra el backend Go (SCA-400).

Endpoints consumidos:
  - POST /api/v1/lotes/{id}/metricas-horno        (Bearer dispositivo)
  - GET  /api/v1/lotes/{id}/consigna-propuesta    (Bearer dispositivo)
  - POST /api/v1/lotes/{id}/aprobar-consigna      (Bearer dispositivo; alias /aplicar-consigna)
  - POST /api/v1/lotes/{id}/consigna/ack          (Bearer dispositivo)

Las URLs se arman desde la plantilla del endpoint y el id del lote, igual
que en backend.lote.cliente y backend.device.cliente. La capa HTTP
compartida (backend.api) se ocupa de serializar y mapear errores.
"""

from __future__ import annotations

import math

from backend.api import (
    ClienteHTTP,
    ErrorConexion,
    ErrorFormato,
    ErrorHttp,
    desenvolver,
)
from backend.control.tipos import (
    ErrorControl,
    LimitesAplicacion,
    ModoControl,
    SetpointPropuesto,
)


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
        # Contrato actual: los campos vienen directo en `data`. Se mantiene
        # el sobre legacy `{propuesta: {...}}` para backends viejos.
        if "propuesta" in payload:
            propuesta = payload.get("propuesta")
            if propuesta is None:
                return None
            if not isinstance(propuesta, dict):
                raise ErrorControl("Propuesta mal formada: propuesta no es objeto")
            payload = propuesta
        if not any(
            clave in payload
            for clave in ("temp1_objetivo", "temp1", "temperaturaObjetivo")
        ):
            raise ErrorControl("Propuesta mal formada: falta temp1_objetivo")
        try:
            return _a_setpoint(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise ErrorControl(f"Propuesta mal formada: {exc}") from exc

    def aprobar_consigna(
        self,
        secret: str,
        lote_id: str,
        endpoint_plantilla: str,
        body: dict | None = None,
    ) -> None:
        """POST /api/v1/lotes/{id}/aprobar-consigna (aprobación del operario).

        El backend sólo registra la aprobación de una propuesta SUPERVISION;
        no aplica el setpoint. La aplicación real la confirma la Pi con
        `ack_consigna`. `body` puede llevar `decision_id` para que el backend
        rechace la aprobación si la propuesta vigente cambió.
        """
        if not secret:
            raise ErrorControl("No hay secret de dispositivo para autenticar")
        path = endpoint_plantilla.format(id=lote_id)
        try:
            self._http.peticion(
                "POST", path, payload=body, token=secret, parsear=False
            )
        except ErrorHttp as exc:
            raise ErrorControl(
                f"El backend rechazó la aprobación de consigna ({exc.codigo}): {exc.detalle}",
                codigo=exc.codigo,
            ) from exc
        except ErrorConexion as exc:
            raise ErrorControl(
                f"No se pudo aprobar la consigna en el backend: {exc.detalle}"
            ) from exc
        except ErrorFormato as exc:
            raise ErrorControl(str(exc)) from exc

    def aplicar_consigna(
        self,
        secret: str,
        lote_id: str,
        endpoint_plantilla: str,
        body: dict | None = None,
    ) -> None:
        """Alias legacy de `aprobar_consigna`; el body se ignora."""
        self.aprobar_consigna(secret, lote_id, endpoint_plantilla)

    def ack_consigna(
        self,
        secret: str,
        lote_id: str,
        endpoint_plantilla: str,
        body: dict,
    ) -> None:
        """POST /api/v1/lotes/{id}/consigna/ack (confirmación de aplicación)."""
        if not secret:
            raise ErrorControl("No hay secret de dispositivo para autenticar")
        path = endpoint_plantilla.format(id=lote_id)
        try:
            self._http.peticion(
                "POST", path, payload=body, token=secret, parsear=False
            )
        except ErrorHttp as exc:
            raise ErrorControl(
                f"El backend rechazó el ack de consigna ({exc.codigo}): {exc.detalle}",
                codigo=exc.codigo,
            ) from exc
        except ErrorConexion as exc:
            raise ErrorControl(
                f"No se pudo confirmar la consigna al backend: {exc.detalle}"
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

    El contrato Go usa snake_case (`temp1_objetivo`, `vel_objetivo`); se
    aceptan también las claves camelCase viejas como fallback para no romper
    backends previos.
    """
    t1 = _float_alguna(payload, "temp1_objetivo", "temperaturaObjetivo")
    vel = _float_alguna(payload, "vel_objetivo", "velocidadCintaObjetivo")
    decision_id_raw = payload.get("decision_id")
    if decision_id_raw is None:
        decision_id_raw = payload.get("decisionId")
    decision_id = str(decision_id_raw) if decision_id_raw is not None else None
    aprobada_por = payload.get("aprobada_por")
    if aprobada_por is not None:
        aplicado_por = "OPERARIO"
    elif payload.get("aplicadoPor") is not None:
        aplicado_por = str(payload["aplicadoPor"])
    else:
        aplicado_por = "MPC"
    ventana = payload.get("ventana_inicio")
    if ventana is None:
        ventana = payload.get("ventana")
    fase_raw = payload.get("fase")
    return SetpointPropuesto(
        temp_horno_1=t1,
        velocidad_cinta=vel,
        aplicado_por=aplicado_por,
        ventana=str(ventana) if ventana is not None else None,
        decision_id=decision_id,
        autorizada=bool(payload.get("autorizada", True)),
        fase=str(fase_raw) if fase_raw is not None else None,
        limites=_limites_de_payload(payload),
        manual_override=bool(payload.get("manual_override", False)),
    )


def _limites_de_payload(payload: dict) -> LimitesAplicacion | None:
    """Mapea el bloque `limites` de la propuesta; None si falta o es inválido.

    Un bloque incompleto o incoherente se descarta en silencio: el clamp cae
    al snapshot del producto y la propuesta se aplica igual.
    """
    bruto = payload.get("limites")
    if not isinstance(bruto, dict):
        return None
    try:
        temp_min = float(bruto["temp_min"])
        temp_max = float(bruto["temp_max"])
        vel_min = float(bruto["vel_min"])
        vel_max = float(bruto["vel_max"])
        # El backend lo llama delta_temp1_max_c (T1); el dominio usa
        # delta_temp_max_c.
        delta_temp_max_c = float(bruto["delta_temp1_max_c"])
        delta_vel_max = float(bruto["delta_vel_max"])
    except (KeyError, TypeError, ValueError):
        return None
    valores = (
        temp_min, temp_max, vel_min, vel_max, delta_temp_max_c, delta_vel_max
    )
    if not all(math.isfinite(v) for v in valores):
        return None
    if temp_max <= temp_min or vel_max <= vel_min:
        return None
    if delta_temp_max_c <= 0 or delta_vel_max <= 0:
        return None
    return LimitesAplicacion(
        delta_temp_max_c=delta_temp_max_c,
        delta_vel_max=delta_vel_max,
        temp_min=temp_min,
        temp_max=temp_max,
        vel_min=vel_min,
        vel_max=vel_max,
    )


def _float_alguna(payload: dict, *claves: str) -> float:
    for clave in claves:
        if payload.get(clave) is not None:
            return float(payload[clave])
    raise KeyError(f"falta {claves[0]}")