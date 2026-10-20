#!/usr/bin/env python3
"""Simulador Modbus TCP del horno SCA-400 — standalone, sin la app.

Hace de PLC/variador por Modbus TCP para probar la ruta real de la app
(`backend/control/modbus.py`) sin hardware: se levanta en una terminal y la
app se conecta con `control.sensores.driver` y `control.actuadores.driver`
en `"modbus_tcp"` (`host`/`port` apuntando acá). La dinámica es register-
driven: las consignas que la app escribe en los registros de setpoint
mueven la planta, y las lecturas de sensores derivan hacia ellos — lazo
cerrado completo por el cable.

Deliberadamente **no importa nada de la app** (sólo pymodbus >= 3.15 y
stdlib): es un "PLC falso" que sólo habla Modbus. El precio es que el mapa
de registros y el modelo dinámico están duplicados del lado de acá.

Mapa de registros (espejo del de `backend/control/modbus.py`; si cambiás
uno, cambiá el otro):

- Sensores: dirección 0, 16 holding regs contiguos = 8 magnitudes, cada
  una un float32 en 2 regs. Offsets (en regs): `temp_horno_1` 0,
  `temp_horno_2` 2, `temp_comb_1` 4, `temp_comb_2` 6, `humedad_horno` 8,
  `temp_ambiente` 10, `humedad_ambiente` 12, `velocidad_cinta` 14.
- Consignas (setpoints): T1 en 100, T2 en 102, velocidad en 104 (2 regs
  c/u, no contiguas entre bloques).
- Direcciones base-0. Los huecos (16-99) quedan inválidos: un request
  ahí falla con excepción, como en un PLC real.

Convenciones:

- float32 en 2 regs como `[LSW, MSW]` (word order "little"): es el orden
  que usa el cliente de la app (`convertir_float32` con
  `word_order="little"`). NOTA: NO usar `DataType.FLOAT32` de pymodbus
  acá: guarda los words al revés (`[MSW, LSW]`).
- T2 de consigna en 0.0 significa "sin objetivo propio": la segunda zona
  deriva hacia `T1 * 0.92` (línea de un solo horno). La app nunca escribe
  0 en T2 (salta el bloque si el setpoint de T2 es None), así que 0.0
  queda como valor "unset" seguro.

Uso:

    python tools/simulador_modbus.py                     # 127.0.0.1:5020
    python tools/simulador_modbus.py --puerto 1502 --semilla 42
    python tools/simulador_modbus.py --rechazar-setpoints  # probar fallos

En `config.json` (control.sensores **y** control.actuadores):

    "driver": "modbus_tcp", "host": "127.0.0.1", "port": 5020

y reiniciar la app. Se puede manejar también con cualquier cliente Modbus
(mbpoll, diagslave): escribir las consignas mueve la planta igual.
"""

from __future__ import annotations

import argparse
import logging
import math
import random
import struct
import threading
import time
from collections.abc import Callable, Coroutine

from pymodbus.server import StartTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

# --- mapa de registros (espejo de backend/control/modbus.py) ------------------

DIRECCION_BASE_SENSORES = 0
CANTIDAD_REGS_SENSORES = 16  # 8 magnitudes x 2 regs
OFFSET_SENSORES: dict[str, int] = {  # offset en regs dentro del bloque
    "temp_horno_1": 0,
    "temp_horno_2": 2,
    "temp_comb_1": 4,
    "temp_comb_2": 6,
    "humedad_horno": 8,
    "temp_ambiente": 10,
    "humedad_ambiente": 12,
    "velocidad_cinta": 14,
}
DIRECCION_TEMP1_OBJETIVO = 100
DIRECCION_TEMP2_OBJETIVO = 102
DIRECCION_VEL_OBJETIVO = 104

# --- codificación float32 (word order "little" del cliente de la app) --------


def codificar_float32(valor: float) -> list[int]:
    """float32 -> 2 regs `[LSW, MSW]` (struct LE partido en palabras)."""
    b = struct.pack("<f", valor)
    return [
        int.from_bytes(b[0:2], "little"),
        int.from_bytes(b[2:4], "little"),
    ]


