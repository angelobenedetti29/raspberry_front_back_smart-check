from typing import Dict
from backend.domain.entities.device import IoTDevice
from backend.domain.interfaces.iot_controller import IIoTController

class MockIoTController(IIoTController):
    def __init__(self):
        # In-memory dictionary to store device states
        self._devices: Dict[str, IoTDevice] = {
            "rele_tostadora": IoTDevice(id="rele_tostadora", name="Relé de la Tostadora", is_on=False, type="relay"),
            "alarma_buzzer": IoTDevice(id="alarma_buzzer", name="Buzzer de Alarma", is_on=False, type="alarm")
        }

    def turn_on(self, device_id: str) -> bool:
        if device_id in self._devices:
            self._devices[device_id].is_on = True
            print(f"[IoT Mock] >>> DISPOSITIVO ENCENDIDO: {self._devices[device_id].name} (ID: {device_id})")
            return True
        print(f"[IoT Mock] Error: Dispositivo {device_id} no encontrado")
        return False

    def turn_off(self, device_id: str) -> bool:
        if device_id in self._devices:
            self._devices[device_id].is_on = False
            print(f"[IoT Mock] <<< DISPOSITIVO APAGADO: {self._devices[device_id].name} (ID: {device_id})")
            return True
        print(f"[IoT Mock] Error: Dispositivo {device_id} no encontrado")
        return False

    def get_status(self, device_id: str) -> bool:
        if device_id in self._devices:
            return self._devices[device_id].is_on
        return False

    def get_device(self, device_id: str) -> IoTDevice:
        if device_id in self._devices:
            return self._devices[device_id]
        raise KeyError(f"Dispositivo '{device_id}' no registrado en el controlador IoT.")
        
    def get_all_devices(self) -> Dict[str, IoTDevice]:
        return self._devices
