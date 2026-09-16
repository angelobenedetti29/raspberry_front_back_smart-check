import logging
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
logger = logging.getLogger(__name__)


@router.post("/api/lotes/finalizar")
def finalizar_lote(
    lote_payload: Dict[str, Any],
    use_case=Depends(get_finalize_lote_use_case),
):
    """Finaliza un lote enviándolo al servidor central.

    Devuelve el resultado del envío. Responde 400 si el payload es inválido
    (``ValueError``), 502 si falla la entrega y 500 ante cualquier error interno
    inesperado.
    """
    try:
        try:
            return use_case.execute(lote_payload)
        except (ValueError, LoteDeliveryError) as e:
            raise finalize_lote_http_exception(e) from e
    except HTTPException:
        raise
    except Exception:
        # No exponer detalles internos del caso de uso al cliente.
        logger.exception("Error inesperado en /api/lotes/finalizar")
        raise HTTPException(
            status_code=500, detail="Error interno al finalizar el lote."
        )


@router.post("/api/lotes/iniciar")
def iniciar_lote(
    payload: Dict[str, Any] | None = None,
    use_case=Depends(get_send_lote_inicio_use_case),
    settings=Depends(get_settings),
):
    """Inicia un lote en el servidor central (dispara la consigna automática).

    Toma ``hornoId`` y ``productoId`` del body y cae a la configuración del
    dispositivo si faltan. Responde 400 si no se pueden resolver, 502 si el
    envío falla y 500 ante cualquier error interno inesperado.
    """
    body = payload or {}
    horno_id = body.get("hornoId") or settings.horno_id
    producto_id = body.get("productoId") or settings.default_producto_id

    try:
        try:
            result = use_case.execute(horno_id, producto_id)
        except LoteDeliveryError as exc:
            # Un caso de uso puede reportar el fallo de entrega con su propio detalle.
            raise lote_delivery_http_exception(
                message=LOTE_START_FAILED_MESSAGE,
                target=exc.target,
                status_code=exc.status_code,
                error=exc.error,
                response_text=exc.response_text,
            ) from exc
        except ValueError as exc:
            # La validación de hornoId/productoId vive en el caso de uso.
            raise HTTPException(
                status_code=400,
                detail=(
                    "Se requieren 'hornoId' y 'productoId' "
                    "(en el body o en la configuración)."
                ),
            ) from exc

        if not result.ok:
            raise lote_delivery_http_exception(
                message=LOTE_START_FAILED_MESSAGE,
                target=use_case.target,
                status_code=result.status_code,
                error=result.error,
                response_text=result.response_text,
            )

        return {
            "status": "success",
            "message": "Inicio de lote enviado al servidor central.",
            "target": use_case.target,
        }
    except HTTPException:
        raise
    except Exception:
        # No exponer detalles internos del caso de uso al cliente.
        logger.exception("Error inesperado en /api/lotes/iniciar")
        raise HTTPException(
            status_code=500, detail="Error interno al iniciar el lote."
        )
