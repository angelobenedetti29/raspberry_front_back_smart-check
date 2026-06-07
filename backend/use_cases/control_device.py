from backend.domain.entities.device import IoTDevice
from backend.domain.interfaces.iot_controller import IIoTController

class ControlDeviceUseCase:
    def __init__(self, iot_controller: IIoTController):
        self.iot_controller = iot_controller

    def turn_on_device(self, device_id: str) -> bool:
        print(f"[Use Case] Solicitando ENCENDER el dispositivo: {device_id}")
        return self.iot_controller.turn_on(device_id)

    def turn_off_device(self, device_id: str) -> bool:
        print(f"[Use Case] Solicitando APAGAR el dispositivo: {device_id}")
        return self.iot_controller.turn_off(device_id)

    def get_device_info(self, device_id: str) -> IoTDevice:
        return self.iot_controller.get_device(device_id)
