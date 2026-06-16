from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

@dataclass
class LoteRequest:
    id: UUID
    productoId: UUID
    productoNombre: str
    turno: str
    inicioAt: datetime
    finAt: datetime
    totalUnidades: int
    correctos: int
    quemados: int
    correctosKg: float
    quemadosKg: float
    tempHorno1: float
    tempHorno2: float
    velocidadHorno: float
    createdAt: datetime
    updatedAt: datetime
    tempCombHorno1: Optional[float] = None
    tempCombHorno2: Optional[float] = None

    def __post_init__(self):
        if self.inicioAt >= self.finAt:
            raise ValueError("La fecha de inicio debe ser anterior a la fecha de fin.")
        if self.correctos < 0 or self.quemados < 0:
            raise ValueError("Los valores de correctos y quemados no pueden ser negativos.")
        if self.correctosKg < 0 or self.quemadosKg < 0:
            raise ValueError("Los valores de correctosKg y quemadosKg no pueden ser negativos.")
        if self.tempHorno1 < 0 or self.tempHorno2 < 0:
            raise ValueError("Las temperaturas del horno no pueden ser negativas.")
        if self.velocidadHorno < 0:
            raise ValueError("La velocidad del horno no puede ser negativa.")
        if self.totalUnidades != self.correctos + self.quemados:
            raise ValueError("El total de unidades debe ser igual a la suma de correctos y quemados.")