def decodificar_float32(regs: list[int]) -> float:
    """2 regs `[LSW, MSW]` -> float32."""
    b = regs[0].to_bytes(2, "little") + regs[1].to_bytes(2, "little")
    return struct.unpack("<f", b)[0]


# --- planta (modelo duplicado del driver in-process de la app) ---------------

T2_FACTOR_UN_HORNO = 0.92  # sin objetivo propio, la 2ª zona sigue a la 1ª
COMB1_FACTOR = 0.78  # cámaras de combustión: rezagadas respecto de T1
COMB2_FACTOR = 0.70
HUMEDAD_HORNO_PCT = 35.0
TEMP_AMBIENTE_C = 24.0
HUMEDAD_AMBIENTE_PCT = 55.0


class PlantaHorno:
    """Modelo de primer orden del horno + cinta, register-driven.

    En cada `avanzar()` lee los objetivos de los 6 regs de consigna y da
    un paso de convergencia exponencial hacia ellos (mismo modelo que el
    driver `simulado` in-process de la app, duplicado acá a propósito
    para no acoplar este script a la app). Wall-clock: puede llamarse con
    cualquier cadencia sin acumular error.
    """

    def __init__(
        self,
        *,
        t1_inicial: float,
        t2_inicial: float,
        v_inicial: float,
        tau_t: float,
        tau_v: float,
        ruido_t: float,
        ruido_v: float,
        semilla: int | None,
    ) -> None:
        self._lock = threading.Lock()
        self._rng = random.Random(semilla)
        # Estado físico inicial.
        self._t1 = t1_inicial
        self._t2 = t2_inicial
        self._v = v_inicial
        self._comb1 = t1_inicial * COMB1_FACTOR
        self._comb2 = t1_inicial * COMB2_FACTOR
        self._humedad_horno = HUMEDAD_HORNO_PCT
        self._temp_ambiente = TEMP_AMBIENTE_C
        self._humedad_ambiente = HUMEDAD_AMBIENTE_PCT
        self._tau_t = tau_t
        self._tau_v = tau_v
        self._ruido_t = ruido_t
        self._ruido_v = ruido_v
        self._ultimo = time.monotonic()

    def avanzar(self, regs_consigna: list[int]) -> None:
        """Integra hasta el ahora; objetivos = regs de consigna decodificados."""
        ahora = time.monotonic()
        with self._lock:
            dt = ahora - self._ultimo
            self._ultimo = ahora
            if dt <= 0.0:
                return
            obj_t1 = decodificar_float32(regs_consigna[0:2])
            obj_t2 = decodificar_float32(regs_consigna[2:4])
            obj_v = decodificar_float32(regs_consigna[4:6])
            if obj_t2 == 0.0:  # convención: sin objetivo propio
                obj_t2 = self._t1 * T2_FACTOR_UN_HORNO
            self._paso(dt, obj_t1, obj_t2, obj_v)

    def _paso(self, dt: float, obj_t1: float, obj_t2: float, obj_v: float) -> None:
        """Un paso de integración (requiere el lock tomado)."""
        k_t = 1.0 - math.exp(-dt / self._tau_t)
        k_v = 1.0 - math.exp(-dt / self._tau_v)
        self._t1 = self._converger(self._t1, obj_t1, k_t, self._ruido_t)
        self._t2 = self._converger(self._t2, obj_t2, k_t, self._ruido_t)
        self._comb1 = self._converger(self._comb1, self._t1 * COMB1_FACTOR, k_t, self._ruido_t)
        self._comb2 = self._converger(self._comb2, self._t1 * COMB2_FACTOR, k_t, self._ruido_t)
        self._v = max(0.0, self._converger(self._v, obj_v, k_v, self._ruido_v))
        self._humedad_horno = self._deambular(self._humedad_horno, HUMEDAD_HORNO_PCT, 0.05)
        self._temp_ambiente = self._deambular(self._temp_ambiente, TEMP_AMBIENTE_C, 0.05)
        self._humedad_ambiente = self._deambular(self._humedad_ambiente, HUMEDAD_AMBIENTE_PCT, 0.10)

    def _converger(self, valor: float, objetivo: float, k: float, ruido: float) -> float:
        """Paso de primer orden hacia el objetivo con ruido gaussiano."""
        nuevo = valor + (objetivo - valor) * k
        if ruido > 0.0:
            nuevo += self._rng.gauss(0.0, ruido)
        return nuevo

    def _deambular(self, valor: float, centro: float, escala: float) -> float:
        """Ruido con reversión a la media (humedades y ambiente)."""
        return centro + 0.99 * (valor - centro) + self._rng.gauss(0.0, escala)

    def valores(self) -> dict[str, float]:
        """Snapshot del estado por campo (para pintar los regs de sensores)."""
        with self._lock:
            return {
                "temp_horno_1": self._t1,
                "temp_horno_2": self._t2,
                "temp_comb_1": self._comb1,
                "temp_comb_2": self._comb2,
                "humedad_horno": self._humedad_horno,
                "temp_ambiente": self._temp_ambiente,
                "humedad_ambiente": self._humedad_ambiente,
                "velocidad_cinta": self._v,
            }


