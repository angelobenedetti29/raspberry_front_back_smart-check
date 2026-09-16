from dataclasses import dataclass


@dataclass
class DeviceMetrics:
    """Métricas de sistema que la Raspberry reporta al servidor central.

    DTO explícito del contrato HTTP (decisión Y2): ``to_ping_payload`` vive aquí
    a propósito, junto a los campos que mapea.
    """

    cpu_pct: float
    mem_ram_disponible_mb: float
    mem_ram_total_mb: float | None = None
    almacenamiento_disponible_mb: float | None = None
    almacenamiento_total_mb: float | None = None
    temp_chip: float = 0.0
    ai_processor_pct: float = 0.0

    def to_ping_payload(self, dispositivo_id: str) -> dict:
        """Arma el body camelCase exacto que espera POST /dispositivos/ping."""
        payload = {
            "dispositivoId": dispositivo_id,
            "cpuPct": round(float(self.cpu_pct), 2),
            "memRamDisponibleMb": round(float(self.mem_ram_disponible_mb), 2),
            "tempChip": round(float(self.temp_chip), 2),
            "aiProcessorPct": round(float(self.ai_processor_pct), 2),
        }

        # memRamTotalMb es opcional: solo se incluye si está disponible.
        if self.mem_ram_total_mb is not None:
            payload["memRamTotalMb"] = round(float(self.mem_ram_total_mb), 2)

        # El almacenamiento va siempre en par: ambos o ninguno.
        if (
            self.almacenamiento_disponible_mb is not None
            and self.almacenamiento_total_mb is not None
        ):
            payload["almacenamientoDisponibleMb"] = round(
                float(self.almacenamiento_disponible_mb), 2
            )
            payload["almacenamientoTotalMb"] = round(
                float(self.almacenamiento_total_mb), 2
            )

        return payload
