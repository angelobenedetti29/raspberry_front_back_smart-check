"""Adaptador entre el monitor de métricas y el puerto de la interfaz.

Es el único módulo del frontend que importa `backend.monitor`, igual que
`adaptador.py` lo es para `backend.streaming`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from frontend.nucleo.controlador_monitor import (
    EnvioMonitorUI,
    EstadoMonitorUI,
    MetricasUI,
)

if TYPE_CHECKING:
    from backend.monitor import EnvioMonitor, MonitorService
    from backend.monitor.metricas import MetricasSistema


class AdaptadorMonitor:
    """Implementa `ControladorMonitor` sobre un `MonitorService`."""

    def __init__(self, servicio: MonitorService) -> None:
        self._servicio = servicio

    def estado(self) -> EstadoMonitorUI:
        """Traduce la instantánea del backend a tipos de la interfaz."""
        estado = self._servicio.estado()
        return EstadoMonitorUI(
            registrado=estado.registrado,
            intervalo_segundos=estado.intervalo_segundos,
            acelerador=estado.acelerador or "",
            recolectadas=_metricas(estado.recolectadas),
            ultimo_envio=_envio(estado.ultimo_envio),
            envios_ok=estado.envios_ok,
            envios_error=estado.envios_error,
            recientes=tuple(_a_envio(e) for e in estado.recientes),
        )


def _a_metricas(metricas: MetricasSistema) -> MetricasUI:
    return MetricasUI(
        cpu_pct=metricas.cpu_pct,
        mem_ram_disponible_mb=metricas.mem_ram_disponible_mb,
        mem_ram_total_mb=metricas.mem_ram_total_mb,
        almacenamiento_disponible_mb=metricas.almacenamiento_disponible_mb,
        almacenamiento_total_mb=metricas.almacenamiento_total_mb,
        temp_chip=metricas.temp_chip,
        ai_processor_pct=metricas.ai_processor_pct,
    )


def _metricas(metricas: MetricasSistema | None) -> MetricasUI | None:
    return None if metricas is None else _a_metricas(metricas)


def _a_envio(envio: EnvioMonitor) -> EnvioMonitorUI:
    return EnvioMonitorUI(
        momento=envio.momento,
        ok=envio.ok,
        error=envio.error or "",
        metricas=_a_metricas(envio.metricas),
    )


def _envio(envio: EnvioMonitor | None) -> EnvioMonitorUI | None:
    return None if envio is None else _a_envio(envio)
