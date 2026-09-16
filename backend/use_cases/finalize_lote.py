from typing import Any, Dict

from backend.domain.entities.lote_request import LoteRequest


class LoteDeliveryError(RuntimeError):
    def __init__(self, status_code, error, response_text, target):
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.response_text = response_text
        self.target = target


class FinalizeLoteUseCase:
    def __init__(self, send_lote_use_case):
        self.send_lote_use_case = send_lote_use_case

    @property
    def target(self) -> str:
        return self.send_lote_use_case.target

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        lote = LoteRequest.from_payload(payload)

        success = self.send_lote_use_case.execute(lote.to_payload())
        if not success:
            raise LoteDeliveryError(
                status_code=self.send_lote_use_case.get_last_status_code(),
                error=self.send_lote_use_case.get_last_error(),
                response_text=self.send_lote_use_case.get_last_response_text(),
                target=self.target,
            )

        return {
            "status": "success",
            "message": "Lote enviado al servidor central.",
            "target": self.target,
        }
