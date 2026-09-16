"""Utilidades de streaming propias del frontend.

Complementa a ``streaming.config`` y ``streaming.publisher`` con las dos piezas
que solo necesita la UI: un publisher de mentira para el modo preview y una
validación adicional de la configuración del stream.
"""


class PreviewOnlyPublisher:
    """Publisher que no publica nada.

    Se usa cuando la configuración de streaming es inválida: la ventana sigue
    mostrando el vídeo en local (preview) pero no se emite por RTSP. Expone la
    misma interfaz que ``FFmpegPublisher`` (start/publish/stop).
    """

    def __init__(self, _config):
        # ``FFmpegPublisher`` recibe la configuración por posición; este publisher
        # no la necesita porque no publica nada, pero conserva la firma.
        pass

    def start(self):
        pass

    def publish(self, _frame):
        return True

    def stop(self):
        pass


def validate_stream_config(config):
    """Valida restricciones del frontend además de las de ``StreamConfig``.

    ``yuv420p`` exige dimensiones pares; si no, FFmpeg falla al publicar.
    Devuelve la misma configuración recibida para poder encadenar la llamada.
    Un objeto de configuración incompleto se rechaza con ``ValueError`` (y no
    con ``AttributeError``) para mantener el contrato de la función.
    """
    try:
        pixel_format = config.pixel_format
        width = config.width
        height = config.height
    except AttributeError as exc:
        raise ValueError("configuración de streaming incompleta") from exc
    if pixel_format == "yuv420p" and (width % 2 or height % 2):
        raise ValueError("width y height deben ser pares para yuv420p")
    return config


__all__ = ["PreviewOnlyPublisher", "validate_stream_config"]
