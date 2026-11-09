"""Monitor de calidad por ventana deslizante temporal para detección de anomalías.

Mantiene buffers independientes para productos quemados y crudos dentro de
una ventana temporal (por defecto 10 s). Si se detectan `umbral_anomalia` o más
unidades de un mismo defecto en esa ventana, dispara la alerta correspondiente
('PORCENTAJE_QUEMADOS_ALTO' o 'PORCENTAJE_CRUDOS_ALTO') con control de cooldown.
Además lleva un buffer con todos los estados (también los OK) para poder medir
el porcentaje real de cada defecto dentro de la ventana (`porcentaje`).
"""

from __future__ import annotations

import time
from collections import deque

from backend.lote.tipos import EstadoProducto

TIPO_ALERTA_QUEMADOS = "PORCENTAJE_QUEMADOS_ALTO"
TIPO_ALERTA_CRUDOS = "PORCENTAJE_CRUDOS_ALTO"


class MonitorCalidad:
    """Evalúa anomalías en la producción en base a una ventana deslizante de tiempo."""

    def __init__(
        self,
        umbral_anomalia: int = 20,
        ventana_segundos: float = 10.0,
        cooldown_segundos: float = 60.0,
    ) -> None:
        self.umbral_anomalia = umbral_anomalia
        self.ventana_segundos = ventana_segundos
        self.cooldown_segundos = cooldown_segundos

        self._buffer_total: deque[float] = deque()
        self._buffer_quemados: deque[float] = deque()
        self._buffer_crudos: deque[float] = deque()

        self._ultimo_disparo_quemados: float = -float("inf")
        self._ultimo_disparo_crudos: float = -float("inf")

    def registrar(
        self,
        estado: EstadoProducto,
        ahora: float | None = None,
    ) -> str | None:
        """Registra el estado de un producto confirmado y evalúa si dispara alerta.

        Retorna el tipo de alerta (ej: 'PORCENTAJE_QUEMADOS_ALTO' o
        'PORCENTAJE_CRUDOS_ALTO') o None si no se superó el umbral o está en cooldown.
        """
        t = time.monotonic() if ahora is None else ahora
        limite_tiempo = t - self.ventana_segundos

        # El total incluye los OK: es el denominador del porcentaje.
        self._buffer_total.append(t)
        while self._buffer_total and self._buffer_total[0] < limite_tiempo:
            self._buffer_total.popleft()

        if estado == EstadoProducto.OK:
            return None

        if estado == EstadoProducto.QUEMADO:
            self._buffer_quemados.append(t)
            while self._buffer_quemados and self._buffer_quemados[0] < limite_tiempo:
                self._buffer_quemados.popleft()

            if len(self._buffer_quemados) >= self.umbral_anomalia:
                if (t - self._ultimo_disparo_quemados) >= self.cooldown_segundos:
                    self._ultimo_disparo_quemados = t
                    return TIPO_ALERTA_QUEMADOS

        elif estado == EstadoProducto.CRUDO:
            self._buffer_crudos.append(t)
            while self._buffer_crudos and self._buffer_crudos[0] < limite_tiempo:
                self._buffer_crudos.popleft()

            if len(self._buffer_crudos) >= self.umbral_anomalia:
                if (t - self._ultimo_disparo_crudos) >= self.cooldown_segundos:
                    self._ultimo_disparo_crudos = t
                    return TIPO_ALERTA_CRUDOS

        return None

    def porcentaje(self, tipo: str, ahora: float | None = None) -> float:
        """Porcentaje (0-100) del defecto `tipo` sobre todo lo registrado en la ventana.

        `tipo` es el que devolvió `registrar` (quemados o crudos). Se mide con
        los mismos eventos que usa el umbral: anomalías del tipo / total de
        eventos de la ventana (OK incluidos). Sin eventos devuelve 0.0.
        """
        t = time.monotonic() if ahora is None else ahora
        limite_tiempo = t - self.ventana_segundos
        total = sum(1 for x in self._buffer_total if x >= limite_tiempo)
        if total == 0:
            return 0.0
        buffer = self._buffer_crudos if tipo == TIPO_ALERTA_CRUDOS else self._buffer_quemados
        anomalias = sum(1 for x in buffer if x >= limite_tiempo)
        return 100.0 * anomalias / total

    def reiniciar(self) -> None:
        """Limpia los buffers y reinicia el estado."""
        self._buffer_total.clear()
        self._buffer_quemados.clear()
        self._buffer_crudos.clear()
        self._ultimo_disparo_quemados = -float("inf")
        self._ultimo_disparo_crudos = -float("inf")

