class SendLoteInicioUseCase:
    """Inicia un lote firmado (DeviceProof) en el servidor central."""

    def __init__(self, transport, api_base_url: str):
        self.transport = transport
        self.api_base_url = api_base_url.rstrip("/")

    @property
    def target(self) -> str:
        return f"{self.api_base_url}/lotes/inicio"

    def execute(self, horno_id: str, producto_id: str) -> bool:
        if not horno_id or not producto_id:
            raise ValueError(
                "Se requieren 'hornoId' y 'productoId' para iniciar el lote."
            )

        payload = {"hornoId": horno_id, "productoId": producto_id}
        return self.transport.post("/lotes/inicio", payload)

    def get_last_error(self):
        return getattr(self.transport, "last_error", None)

    def get_last_status_code(self):
        return getattr(self.transport, "last_status_code", None)

    def get_last_response_text(self):
        return getattr(self.transport, "last_response_text", None)
