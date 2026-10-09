"""Tests del ClienteLotes (capa HTTP contra los endpoints de sector/lotes).

Cubre el ciclo `abrir → reportar → cerrar` y los parseos de respuesta del
backend (envelope `{success, data}`, camelCase → snake_case). Mockeamos
`urllib.request.urlopen` para no depender del backend real.
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import pytest

from backend.config import ApiConfig
from backend.lote.cliente import ClienteLotes
from backend.lote.tipos import (
    EstadoProducto,
    ErrorLotes,
    EventoDeteccion,
    MotivoCierre,
)
from tests.backend.conftest import http_error


class _RespuestaFalsa:
    def __init__(self, cuerpo: bytes = b"", status: int = 200) -> None:
        self._cuerpo = cuerpo
        self.status = status

    def read(self) -> bytes:
        return self._cuerpo

    def __enter__(self) -> "_RespuestaFalsa":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _envelope(data: Any) -> bytes:
    return json.dumps({"success": True, "data": data}, ensure_ascii=False).encode()


def _api() -> ApiConfig:
    return ApiConfig(
        base_url="http://backend",
        registration_requests_endpoint="/api/v1/registration-requests/",
        dispositivos_ping_endpoint="/api/v1/dispositivos/ping",
        dispositivos_nombre_endpoint="/api/v1/dispositivos/nombre",
        dispositivos_rename_url_endpoint="/api/v1/dispositivos/rename-url",
        productos_endpoint="/api/v1/productos",
        dispositivos_sector_endpoint="/api/v1/dispositivos/sector",
        lotes_endpoint="/api/v1/lotes",
    )


def _mock_urlopen(respuesta: Any) -> Any:
    return mock.patch("urllib.request.urlopen", return_value=respuesta)


# --- lote_abierto ---


def test_lote_abierto_con_lote_none_devuelve_none() -> None:
    cliente = ClienteLotes(_api(), timeout=1.0)
    with _mock_urlopen(_RespuestaFalsa(_envelope({"lote": None}))):
        assert cliente.lote_abierto("secret") is None


def test_lote_abierto_con_lote_iniciado_manualmente_true() -> None:
    """F-R11: el flag `iniciado_manualmente` del backend debe propagarse."""
    cliente = ClienteLotes(_api(), timeout=1.0)
    payload = {
        "id": "lote-7",
        "sector_id": "sec-A",
        "estado": "ABIERTO",
        "producto_id": "p-1",
        "producto_nombre": "Tostadas v2",
        "abierto_en": "2026-10-04T12:00:00Z",
        "abierto_por": None,
        "conteos": {"ok": 0, "crudo": 0, "quemado": 0, "total": 0},
        "ultimo_evento_en": None,
        "inactividad_segundos": 0.0,
        "modo": "MANUAL",
        "horno2_habilitado": False,
        "iniciado_manualmente": True,
    }
    with _mock_urlopen(_RespuestaFalsa(_envelope({"lote": payload}))):
        lote = cliente.lote_abierto("secret")
    assert lote is not None
    assert lote.iniciado_manualmente is True
    assert lote.modo == "MANUAL"


# --- abrir_lote ---


def test_abrir_lote_devuelve_creado_true_y_lote() -> None:
    cliente = ClienteLotes(_api(), timeout=1.0)
    payload = {
        "lote": {
            "id": "lote-1",
            "sector_id": "sec-A",
            "estado": "ABIERTO",
            "producto_id": "p-1",
            "producto_nombre": "Tostadas v2",
            "abierto_en": "2026-10-04T12:00:00Z",
            "abierto_por": None,
            "conteos": {"ok": 0, "crudo": 0, "quemado": 0, "total": 0},
            "ultimo_evento_en": None,
            "inactividad_segundos": 0.0,
            "modo": "AUTOMATICO",
            "horno2_habilitado": True,
            "iniciado_manualmente": False,
        },
        "creado": True,
    }
    with _mock_urlopen(_RespuestaFalsa(_envelope(payload))):
        lote, creado = cliente.abrir_lote(
            "secret", idempotency_key="k-1", producto_id="p-1",
            momento="2026-10-04T12:00:00Z",
        )
    assert creado is True
    assert lote.id == "lote-1"
    assert lote.horno2_habilitado is True


def test_abrir_lote_payload_incluye_idempotency_key() -> None:
    """La idempotency_key es obligatoria para que el backend dedupe."""
    cliente = ClienteLotes(_api(), timeout=1.0)
    capturado: dict[str, Any] = {}

    def _captura(req: Any, *_args: Any, **_kwargs: Any) -> _RespuestaFalsa:
        capturado["data"] = req.data  # body vive en Request.data
        return _RespuestaFalsa(_envelope({"lote": {"id": "x"}, "creado": True}))

    with mock.patch("urllib.request.urlopen", side_effect=_captura):
        cliente.abrir_lote(
            "secret", idempotency_key="k-uuid", producto_id="p-1",
            momento="2026-10-04T12:00:00Z",
        )
    assert b"k-uuid" in capturado["data"]
    assert b"producto_id" in capturado["data"]


# --- reportar ---


def _evento() -> EventoDeteccion:
    return EventoDeteccion(
        evento_id="e-1",
        producto_id="p-1",
        estado=EstadoProducto.OK,
        confianza=0.91,
        pista=0,
        frame=42,
        modelo_id="tostadas-v2",
        momento="2026-10-04T12:00:00Z",
    )


def test_reportar_serializa_eventos_en_formato_backend() -> None:
    cliente = ClienteLotes(_api(), timeout=1.0)
    capturado: dict[str, Any] = {}

    def _captura(req: Any, *_args: Any, **_kwargs: Any) -> _RespuestaFalsa:
        capturado["data"] = req.data
        capturado["url"] = req.full_url
        return _RespuestaFalsa(_envelope(None))

    with mock.patch("urllib.request.urlopen", side_effect=_captura):
        cliente.reportar("secret", "lote-1", [_evento()])
    # El payload serializa el evento con su estado.value y el evento_id.
    payload_bytes = capturado["data"]
    assert payload_bytes is not None
    payload_str = payload_bytes.decode("utf-8")
    assert "e-1" in payload_str
    assert '"ok"' in payload_str  # EstadoProducto.OK.value
    # La URL debe apuntar a /lotes/{id}/eventos.
    assert "/lotes/lote-1/eventos" in capturado["url"]


# --- cerrar ---


def test_cerrar_incluye_motivo_y_conteos() -> None:
    cliente = ClienteLotes(_api(), timeout=1.0)
    from backend.lote.tipos import Conteos

    capturado: dict[str, Any] = {}

    def _captura(req: Any, *_args: Any, **_kwargs: Any) -> _RespuestaFalsa:
        capturado["data"] = req.data
        return _RespuestaFalsa(
            _envelope(
                {
                    "lote": {
                        "id": "lote-1",
                        "sector_id": "sec-A",
                        "estado": "CERRADO",
                        "producto_id": "p-1",
                        "producto_nombre": "Tostadas v2",
                        "abierto_en": "2026-10-04T12:00:00Z",
                        "abierto_por": None,
                        "conteos": {"ok": 5, "crudo": 1, "quemado": 2, "total": 8},
                        "ultimo_evento_en": None,
                        "inactividad_segundos": 0.0,
                        "cerrado_en": "2026-10-04T13:00:00Z",
                        "motivo_cierre": "manual",
                    }
                }
            )
        )

    with mock.patch("urllib.request.urlopen", side_effect=_captura):
        lote = cliente.cerrar(
            "secret", "lote-1",
            idempotency_key="k-1",
            motivo=MotivoCierre.MANUAL,
            conteos=Conteos(ok=5, crudo=1, quemado=2, total=8),
            momento="2026-10-04T13:00:00Z",
        )
    payload_bytes = capturado["data"]
    assert payload_bytes is not None
    payload_str = payload_bytes.decode("utf-8")
    assert '"manual"' in payload_str
    assert '"total": 8' in payload_str or '"total":8' in payload_str
    assert lote.estado.value == "CERRADO"


# --- errores ---


def test_5xx_se_traduce_a_error_lotes_reintentable() -> None:
    """Un 500 debe ser reintentable (codigo>=500)."""
    cliente = ClienteLotes(_api(), timeout=1.0)
    with mock.patch(
        "urllib.request.urlopen", side_effect=http_error(500, b'{"error":"crash"}')
    ):
        with pytest.raises(ErrorLotes) as info:
            cliente.reportar("secret", "lote-1", [_evento()])
    assert info.value.codigo == 500
    assert info.value.reintentable is True


def test_4xx_se_traduce_a_error_lotes_no_reintentable() -> None:
    """Un 400 no debe reintentarse: es bug del lado de la Pi."""
    cliente = ClienteLotes(_api(), timeout=1.0)
    with mock.patch(
        "urllib.request.urlopen", side_effect=http_error(400, b'{"error":"bad"}')
    ):
        with pytest.raises(ErrorLotes) as info:
            cliente.abrir_lote(
                "secret", idempotency_key="k", producto_id="p-1",
                momento="2026-10-04T12:00:00Z",
            )
    assert info.value.codigo == 400
    assert info.value.reintentable is False


def test_401_es_no_reintentable_pero_indica_credencial_invalida() -> None:
    """El 401 no es reintentable pero el servicio debe invalidar la credencial."""
    cliente = ClienteLotes(_api(), timeout=1.0)
    with mock.patch(
        "urllib.request.urlopen", side_effect=http_error(401, b'{"error":"unauth"}')
    ):
        with pytest.raises(ErrorLotes) as info:
            cliente.reportar("secret", "lote-1", [_evento()])
    assert info.value.codigo == 401
    assert info.value.reintentable is False


def test_timeout_red_se_traduce_a_error_lotes_reintentable() -> None:
    cliente = ClienteLotes(_api(), timeout=1.0)
    with mock.patch(
        "urllib.request.urlopen", side_effect=TimeoutError("red caída")
    ):
        with pytest.raises(ErrorLotes) as info:
            cliente.lote_abierto("secret")
    assert info.value.codigo is None
    assert info.value.reintentable is True


def test_envelope_sin_success_se_traduce_a_error_formato() -> None:
    """El backend puede devolver {success: false, errors: [...]}."""
    cliente = ClienteLotes(_api(), timeout=1.0)
    cuerpo = json.dumps(
        {"success": False, "errors": ["lote no encontrado"]}
    ).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        with pytest.raises(ErrorLotes, match="lote no encontrado"):
            cliente.lote_abierto("secret")