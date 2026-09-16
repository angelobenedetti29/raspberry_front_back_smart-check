class SendPingRequestUseCase:
    """Envía telemetría periódica firmada (DeviceProof) al servidor central."""

    def __init__(self, transport, api_base_url: str):
        self.transport = transport
        self.api_base_url = api_base_url.rstrip("/")

    @property
    def target(self) -> str:
        return f"{self.api_base_url}/dispositivos/ping"

    def execute(self, payload: dict) -> bool:
        if not payload.get("dispositivoId"):
            raise ValueError("El payload de ping requiere 'dispositivoId'.")

        return self.transport.post("/dispositivos/ping", payload)

    def get_last_error(self):
        return getattr(self.transport, "last_error", None)
