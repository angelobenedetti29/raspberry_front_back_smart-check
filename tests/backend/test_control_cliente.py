"""Tests del ClienteControl (capa HTTP contra el backend para SCA-400).

Mockeamos `urllib.request.urlopen` para no necesitar un backend real: los
clientes son adaptadores finos sobre `ClienteHTTP` y la lógica de
negocio vive en traducir la respuesta / error al dominio (`ErrorControl`,
`SetpointPropuesto`, etc.).
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import pytest

from backend.control.cliente import ClienteControl
from backend.control.tipos import ErrorControl
from tests.backend.conftest import http_error


class _RespuestaFalsa:
    """Suplanta el context manager que devuelve `urlopen`.

    `urllib.request.urlopen` se usa con `with`, así que necesitamos
    `__enter__` y `__exit__` además de `read()` y `status`.
    """

    def __init__(self, cuerpo: bytes = b"", status: int = 200) -> None:
        self._cuerpo = cuerpo
        self.status = status

    def read(self) -> bytes:
        return self._cuerpo

    def __enter__(self) -> "_RespuestaFalsa":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _mock_urlopen(respuesta: Any) -> Any:
    """Construye un patch de `urlopen` que devuelve la respuesta dada."""
    return mock.patch("urllib.request.urlopen", return_value=respuesta)


# --- publicar_telemetria ---


def test_publicar_telemetria_exitoso_no_devuelve_nada() -> None:
    cliente = ClienteControl("http://backend", timeout=1.0)
    with _mock_urlopen(_RespuestaFalsa(b"", 200)):
        # No debe lanzar; el parsear=False hace que el cuerpo se ignore.
        cliente.publicar_telemetria(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/metricas-horno",
            {"temp_horno_1": 180.0},
        )


def test_publicar_telemetria_409_se_traduce_a_error_control() -> None:
    cliente = ClienteControl("http://backend", timeout=1.0)
    with mock.patch(
        "urllib.request.urlopen", side_effect=http_error(409, b'{"error":"cerrado"}')
    ):
        with pytest.raises(ErrorControl) as info:
            cliente.publicar_telemetria(
                "secret-x", "lote-1", "/api/v1/lotes/{id}/metricas-horno", {}
            )
    assert info.value.codigo == 409
    assert "cerrado" in info.value.args[0]


def test_publicar_telemetria_sin_secret_falla_en_cliente() -> None:
    cliente = ClienteControl("http://backend", timeout=1.0)
    with pytest.raises(ErrorControl, match="No hay secret"):
        cliente.publicar_telemetria(
            "", "lote-1", "/api/v1/lotes/{id}/metricas-horno", {}
        )


def test_publicar_telemetria_timeout_se_traduce_a_error_control() -> None:
    cliente = ClienteControl("http://backend", timeout=1.0)
    with mock.patch(
        "urllib.request.urlopen",
        side_effect=TimeoutError("timeout"),
    ):
        with pytest.raises(ErrorControl, match="No se pudo publicar"):
            cliente.publicar_telemetria(
                "secret-x", "lote-1", "/api/v1/lotes/{id}/metricas-horno", {}
            )


# --- obtener_propuesta ---


def test_obtener_propuesta_204_devuelve_none() -> None:
    cliente = ClienteControl("http://backend", timeout=1.0)
    # urllib no levanta HTTPError para 2xx: un 204 llega como respuesta vacía.
    with _mock_urlopen(_RespuestaFalsa(b"", 204)):
        resultado = cliente.obtener_propuesta(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
        )
    assert resultado is None


def test_obtener_propuesta_envelope_con_propuesta_none_devuelve_none() -> None:
    cliente = ClienteControl("http://backend", timeout=1.0)
    cuerpo = json.dumps({"success": True, "data": {"propuesta": None}}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        resultado = cliente.obtener_propuesta(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
        )
    assert resultado is None


def test_obtener_propuesta_camel_case_se_mapea_a_snake_case() -> None:
    """El backend devuelve camelCase; el cliente traduce a snake_case."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    propuesta_backend = {
        "temperaturaObjetivo": 180.0,
        "velocidadCintaObjetivo": 1.2,
        "tempHorno2Objetivo": 175.5,
        "aplicadoPor": "MPC",
        "ventana": "2026-10-04T12:00:00Z",
        "decisionId": "dec-12345",
    }
    cuerpo = json.dumps({"success": True, "data": {"propuesta": propuesta_backend}}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        resultado = cliente.obtener_propuesta(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
        )
    assert resultado is not None
    assert resultado.temp_horno_1 == 180.0
    assert resultado.velocidad_cinta == 1.2
    assert resultado.temp_horno_2 == 175.5
    assert resultado.aplicado_por == "MPC"
    assert resultado.ventana == "2026-10-04T12:00:00Z"
    assert resultado.decision_id == "dec-12345"


def test_obtener_propuesta_sin_decision_id_devuelve_none() -> None:
    cliente = ClienteControl("http://backend", timeout=1.0)
    propuesta_backend = {
        "temperaturaObjetivo": 180.0,
        "velocidadCintaObjetivo": 1.2,
        "aplicadoPor": "MPC",
    }
    cuerpo = json.dumps({"success": True, "data": {"propuesta": propuesta_backend}}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        resultado = cliente.obtener_propuesta(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
        )
    assert resultado is not None
    assert resultado.decision_id is None


def test_obtener_propuesta_payload_mal_formado_se_traduce_a_error() -> None:
    """Falta `temperaturaObjetivo` (clave obligatoria): no debe romper el lazo."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    cuerpo = json.dumps({"success": True, "data": {"propuesta": {}}}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        with pytest.raises(ErrorControl, match="Propuesta mal formada"):
            cliente.obtener_propuesta(
                "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
            )


# --- aplicar_consigna ---


def test_aplicar_consigna_payload_incluye_decision_id() -> None:
    """El ack al backend debe llevar el `decision_id` para reconciliación."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    body = {
        "temperatura_objetivo": 180.0,
        "velocidad_cinta_objetivo": 1.2,
        "decision_id": "dec-9999",
    }
    capturado: dict[str, Any] = {}

    def _captura(req: Any, *_args: Any, **_kwargs: Any) -> _RespuestaFalsa:
        capturado["url"] = req.full_url
        capturado["data"] = req.data  # el body vive en Request.data
        return _RespuestaFalsa(b"", 200)

    with mock.patch("urllib.request.urlopen", side_effect=_captura):
        cliente.aplicar_consigna(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/aplicar-consigna", body
        )
    # La URL debe incluir el id del lote (formato {id}).
    assert "lote-1" in capturado["url"]
    # El body serializado debe contener el decision_id.
    assert b"dec-9999" in capturado["data"]


def test_cambiar_modo_500_se_traduce_a_error_control_con_codigo() -> None:
    """Un 5xx expone el código para que el servicio decida si reintentar."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    with mock.patch(
        "urllib.request.urlopen", side_effect=http_error(503, b'{"error":"down"}')
    ):
        from backend.control.tipos import ModoControl

        with pytest.raises(ErrorControl) as info:
            cliente.cambiar_modo(
                "secret-x", "lote-1", "/api/v1/lotes/{id}/modo", ModoControl.MANUAL
            )
    assert info.value.codigo == 503


def test_cambiar_modo_200_no_falla() -> None:
    cliente = ClienteControl("http://backend", timeout=1.0)
    from backend.control.tipos import ModoControl

    with _mock_urlopen(_RespuestaFalsa(b"", 200)):
        cliente.cambiar_modo(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/modo", ModoControl.AUTOMATICO
        )