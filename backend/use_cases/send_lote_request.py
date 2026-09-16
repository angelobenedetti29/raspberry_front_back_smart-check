class SendLoteRequestUseCase:
    """Envía el cierre de un lote firmado (DeviceProof) al servidor central."""

    def __init__(self, transport, api_base_url: str):
        self.transport = transport
        self.api_base_url = api_base_url.rstrip("/")

    @property
    def target(self) -> str:
        return f"{self.api_base_url}/lotes"

    def execute(self, payload: dict) -> bool:
        return self.transport.post("/lotes", payload)

    def get_last_error(self):
        return getattr(self.transport, "last_error", None)

    def get_last_status_code(self):
        return getattr(self.transport, "last_status_code", None)

    def get_last_response_text(self):
        return getattr(self.transport, "last_response_text", None)
