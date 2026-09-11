from abc import ABC, abstractmethod

from backend.domain.entities.sensor_readings import SensorReadings


class ISensorProvider(ABC):
    @abstractmethod
    def read(self) -> SensorReadings:
        """Read the current sensor values."""
        pass
