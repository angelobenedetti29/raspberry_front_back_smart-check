"""Configuration shared by the capture, processing and publishing stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from smartcheck_config import (
    CaptureSettings,
    InferenceSettings,
    PublisherSettings,
    ReconnectSettings,
    StorageSettings,
    StreamSettings,
)

__all__ = ["StreamConfig"]


@dataclass(frozen=True)
class StreamConfig(StreamSettings):
    """Configuración del pipeline de streaming.

    Hereda el bloque ``stream`` anidado del kernel (``StreamSettings``) para
    reutilizar sus sub-dataclasses y su validación, sin duplicar el árbol ni
    mantener un ``__post_init__`` propio. La única puerta de entrada desde
    ``config.json`` es ``from_app_config``.
    """

    @classmethod
    def from_app_config(cls, stream_settings: StreamSettings) -> "StreamConfig":
        """Construye la configuración desde el ``StreamSettings`` anidado.

        ``config.json`` es la única fuente: no se releen variables de entorno ni
        argumentos de línea de comandos. Se reconstruye cada subsección con sus
        sub-dataclasses del kernel, de modo que las validaciones (``SchemaError``)
        vuelven a aplicarse sobre el resultado.
        """
        data = asdict(stream_settings)
        return cls(
            capture=CaptureSettings(**data["capture"]),
            publisher=PublisherSettings(**data["publisher"]),
            inference=InferenceSettings(**data["inference"]),
            storage=StorageSettings(**data["storage"]),
            reconnect=ReconnectSettings(**data["reconnect"]),
        )
