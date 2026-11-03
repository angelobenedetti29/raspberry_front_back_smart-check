"""Router del push de renombre del backend Go a la Raspberry.

Hoy la Raspberry ya empuja su nombre al Go (`ClienteDispositivo.renombrar`).
Este router es el camino inverso: expone un único endpoint PUT autenticado con
el `rename_token` que el Go entrega al aprobar el alta (no con el `secret`
principal del dispositivo), para que el backend pueda renombrar el dispositivo.

Es agnóstico de `ServidorHTTP`: sólo conoce FastAPI y el `DeviceService` que
recibe su factory. Mantiene el contrato HTTP externo del handler anterior
(misma ruta, método, header `Authorization`, body y shape de error) para no
romper al backend Go.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from backend.device import DeviceService

logger = logging.getLogger(__name__)

# Endpoint y método únicos del push de renombre. El path lleva el `device_id`
# del dispositivo aprobado en la URL (`/api/v1/dispositivos/<uuid>/nombre`),
# igual que el resto del contrato del Go: la Pi valida que el `device_id`
# extraído coincida con el del `DeviceService` local antes de aplicar el
# renombre (defensa en profundidad; el `rename_token` ya autentica).
PREFIJO_RENOMBRE = "/api/v1/dispositivos/"
SUFIJO_RENOMBRE = "/nombre"


def _extraer_bearer(authorization_header: str | None) -> str | None:
    """Devuelve el token después de `"Bearer "` o None si no matchea."""
    if not authorization_header:
        return None
    prefijo = "Bearer "
    if not authorization_header.startswith(prefijo):
        return None
    return authorization_header[len(prefijo) :]


def _comparar_token(recibido: str, esperado: str) -> bool:
    """Comparación en tiempo constante de dos tokens (en bytes).

    Se compara bytes porque los headers pueden traer latín-1 y
    `compare_digest` rechaza str con caracteres no ASCII.
    """
    return hmac.compare_digest(recibido.encode("utf-8"), esperado.encode("utf-8"))


def construir_router_renombre(device_service: DeviceService) -> APIRouter:
    """Arma el router del renombre cerrando sobre el `DeviceService`."""
    router = APIRouter()

    @router.put(PREFIJO_RENOMBRE + "{device_id}" + SUFIJO_RENOMBRE)
    async def renombrar(device_id: str, request: Request) -> JSONResponse:
        # 1) Defensa en profundidad: el `device_id` del path debe ser el del
        # dispositivo aprobado localmente (el rename_token ya autentica, pero
        # no aceptamos un push dirigido a otro UUID).
        device_id_local = device_service.device_id()
        if device_id_local is None or device_id_local != device_id:
            raise HTTPException(
                status_code=403,
                detail="El dispositivo de la URL no está registrado en esta Pi",
            )

        # 2) Autenticación con el rename_token. Sin token la Pi no está
        # aprobada: ningún request autentica (403). Header que no matchea: 401.
        rename_token = device_service.rename_token()
        if rename_token is None:
            raise HTTPException(
                status_code=403,
                detail="El dispositivo no está registrado",
            )
        token = _extraer_bearer(request.headers.get("Authorization"))
        if token is None or not _comparar_token(token, rename_token):
            raise HTTPException(status_code=401, detail="Credencial inválida")

        # 3) Body JSON validado a mano para conservar el shape `{"error": ...}`
        # del handler anterior (no usamos modelos Pydantic como dependencia:
        # FastAPI respondería 422 con otro shape).
        try:
            crudo = await request.body()
            datos = json.loads(crudo.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse(
                status_code=400,
                content={"error": "El cuerpo no es JSON válido"},
            )
        if not isinstance(datos, dict):
            return JSONResponse(
                status_code=400,
                content={"error": "El cuerpo debe ser un objeto JSON"},
            )
        nombre = datos.get("nombre")
        if not isinstance(nombre, str) or not nombre.strip():
            return JSONResponse(
                status_code=400,
                content={"error": "El campo 'nombre' debe ser un string no vacío"},
            )
        nombre_limpio = nombre.strip()

        # 4) Hace todo el trabajo: memoria del DeviceService + config.json
        # atómico y validado. Si falla, NO se responde 200.
        try:
            device_service.aplicar_renombre_externo(nombre_limpio)
        except ValueError as exc:
            # El "nombre vacío" es input inválido, no un fallo interno.
            return JSONResponse(status_code=400, content={"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - se traduce a 500 con detalle
            # El detalle del error no contiene el secret.
            logger.warning("device: fallo al aplicar renombre externo: %s", exc)
            return JSONResponse(
                status_code=500,
                content={"error": f"No se pudo aplicar el renombre: {exc}"},
            )
        return JSONResponse(status_code=200, content={"nombre": nombre_limpio})

    return router
