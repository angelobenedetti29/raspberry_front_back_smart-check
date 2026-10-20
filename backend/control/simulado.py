"""Planta de horno simulada y driver `simulado` (sin hardware).

Cubre las pruebas sin PLC/variador conectado (SCA-400): con
`control.sensores.driver` y `control.actuadores.driver` en `"simulado"`,
los datos del panel Horno y cinta cobran vida sin comunicación Modbus.

Dos formas de probar sin hardware, complementarias:

- **Driver `simulado` (este módulo)**: planta en memoria dentro del proceso
  de la app. El lector devuelve lecturas que derivan hacia el último
  setpoint aplicado. Ejercita la lógica del ControlService y la UI, no el
  stack Modbus.
- **Simulador externo** (`tools/simulador_modbus.py`, independiente de esta
  app): esclavo Modbus TCP con dinámica propia, se usa con
  `driver = "modbus_tcp"`. Ejercita el stack Modbus completo.

Como los stubs de `sensores.py`, este driver vive dentro de la app: la
factory `crear_lector`/`crear_aplicador` lo resuelve por config y corre en
el proceso de la Pi. La dinámica es un modelo de primer orden (convergencia
exponencial hacia el objetivo) con ruido gaussiano chico, avanzada por
wall-clock: sin consignas la planta queda quieta; con consignas converge
con las constantes de_tau de abajo.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time

from backend.control.tipos import LecturaHorno

logger = logging.getLogger(__name__)

# --- parámetros por defecto de la planta -------------------------------------
# Valores de arranque por debajo del setpoint típico (180-220 °C) para que
# la convergencia se vea en la gráfica del panel.

T1_INICIAL_C = 150.0
T2_INICIAL_C = 140.0
V_INICIAL_M_S = 0.15

# Constantes de tiempo de la convergencia (primer orden).
TAU_TEMP_SEGUNDOS = 60.0  # horno: masa térmica grande, tarda minutos
TAU_VEL_SEGUNDOS = 8.0  # variador: responde en segundos

# Ruido gaussiano por paso (sensor/planta, no de medición).
RUIDO_TEMP_C = 0.2
RUIDO_VEL_M_S = 0.003

# Sin objetivo propio (línea de un horno), la 2ª zona sigue a la 1ª.
T2_FACTOR_UN_HORNO = 0.92
COMB1_FACTOR = 0.78  # cámaras de combustión: rezagadas respecto de T1
COMB2_FACTOR = 0.70

# Magnitudes ambiente: reversión a la media alrededor de estos centros.
HUMEDAD_HORNO_PCT = 35.0
TEMP_AMBIENTE_C = 24.0
HUMEDAD_AMBIENTE_PCT = 55.0


class PlantaSimulada:
    """Modelo dinámico del horno + cinta para pruebas sin hardware.

    Estado interno (T1, T2, V, cámaras, humedades, ambiente) + objetivos de
    consigna. `avanzar()` integra un paso de primer orden hacia el objetivo
    usando el tiempo transcurrido real: puede llamarse desde cualquier hilo
    y con cualquier cadencia sin acumular error.

    - Sin `aplicar_setpoint(...)`: los objetivos son los valores iniciales
      y la planta queda (casi) quieta, con el ruido del termómetro.
    - Con consigna: T1/T2/V convergen exponencialmente (`tau_temp`/
      `tau_vel`); T2 sin objetivo propio deriva hacia `T1 * 0.92` (la
      segunda zona sigue a la primera, línea de un solo horno).
    """

    def __init__(
        self,
        *,
        t1_inicial: float = T1_INICIAL_C,
        t2_inicial: float = T2_INICIAL_C,
        v_inicial: float = V_INICIAL_M_S,
        tau_temp: float = TAU_TEMP_SEGUNDOS,
        tau_vel: float = TAU_VEL_SEGUNDOS,
        ruido_temp: float = RUIDO_TEMP_C,
        ruido_vel: float = RUIDO_VEL_M_S,
        semilla: int | None = None,
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
        # Objetivos: sin consigna, quieto (T2 arranca sin objetivo propio).
        self._obj_t1: float = t1_inicial
        self._obj_t2: float | None = None
        self._obj_v: float = v_inicial
        # Dinámica.
        self._tau_temp = tau_temp
        self._tau_vel = tau_vel
        self._ruido_temp = ruido_temp
        self._ruido_vel = ruido_vel
        self._ultimo_avance = time.monotonic()

    # --- consignas ---

    def aplicar_setpoint(
        self,
        temp_1: float,
        temp_2: float | None,
        velocidad: float,
    ) -> None:
        """Fija los objetivos de una vez (`temp_2=None` deja el actual)."""
        with self._lock:
            self._obj_t1 = temp_1
            if temp_2 is not None:
                self._obj_t2 = temp_2
            self._obj_v = max(0.0, velocidad)

    # --- dinámica ---

    def avanzar(self) -> None:
        """Integra el estado hasta el instante actual (idempotente)."""
        ahora = time.monotonic()
        with self._lock:
            dt = ahora - self._ultimo_avance
            self._ultimo_avance = ahora
            if dt > 0.0:
                self._paso(dt)

    def _paso(self, dt: float) -> None:
        """Un paso de integración (requiere el lock tomado)."""
        k_temp = 1.0 - math.exp(-dt / self._tau_temp)
        k_vel = 1.0 - math.exp(-dt / self._tau_vel)
        # T2 sin objetivo propio: la segunda zona sigue a la primera.
        obj_t2 = (
            self._obj_t2
            if self._obj_t2 is not None
            else self._t1 * T2_FACTOR_UN_HORNO
        )
        self._t1 = self._converger(self._t1, self._obj_t1, k_temp, self._ruido_temp)
        self._t2 = self._converger(self._t2, obj_t2, k_temp, self._ruido_temp)
        # Cámaras de combustión: rezagadas respecto de T1.
        self._comb1 = self._converger(
            self._comb1, self._t1 * COMB1_FACTOR, k_temp, self._ruido_temp
        )
        self._comb2 = self._converger(
            self._comb2, self._t1 * COMB2_FACTOR, k_temp, self._ruido_temp
        )
        # Velocidad: nunca negativa.
        self._v = max(0.0, self._converger(self._v, self._obj_v, k_vel, self._ruido_vel))
        # Ambiente: reversión a la media (random walk acotado).
        self._humedad_horno = self._deambular(self._humedad_horno, HUMEDAD_HORNO_PCT, 0.05)
        self._temp_ambiente = self._deambular(self._temp_ambiente, TEMP_AMBIENTE_C, 0.05)
        self._humedad_ambiente = self._deambular(
            self._humedad_ambiente, HUMEDAD_AMBIENTE_PCT, 0.10
        )

    def _converger(self, valor: float, objetivo: float, k: float, ruido: float) -> float:
        """Da un paso de primer orden hacia `objetivo` con ruido gaussiano."""
        nuevo = valor + (objetivo - valor) * k
        if ruido > 0.0:
            nuevo += self._rng.gauss(0.0, ruido)
        return nuevo

    def _deambular(self, valor: float, centro: float, escala: float) -> float:
        """Ruido con reversión a la media (humedades y ambiente)."""
        return centro + 0.99 * (valor - centro) + self._rng.gauss(0.0, escala)

    # --- lectura ---

    def estado(self) -> LecturaHorno:
        """Avanza al ahora y devuelve la lectura instantánea completa."""
        self.avanzar()
        with self._lock:
            return LecturaHorno(
                temp_horno_1=self._t1,
                temp_horno_2=self._t2,
                temp_comb_1=self._comb1,
                temp_comb_2=self._comb2,
                humedad_horno=self._humedad_horno,
                temp_ambiente=self._temp_ambiente,
                humedad_ambiente=self._humedad_ambiente,
                velocidad_cinta=self._v,
                temp_objetivo=self._obj_t1,
                vel_objetivo=self._obj_v,
                # La confianza viene del streaming, no del horno.
                confianza_deteccion=None,
            )


# --- driver: lector y aplicador ----------------------------------------------

# Planta única del proceso: lector y aplicador se crean por separado (uno
# por `control.sensores` y otro por `control.actuadores`, con configs
# distintas) pero deben compartir el mismo horno físico. Un SCA-400 por Pi.
_PLANTA: PlantaSimulada | None = None
_PLANTA_LOCK = threading.Lock()


def planta_compartida() -> PlantaSimulada:
    """Devuelve la planta del proceso (la crea al primer uso)."""
    global _PLANTA
    with _PLANTA_LOCK:
        if _PLANTA is None:
            _PLANTA = PlantaSimulada()
        return _PLANTA


class LectorSensoresSimulado:
    """Lector que expone la planta simulada (cumple `LectorSensores`)."""

    def __init__(self, planta: PlantaSimulada | None = None) -> None:
        self._planta = planta if planta is not None else planta_compartida()

    def leer(self) -> LecturaHorno:
        return self._planta.estado()


class AplicadorConsignaSimulado:
    """Aplicador que fija objetivos en la planta (cumple `AplicadorConsigna`).

    PLC "perfecto": acepta todo y devuelve la consigna como efectiva. El
    rechazo de escrituras se prueba por la vía Modbus, con el simulador
    externo en modo `--rechazar-setpoints`.
    """

    def __init__(self, planta: PlantaSimulada | None = None) -> None:
        self._planta = planta if planta is not None else planta_compartida()

    def aplicar(
        self,
        temp_objetivo: float,
        temp_objetivo_2: float | None,
        velocidad_objetivo: float,
    ) -> tuple[float | None, float | None, float | None]:
        self._planta.aplicar_setpoint(temp_objetivo, temp_objetivo_2, velocidad_objetivo)
        logger.info(
            "aplicador simulado: T1=%.2f T2=%s V=%.3f",
            temp_objetivo,
            temp_objetivo_2,
            velocidad_objetivo,
        )
        return (temp_objetivo, temp_objetivo_2, velocidad_objetivo)
