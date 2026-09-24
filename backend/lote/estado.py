"""Traducción de la clase del detector al estado de calidad del producto.

El modelo define qué estados puede emitir: si no tiene una clase de "crudo", ese
estado simplemente no aparece en el lote. La traducción es local, por convención
de nombre; no hay tabla de alias del backend (ver `docs/backend-go-lotes-sector.md`).

No confundir con `estabilizador._es_quemada`: aquello es el latch anti-parpadeo del
estado "burnt"; esto es la etiqueta de calidad que viaja al backend.
"""

from __future__ import annotations

import re

from backend.lote.tipos import EstadoProducto

# Raíces que se buscan como subcadena (identifican quemado/crudo de forma robusta).
_RAIZ_QUEMADO = ("quemad", "burnt")
_RAIZ_CRUDO = ("crud", "raw")
# Tokens exactos para "ok": por subcadena daría falsos positivos (p. ej. "book").
_TOKENS_OK = frozenset({"ok", "tcok", "correct", "correcta", "correcto", "correctos"})
_TOKENS_QUEMADO = frozenset({"tcq"})
_TOKENS_CRUDO = frozenset({"tcr"})

_SEPARADOR = re.compile(r"[^a-z0-9]+")


def clasificar_estado(label: str) -> EstadoProducto | None:
    """Devuelve el estado del producto para un label del detector.

    None si el label no coincide con ningún estado conocido.
    """
    normal = label.strip().lower()
    if not normal:
        return None
    if any(raiz in normal for raiz in _RAIZ_QUEMADO):
        return EstadoProducto.QUEMADO
    if any(raiz in normal for raiz in _RAIZ_CRUDO):
        return EstadoProducto.CRUDO
    tokens = set(_SEPARADOR.split(normal))
    if tokens & _TOKENS_QUEMADO:
        return EstadoProducto.QUEMADO
    if tokens & _TOKENS_CRUDO:
        return EstadoProducto.CRUDO
    if tokens & _TOKENS_OK or "correct" in normal:
        return EstadoProducto.OK
    return None
