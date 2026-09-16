from fastapi import HTTPException

from backend.use_cases.finalize_lote import LoteDeliveryError

LOTE_FINALIZE_FAILED_MESSAGE = "No se pudo registrar el lote en el servidor central."
LOTE_START_FAILED_MESSAGE = "No se pudo iniciar el lote en el servidor central."


def lote_delivery_http_exception(
    *,
    message: str,
    target: str | None,
    status_code: int | None,
    error: str | None,
    response_text: str | None,
) -> HTTPException:
    """502 compartido por los envíos de lote (finalizar e iniciar)."""
    return HTTPException(
        status_code=502,
        detail={
            "message": message,
            "target": target,
            "status_code": status_code,
            "error": error,
            "response": response_text,
        },
    )


def finalize_lote_http_exception(exc: Exception) -> HTTPException:
    """Traduce un fallo de finalización de lote al HTTPException correspondiente.

    Compartido por ``routers/lotes.py`` y ``routers/detection.py``.
    """
    if isinstance(exc, LoteDeliveryError):
        return lote_delivery_http_exception(
            message=LOTE_FINALIZE_FAILED_MESSAGE,
            target=exc.target,
            status_code=exc.status_code,
            error=exc.error,
            response_text=exc.response_text,
        )

    if isinstance(exc, ValueError):
        # Error de validación del payload de lote.
        return HTTPException(status_code=400, detail=str(exc))

    # Cualquier otro fallo es inesperado para el cliente.
    return HTTPException(status_code=500, detail=str(exc))
