"""Resolución de etiquetas del detector a ``(producto, estado)``.

El mapa ``etiquetas.clases`` del config traduce códigos cortos (``tc``,
``tcok``, ``tcq``, ``cha``, ``fajita``, ``chaok``, ``chaq``) a
``(producto, estado)``. Si la etiqueta no está en el mapa, se mantiene la
convención ``<producto>_<estado>``: el estado es el sufijo tras el último
``_`` y sólo puede ser ``crudo``, ``ok`` o ``quemado``; todo lo que precede a
ese sufijo identifica al producto (``tostada_comun_quemado`` → producto
``tostada_comun``, estado ``quemado``). El prefijo se matchea por nombre
normalizado contra el catálogo de productos del backend (ver
``normalizar_clave``).

Sin sufijo válido no hay estado: el evento no cuenta para el lote.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

# Estados válidos como sufijo de etiqueta (mismos valores que EstadoProducto).
ESTADOS_VALIDOS = ("crudo", "ok", "quemado")

# Clases por defecto: fallback para configs viejas (sin la sección
# `etiquetas`) y para los tests. No es el único mapa: el config puede
# reemplazarlo con `etiquetas.clases`.
CLASES_POR_DEFECTO: dict[str, tuple[str, str | None]] = {
    "tcq": ("tostada_comun", "quemado"),
    "tcok": ("tostada_comun", "ok"),
    "tostada_quemada": ("tostada_comun", "quemado"),
    "tostadas_ok": ("tostada_comun", "ok"),
}

_NO_ALFANUM = re.compile(r"[^a-z0-9]+")
_ES_COMBINANTE = unicodedata.combining


def normalizar_clave(texto: str) -> str:
    """Normaliza para comparar nombres: minúsculas, sin tildes, separadores → ``_``.

    ``"Tostada Común"`` → ``"tostada_comun"``. Es la misma normalización para
    el prefijo del label y para el nombre del producto del catálogo del backend.
    """
    plano = unicodedata.normalize("NFKD", texto.strip().lower())
    sin_tildes = "".join(c for c in plano if not _ES_COMBINANTE(c))
    return _NO_ALFANUM.sub("_", sin_tildes).strip("_")


@dataclass(frozen=True, slots=True)
class ResolutorEtiquetas:
    """Mapa normalizado ``etiqueta → (producto, estado)`` con fallback por sufijo."""

    clases: Mapping[str, tuple[str, str | None]]

    def separar(self, label: str) -> tuple[str, str | None]:
        normal = normalizar_clave(label)
        if not normal:
            return ("", None)
        clase = self.clases.get(normal)
        if clase is not None:
            return clase
        producto, _, sufijo = normal.rpartition("_")
        if sufijo in ESTADOS_VALIDOS and producto:
            return (producto, sufijo)
        return (normal, None)


_POR_DEFECTO = ResolutorEtiquetas(CLASES_POR_DEFECTO)


def separar_etiqueta(
    label: str, resolutor: ResolutorEtiquetas | None = None
) -> tuple[str, str | None]:
    """Separa un label del detector en ``(producto, estado)``.

    Usa el resolutor dado o el mapa por defecto. El estado es el sufijo tras el
    último ``_`` si es un estado válido; si no, el label entero es el producto y
    el estado es ``None``.
    """
    return (resolutor or _POR_DEFECTO).separar(label)
