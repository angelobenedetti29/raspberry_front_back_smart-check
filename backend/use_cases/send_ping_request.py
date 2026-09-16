from backend.use_cases.signed_sender import SignedSenderUseCase


class SendPingRequestUseCase(SignedSenderUseCase):
    """Envía telemetría periódica firmada (DeviceProof) al servidor central."""

    PATH = "/dispositivos/ping"

    def execute(self, payload: dict) -> bool:
        if not payload.get("dispositivoId"):
            raise ValueError("El payload de ping requiere 'dispositivoId'.")

        return self._post(payload)
