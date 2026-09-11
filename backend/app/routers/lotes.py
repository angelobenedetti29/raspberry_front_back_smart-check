from typing import Any, Dict

from fastapi import APIRouter, Depends

from backend.app.dependencies import get_finalize_lote_use_case
from backend.app.errors import finalize_lote_http_exception
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
