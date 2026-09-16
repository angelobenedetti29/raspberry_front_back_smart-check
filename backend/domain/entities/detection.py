from dataclasses import dataclass
from typing import Tuple


@dataclass
class DetectionResult:
    """Detección cruda emitida por un detector de imágenes.

    ``label`` es la etiqueta tal cual la entrega el modelo y ``bbox`` la caja
    ``(x, y, width, height)`` en píxeles.
    """

    label: str
    confidence: float
    bbox: Tuple[int, int, int, int]  # x, y, width, height


def is_burnt(label: str | None = None, state: str | None = None) -> bool:
    """Indica si una detección corresponde a una tostada quemada.

    Fuente única de verdad del backend: reconoce el estado ya confirmado por el
    tracker (``state == "burnt"``) y las etiquetas que puede entregar el modelo
    (``TCQ`` de tostadas v2, ``Tostada Quemada`` de v1, o cualquier variante que
    contenga "quemada").
    """
    if state == "burnt":
        return True
    normalized = (label or "").lower()
    return "quemada" in normalized or normalized == "tcq"
