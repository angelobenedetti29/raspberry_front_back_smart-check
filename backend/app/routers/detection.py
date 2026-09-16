import json
import logging

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend.app.dependencies import get_detect_use_case, get_finalize_lote_use_case
from backend.app.errors import finalize_lote_http_exception
from backend.domain.entities.detection import is_burnt
from backend.use_cases.finalize_lote import LoteDeliveryError

router = APIRouter()
logger = logging.getLogger(__name__)


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
    """
    try:
        # Read file bytes
        contents = file.file.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            raise HTTPException(status_code=400, detail="Formato de imagen inválido.")

        # Execute Use Case
        detections = detect_use_case.execute(img)

        # Format response
        results = []
        for det in detections:
            res_item = {
                "label": det.label,
                "confidence": float(det.confidence),
                "bbox": {
                    "x": det.bbox[0],
                    "y": det.bbox[1],
                    "width": det.bbox[2],
                    "height": det.bbox[3],
                },
            }
            if hasattr(det, "id"):
                res_item["id"] = det.id
                res_item["state"] = det.state
            results.append(res_item)

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
