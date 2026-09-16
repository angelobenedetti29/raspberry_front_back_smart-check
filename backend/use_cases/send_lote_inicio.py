from backend.use_cases.signed_sender import LoteSenderUseCase


class SendLoteInicioUseCase(LoteSenderUseCase):
    """Inicia un lote firmado (DeviceProof) en el servidor central."""

    PATH = "/lotes/inicio"

    def execute(self, horno_id: str, producto_id: str):
        """Inicia el lote firmado y devuelve el ``SendResult``.

        Exige ``horno_id`` y ``producto_id`` no vacíos.
        """
        if not horno_id or not producto_id:
            raise ValueError(
                "Se requieren 'hornoId' y 'productoId' para iniciar el lote."
            )

        return self._post({"hornoId": horno_id, "productoId": producto_id})
