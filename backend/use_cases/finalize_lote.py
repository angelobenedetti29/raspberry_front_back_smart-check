from typing import Any, Dict

from backend.domain.entities.lote_request import LoteRequest


class LoteDeliveryError(RuntimeError):
    """Error de entrega de lote que conserva el detalle de la respuesta fallida.

    Agrupa status, mensaje, cuerpo y destino para que la capa HTTP pueda
    reportar el fallo del central sin volver a consultar el transporte.
    """

    def __init__(self, status_code, error, response_text, target):
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.response_text = response_text
        self.target = target


class FinalizeLoteUseCase:
    """Valida y envía el cierre de un lote, propagando los fallos de entrega."""

    def __init__(self, send_lote_use_case):
        self.send_lote_use_case = send_lote_use_case

    @property
    def target(self) -> str:
        """URL destino del envío, tomada del emisor de lote."""
        return self.send_lote_use_case.target

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Valida el payload camelCase, lo envía y arma la respuesta de éxito."""
        # El roundtrip valida y normaliza el payload antes de firmarlo.
        lote = LoteRequest.from_payload(payload)

        result = self.send_lote_use_case.execute(lote.to_payload())
        if not result.ok:
            raise LoteDeliveryError(
                status_code=result.status_code,
                error=result.error,
                response_text=result.response_text,
                target=self.target,
            )

        return {
            "status": "success",
            "message": "Lote enviado al servidor central.",
            "target": self.target,
        }
