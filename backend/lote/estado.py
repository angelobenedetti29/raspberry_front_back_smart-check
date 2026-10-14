"""Traducción de la clase del detector al estado de calidad del producto.

El estado es el sufijo de la etiqueta según la convención ``<producto>_<estado>``
(ver ``backend.etiquetas``): si el modelo no tiene ninguna clase con sufijo
``_crudo``, ese estado simplemente no aparece en el lote. La traducción es
local, por convención de nombre; no hay tabla de alias del backend.

No confundir con `estabilizador._es_quemada`: aquello es el latch anti-parpadeo
del estado "burnt"; esto es la etiqueta de calidad que viaja al backend.
"""

from __future__ import annotations

from backend.etiquetas import separar_etiqueta
from backend.lote.tipos import EstadoProducto


def clasificar_estado(label: str) -> EstadoProducto | None:
    """Devuelve el estado del producto para un label del detector.

    None si la etiqueta no tiene sufijo de estado válido (``_crudo``/``_ok``/
    ``_quemado``): el evento no cuenta para el lote.
    """
    estado = separar_etiqueta(label)[1]
    return EstadoProducto(estado) if estado is not None else None
