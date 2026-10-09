"""Configuración común de pytest para tests/backend/.

Inyecta la raíz del repo en sys.path para que `import backend.X` resuelva sin
instalar el proyecto. Es la forma explícita de evitar el editable install
sólo para correr tests.

Expone además `http_error` como side_effect de mock para los tests que
necesitan hacer que `urllib.request.urlopen` levante un `HTTPError` real
con un cuerpo específico, y `captura_request` para inspeccionar el body
que `api/cliente.py` puso en el `Request.data`.
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError

_RAIZ_REPO = Path(__file__).resolve().parents[2]
if str(_RAIZ_REPO) not in sys.path:
    sys.path.insert(0, str(_RAIZ_REPO))


def http_error(code: int, body: bytes = b"") -> Callable[..., Any]:
    """Devuelve un side_effect que levanta un `urllib.error.HTTPError` real.

    Mock reconoce instancias de `Exception` y las levanta cuando se invoca
    el mock, así que pasamos la excepción directamente. Usamos
    `HTTPError` (no una subclase nuestra) porque `api/cliente.py` lo
    captura con `except urllib.error.HTTPError`; un fake que no herede
    de él burbujea sin convertirse a `ErrorHttp` / `ErrorConexion`.

    El cuerpo se sirve con un `BytesIO` para que `exc.read()` funcione
    dentro de `_detalle_error()` del cliente HTTP. Mock no acepta
    `HTTPError` como side_effect directo por cómo inspecciona la firma
    (`_is_exception` falla en runtime con algunas subclases); por eso
    lo envolvemos en un callable que lo levanta.
    """
    err = HTTPError(
        url="http://test",
        code=code,
        msg="error simulado",
        # `hdrs` no se usa en tests (`_detalle_error` lee el cuerpo vía
        # `exc.read()`). HTTPError espera `Message[str, str]` pero ningún
        # caller lo lee; pasamos un dict vacío y silenciamos al LSP.
        hdrs={},  # type: ignore[arg-type]
        fp=BytesIO(body),
    )

    def _levantar(*_args: object, **_kwargs: object) -> Any:
        raise err

    return _levantar