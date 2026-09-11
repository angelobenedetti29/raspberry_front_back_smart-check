from fastapi import APIRouter, Depends, HTTPException

from backend.app.dependencies import get_control_device_use_case, get_iot_controller

router = APIRouter()


@router.get("/api/devices")
def list_devices(iot_controller=Depends(get_iot_controller)):
    devices = iot_controller.get_all_devices()
    return [
        {
            "id": dev.id,
            "name": dev.name,
            "is_on": dev.is_on,
            "type": dev.type,
        }
        for dev in devices.values()
    ]


@router.post("/api/devices/{device_id}/turn-on")
def turn_on_device(device_id: str, use_case=Depends(get_control_device_use_case)):
    success = use_case.turn_on_device(device_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Dispositivo '{device_id}' no encontrado.")
    return {"status": "success", "device_id": device_id, "is_on": True}


@router.post("/api/devices/{device_id}/turn-off")
def turn_off_device(device_id: str, use_case=Depends(get_control_device_use_case)):
    success = use_case.turn_off_device(device_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Dispositivo '{device_id}' no encontrado.")
    return {"status": "success", "device_id": device_id, "is_on": False}


@router.post("/api/devices/{device_id}/toggle")
def toggle_device(device_id: str, use_case=Depends(get_control_device_use_case)):
    try:
        device = use_case.get_device_info(device_id)
        if device.is_on:
            use_case.turn_off_device(device_id)
        else:
            use_case.turn_on_device(device_id)
        return {"status": "success", "device_id": device_id, "is_on": device.is_on}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Dispositivo '{device_id}' no encontrado.")
