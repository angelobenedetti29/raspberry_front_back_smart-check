"""Modelo de métricas de hardware y su serialización al contrato del backend."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MetricasSistema:
    """Foto de las métricas de hardware de un dispositivo.

    Todos los campos son opcionales: si una métrica no se puede obtener, su
    valor es None y el payload la envía como null (o la omite, según el
    contrato del backend) en vez de interrumpir la recolección.
    """

    cpu_pct: float | None = None
    mem_ram_disponible_mb: float | None = None
    mem_ram_total_mb: float | None = None
    almacenamiento_disponible_mb: float | None = None
    almacenamiento_total_mb: float | None = None
    temp_chip: float | None = None
    ai_processor_pct: float | None = None

    def a_payload(self) -> dict[str, Any]:
        """Arma el JSON exacto que acepta POST /api/v1/dispositivos/ping.

        El backend usa un decoder estricto: sólo admite estas claves, no acepta
        un timestamp (lo pone él) y exige que el par de almacenamiento vaya
        completo o ausente. cpuPct, memRamDisponibleMb, tempChip y
        aiProcessorPct son float64 no-pointer: si viajan como null el backend
        los guarda como 0, que es su representación de "no disponible".
        """
        payload: dict[str, Any] = {
            "cpuPct": self.cpu_pct,
            "memRamDisponibleMb": self.mem_ram_disponible_mb,
            "tempChip": self.temp_chip,
            "aiProcessorPct": self.ai_processor_pct,
        }
        if self.mem_ram_total_mb is not None:
            payload["memRamTotalMb"] = self.mem_ram_total_mb
        if (
            self.almacenamiento_disponible_mb is not None
            and self.almacenamiento_total_mb is not None
        ):
            payload["almacenamientoDisponibleMb"] = self.almacenamiento_disponible_mb
            payload["almacenamientoTotalMb"] = self.almacenamiento_total_mb
        return payload
