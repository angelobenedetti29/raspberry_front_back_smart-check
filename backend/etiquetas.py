"""Convención de etiquetas del modelo único: <producto>_<estado>.

El estado es el sufijo tras el último ``_`` y sólo puede ser ``crudo``,
``ok`` o ``quemado``; todo lo que precede a ese sufijo identifica al
producto (``tostada_comun_quemado`` → producto ``tostada_comun``, estado
``quemado``). El prefijo se matchea por nombre normalizado contra el
catálogo de productos del backend (ver ``normalizar_clave``).

Sin sufijo válido no hay estado: el evento no cuenta para el lote.
"""

from __future__ import annotations

import re
import unicodedata

# Estados válidos como sufijo de etiqueta (mismos valores que EstadoProducto).
ESTADOS_VALIDOS = ("crudo", "ok", "quemado")

# Alias transitorios de los modelos actuales (``tcq``/``tcok`` y
# ``tostada quemada``/``tostadas ok``): mantienen la clasificación hasta
# que llegue el modelo reentrenado con clases ``producto_estado``.
# BORRAR este bloque completo cuando ese modelo esté en producción.
_ALIAS_TRANSITORIOS: dict[str, tuple[str, str]] = {
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


def separar_etiqueta(label: str) -> tuple[str, str | None]:
    """Separa un label del detector en ``(producto, estado)``.

    El estado es el sufijo tras el último ``_`` si es un estado válido; si no,
    se prueban los alias transitorios. Sin ninguno de los dos, el label entero
    es el producto y el estado es ``None``.
    """
    normal = normalizar_clave(label)
    if not normal:
        return ("", None)
    alias = _ALIAS_TRANSITORIOS.get(normal)
    if alias is not None:
        return alias
    producto, _, sufijo = normal.rpartition("_")
    if sufijo in ESTADOS_VALIDOS and producto:
        return (producto, sufijo)
    return (normal, None)
