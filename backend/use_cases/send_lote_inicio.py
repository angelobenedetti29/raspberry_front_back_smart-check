from backend.use_cases.signed_sender import LoteSenderUseCase


class SendLoteInicioUseCase(LoteSenderUseCase):
    """Inicia un lote firmado (DeviceProof) en el servidor central."""

    PATH = "/lotes/inicio"

    def execute(self, horno_id: str, producto_id: str) -> bool:
        if not horno_id or not producto_id:
            raise ValueError(
                "Se requieren 'hornoId' y 'productoId' para iniciar el lote."
            )

        return self._post({"hornoId": horno_id, "productoId": producto_id})