# --- el action: puente entre el runtime pymodbus y la planta -----------------

# Tipo del callback `action` de SimDevice (pymodbus 3.15): se invoca en
# cada request con el bloque crudo; mutarlo inline afecta la respuesta.
Accion = Callable[
    [int, int, int, int, list[int], list[int] | None],
    Coroutine[None, None, object],
]

NOMBRE_CONSIGNA = {
    DIRECCION_TEMP1_OBJETIVO: "T1",
    DIRECCION_TEMP2_OBJETIVO: "T2",
    DIRECCION_VEL_OBJETIVO: "V",
}


def crear_accion(planta: PlantaHorno, rechazar: bool) -> Accion:
    """Devuelve el action async que avanza la planta y refresca sensores.

    Se llama en cada request (lectura o escritura, a cualquier dirección):
    1. Avanza la planta al ahora (lee los objetivos de los regs de
       consigna: si es una escritura, éstos aún no aterrizaron — la planta
       los ve en el request siguiente).
    2. Refresca los 16 regs de sensores con el estado avanzado, así toda
       lectura devuelve valores vivos.
    3. Loguea las escrituras de consigna (las 3 que hace el aplicador de
       la app, una por bloque).
    """

    async def accion(
        function_code: int,
        start_address: int,
        address: int,
        count: int,
        current_registers: list[int],
        set_values: list[int] | None,
    ) -> object:
        base = DIRECCION_BASE_SENSORES - start_address  # offset del mapa en el bloque
        planta.avanzar(current_registers[base + DIRECCION_TEMP1_OBJETIVO :][:6])
        valores = planta.valores()
        for campo, offset in OFFSET_SENSORES.items():
            regs = codificar_float32(valores[campo])
            current_registers[base + offset : base + offset + 2] = regs
        if set_values is not None:
            _log_consigna(address, set_values, rechazar)
        return None

    return accion


def _log_consigna(address: int, set_values: list[int], rechazar: bool) -> None:
    """Loguea una escritura de consigna con el valor float32 decodificado."""
    nombre = NOMBRE_CONSIGNA.get(address)
    if nombre is None:
        return  # escritura fuera del mapa de consignas: la deja pasar el runtime
    valor = decodificar_float32(list(set_values[:2]))
    sufijo = " [RECHAZADA: bloque readonly]" if rechazar else ""
    logging.info("consigna %s <- %.2f%s", nombre, valor, sufijo)


# --- armado del dispositivo Modbus -------------------------------------------


