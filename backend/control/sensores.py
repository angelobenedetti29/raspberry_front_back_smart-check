"""Interfaces abstractas para sensores del horno y aplicación de consignas.

La planta real usa PLC + variador; en desarrollo o sin hardware conectado,
se usan implementaciones stub que devuelven None o no hacen nada. El
ControlService depende sólo de estas interfaces; los detalles del hardware
se inyectan desde la composición (run.py o un factory).

Para no acoplar el control al resto de la app, los stubs viven acá mismo.
Para probar sin hardware hay dos caminos: el driver `simulado`
(`simulado.py`, planta en memoria dentro del proceso) y el simulador
Modbus externo (`tools/simulador_modbus.py`, con `driver = "modbus_tcp"`).
El driver real (Modbus RTU/TCP) vive en `modbus.py`; la factory de abajo
elige por config, sin que el ControlService se entere.
"""

from __future__ import annotations

import logging
from typing import Protocol

from backend.config import ModbusDriverConfig
from backend.control.modbus import (
    CANTIDAD_REGS_SENSORES,
    ClienteModbus,
    DIRECCION_BASE_SENSORES,
    DIRECCION_TEMP1_OBJETIVO,
    DIRECCION_VEL_OBJETIVO,
    _OFFSET_SENSORES,
)
from backend.control.simulado import (
    AplicadorConsignaSimulado,
    LectorSensoresSimulado,
)
from backend.control.tipos import LecturaHorno

logger = logging.getLogger(__name__)


class LectorSensores(Protocol):
    """Contrato mínimo para leer el estado actual del horno.

    La implementación real lee termocuplas/encoders/variador y devuelve una
    LecturaHorno. El stub devuelve todo None: la Pi publica un heartbeat
    con los campos en null y el backend igual lo acepta.
    """

    def leer(self) -> LecturaHorno: ...


class AplicadorConsigna(Protocol):
    """Contrato mínimo para aplicar un setpoint al horno.

    La implementación real escribe en el PLC/variador y devuelve la lectura
    efectiva (puede diferir si el controlador rechaza). El stub no hace nada
    y devuelve None en los campos afectados.
    """

    def aplicar(
        self,
        temp_objetivo: float,
        velocidad_objetivo: float,
    ) -> tuple[float | None, float | None]: ...


class LectorSensoresStub:
    """Lector vacío: la Pi sólo publica heartbeats con los campos en null."""

    def leer(self) -> LecturaHorno:
        return LecturaHorno(
            temp_horno_1=None,
            temp_comb_1=None,
            humedad_horno=None,
            temp_ambiente=None,
            humedad_ambiente=None,
            velocidad_cinta=None,
            temp_objetivo=None,
            vel_objetivo=None,
            confianza_deteccion=None,
        )


class AplicadorConsignaStub:
    """Aplicador vacío: registra el último setpoint recibido pero no hace nada."""

    def __init__(self) -> None:
        self._ultimo: tuple[float | None, float | None] | None = None

    def aplicar(
        self,
        temp_objetivo: float,
        velocidad_objetivo: float,
    ) -> tuple[float | None, float | None]:
        self._ultimo = (temp_objetivo, velocidad_objetivo)
        logger.info(
            "aplicador stub: T1=%.2f V=%.3f",
            temp_objetivo,
            velocidad_objetivo,
        )
        return self._ultimo


# --- Factory ---------------------------------------------------------------


def crear_lector(cfg: ModbusDriverConfig) -> LectorSensores:
    """Devuelve la implementación adecuada según `cfg.driver`.

    Con `driver == "stub"` no se instancia nada de Modbus, así que este
    factory es seguro de llamar aunque `pymodbus` no esté instalado. Con
    `"simulado"` lee la planta en memoria de `simulado.py` (sin hardware
    ni Modbus). Con `"modbus_rtu"`/`"modbus_tcp"` usa el driver real.
    """
    if cfg.driver == "stub":
        return LectorSensoresStub()
    if cfg.driver == "simulado":
        return LectorSensoresSimulado()
    return LectorSensoresModbus(ClienteModbus(cfg))


