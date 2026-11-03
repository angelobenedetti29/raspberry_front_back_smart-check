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

- Sensores: dirección 0, 16 holding regs contiguos, cada magnitud un
  float32 en 2 regs. Offsets (en regs): `temp_horno_1` 0, `temp_comb_1` 4,
  `humedad_horno` 8, `temp_ambiente` 10, `humedad_ambiente` 12,
  `velocidad_cinta` 14. Los offsets 2 y 6 quedan sin uso (bloque fijo del
  PLC), pero no se renumeran.
- Consignas (setpoints): T1 en 100, velocidad en 104 (2 regs c/u).
  102-103 queda sin uso.
- Direcciones base-0. Los huecos (16-99) quedan inválidos: un request
  ahí falla con excepción, como en un PLC real.

Convenciones:

- float32 en 2 regs como `[LSW, MSW]` (word order "little"): es el orden
  que usa el cliente de la app (`convertir_float32` con
  `word_order="little"`). NOTA: NO usar `DataType.FLOAT32` de pymodbus
  acá: guarda los words al revés (`[MSW, LSW]`).

Uso:

    python tools/simulador_modbus.py                     # 127.0.0.1:5020
    python tools/simulador_modbus.py --puerto 1502 --semilla 42
    python tools/simulador_modbus.py --rechazar-setpoints  # probar fallos

Panel web de pruebas (opcional, `--sin-ui` lo apaga):

    python tools/simulador_modbus.py --ui-puerto 8091
    # abrir http://127.0.0.1:8091/

El panel permite ver la planta, cambiar parámetros en caliente, mover
consignas y alternar el rechazo de setpoints sin reiniciar el simulador.

AVISO DE SEGURIDAD: el panel **no tiene autenticación**. Por defecto
escucha en `127.0.0.1`; bindearlo a `0.0.0.0` (`--ui-host 0.0.0.0`) lo
expone a toda la red. Usalo sólo en un entorno de pruebas controlado.

En `config.json` (control.sensores **y** control.actuadores):

    "driver": "modbus_tcp", "host": "127.0.0.1", "port": 5020

y reiniciar la app. Se puede manejar también con cualquier cliente Modbus
(mbpoll, diagslave): escribir las consignas mueve la planta igual.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import struct
import threading
import time
from collections import deque
from collections.abc import Callable, Coroutine
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pymodbus.constants import ExcCodes
from pymodbus.server import StartTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

# --- mapa de registros (espejo de backend/control/modbus.py) ------------------

DIRECCION_BASE_SENSORES = 0
CANTIDAD_REGS_SENSORES = 16  # bloque fijo; 2 y 6 quedan sin uso
OFFSET_SENSORES: dict[str, int] = {  # offset en regs dentro del bloque
    "temp_horno_1": 0,
    "temp_comb_1": 4,
    "humedad_horno": 8,
    "temp_ambiente": 10,
    "humedad_ambiente": 12,
    "velocidad_cinta": 14,
}
DIRECCION_TEMP1_OBJETIVO = 100
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

COMB1_FACTOR = 0.78  # cámara de combustión: rezagada respecto de T1
HUMEDAD_HORNO_PCT = 35.0
TEMP_AMBIENTE_C = 24.0
HUMEDAD_AMBIENTE_PCT = 55.0

# Rangos válidos de los parámetros mutables y de las consignas. Se validan
# tanto en la planta (uso interno) como en el panel (error 400).
RANGOS_PARAMETROS: dict[str, tuple[float, float]] = {
    "tau_t": (1.0, 3600.0),
    "tau_v": (0.5, 600.0),
    "ruido_t": (0.0, 50.0),
    "ruido_v": (0.0, 1.0),
    "humedad_horno": (0.0, 100.0),
    "temp_ambiente": (-30.0, 80.0),
    "humedad_ambiente": (0.0, 100.0),
    "comb1_factor": (0.0, 1.5),
    "t1_inicial": (-50.0, 500.0),
    "v_inicial": (0.0, 5.0),
}
RANGO_T1 = (0.0, 500.0)
RANGO_V = (0.0, 5.0)

LIMITE_CUERPO_HTTP = 64 * 1024
_SIN_VALOR = object()  # distingue "no enviado" de un valor válido (0, null, ...)


