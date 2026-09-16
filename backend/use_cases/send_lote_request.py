from backend.use_cases.signed_sender import LoteSenderUseCase


class SendLoteRequestUseCase(LoteSenderUseCase):
    """Envía el cierre de un lote firmado (DeviceProof) al servidor central."""

    PATH = "/lotes"

    def execute(self, payload: dict):
        """Envía el payload de cierre de lote y devuelve el ``SendResult``."""
        return self._post(payload)
