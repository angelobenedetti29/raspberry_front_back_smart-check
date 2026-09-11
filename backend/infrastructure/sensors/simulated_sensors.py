import random

from backend.domain.entities.sensor_readings import SensorReadings
from backend.domain.interfaces.sensor_provider import ISensorProvider


class SimulatedSensorProvider(ISensorProvider):
    """Reproduces the simulation ranges used by the frontend worker."""

    def __init__(self, rng: random.Random | None = None):
        self._rng = rng if rng is not None else random.Random()

    def read(self) -> SensorReadings:
        return SensorReadings(
            tempHorno1=220.0 + self._rng.uniform(-1.5, 1.5),
            tempCombHorno1=315.0 + self._rng.uniform(-2.0, 2.0),
            tempHorno2=218.0 + self._rng.uniform(-1.5, 1.5),
            tempCombHorno2=312.0 + self._rng.uniform(-2.0, 2.0),
            velocidadCinta=1.10 + self._rng.uniform(-0.05, 0.05),
        )
