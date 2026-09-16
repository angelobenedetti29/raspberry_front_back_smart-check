from backend.use_cases.signed_sender import LoteSenderUseCase


class SendLoteRequestUseCase(LoteSenderUseCase):
    """Envía el cierre de un lote firmado (DeviceProof) al servidor central."""

    PATH = "/lotes"

    def execute(self, payload: dict) -> bool:
        return self._post(payload)
