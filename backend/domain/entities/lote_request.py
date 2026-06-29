from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

@dataclass
class LoteRequest:
    productoId: UUID
    turno: str
    inicioAt: datetime
    finAt: datetime
    totalUnidades: int
    correctos: int
    quemados: int
    crudas: int
    correctosKg: float
    quemadosKg: float
    crudosKg: float
    tempHorno1: float
    tempCombHorno1: float
    tempHorno2: float
    tempCombHorno2: float
    velocidadCinta: float

    def __post_init__(self):
        if self.inicioAt >= self.finAt:
            raise ValueError("La fecha de inicio debe ser anterior a la fecha de fin.")
        if self.correctos < 0 or self.quemados < 0 or self.crudas < 0:
            raise ValueError("Los valores de correctos, quemados y crudas no pueden ser negativos.")
        if self.correctosKg < 0 or self.quemadosKg < 0 or self.crudosKg < 0:
            raise ValueError("Los valores de correctosKg, quemadosKg y crudosKg no pueden ser negativos.")
        if self.tempHorno1 < 0 or self.tempHorno2 < 0 or self.tempCombHorno1 < 0 or self.tempCombHorno2 < 0:
            raise ValueError("Las temperaturas del horno no pueden ser negativas.")
        if self.velocidadCinta < 0:
            raise ValueError("La velocidad de la cinta no puede ser negativa.")
        if self.totalUnidades != self.correctos + self.quemados + self.crudas:
            raise ValueError("El total de unidades debe ser igual a la suma de correctos, quemados y crudas.")