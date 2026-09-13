from abc import ABC, abstractmethod

from backend.domain.entities.device_metrics import DeviceMetrics


class ISystemMetricsProvider(ABC):
    @abstractmethod
    def sample(self) -> DeviceMetrics:
        """Toma una muestra de las métricas del sistema."""
        pass
