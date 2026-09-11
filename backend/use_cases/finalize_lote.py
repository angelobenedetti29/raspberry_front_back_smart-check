from datetime import datetime
from typing import Any, Dict
from uuid import UUID

from backend.domain.entities.lote_request import LoteRequest

REQUIRED_FIELDS = [
    "productoId",
    "turno",
    "inicioAt",
    "finAt",
    "totalUnidades",
    "correctos",
    "quemados",
    "crudas",
    "correctosKg",
    "quemadosKg",
    "crudosKg",
    "tempHorno1",
    "tempCombHorno1",
    "tempHorno2",
    "tempCombHorno2",
    "velocidadCinta",
]


class LoteDeliveryError(RuntimeError):
    def __init__(self, status_code, error, response_text, target):
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.response_text = response_text
        self.target = target


def parse_datetime(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    raise ValueError(f"Formato de fecha inválido: {val}")


def parse_uuid(val: Any) -> UUID:
    if isinstance(val, UUID):
        return val
    if isinstance(val, str):
        return UUID(val)
    raise ValueError(f"Formato UUID inválido: {val}")


class FinalizeLoteUseCase:
    def __init__(self, send_lote_use_case, base_url: str):
        self.send_lote_use_case = send_lote_use_case
        self.base_url = base_url

    @property
    def target(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/lotes"

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        missing_fields = [field for field in REQUIRED_FIELDS if field not in payload]
        if missing_fields:
            raise ValueError(f"Faltan campos obligatorios: {', '.join(missing_fields)}")

        # Validate the payload with the domain entity's business rules.
        try:
            LoteRequest(
                productoId=parse_uuid(payload["productoId"]),
                turno=str(payload["turno"]),
                inicioAt=parse_datetime(payload["inicioAt"]),
                finAt=parse_datetime(payload["finAt"]),
                totalUnidades=int(payload["totalUnidades"]),
                correctos=int(payload["correctos"]),
                quemados=int(payload["quemados"]),
                crudas=int(payload["crudas"]),
                correctosKg=float(payload["correctosKg"]),
                quemadosKg=float(payload["quemadosKg"]),
                crudosKg=float(payload["crudosKg"]),
                tempHorno1=float(payload["tempHorno1"]),
                tempCombHorno1=float(payload["tempCombHorno1"]),
                tempHorno2=float(payload["tempHorno2"]),
                tempCombHorno2=float(payload["tempCombHorno2"]),
                velocidadCinta=float(payload["velocidadCinta"]),
            )
        except (ValueError, TypeError, KeyError) as e:
            raise ValueError(str(e)) from e

        success = self.send_lote_use_case.execute(payload)
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
