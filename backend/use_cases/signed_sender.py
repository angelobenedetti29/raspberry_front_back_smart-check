class SignedSenderUseCase:
    """Base común de los emisores firmados (DeviceProof).

    Centraliza el transporte y la URL base. Cada subclase define su ``PATH`` y
    su ``execute``; el resultado por llamada lo devuelve el propio transporte
    (duck-typed, sin importar ``device_enrollment`` desde el backend).
    """

    PATH: str = ""

    def __init__(self, transport, api_base_url: str):
        self.transport = transport
        # Normaliza la URL base para no duplicar la barra al unirla con PATH.
        self.api_base_url = api_base_url.rstrip("/")

    @property
    def target(self) -> str:
        """URL absoluta del endpoint definido por ``PATH``."""
        return f"{self.api_base_url}{self.PATH}"

    def _post(self, payload: dict):
        """Delega el POST en el transporte y devuelve su ``SendResult``."""
        return self.transport.post(self.PATH, payload)


class LoteSenderUseCase(SignedSenderUseCase):
    """Emisor de lote (base compartida de cierre e inicio)."""