def _validar_numero(valor: object, nombre: str) -> float:
    """Exige un número finito (bool no cuenta como número)."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ValueError(f"{nombre} debe ser un número")
    numero = float(valor)
    if not math.isfinite(numero):
        raise ValueError(f"{nombre} debe ser finito")
    return numero


def _validar_rango(valor: object, nombre: str, minimo: float, maximo: float) -> float:
    """Exige un número finito dentro de [minimo, maximo]."""
    numero = _validar_numero(valor, nombre)
    if not minimo <= numero <= maximo:
        raise ValueError(f"{nombre} fuera de rango [{minimo:g}, {maximo:g}]")
    return numero


def _validar_semilla(valor: object) -> int | None:
    """Semilla del RNG: entero o null."""
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ValueError("semilla debe ser un entero o null")
    return valor


def _validar_parametro(clave: str, valor: object) -> float | int | None:
    """Valida un parámetro mutable por nombre."""
    if clave == "semilla":
        return _validar_semilla(valor)
    try:
        minimo, maximo = RANGOS_PARAMETROS[clave]
    except KeyError:
        raise ValueError(f"parámetro desconocido: {clave}") from None
    return _validar_rango(valor, clave, minimo, maximo)


class PlantaHorno:
    """Modelo de primer orden del horno + cinta, register-driven.

    En cada `avanzar()` da un paso de convergencia exponencial hacia los
    objetivos que fijó `fijar_objetivos()` (mismo modelo que el driver
    `simulado` in-process de la app, duplicado acá a propósito para no
    acoplar este script a la app). Wall-clock: puede llamarse con cualquier
    cadencia y desde varios hilos sin acumular error.
    """

    def __init__(
        self,
        *,
        t1_inicial: float,
        v_inicial: float,
        tau_t: float,
        tau_v: float,
        ruido_t: float,
        ruido_v: float,
        semilla: int | None,
        humedad_horno: float = HUMEDAD_HORNO_PCT,
        temp_ambiente: float = TEMP_AMBIENTE_C,
        humedad_ambiente: float = HUMEDAD_AMBIENTE_PCT,
        comb1_factor: float = COMB1_FACTOR,
    ) -> None:
        # Validar antes de tocar estado: la CLI debe fallar temprano.
        t1_inicial = _validar_rango(t1_inicial, "t1_inicial", *RANGOS_PARAMETROS["t1_inicial"])
        v_inicial = _validar_rango(v_inicial, "v_inicial", *RANGOS_PARAMETROS["v_inicial"])
        tau_t = _validar_rango(tau_t, "tau_t", *RANGOS_PARAMETROS["tau_t"])
        tau_v = _validar_rango(tau_v, "tau_v", *RANGOS_PARAMETROS["tau_v"])
        ruido_t = _validar_rango(ruido_t, "ruido_t", *RANGOS_PARAMETROS["ruido_t"])
        ruido_v = _validar_rango(ruido_v, "ruido_v", *RANGOS_PARAMETROS["ruido_v"])
        humedad_horno = _validar_rango(
            humedad_horno, "humedad_horno", *RANGOS_PARAMETROS["humedad_horno"]
        )
        temp_ambiente = _validar_rango(
            temp_ambiente, "temp_ambiente", *RANGOS_PARAMETROS["temp_ambiente"]
        )
        humedad_ambiente = _validar_rango(
            humedad_ambiente,
            "humedad_ambiente",
            *RANGOS_PARAMETROS["humedad_ambiente"],
        )
        comb1_factor = _validar_rango(
            comb1_factor, "comb1_factor", *RANGOS_PARAMETROS["comb1_factor"]
        )
        self._lock = threading.Lock()
        self._rng = random.Random(semilla)
        self._semilla = semilla
        # Baseline para reiniciar (no mueve el estado actual por sí sola).
        self._t1_inicial = t1_inicial
        self._v_inicial = v_inicial
        # Objetivos vigentes.
        self._objetivo_t1 = t1_inicial
        self._objetivo_v = v_inicial
        # Parámetros mutables.
        self._tau_t = tau_t
        self._tau_v = tau_v
        self._ruido_t = ruido_t
        self._ruido_v = ruido_v
        self._comb1_factor = comb1_factor
        self._centro_humedad_horno = humedad_horno
        self._centro_temp_ambiente = temp_ambiente
        self._centro_humedad_ambiente = humedad_ambiente
        # Estado físico inicial.
        self._t1 = t1_inicial
        self._v = v_inicial
        self._comb1 = self._t1 * self._comb1_factor
        self._humedad_horno = self._centro_humedad_horno
        self._temp_ambiente = self._centro_temp_ambiente
        self._humedad_ambiente = self._centro_humedad_ambiente
        self._ultimo = time.monotonic()

    def fijar_objetivos(self, t1: float, v: float) -> None:
        """Fija los objetivos de T1 y V (valida rango de consigna)."""
        objetivo_t1 = _validar_rango(t1, "t1", *RANGO_T1)
        objetivo_v = _validar_rango(v, "v", *RANGO_V)
        with self._lock:
            self._objetivo_t1 = objetivo_t1
            self._objetivo_v = objetivo_v

    def avanzar(self) -> None:
        """Integra hasta el ahora usando los objetivos vigentes."""
        ahora = time.monotonic()
        with self._lock:
            dt = ahora - self._ultimo
            self._ultimo = ahora
            if dt <= 0.0:
                return
            self._paso(dt, self._objetivo_t1, self._objetivo_v)

    def _paso(self, dt: float, obj_t1: float, obj_v: float) -> None:
        """Un paso de integración (requiere el lock tomado)."""
        k_t = 1.0 - math.exp(-dt / self._tau_t)
        k_v = 1.0 - math.exp(-dt / self._tau_v)
        self._t1 = self._converger(self._t1, obj_t1, k_t, self._ruido_t)
        self._comb1 = self._converger(
            self._comb1, self._t1 * self._comb1_factor, k_t, self._ruido_t
        )
        self._v = max(0.0, self._converger(self._v, obj_v, k_v, self._ruido_v))
        self._humedad_horno = self._deambular(self._humedad_horno, self._centro_humedad_horno, 0.05)
        self._temp_ambiente = self._deambular(self._temp_ambiente, self._centro_temp_ambiente, 0.05)
        self._humedad_ambiente = self._deambular(
            self._humedad_ambiente, self._centro_humedad_ambiente, 0.10
        )

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
                "temp_comb_1": self._comb1,
                "humedad_horno": self._humedad_horno,
                "temp_ambiente": self._temp_ambiente,
                "humedad_ambiente": self._humedad_ambiente,
                "velocidad_cinta": self._v,
            }

    def consignas(self) -> dict[str, float]:
        """Objetivos vigentes de T1 y V."""
        with self._lock:
            return {"t1": self._objetivo_t1, "v": self._objetivo_v}

    def parametros(self) -> dict[str, float | int | None]:
        """Snapshot de los parámetros mutables."""
        with self._lock:
            return {
                "tau_t": self._tau_t,
                "tau_v": self._tau_v,
                "ruido_t": self._ruido_t,
                "ruido_v": self._ruido_v,
                "humedad_horno": self._centro_humedad_horno,
                "temp_ambiente": self._centro_temp_ambiente,
                "humedad_ambiente": self._centro_humedad_ambiente,
                "comb1_factor": self._comb1_factor,
                "t1_inicial": self._t1_inicial,
                "v_inicial": self._v_inicial,
                "semilla": self._semilla,
            }

    def actualizar_parametros(self, cambios: dict[str, object]) -> None:
        """Aplica un subconjunto de parámetros mutables, validando todo antes."""
        if not isinstance(cambios, dict):
            raise ValueError("parámetros inválidos")
        limpios = {clave: _validar_parametro(clave, valor) for clave, valor in cambios.items()}
        with self._lock:
            for clave, valor in limpios.items():
                if clave == "semilla":
                    self._semilla = valor
                    self._rng = random.Random(valor)
                elif clave == "humedad_horno":
                    self._centro_humedad_horno = float(valor)
                elif clave == "temp_ambiente":
                    self._centro_temp_ambiente = float(valor)
                elif clave == "humedad_ambiente":
                    self._centro_humedad_ambiente = float(valor)
                elif clave == "comb1_factor":
                    self._comb1_factor = float(valor)
                elif clave == "t1_inicial":
                    self._t1_inicial = float(valor)
                elif clave == "v_inicial":
                    self._v_inicial = float(valor)
                else:  # tau_t, tau_v, ruido_t, ruido_v
                    setattr(self, f"_{clave}", float(valor))

    def reiniciar(
        self,
        *,
        t1_inicial: object = _SIN_VALOR,
        v_inicial: object = _SIN_VALOR,
        semilla: object = _SIN_VALOR,
    ) -> None:
        """Vuelve al estado físico inicial; opcionalmente cambia el baseline."""
        nuevo_t1 = (
            _SIN_VALOR
            if t1_inicial is _SIN_VALOR
            else _validar_rango(t1_inicial, "t1_inicial", *RANGOS_PARAMETROS["t1_inicial"])
        )
        nuevo_v = (
            _SIN_VALOR
            if v_inicial is _SIN_VALOR
            else _validar_rango(v_inicial, "v_inicial", *RANGOS_PARAMETROS["v_inicial"])
        )
        nueva_semilla = _SIN_VALOR if semilla is _SIN_VALOR else _validar_semilla(semilla)
        with self._lock:
            if nuevo_t1 is not _SIN_VALOR:
                self._t1_inicial = float(nuevo_t1)
            if nuevo_v is not _SIN_VALOR:
                self._v_inicial = float(nuevo_v)
            if nueva_semilla is not _SIN_VALOR:
                self._semilla = nueva_semilla
                self._rng = random.Random(nueva_semilla)
            self._t1 = self._t1_inicial
            self._v = self._v_inicial
            self._comb1 = self._t1 * self._comb1_factor
            self._humedad_horno = self._centro_humedad_horno
            self._temp_ambiente = self._centro_temp_ambiente
            self._humedad_ambiente = self._centro_humedad_ambiente
            self._objetivo_t1 = self._t1_inicial
            self._objetivo_v = self._v_inicial
            self._ultimo = time.monotonic()


# --- estado compartido entre Modbus y el panel -------------------------------


class EstadoSimulador:
    """Estado thread-safe que comparten el action de pymodbus y el HTTP.

    El lock propio serializa pendientes, toggle, log y ultimo_request; la
    planta tiene su propio lock interno. El orden es siempre estado ->
    planta, nunca al revés.
    """

    def __init__(
        self,
        planta: PlantaHorno,
        *,
        rechazar_setpoints: bool,
        host: str,
        puerto: int,
    ) -> None:
        self._lock = threading.Lock()
        self._planta = planta
        self._rechazar_setpoints = rechazar_setpoints
        self._host = host
        self._puerto = puerto
        self._ultimo_request: float | None = None
        self._log: deque[dict[str, object]] = deque(maxlen=100)
        # Escrituras del panel que todavía no aterrizaron en los regs.
        self._pendientes: dict[int, float] = {}

    def instantanea(self) -> dict[str, object]:
        """Snapshot para `GET /api/estado`; también hace avanzar la planta."""
        with self._lock:
            self._planta.avanzar()
            return {
                "ahora": time.time(),
                "sensores": self._planta.valores(),
                "consignas": self._planta.consignas(),
                "parametros": self._planta.parametros(),
                "rechazar_setpoints": self._rechazar_setpoints,
                "ultimo_request": self._ultimo_request,
                "log": list(self._log),
                "modbus": {"host": self._host, "puerto": self._puerto},
            }

    def aplicar_consignas(
        self, *, t1: object = _SIN_VALOR, v: object = _SIN_VALOR
    ) -> dict[str, float]:
        """Aplica ya los objetivos y deja pendiente la escritura a registros."""
        with self._lock:
            if t1 is _SIN_VALOR and v is _SIN_VALOR:
                raise ValueError("indicá al menos una consigna (t1 o v)")
            nuevo_t1 = _SIN_VALOR if t1 is _SIN_VALOR else _validar_rango(t1, "t1", *RANGO_T1)
            nuevo_v = _SIN_VALOR if v is _SIN_VALOR else _validar_rango(v, "v", *RANGO_V)
            actuales = self._planta.consignas()
            t1_final = actuales["t1"] if nuevo_t1 is _SIN_VALOR else nuevo_t1
            v_final = actuales["v"] if nuevo_v is _SIN_VALOR else nuevo_v
            self._planta.fijar_objetivos(t1_final, v_final)
            ts = time.time()
            if nuevo_t1 is not _SIN_VALOR:
                self._pendientes[DIRECCION_TEMP1_OBJETIVO] = nuevo_t1
                self._log.append(
                    {
                        "ts": ts,
                        "origen": "ui",
                        "nombre": "T1",
                        "valor": nuevo_t1,
                        "aceptada": True,
                    }
                )
            if nuevo_v is not _SIN_VALOR:
                self._pendientes[DIRECCION_VEL_OBJETIVO] = nuevo_v
                self._log.append(
                    {
                        "ts": ts,
                        "origen": "ui",
                        "nombre": "V",
                        "valor": nuevo_v,
                        "aceptada": True,
                    }
                )
            return self._planta.consignas()

    def actualizar_parametros(self, cambios: dict[str, object]) -> dict[str, float | int | None]:
        """Aplica parámetros en caliente y devuelve el snapshot."""
        with self._lock:
            self._planta.actualizar_parametros(cambios)
            return self._planta.parametros()

    def reiniciar(
        self,
        *,
        t1_inicial: object = _SIN_VALOR,
        v_inicial: object = _SIN_VALOR,
        semilla: object = _SIN_VALOR,
    ) -> dict[str, object]:
        """Reinicia la planta y alinea las pendientes con los objetivos."""
        with self._lock:
            self._planta.reiniciar(t1_inicial=t1_inicial, v_inicial=v_inicial, semilla=semilla)
            # Los regs 100/104 son la fuente de verdad: hay que dejarlos
            # alineados con el reset o el próximo request Modbus los volvería
            # a imponer sobre los objetivos recién reiniciados.
            consignas = self._planta.consignas()
            self._pendientes = {
                DIRECCION_TEMP1_OBJETIVO: consignas["t1"],
                DIRECCION_VEL_OBJETIVO: consignas["v"],
            }
            return {
                "parametros": self._planta.parametros(),
                "consignas": consignas,
            }

    def fijar_rechazo(self, rechazar: bool) -> bool:
        """Cambia en caliente el rechazo de escrituras Modbus a consignas."""
        with self._lock:
            self._rechazar_setpoints = rechazar
            return rechazar

    def procesar_request_modbus(
        self,
        current_registers: list[int],
        base: int,
        address: int,
        set_values: list[int] | list[bool] | None,
    ) -> bool:
        """Puente de un request Modbus: pendientes, sync, avance y log.

        Devuelve True si la escritura de consigna debe rechazarse.
        """
        with self._lock:
            self._ultimo_request = time.time()
            # 1. Las consignas del panel aterrizan en los regs reales.
            for direccion, valor in self._pendientes.items():
                offset = base + direccion
                current_registers[offset : offset + 2] = codificar_float32(valor)
            self._pendientes.clear()

            # Sólo una escritura completa de los 2 regs del float32 cuenta
            # como consigna; una parcial no debe corromper el bloque.
            nombre = (
                NOMBRE_CONSIGNA.get(address)
                if set_values is not None and len(set_values) >= 2
                else None
            )
            rechazada = bool(nombre is not None and self._rechazar_setpoints)
            if nombre is not None and not rechazada:
                # Reflejar la escritura ya en los regs para que el sync de
                # abajo vea el valor nuevo en este mismo request (pymodbus
                # aplica set_values después del action).
                offset = base + address
                current_registers[offset : offset + 2] = list(set_values[:2])

            # 2. Los regs 100/104 siguen siendo la fuente de verdad.
            try:
                self._planta.fijar_objetivos(
                    decodificar_float32(current_registers[base + DIRECCION_TEMP1_OBJETIVO :][:2]),
                    decodificar_float32(current_registers[base + DIRECCION_VEL_OBJETIVO :][:2]),
                )
            except ValueError:
                logging.warning("consigna inválida en los regs 100/104; se conserva la anterior")

            # 3. Avanzar y refrescar los 16 regs de sensores.
            self._planta.avanzar()
            valores = self._planta.valores()
            for campo, offset in OFFSET_SENSORES.items():
                regs = codificar_float32(valores[campo])
                current_registers[base + offset : base + offset + 2] = regs

            # 4. Log acotado de escrituras de consigna.
            if nombre is not None:
                self._log.append(
                    {
                        "ts": time.time(),
                        "origen": "modbus",
                        "nombre": nombre,
                        "valor": decodificar_float32(list(set_values[:2])),
                        "aceptada": not rechazada,
                    }
                )
            return rechazada


# --- el action: puente entre el runtime pymodbus y la planta -----------------

# Tipo del callback `action` de SimDevice (pymodbus 3.15): se invoca en
# cada request con el bloque crudo; mutarlo inline afecta la respuesta.
Accion = Callable[
    [int, int, int, int, list[int], list[int] | None],
    Coroutine[None, None, object],
]

NOMBRE_CONSIGNA = {
    DIRECCION_TEMP1_OBJETIVO: "T1",
    DIRECCION_VEL_OBJETIVO: "V",
}


def crear_accion(estado: EstadoSimulador) -> Accion:
    """Devuelve el action async que sincroniza, avanza y refresca sensores."""

    async def accion(
        function_code: int,
        start_address: int,
        address: int,
        count: int,
        current_registers: list[int],
        set_values: list[int] | None,
    ) -> object:
        base = DIRECCION_BASE_SENSORES - start_address  # offset del mapa en el bloque
        rechazada = estado.procesar_request_modbus(current_registers, base, address, set_values)
        return ExcCodes.ILLEGAL_ADDRESS if rechazada else None

    return accion


# --- armado del dispositivo Modbus -------------------------------------------


def construir_dispositivo(planta: PlantaHorno, estado: EstadoSimulador) -> SimDevice:
    """SimDevice con el bloque de sensores y el de consignas.

    `DataType.REGISTERS` (regs crudos) en ambos: el encoding float32 lo
    hacemos nosotros para matchear el word order del cliente de la app
    (el `DataType.FLOAT32` de pymodbus guarda los words invertidos).
    Consignas iniciales: T1/V en sus valores de arranque (planta quieta).
    El par 102-103 queda en 0.0 porque el bloque se mantiene contiguo.

    `readonly` queda siempre en False: el rechazo dinámico se hace en el
    action para que `--rechazar-setpoints` y el toggle del panel funcionen
    en caliente sin recrear el dispositivo.
    """
    valores = planta.valores()
    consignas = planta.consignas()
    regs_sensores = [0] * CANTIDAD_REGS_SENSORES
    for campo, offset in OFFSET_SENSORES.items():
        regs_sensores[offset : offset + 2] = codificar_float32(valores[campo])
    regs_consignas = (
        codificar_float32(consignas["t1"])
        + codificar_float32(0.0)
        + codificar_float32(consignas["v"])
    )
    sensores = SimData(
        address=DIRECCION_BASE_SENSORES,
        count=1,
        values=regs_sensores,
        datatype=DataType.REGISTERS,
    )
    consignas_sim = SimData(
        address=DIRECCION_TEMP1_OBJETIVO,
        count=1,
        values=regs_consignas,
        datatype=DataType.REGISTERS,
        readonly=False,
    )
    return SimDevice(
        id=0,  # responde a cualquier unit_id
        simdata=[sensores, consignas_sim],
        action=crear_accion(estado),
    )


# --- panel web de pruebas (stdlib, sin autenticación) ------------------------


class ServidorPanel(ThreadingHTTPServer):
    """Servidor HTTP del panel, con el estado compartido colgado del server."""

    daemon_threads = True

    def __init__(self, direccion: tuple[str, int], estado: EstadoSimulador) -> None:
        self.estado = estado
        super().__init__(direccion, ManejadorPanel)


class ManejadorPanel(BaseHTTPRequestHandler):
    """Handler mínimo: sirve el HTML y una API JSON para el panel."""

    server: ServidorPanel

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logging.debug("panel %s - %s", self.address_string(), format % args)

    def _responder(self, codigo: int, cuerpo: bytes, tipo: str) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _responder_json(self, codigo: int, payload: object) -> None:
        cuerpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._responder(codigo, cuerpo, "application/json")

    def _leer_json(self, *, permitir_vacio: bool = False) -> dict[str, object]:
        """Lee y valida el cuerpo JSON (objeto, <= 64 KiB)."""
        largo_crudo = self.headers.get("Content-Length")
        if largo_crudo is None:
            if permitir_vacio:
                return {}
            raise ValueError("falta Content-Length")
        try:
            largo = int(largo_crudo)
        except ValueError:
            raise ValueError("Content-Length inválido") from None
        if largo < 0 or largo > LIMITE_CUERPO_HTTP:
            raise ValueError("cuerpo demasiado grande (máx 64 KiB)")
        if largo == 0:
            if permitir_vacio:
                return {}
            raise ValueError("cuerpo vacío")
        try:
            cuerpo = json.loads(self.rfile.read(largo))
        except json.JSONDecodeError:
            raise ValueError("JSON inválido") from None
        if not isinstance(cuerpo, dict):
            raise ValueError("el cuerpo debe ser un objeto JSON")
        return cuerpo

    def do_GET(self) -> None:  # noqa: N802
        ruta = self.path.split("?", 1)[0]
        try:
            if ruta == "/":
                self._servir_html()
            elif ruta == "/api/estado":
                self._responder_json(200, self.server.estado.instantanea())
            else:
                self._responder_json(404, {"error": "ruta no encontrada"})
        except Exception:
            logging.exception("error procesando GET %s", ruta)
            self._responder_json(500, {"error": "error interno del panel"})

    def do_POST(self) -> None:  # noqa: N802
        ruta = self.path.split("?", 1)[0]
        manejadores = {
            "/api/consignas": self._post_consignas,
            "/api/parametros": self._post_parametros,
            "/api/reiniciar": self._post_reiniciar,
            "/api/rechazar": self._post_rechazar,
        }
        manejador = manejadores.get(ruta)
        if manejador is None:
            self._responder_json(404, {"error": "ruta no encontrada"})
            return
        try:
            manejador()
        except ValueError as exc:
            self._responder_json(400, {"error": str(exc)})
        except Exception:
            logging.exception("error procesando %s", ruta)
            self._responder_json(500, {"error": "error interno del panel"})

    def _servir_html(self) -> None:
        ruta = Path(__file__).with_name("simulador_ui.html")
        try:
            cuerpo = ruta.read_bytes()
        except OSError:
            self._responder_json(
                500,
                {"error": f"no se pudo leer {ruta.name} junto al simulador"},
            )
            return
        self._responder(200, cuerpo, "text/html; charset=utf-8")

    def _post_consignas(self) -> None:
        cuerpo = self._leer_json()
        desconocidas = set(cuerpo) - {"t1", "v"}
        if desconocidas:
            raise ValueError(f"clave desconocida: {sorted(desconocidas)[0]}")
        if not cuerpo:
            raise ValueError("indicá al menos una consigna (t1 o v)")
        consignas = self.server.estado.aplicar_consignas(
            t1=cuerpo.get("t1", _SIN_VALOR), v=cuerpo.get("v", _SIN_VALOR)
        )
        self._responder_json(200, {"ok": True, "consignas": consignas})

    def _post_parametros(self) -> None:
        cuerpo = self._leer_json()
        parametros = self.server.estado.actualizar_parametros(cuerpo)
        self._responder_json(200, {"ok": True, "parametros": parametros})

    def _post_reiniciar(self) -> None:
        cuerpo = self._leer_json(permitir_vacio=True)
        desconocidas = set(cuerpo) - {"t1_inicial", "v_inicial", "semilla"}
        if desconocidas:
            raise ValueError(f"clave desconocida: {sorted(desconocidas)[0]}")
        resultado = self.server.estado.reiniciar(
            t1_inicial=cuerpo.get("t1_inicial", _SIN_VALOR),
            v_inicial=cuerpo.get("v_inicial", _SIN_VALOR),
            semilla=cuerpo.get("semilla", _SIN_VALOR),
        )
        self._responder_json(200, {"ok": True, **resultado})

    def _post_rechazar(self) -> None:
        cuerpo = self._leer_json()
        if set(cuerpo) != {"rechazar"}:
            raise ValueError('se espera {"rechazar": bool}')
        rechazar = cuerpo["rechazar"]
        if not isinstance(rechazar, bool):
            raise ValueError("rechazar debe ser booleano")
        valor = self.server.estado.fijar_rechazo(rechazar)
        self._responder_json(200, {"ok": True, "rechazar_setpoints": valor})


# --- CLI ----------------------------------------------------------------------


def _parsear_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Simulador Modbus TCP del horno SCA-400 (standalone). "
            "El panel web no tiene autenticación: por defecto escucha en "
            "127.0.0.1; bindearlo a 0.0.0.0 lo expone en la red."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind Modbus (default: 127.0.0.1)")
    parser.add_argument(
        "--puerto", type=int, default=5020, help="puerto Modbus TCP (default: 5020)"
    )
    parser.add_argument("--t1-inicial", type=float, default=150.0, help="°C (default: 150)")
    parser.add_argument("--v-inicial", type=float, default=0.15, help="m/s (default: 0.15)")
    parser.add_argument("--tau-t", type=float, default=60.0, help="τ de T en s (default: 60)")
    parser.add_argument("--tau-v", type=float, default=8.0, help="τ de V en s (default: 8)")
    parser.add_argument("--ruido-t", type=float, default=0.2, help="σ de T en °C (default: 0.2)")
    parser.add_argument(
        "--ruido-v", type=float, default=0.003, help="σ de V en m/s (default: 0.003)"
    )
    parser.add_argument(
        "--semilla", type=int, default=None, help="semilla del ruido (reproducible)"
    )
    parser.add_argument(
        "--rechazar-setpoints",
        action="store_true",
        help="consignas readonly: la escritura falla (estado inicial del toggle del panel)",
    )
    parser.add_argument(
        "--ui-host",
        default="127.0.0.1",
        help="bind del panel web (default: 127.0.0.1; 0.0.0.0 lo expone en la red)",
    )
    parser.add_argument(
        "--ui-puerto", type=int, default=8091, help="puerto del panel web (default: 8091)"
    )
    parser.add_argument("--sin-ui", action="store_true", help="no arrancar el panel web de pruebas")
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
        v_inicial=args.v_inicial,
        tau_t=args.tau_t,
        tau_v=args.tau_v,
        ruido_t=args.ruido_t,
        ruido_v=args.ruido_v,
        semilla=args.semilla,
    )
    estado = EstadoSimulador(
        planta,
        rechazar_setpoints=args.rechazar_setpoints,
        host=args.host,
        puerto=args.puerto,
    )
    dispositivo = construir_dispositivo(planta, estado)
    logging.info(
        "simulador SCA-400 en %s:%d (unit_id: cualquiera) | sensores @%d (%d regs) "
        "| consignas @%d/@%d%s",
        args.host,
        args.puerto,
        DIRECCION_BASE_SENSORES,
        CANTIDAD_REGS_SENSORES,
        DIRECCION_TEMP1_OBJETIVO,
        DIRECCION_VEL_OBJETIVO,
        " [RECHAZA CONSIGNAS]" if args.rechazar_setpoints else "",
    )
    logging.info(
        'en config.json: control.sensores/actuadores -> "driver": "modbus_tcp", '
        '"host": "%s", "port": %d',
        args.host,
        args.puerto,
    )
    servidor_ui: ServidorPanel | None = None
    try:
        if not args.sin_ui:
            servidor_ui = ServidorPanel((args.ui_host, args.ui_puerto), estado)
            threading.Thread(
                target=servidor_ui.serve_forever,
                name="panel-simulador",
                daemon=True,
            ).start()
            logging.info("panel en http://%s:%d", args.ui_host, args.ui_puerto)
            if args.ui_host not in ("127.0.0.1", "localhost", "::1"):
                logging.warning("el panel no tiene autenticación y está expuesto en la red")
        StartTcpServer(context=dispositivo, address=(args.host, args.puerto))
    except KeyboardInterrupt:
        logging.info("simulador detenido")
    finally:
        if servidor_ui is not None:
            servidor_ui.shutdown()
            servidor_ui.server_close()


if __name__ == "__main__":
    main()
