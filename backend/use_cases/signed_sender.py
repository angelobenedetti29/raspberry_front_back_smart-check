class SignedSenderUseCase:
    """Base común de los emisores firmados (DeviceProof).

    Centraliza el transporte, la URL base y el último error reportado. Cada
    subclase define su ``PATH`` y su ``execute``.
    """

    PATH: str = ""

    def __init__(self, transport, api_base_url: str):
        self.transport = transport
        self.api_base_url = api_base_url.rstrip("/")

    @property
    def target(self) -> str:
        return f"{self.api_base_url}{self.PATH}"

    def _post(self, payload: dict) -> bool:
        return self.transport.post(self.PATH, payload)

    def get_last_error(self):
        return getattr(self.transport, "last_error", None)


class LoteSenderUseCase(SignedSenderUseCase):
    """Emisor de lote: además del último error, expone status y cuerpo."""

    def get_last_status_code(self):
        return getattr(self.transport, "last_status_code", None)

    def get_last_response_text(self):
        return getattr(self.transport, "last_response_text", None)
