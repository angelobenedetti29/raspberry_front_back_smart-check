from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from backend.app.config import get_settings
from backend.app.dependencies import (
    get_finalize_lote_use_case,
    get_send_lote_inicio_use_case,
)
from backend.app.errors import (
    LOTE_START_FAILED_MESSAGE,
    finalize_lote_http_exception,
    lote_delivery_http_exception,
)
from backend.use_cases.finalize_lote import LoteDeliveryError

router = APIRouter()


@router.post("/api/lotes/finalizar")
def finalizar_lote(
    lote_payload: Dict[str, Any],
    use_case=Depends(get_finalize_lote_use_case),
):
    try:
        return use_case.execute(lote_payload)
    except (ValueError, LoteDeliveryError) as e:
        raise finalize_lote_http_exception(e)


@router.post("/api/lotes/iniciar")
def iniciar_lote(
    payload: Dict[str, Any] | None = None,
    use_case=Depends(get_send_lote_inicio_use_case),
    settings=Depends(get_settings),
):
    """Inicia un lote en el servidor central (dispara la consigna automática)."""
    body = payload or {}
    horno_id = body.get("hornoId") or settings.horno_id
    producto_id = body.get("productoId") or settings.default_producto_id

    if not horno_id or not producto_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Se requieren 'hornoId' y 'productoId' "
                "(en el body o en la configuración)."
            ),
        )

    success = use_case.execute(horno_id, producto_id)
    if not success:
        raise lote_delivery_http_exception(
            message=LOTE_START_FAILED_MESSAGE,
            target=use_case.target,
            status_code=use_case.get_last_status_code(),
            error=use_case.get_last_error(),
            response_text=use_case.get_last_response_text(),
        )

    return {
        "status": "success",
        "message": "Inicio de lote enviado al servidor central.",
        "target": use_case.target,
    }
