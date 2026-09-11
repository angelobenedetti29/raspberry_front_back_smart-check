from fastapi import HTTPException

from backend.use_cases.finalize_lote import LoteDeliveryError


def finalize_lote_http_exception(exc: Exception) -> HTTPException:
    """Translate a lote finalization failure into the matching HTTPException.

    Shared by ``routers/lotes.py`` and ``routers/detection.py`` so the two
    endpoints cannot drift apart.
    """
    if isinstance(exc, LoteDeliveryError):
        return HTTPException(
            status_code=502,
            detail={
                "message": "No se pudo registrar el lote en el servidor central.",
                "target": exc.target,
                "status_code": exc.status_code,
                "error": exc.error,
                "response": exc.response_text,
            },
        )

    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))

    return HTTPException(status_code=500, detail=str(exc))
