import json
import logging

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend.app.dependencies import get_detect_use_case, get_finalize_lote_use_case
from backend.app.errors import finalize_lote_http_exception
from backend.app.serializers import detection_to_dict
from backend.domain.entities.detection import is_burnt
from backend.use_cases.finalize_lote import LoteDeliveryError

router = APIRouter()
logger = logging.getLogger(__name__)

# Cota de bytes que el endpoint entrega a cv2.imdecode. NO limita la ingesta:
# Starlette/FastAPI ya recibió y bufferizó (spool a disco) el cuerpo completo
# antes de ejecutar este cuerpo de endpoint, así que esto solo acota la memoria
# del `bytes` post-parseo y produce un 413 cuando se excede. NO acota el
# spooling en disco, la CPU/red del parseo multipart, ni el tamaño del bitmap
# decodificado (un PNG/JPEG pequeño puede descomprimir a una imagen enorme), ni
# los campos del formulario (el max_part_size de 1 MiB de Starlette aplica a
# campos, no a partes de archivo). Un límite real de tamaño de cuerpo corresponde
# al proxy o al ASGI server (run.py lanza uvicorn) o a un middleware ASGI que
# cuente `receive`.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@router.post("/api/detect")
def detect_toast(
    file: UploadFile = File(...),
    lote_payload: str | None = Form(None),
    detect_use_case=Depends(get_detect_use_case),
    finalize_lote_use_case=Depends(get_finalize_lote_use_case),
):
    """
    Recibe una imagen a través de HTTP POST y realiza la inferencia con YOLOv11.

    Es un endpoint sincrónico a propósito: FastAPI lo ejecuta en su threadpool,
    de modo que la inferencia (CPU/NPU) no bloquea el event loop.

    Devuelve las detecciones y un resumen (``total_detected`` y
    ``burned_toast_found``); si se envía ``lote_payload`` (JSON), finaliza el
    lote y agrega el resultado en la clave ``lote``. Responde 400 si la imagen
    o el ``lote_payload`` son inválidos, 502 si falla el envío del lote y 500
    ante cualquier error interno de la inferencia.
    """
    try:
        # Lee los bytes del archivo subido, con una cota dura de memoria: se pide
        # un byte de más para poder distinguir "exactamente el límite" de "excedido".
        contents = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(contents) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="La imagen excede el tamaño máximo permitido.",
            )
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            raise HTTPException(status_code=400, detail="Formato de imagen inválido.")

        # Ejecuta el caso de uso.
        detections = detect_use_case.execute(img)

        # Formatea la respuesta.
        results = [detection_to_dict(det) for det in detections]

        has_burned = any(
            is_burnt(det.label, getattr(det, "state", None)) for det in detections
        )

        response_payload = {
            "detections": results,
            "summary": {
                "total_detected": len(results),
                "burned_toast_found": has_burned,
            },
        }

        if lote_payload:
            try:
                parsed_payload = json.loads(lote_payload)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="El campo lote_payload debe ser un JSON válido.") from exc

            if not isinstance(parsed_payload, dict):
                raise HTTPException(status_code=400, detail="El campo lote_payload debe ser un objeto JSON.")

            try:
                response_payload["lote"] = finalize_lote_use_case.execute(parsed_payload)
            except (ValueError, LoteDeliveryError) as exc:
                raise finalize_lote_http_exception(exc)

        return response_payload

    except HTTPException:
        raise
    except Exception:
        # No exponer detalles internos (paths, errores de numpy/OpenCV) al cliente.
        logger.exception("Error inesperado en /api/detect")
        raise HTTPException(status_code=500, detail="Error interno durante la inferencia.")
