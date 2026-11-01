"""Construcción del path de publicación RTSP a partir del nombre y el tipo.

El front web deriva el WHEP con el mismo path (`<slug(nombre)>-<entrada|salida>`),
así que cualquier cambio acá tiene que replicarse allá.
"""

import re
import unicodedata

from backend.device.tipos import TipoDispositivo

_NO_ALFANUMERICO = re.compile(r"[^a-z0-9]+")


def slug_nombre(nombre: str) -> str:
    """Normaliza el nombre del dispositivo a un único segmento de path.

    Sin acentos ni mayúsculas; todo lo que no sea `[a-z0-9]` pasa a `-`.
    """
    sin_marcas = "".join(
        c for c in unicodedata.normalize("NFKD", nombre.strip()) if not unicodedata.combining(c)
    )
    slug = _NO_ALFANUMERICO.sub("-", sin_marcas.lower()).strip("-")
    return slug or "dispositivo"


def construir_url_publicacion(server_url: str, nombre: str, tipo: TipoDispositivo) -> str:
    """Arma `<server_url>/<slug(nombre)>-<entrada|salida>`."""
    return f"{server_url.rstrip('/')}/{slug_nombre(nombre)}-{tipo.sufijo_stream}"
