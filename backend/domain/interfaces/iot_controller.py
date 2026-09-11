from abc import ABC, abstractmethod
from typing import Dict
from backend.domain.entities.device import IoTDevice

class IIoTController(ABC):
    @abstractmethod
    def turn_on(self, device_id: str) -> bool:
        pass

    @abstractmethod
    def turn_off(self, device_id: str) -> bool:
        pass

    @abstractmethod
    def get_status(self, device_id: str) -> bool:
        pass

    @abstractmethod
    def get_device(self, device_id: str) -> IoTDevice:
        pass

    @abstractmethod
    def get_all_devices(self) -> Dict[str, IoTDevice]:
        pass
