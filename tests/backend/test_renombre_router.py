"""Tests del camino de autenticación del router de renombre.

No se levanta FastAPI ni se hace HTTP real: se toma el endpoint del router y se
lo invoca con un request mínimo. Así se valida el contrato (403/401/400/200)
sin sumar dependencias de test.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

from backend.http.routers.renombre import construir_router_renombre


class _Peticion:
    """Request mínima: sólo lo que usa el endpoint (headers y body)."""

    def __init__(self, *, headers: dict | None = None, cuerpo: bytes = b"") -> None:
        self.headers = headers or {}
        self._cuerpo = cuerpo

    async def body(self) -> bytes:
        return self._cuerpo


class _DispositivoFalso:
    """DeviceService falso: registra los renombres aplicados."""

    def __init__(
        self,
        device_id: str | None = "dev-1",
        rename_token: str | None = "token-1",
    ) -> None:
        self._device_id = device_id
        self._rename_token = rename_token
        self.renombres: list[str] = []

    def device_id(self) -> str | None:
        return self._device_id

    def rename_token(self) -> str | None:
        return self._rename_token

    def aplicar_renombre_externo(self, nombre: str) -> None:
        self.renombres.append(nombre)


def _llamar(
    servicio: _DispositivoFalso,
    *,
    device_id: str = "dev-1",
    headers: dict | None = None,
    cuerpo: bytes = b"",
):
    router = construir_router_renombre(servicio)
    ruta = next(r for r in router.routes if r.path.endswith("/nombre"))
    return asyncio.run(ruta.endpoint(device_id, _Peticion(headers=headers, cuerpo=cuerpo)))


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_device_id_ausente_devuelve_403() -> None:
    servicio = _DispositivoFalso(device_id=None)
    with pytest.raises(HTTPException) as info:
        _llamar(servicio, headers=_bearer("token-1"))
    assert info.value.status_code == 403


def test_device_id_distinto_devuelve_403() -> None:
    with pytest.raises(HTTPException) as info:
        _llamar(_DispositivoFalso(), device_id="otro", headers=_bearer("token-1"))
    assert info.value.status_code == 403


def test_sin_rename_token_devuelve_403() -> None:
    servicio = _DispositivoFalso(rename_token=None)
    with pytest.raises(HTTPException) as info:
        _llamar(servicio, headers=_bearer("token-1"))
    assert info.value.status_code == 403


def test_bearer_incorrecto_devuelve_401() -> None:
    with pytest.raises(HTTPException) as info:
        _llamar(_DispositivoFalso(), headers=_bearer("otro-token"))
    assert info.value.status_code == 401


def test_json_invalido_devuelve_400() -> None:
    respuesta = _llamar(_DispositivoFalso(), headers=_bearer("token-1"), cuerpo=b"{no-json")
    assert respuesta.status_code == 400
    assert "JSON" in json.loads(respuesta.body)["error"]


@pytest.mark.parametrize("cuerpo", [b'{"nombre": ""}', b'{"nombre": "   "}', b"{}"])
def test_nombre_vacio_devuelve_400(cuerpo: bytes) -> None:
    respuesta = _llamar(_DispositivoFalso(), headers=_bearer("token-1"), cuerpo=cuerpo)
    assert respuesta.status_code == 400


def test_renombre_valido_devuelve_200_y_aplica_nombre_limpio() -> None:
    servicio = _DispositivoFalso()
    respuesta = _llamar(
        servicio,
        headers=_bearer("token-1"),
        cuerpo=b'{"nombre": "  Nueva  "}',
    )
    assert respuesta.status_code == 200
    assert json.loads(respuesta.body) == {"nombre": "Nueva"}
    assert servicio.renombres == ["Nueva"]
