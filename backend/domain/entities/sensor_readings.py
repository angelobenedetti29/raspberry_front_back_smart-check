from dataclasses import dataclass


@dataclass(frozen=True)
class SensorReadings:
    tempHorno1: float
    tempCombHorno1: float
    tempHorno2: float
    tempCombHorno2: float
    velocidadCinta: float
