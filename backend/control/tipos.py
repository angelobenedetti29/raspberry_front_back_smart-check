"""Tipos del subsistema de control (SCA-400).

La Pi ENTRADA ejecuta el ControlService, que:
  1. Lee T1/velocidad/ambiente cada `publicar_telemetria_segundos`
     y los publica al backend (`POST /api/v1/lotes/{id}/metricas-horno`).
  2. Pollea el último setpoint propuesto/aplicable por el backend
     (`GET /api/v1/lotes/{id}/consigna-propuesta`) y lo aplica al PLC/variador
     con un clamp previo al `delta_temp_max_c` y `delta_vel_max` por ciclo.
     Un override manual ya aprobado se aplica sin ese Δmáx, sólo con el
     rango del producto.

En SUPERVISION la propuesta requiere la aprobación del operario; la Pi sólo
aplica las que llegan con `autorizada=true` (AUTOMATICO, o SUPERVISION ya
aprobada) y confirma con `POST /api/v1/lotes/{id}/consigna/ack`.

Estos tipos son el espejo Python del contrato Go: el backend publica el
setpoint, la Pi lo aplica con la cadencia y los límites del lote.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModoControl(str, Enum):
    """Modo de operación del lote (espejo del dominio Go)."""

    MANUAL = "MANUAL"
    SUPERVISION = "SUPERVISION"
    AUTOMATICO = "AUTOMATICO"


class ErrorControl(Exception):
    """Error de red, HTTP o de payload al hablar con el backend.

    `codigo` replica el status HTTP cuando aplique (None en errores de
    conexión o de payload). Permite que el servicio decida si un 401/403
    debe disparar la invalidación de credencial.
    """

    def __init__(self, detalle: str, codigo: int | None = None) -> None:
        super().__init__(detalle)
        self.codigo = codigo


@dataclass(frozen=True, slots=True)
class LecturaHorno:
    """Lectura instantánea de los sensores del horno.

    Los campos opcionales reflejan sensores que pueden no existir (humedad,
    ambiente).
    """

    temp_horno_1: float | None
    temp_comb_1: float | None
    humedad_horno: float | None
    temp_ambiente: float | None
    humedad_ambiente: float | None
    velocidad_cinta: float | None
    temp_objetivo: float | None  # setpoint activo de T1
    vel_objetivo: float | None  # setpoint activo de velocidad
    confianza_deteccion: float | None  # promedio de la última ventana

    def a_payload(self) -> dict:
        """Serializa como el body de POST /api/v1/lotes/{id}/metricas-horno."""
        payload: dict = {}
        if self.temp_horno_1 is not None:
            payload["temp_horno_1"] = self.temp_horno_1
        if self.temp_comb_1 is not None:
            payload["temp_comb_1"] = self.temp_comb_1
        if self.humedad_horno is not None:
            payload["humedad_horno"] = self.humedad_horno
        if self.temp_ambiente is not None:
            payload["temp_ambiente"] = self.temp_ambiente
        if self.humedad_ambiente is not None:
            payload["humedad_ambiente"] = self.humedad_ambiente
        if self.velocidad_cinta is not None:
            payload["velocidad_cinta"] = self.velocidad_cinta
        if self.temp_objetivo is not None:
            payload["temp_objetivo"] = self.temp_objetivo
        if self.vel_objetivo is not None:
            payload["vel_objetivo"] = self.vel_objetivo
        if self.confianza_deteccion is not None:
            payload["confianza_deteccion"] = self.confianza_deteccion
        return payload


@dataclass(frozen=True, slots=True)
class SetpointPropuesto:
    """Setpoint propuesto por el backend (control automático o supervisión).

    En modo AUTOMATICO llega con `autorizada=True` y la Pi lo aplica sin pedir
    confirmación. En SUPERVISION llega con `autorizada=False` hasta que el
    operario aprueba; recién entonces la Pi lo aplica. En MANUAL el MPC no
    propone, pero puede llegar una consigna manual del operario ya aprobada
    (`manual_override=True`).
    """

    temp_horno_1: float
    velocidad_cinta: float
    aplicado_por: str  # "MPC" o "OPERARIO"
    ventana: str | None  # ISO timestamp de la ventana que originó la propuesta
    decision_id: str | None = None  # id de la decisión en el backend (dedup + TTL)
    autorizada: bool = True  # la Pi sólo aplica las autorizadas
    fase: str | None = None  # fase del lote, si el backend la informa
    # Límites vivos del lote que manda el backend con la propuesta. Si llegan,
    # el clamp defensivo los usa en vez del snapshot del producto.
    limites: LimitesAplicacion | None = None
    # True cuando el backend reenvía una consigna manual del operario ya
    # aprobada; en ese caso no se aplica el Δmáx por ciclo, sólo el rango del
    # producto.
    manual_override: bool = False

    def a_payload_aplicacion(self) -> dict:
        """Body legacy del POST de aprobación de consigna.

        El endpoint de aprobación actual ignora el body; se conserva por
        compatibilidad con callers/tests viejos.
        """
        payload: dict = {
            "temperatura_objetivo": self.temp_horno_1,
            "velocidad_cinta_objetivo": self.velocidad_cinta,
        }
        if self.decision_id is not None:
            payload["decision_id"] = self.decision_id
        return payload

    def a_payload_ack(self) -> dict:
        """Body de POST /api/v1/lotes/{id}/consigna/ack (qué aplicó la Pi)."""
        payload: dict = {
            "temp_aplicado": self.temp_horno_1,
            "vel_aplicado": self.velocidad_cinta,
        }
        if self.decision_id is not None:
            payload["decision_id"] = self.decision_id
        return payload


@dataclass(frozen=True, slots=True)
class LimitesAplicacion:
    """Límites por ciclo para aplicar un setpoint.

    El MPC ya respeta los límites de producto, pero la Pi los vuelve a aplicar
    como cinturón de seguridad: si llega un setpoint que violaría un Δmáx,
    se clampea al límite antes de enviarlo al PLC.
    """

    delta_temp_max_c: float  # |ΔT| máximo por aplicación (°C)
    delta_vel_max: float  # |ΔV| máximo por aplicación (m/s)
    temp_min: float  # rango seguro del producto (horno 1)
    temp_max: float
    vel_min: float
    vel_max: float