def crear_aplicador(cfg: ModbusDriverConfig) -> AplicadorConsigna:
    """Devuelve la implementación adecuada según `cfg.driver`.

    Igual que `crear_lector`: stub no hace nada, `simulado` fija los
    objetivos de la planta en memoria, Modbus escribe en el PLC/variador.
    """
    if cfg.driver == "stub":
        return AplicadorConsignaStub()
    if cfg.driver == "simulado":
        return AplicadorConsignaSimulado()
    return AplicadorConsignaModbus(ClienteModbus(cfg))


# --- Implementaciones Modbus -------------------------------------------------


class LectorSensoresModbus:
    """Lector que lee 16 holding registers contiguos en una sola transacción.

    Decodifica cada par como float32 LE y mapea a los campos de
    `LecturaHorno` definidos por el PLC genérico (ver mapa en modbus.py).
    Los campos que no estén en el mapa quedan `None`. Si la transacción
    falla, devuelve un `LecturaHorno` con todos los campos en `None` — el
    `ControlService._lectura_tiene_datos` lo filtra y no publica telemetría.
    """

    def __init__(self, cliente: ClienteModbus) -> None:
        self._cliente = cliente

    def leer(self) -> LecturaHorno:
        regs = self._cliente.leer_bloque(
            DIRECCION_BASE_SENSORES, CANTIDAD_REGS_SENSORES
        )
        if regs is None:
            return LecturaHorno(
                temp_horno_1=None,
                temp_comb_1=None,
                humedad_horno=None,
                temp_ambiente=None,
                humedad_ambiente=None,
                velocidad_cinta=None,
                temp_objetivo=None,
                vel_objetivo=None,
                confianza_deteccion=None,
            )
        valores: dict[str, float | None] = {}
        for campo, offset in _OFFSET_SENSORES.items():
            regs_par = regs[offset : offset + 2]
            if len(regs_par) != 2:
                valores[campo] = None
            else:
                valores[campo] = self._cliente.convertir_float32(regs_par)
        return LecturaHorno(
            temp_horno_1=valores.get("temp_horno_1"),
            temp_comb_1=valores.get("temp_comb_1"),
            humedad_horno=valores.get("humedad_horno"),
            temp_ambiente=valores.get("temp_ambiente"),
            humedad_ambiente=valores.get("humedad_ambiente"),
            velocidad_cinta=valores.get("velocidad_cinta"),
            temp_objetivo=None,
            vel_objetivo=None,
            confianza_deteccion=None,
        )


class AplicadorConsignaModbus:
    """Aplicador que escribe los 2 setpoints con verificación por read-back.

    Cada bloque (T1, V) se escribe y se relee en transacciones
    independientes. El retorno usa `None` en el campo afectado si la
    verificación falla — el `_clampear_y_aplicar` del ControlService ya
    captura excepciones, pero devolver `None` evita el throw.
    """

    def __init__(self, cliente: ClienteModbus) -> None:
        self._cliente = cliente

    def aplicar(
        self,
        temp_objetivo: float,
        velocidad_objetivo: float,
    ) -> tuple[float | None, float | None]:
        # T1 — siempre presente.
        regs_t1 = self._cliente.codificar_float32(temp_objetivo)
        t1_ok = self._cliente.escribir_verificado(
            DIRECCION_TEMP1_OBJETIVO, regs_t1
        )
        t1_efectivo = temp_objetivo if t1_ok else None

        # Velocidad de cinta.
        regs_v = self._cliente.codificar_float32(velocidad_objetivo)
        v_ok = self._cliente.escribir_verificado(
            DIRECCION_VEL_OBJETIVO, regs_v
        )
        v_efectivo = velocidad_objetivo if v_ok else None

        logger.info(
            "aplicador modbus: T1=%s V=%s",
            t1_efectivo,
            v_efectivo,
        )
        return (t1_efectivo, v_efectivo)