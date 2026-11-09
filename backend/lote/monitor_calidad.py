"""Monitor de calidad por ventana deslizante temporal para detección de anomalías.

Mantiene buffers independientes para productos quemados y crudos dentro de
una ventana temporal (por defecto 10 s). Si se detectan 20 o más unidades de
un mismo defecto en esa ventana, dispara la alerta correspondiente
('PORCENTAJE_QUEMADOS_ALTO' o 'PORCENTAJE_CRUDOS_ALTO') con control de cooldown.
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
        if estado == EstadoProducto.OK:
            return None

        t = time.monotonic() if ahora is None else ahora
        limite_tiempo = t - self.ventana_segundos

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

    def reiniciar(self) -> None:
        """Limpia los buffers y reinicia el estado."""
        self._buffer_quemados.clear()
        self._buffer_crudos.clear()
        self._ultimo_disparo_quemados = -float("inf")
        self._ultimo_disparo_crudos = -float("inf")

