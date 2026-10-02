"""Tipos del subsistema de control (SCA-400).

La Pi ENTRADA ejecuta el ControlService, que:
  1. Lee T1/T2/velocidad/ambiente cada `publicar_telemetria_segundos`
     y los publica al backend (`POST /api/v1/lotes/{id}/metricas-horno`).
  2. Pollea el último setpoint propuesto/aplicable por el backend
     (`GET /api/v1/lotes/{id}/consigna-propuesta`) y lo aplica al PLC/variador
     con un clamp previo al `delta_temp_max_c` y `delta_vel_max` por ciclo.

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
    """Error de red, HTTP o de payload al hablar con el backend."""


@dataclass(frozen=True, slots=True)
class LecturaHorno:
    """Lectura instantánea de los sensores del horno.

    Los campos opcionales reflejan sensores que pueden no existir (humedad,
    ambiente) o no llegar (T2 cuando la línea tiene un solo horno).
    """

    temp_horno_1: float | None
    temp_horno_2: float | None
    temp_comb_1: float | None
    temp_comb_2: float | None
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
        if self.temp_horno_2 is not None:
            payload["temp_horno_2"] = self.temp_horno_2
        if self.temp_comb_1 is not None:
            payload["temp_comb_1"] = self.temp_comb_1
        if self.temp_comb_2 is not None:
            payload["temp_comb_2"] = self.temp_comb_2
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

    En modo AUTOMATICO la Pi lo aplica sin pedir confirmación; en modo
    SUPERVISION ya pasó por la aprobación del operario. En MANUAL nunca llega.
    """

    temp_horno_1: float
    temp_horno_2: float | None  # None cuando la línea tiene un solo horno
    velocidad_cinta: float
    aplicado_por: str  # "MPC" o "OPERARIO"
    ventana: str | None  # ISO timestamp de la ventana que originó la propuesta

    def a_payload_aplicacion(self) -> dict:
        """Body de POST /api/v1/lotes/{id}/aplicar-consigna."""
        payload: dict = {
            "temperatura_objetivo": self.temp_horno_1,
            "velocidad_cinta_objetivo": self.velocidad_cinta,
        }
        if self.temp_horno_2 is not None:
            payload["temp_horno_2_objetivo"] = self.temp_horno_2
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


@dataclass(frozen=True, slots=True)
class ConfigControl:
    """Subsección `control` del config.json."""

    habilitado: bool
    publicar_telemetria_segundos: float
    poll_consigna_propuesta_segundos: float
    metricas_horno_endpoint: str  # plantilla con {id}
    consigna_propuesta_endpoint: str  # plantilla con {id}
    aplicar_consigna_endpoint: str  # plantilla con {id}