def construir_dispositivo(planta: PlantaHorno, args: argparse.Namespace) -> SimDevice:
    """SimDevice con el bloque de sensores y el de consignas.

    `DataType.REGISTERS` (regs crudos) en ambos: el encoding float32 lo
    hacemos nosotros para matchear el word order del cliente de la app
    (el `DataType.FLOAT32` de pymodbus guarda los words invertidos).
    Consignas iniciales: T1/V en sus valores de arranque (planta quieta)
    y T2 en 0.0 (sin objetivo propio).
    """
    valores = planta.valores()
    regs_sensores: list[int] = []
    for campo in OFFSET_SENSORES:
        regs_sensores.extend(codificar_float32(valores[campo]))
    assert len(regs_sensores) == CANTIDAD_REGS_SENSORES
    regs_consignas = (
        codificar_float32(args.t1_inicial)
        + codificar_float32(0.0)
        + codificar_float32(args.v_inicial)
    )
    sensores = SimData(
        address=DIRECCION_BASE_SENSORES,
        count=1,
        values=regs_sensores,
        datatype=DataType.REGISTERS,
    )
    consignas = SimData(
        address=DIRECCION_TEMP1_OBJETIVO,
        count=1,
        values=regs_consignas,
        datatype=DataType.REGISTERS,
        readonly=args.rechazar_setpoints,
    )
    return SimDevice(
        id=0,  # responde a cualquier unit_id
        simdata=[sensores, consignas],
        action=crear_accion(planta, args.rechazar_setpoints),
    )


# --- CLI ----------------------------------------------------------------------


def _parsear_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulador Modbus TCP del horno SCA-400 (standalone).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind (default: 127.0.0.1)")
    parser.add_argument("--puerto", type=int, default=5020, help="puerto TCP (default: 5020)")
    parser.add_argument("--t1-inicial", type=float, default=150.0, help="°C (default: 150)")
    parser.add_argument("--t2-inicial", type=float, default=140.0, help="°C (default: 140)")
    parser.add_argument("--v-inicial", type=float, default=0.15, help="m/s (default: 0.15)")
    parser.add_argument("--tau-t", type=float, default=60.0, help="τ de T en s (default: 60)")
    parser.add_argument("--tau-v", type=float, default=8.0, help="τ de V en s (default: 8)")
    parser.add_argument("--ruido-t", type=float, default=0.2, help="σ de T en °C (default: 0.2)")
    parser.add_argument("--ruido-v", type=float, default=0.003, help="σ de V en m/s (default: 0.003)")
    parser.add_argument("--semilla", type=int, default=None, help="semilla del ruido (reproducible)")
    parser.add_argument(
        "--rechazar-setpoints",
        action="store_true",
        help="consignas readonly: la escritura falla (probar el read-back del aplicador)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parsear_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    planta = PlantaHorno(
        t1_inicial=args.t1_inicial,
        t2_inicial=args.t2_inicial,
        v_inicial=args.v_inicial,
        tau_t=args.tau_t,
        tau_v=args.tau_v,
        ruido_t=args.ruido_t,
        ruido_v=args.ruido_v,
        semilla=args.semilla,
    )
    dispositivo = construir_dispositivo(planta, args)
    logging.info(
        "simulador SCA-400 en %s:%d (unit_id: cualquiera) | sensores @%d (%d regs) "
        "| consignas @%d/@%d/@%d%s",
        args.host,
        args.puerto,
        DIRECCION_BASE_SENSORES,
        CANTIDAD_REGS_SENSORES,
        DIRECCION_TEMP1_OBJETIVO,
        DIRECCION_TEMP2_OBJETIVO,
        DIRECCION_VEL_OBJETIVO,
        " [READONLY]" if args.rechazar_setpoints else "",
    )
    logging.info(
        'en config.json: control.sensores/actuadores -> "driver": "modbus_tcp", '
        '"host": "%s", "port": %d',
        args.host,
        args.puerto,
    )
    try:
        StartTcpServer(context=dispositivo, address=(args.host, args.puerto))
    except KeyboardInterrupt:
        logging.info("simulador detenido")


if __name__ == "__main__":
    main()
