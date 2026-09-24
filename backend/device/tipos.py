"""Tipo operativo del dispositivo: entrada o salida de horno.

Módulo aparte para evitar imports circulares: el paquete `backend.device`
importa `cliente` y `almacen`, así que ambos necesitan el tipo acá.
"""

from enum import Enum


class ErrorTipoDispositivo(Exception):
    """Tipo de dispositivo ausente, vacío o fuera del catálogo permitido."""


class TipoDispositivo(str, Enum):
    """Ubicación del dispositivo dentro de la línea de horneado."""

    ENTRADA_HORNO = "ENTRADA_HORNO"
    SALIDA_HORNO = "SALIDA_HORNO"

    @classmethod
    def desde(cls, valor: object) -> "TipoDispositivo":
        """Normaliza `valor` a un TipoDispositivo válido.

        Acepta una instancia de TipoDispositivo (la devuelve tal cual) o un
        string que coincida con alguno de los valores, ignorando mayúsculas y
        espacios. None, vacío o desconocido lanzan ErrorTipoDispositivo.
        """
        if isinstance(valor, cls):
            return valor
        if isinstance(valor, str):
            normalizado = valor.strip().upper()
            for miembro in cls:
                if miembro.value == normalizado:
                    return miembro
        raise ErrorTipoDispositivo(
            f"Tipo de dispositivo inválido: {valor!r}. "
            f"Se esperaba uno de {[miembro.value for miembro in cls]}"
        )
