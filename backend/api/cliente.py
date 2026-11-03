"""Cliente HTTP mínimo contra el backend Go (sólo stdlib: urllib).

Centraliza URL, headers (`Accept`/`Content-Type`/Bearer), serialización JSON,
timeout y clasificación de errores. No conoce endpoints ni dominio: quien lo usa
arma el `path` y traduce los errores a su tipo de dominio.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from backend.api.errores import ErrorConexion, ErrorFormato, ErrorHttp

_JSON = "application/json"


class ClienteHTTP:
    """Ejecuta peticiones HTTP y devuelve el JSON parseado (o None si vacío).

    Las rutas se derivan de `base_url` (esquema + host + puerto) y del `path`
    que recibe cada llamada; el cliente no lee la config.
    """

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def peticion(
        self,
        metodo: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        token: str | None = None,
        parsear: bool = True,
    ) -> Any:
        """Ejecuta la petición y devuelve el JSON parseado.

        `payload` se serializa a JSON; `token` se envía como `Bearer`. Si
        `parsear` es False (respuestas cuyo cuerpo se ignora), no decodifica.
        Levanta `ErrorHttp`, `ErrorConexion` o `ErrorFormato` ante un fallo.
        """
        url = f"{self._base_url}{path}"
        headers = {"Accept": _JSON}
        datos: bytes | None = None
        if payload is not None:
            datos = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = _JSON
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"

        peticion = urllib.request.Request(url, data=datos, headers=headers, method=metodo)
        try:
            with urllib.request.urlopen(peticion, timeout=self._timeout) as respuesta:
                cuerpo = respuesta.read()
        except urllib.error.HTTPError as exc:
            raise ErrorHttp(exc.code, _detalle_error(exc), metodo=metodo, url=url) from exc
        except urllib.error.URLError as exc:
            raise ErrorConexion(str(exc.reason), metodo=metodo, url=url) from exc
        except OSError as exc:
            raise ErrorConexion(str(exc), metodo=metodo, url=url) from exc

        if not cuerpo or not parsear:
            return None
        try:
            return json.loads(cuerpo.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ErrorFormato(
                f"respuesta no-JSON del backend en {metodo} {url}",
                metodo=metodo,
                url=url,
            ) from exc


def desenvolver(data: Any) -> Any:
    """Valida el envelope `{success, data}` del backend y devuelve `data`.

    Es el protocolo que usan los endpoints de lotes. Levanta `ErrorFormato` con
    el detalle si el envelope no tiene éxito o no es un objeto.
    """
    if not isinstance(data, dict):
        raise ErrorFormato("respuesta del backend no es un objeto JSON")
    if not data.get("success", False):
        detalle = str(
            data.get("errors") or data.get("error") or data.get("message") or "respuesta sin éxito"
        )
        raise ErrorFormato(detalle)
    return data.get("data")


def _detalle_error(exc: urllib.error.HTTPError) -> str:
    """Extrae el detalle de error del cuerpo de un HTTPError, si se puede."""
    try:
        cuerpo = exc.read().decode("utf-8", "replace")
    except OSError:
        return str(exc.reason or "error HTTP")
    try:
        datos = json.loads(cuerpo)
    except json.JSONDecodeError:
        return cuerpo.strip()[:200] or str(exc.reason or "error HTTP")
    if isinstance(datos, dict):
        detalle = datos.get("errors") or datos.get("error") or datos.get("message") or cuerpo
        return str(detalle)[:200]
    return cuerpo[:200]
