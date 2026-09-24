"""Puerto del monitor de métricas hacia las secciones de la interfaz."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class MetricasUI:
    """Métricas de hardware tal como las ve la interfaz."""

    cpu_pct: float | None
    mem_ram_disponible_mb: float | None
    mem_ram_total_mb: float | None
    almacenamiento_disponible_mb: float | None
    almacenamiento_total_mb: float | None
    temp_chip: float | None
    ai_processor_pct: float | None


@dataclass(frozen=True, slots=True)
class EnvioMonitorUI:
    """Un intento de envío de métricas al backend."""

    momento: datetime
    ok: bool
    error: str
    metricas: MetricasUI


@dataclass(frozen=True, slots=True)
class EstadoMonitorUI:
    """Estado del monitor del dispositivo, sin tipos del backend."""

    registrado: bool
    intervalo_segundos: float
    acelerador: str
    recolectadas: MetricasUI | None
    ultimo_envio: EnvioMonitorUI | None
    envios_ok: int
    envios_error: int
    recientes: tuple[EnvioMonitorUI, ...]


@runtime_checkable
class ControladorMonitor(Protocol):
    """Lo que la sección Métricas le pide al monitor del dispositivo."""

    def estado(self) -> EstadoMonitorUI: ...
