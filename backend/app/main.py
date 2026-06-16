import cv2
import numpy as np
import os
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any, List

# Domain & Use Cases
from backend.infrastructure.ai.yolo_detector import YoloDetector
from backend.infrastructure.iot.mock_controller import MockIoTController
from backend.infrastructure.http.requests_client import RequestsHttpClient
from backend.use_cases.detect_and_notify import DetectAndNotifyUseCase
from backend.use_cases.control_device import ControlDeviceUseCase
from backend.use_cases.send_lote_request import SendLoteRequestUseCase


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(Path(__file__).resolve().parents[1] / ".env")

app = FastAPI(
    title="YOLOv11 IoT Toast Detection API",
    description="Backend en Arquitectura Limpia para control de IoT e inferencia YOLO",
    version="1.0.0"
)

# CORS middleware for local frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instantiate Infrastructure adapters
try:
    detector = YoloDetector()
    print("[Backend] Detector YOLO inicializado correctamente.")
except Exception as e:
    print(f"[Backend] Advertencia al cargar YOLO (se usará detección simulada si falla): {e}")
    # Create a mock detector if model not found to prevent server crash during tests
    class MockDetector:
        def detect_frame(self, frame):
            return []
        def get_class_names(self):
            return ['Tostada Quemada', 'tostadas ok']
    detector = MockDetector()

iot_controller = MockIoTController()
http_client = RequestsHttpClient()
central_lotes_base_url = os.getenv("CENTRAL_LOTES_BASE_URL", "http://localhost:8080")
central_lotes_api_key = os.getenv("CENTRAL_LOTES_API_KEY", "")

# Instantiate Use Cases
detect_use_case = DetectAndNotifyUseCase(detector, iot_controller, http_client)
control_device_use_case = ControlDeviceUseCase(iot_controller)
send_lote_use_case = SendLoteRequestUseCase(http_client, central_lotes_base_url, central_lotes_api_key)


@app.get("/api/status")
def get_status():
    return {
        "status": "online",
        "detector": {
            "model_path": getattr(detector, "model_path", "Mocked"),
            "classes": detector.get_class_names()
        }
    }


@app.get("/api/devices")
def list_devices():
    devices = iot_controller.get_all_devices()
    return [
        {
            "id": dev.id,
            "name": dev.name,
            "is_on": dev.is_on,
            "type": dev.type
        } for dev in devices.values()
    ]


@app.post("/api/devices/{device_id}/turn-on")
def turn_on_device(device_id: str):
    success = control_device_use_case.turn_on_device(device_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Dispositivo '{device_id}' no encontrado.")
    return {"status": "success", "device_id": device_id, "is_on": True}


@app.post("/api/devices/{device_id}/turn-off")
def turn_off_device(device_id: str):
    success = control_device_use_case.turn_off_device(device_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Dispositivo '{device_id}' no encontrado.")
    return {"status": "success", "device_id": device_id, "is_on": False}


@app.post("/api/devices/{device_id}/toggle")
def toggle_device(device_id: str):
    try:
        device = control_device_use_case.get_device_info(device_id)
        if device.is_on:
            control_device_use_case.turn_off_device(device_id)
        else:
            control_device_use_case.turn_on_device(device_id)
        return {"status": "success", "device_id": device_id, "is_on": device.is_on}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Dispositivo '{device_id}' no encontrado.")


@app.post("/api/lotes/finalizar")
def finalizar_lote(lote_payload: Dict[str, Any]):
    try:
        required_fields = [
            "id",
            "productoId",
            "productoNombre",
            "turno",
            "inicioAt",
            "finAt",
            "totalUnidades",
            "correctos",
            "quemados",
            "correctosKg",
            "quemadosKg",
            "tempHorno1",
            "tempCombHorno1",
            "tempHorno2",
            "tempCombHorno2",
            "velocidadHorno",
            "createdAt",
            "updatedAt",
        ]
        missing_fields = [field for field in required_fields if field not in lote_payload]
        if missing_fields:
            raise HTTPException(status_code=400, detail=f"Faltan campos obligatorios: {', '.join(missing_fields)}")

        success = send_lote_use_case.execute(lote_payload)
        if not success:
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "No se pudo registrar el lote en el servidor central.",
                    "target": f"{central_lotes_base_url.rstrip('/')}/api/v1/lotes",
                    "status_code": send_lote_use_case.get_last_status_code(),
                    "error": send_lote_use_case.get_last_error(),
                    "response": send_lote_use_case.get_last_response_text(),
                },
            )
        return {
            "status": "success",
            "message": "Lote enviado al servidor central.",
            "target": f"{central_lotes_base_url.rstrip('/')}/api/v1/lotes",
        }
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Falta el campo obligatorio: {str(e)}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/detect")
async def detect_toast(file: UploadFile = File(...), notification_url: str = None):
    """
    Recibe una imagen a través de HTTP POST, realiza la inferencia con YOLOv11
    y notifica/actualiza el hardware de IoT si se detecta una tostada quemada.
    """
    try:
        # Read file bytes
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            raise HTTPException(status_code=400, detail="Formato de imagen inválido.")

        # Execute Use Case
        detections = detect_use_case.execute(img, notification_url=notification_url)
        
        # Format response
        results = []
        for det in detections:
            results.append({
                "label": det.label,
                "confidence": float(det.confidence),
                "bbox": {
                    "x": det.bbox[0],
                    "y": det.bbox[1],
                    "width": det.bbox[2],
                    "height": det.bbox[3]
                }
            })
            
        return {
            "detections": results,
            "summary": {
                "total_detected": len(results),
                "burned_toast_found": any(det.label.lower() == "tostada quemada" for det in detections)
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en inferencia: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
