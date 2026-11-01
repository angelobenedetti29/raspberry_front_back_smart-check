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


def test_obtener_propuesta_mapea_el_contrato_real() -> None:
    """El backend devuelve snake_case con autorizada/fase del contrato Go."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    propuesta_backend = {
        "decision_id": "dec-12345",
        "temp1_objetivo": 180.0,
        "vel_objetivo": 1.2,
        "generated_at": "2026-10-04T11:59:00Z",
        "ttl_segundos": 30,
        "ventana_inicio": "2026-10-04T12:00:00Z",
        "ventana_fin": "2026-10-04T12:05:00Z",
        "autorizada": False,
        "fase": "HORNEADO",
        "aprobada_por": "op-7",
    }
    cuerpo = json.dumps({"success": True, "data": propuesta_backend}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        resultado = cliente.obtener_propuesta(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
        )
    assert resultado is not None
    assert resultado.temp_horno_1 == 180.0
    assert resultado.velocidad_cinta == 1.2
    assert resultado.decision_id == "dec-12345"
    assert resultado.ventana == "2026-10-04T12:00:00Z"
    assert resultado.autorizada is False
    assert resultado.fase == "HORNEADO"
    # aprobada_por presente ⇒ la aprobó el operario.
    assert resultado.aplicado_por == "OPERARIO"


def test_obtener_propuesta_autorizada_por_mpc_y_ventana_de_ventana() -> None:
    """AUTOMATICO: autorizada=true, sin aprobada_por, ventana bajo `ventana`."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    propuesta_backend = {
        "temp1_objetivo": 180.0,
        "vel_objetivo": 1.2,
        "autorizada": True,
        "ventana": "2026-10-04T12:00:00Z",
    }
    cuerpo = json.dumps({"success": True, "data": propuesta_backend}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        resultado = cliente.obtener_propuesta(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
        )
    assert resultado is not None
    assert resultado.autorizada is True
    assert resultado.aplicado_por == "MPC"
    assert resultado.fase is None
    assert resultado.ventana == "2026-10-04T12:00:00Z"


def test_obtener_propuesta_camel_case_viejo_sigue_parseando() -> None:
    """Compatibilidad con backends previos al contrato snake_case."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    propuesta_backend = {
        "temperaturaObjetivo": 180.0,
        "velocidadCintaObjetivo": 1.2,
        "aplicadoPor": "OPERARIO",
        "ventana": "2026-10-04T12:00:00Z",
        "decisionId": "dec-viejo",
    }
    cuerpo = json.dumps({"success": True, "data": {"propuesta": propuesta_backend}}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        resultado = cliente.obtener_propuesta(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
        )
    assert resultado is not None
    assert resultado.temp_horno_1 == 180.0
    assert resultado.velocidad_cinta == 1.2
    assert resultado.aplicado_por == "OPERARIO"
    assert resultado.decision_id == "dec-viejo"
    # Un backend viejo no manda `autorizada`: se asume autorizada.
    assert resultado.autorizada is True


def test_obtener_propuesta_sin_decision_id_devuelve_none() -> None:
    cliente = ClienteControl("http://backend", timeout=1.0)
    propuesta_backend = {
        "temp1_objetivo": 180.0,
        "vel_objetivo": 1.2,
    }
    cuerpo = json.dumps({"success": True, "data": {"propuesta": propuesta_backend}}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        resultado = cliente.obtener_propuesta(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
        )
    assert resultado is not None
    assert resultado.decision_id is None


def test_obtener_propuesta_mapea_limites_del_lote() -> None:
    """El bloque `limites` viaja con la propuesta y mapea al dominio."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    propuesta_backend = {
        "temp1_objetivo": 180.0,
        "vel_objetivo": 1.2,
        "limites": {
            "temp_min": 100.0,
            "temp_max": 300.0,
            "vel_min": 0.05,
            "vel_max": 0.5,
            "delta_temp1_max_c": 2.0,
            "delta_vel_max": 0.01,
            "tolerancia_temp_c": 1.5,  # informativo: el clamp no lo usa
        },
    }
    cuerpo = json.dumps({"success": True, "data": propuesta_backend}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        resultado = cliente.obtener_propuesta(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
        )
    assert resultado is not None
    assert resultado.limites is not None
    assert resultado.limites.temp_min == 100.0
    assert resultado.limites.temp_max == 300.0
    assert resultado.limites.vel_min == 0.05
    assert resultado.limites.vel_max == 0.5
    # delta_temp1_max_c del backend → delta_temp_max_c del dominio.
    assert resultado.limites.delta_temp_max_c == 2.0
    assert resultado.limites.delta_vel_max == 0.01


def test_obtener_propuesta_sin_limites_devuelve_none() -> None:
    cliente = ClienteControl("http://backend", timeout=1.0)
    propuesta_backend = {"temp1_objetivo": 180.0, "vel_objetivo": 1.2}
    cuerpo = json.dumps({"success": True, "data": propuesta_backend}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        resultado = cliente.obtener_propuesta(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
        )
    assert resultado is not None
    assert resultado.limites is None


@pytest.mark.parametrize(
    "limites",
    [
        # Falta una de las seis claves obligatorias.
        {
            "temp_min": 100.0,
            "temp_max": 300.0,
            "vel_min": 0.05,
            "vel_max": 0.5,
            "delta_temp1_max_c": 2.0,
        },
        # No numérico.
        {
            "temp_min": 100.0,
            "temp_max": "caliente",
            "vel_min": 0.05,
            "vel_max": 0.5,
            "delta_temp1_max_c": 2.0,
            "delta_vel_max": 0.01,
        },
        # max <= min.
        {
            "temp_min": 300.0,
            "temp_max": 100.0,
            "vel_min": 0.05,
            "vel_max": 0.5,
            "delta_temp1_max_c": 2.0,
            "delta_vel_max": 0.01,
        },
        # Delta no positivo.
        {
            "temp_min": 100.0,
            "temp_max": 300.0,
            "vel_min": 0.05,
            "vel_max": 0.5,
            "delta_temp1_max_c": 0.0,
            "delta_vel_max": 0.01,
        },
        # El bloque no es un objeto.
        [100.0, 300.0],
    ],
)
def test_obtener_propuesta_limites_mal_formados_no_rompen(
    limites: object,
) -> None:
    """Un bloque `limites` inválido se ignora; la propuesta sigue siendo válida."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    propuesta_backend = {
        "temp1_objetivo": 180.0,
        "vel_objetivo": 1.2,
        "limites": limites,
    }
    cuerpo = json.dumps({"success": True, "data": propuesta_backend}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        resultado = cliente.obtener_propuesta(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
        )
    assert resultado is not None
    assert resultado.temp_horno_1 == 180.0
    assert resultado.limites is None


def test_obtener_propuesta_payload_mal_formado_se_traduce_a_error() -> None:
    """Falta `temp1_objetivo` (clave obligatoria): no debe romper el lazo."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    cuerpo = json.dumps({"success": True, "data": {"propuesta": {}}}).encode()
    with _mock_urlopen(_RespuestaFalsa(cuerpo, 200)):
        with pytest.raises(ErrorControl, match="Propuesta mal formada"):
            cliente.obtener_propuesta(
                "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna-propuesta"
            )


# --- aprobar_consigna / ack_consigna ---


def test_aprobar_consigna_hace_post_sin_body_al_endpoint() -> None:
    """La aprobación avisa al backend; el body no se usa (payload None)."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    capturado: dict[str, Any] = {}

    def _captura(req: Any, *_args: Any, **_kwargs: Any) -> _RespuestaFalsa:
        capturado["url"] = req.full_url
        capturado["data"] = req.data
        capturado["metodo"] = req.get_method()
        return _RespuestaFalsa(b"", 200)

    with mock.patch("urllib.request.urlopen", side_effect=_captura):
        cliente.aprobar_consigna(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/aprobar-consigna"
        )
    assert capturado["metodo"] == "POST"
    assert capturado["url"].endswith("/api/v1/lotes/lote-1/aprobar-consigna")
    assert capturado["data"] is None


def test_aprobar_consigna_envia_decision_id_si_se_provee() -> None:
    """El backend rechaza la aprobación si la propuesta vigente cambió."""
    cliente = ClienteControl("http://backend", timeout=1.0)
    capturado: dict[str, Any] = {}

    def _captura(req: Any, *_args: Any, **_kwargs: Any) -> _RespuestaFalsa:
        capturado["data"] = req.data
        return _RespuestaFalsa(b"", 200)

    with mock.patch("urllib.request.urlopen", side_effect=_captura):
        cliente.aprobar_consigna(
            "secret-x",
            "lote-1",
            "/api/v1/lotes/{id}/aprobar-consigna",
            {"decision_id": "dec-1"},
        )
    assert b"dec-1" in capturado["data"]


def test_ack_consigna_envia_decision_y_valores_aplicados() -> None:
    """El ack confirma qué aplicó la Pi con el decision_id de reconciliación."""
    from backend.control.tipos import SetpointPropuesto

    cliente = ClienteControl("http://backend", timeout=1.0)
    body = SetpointPropuesto(
        temp_horno_1=180.0,
        velocidad_cinta=1.2,
        aplicado_por="MPC",
        ventana=None,
        decision_id="dec-9999",
    ).a_payload_ack()
    capturado: dict[str, Any] = {}

    def _captura(req: Any, *_args: Any, **_kwargs: Any) -> _RespuestaFalsa:
        capturado["url"] = req.full_url
        capturado["data"] = req.data
        return _RespuestaFalsa(b"", 200)

    with mock.patch("urllib.request.urlopen", side_effect=_captura):
        cliente.ack_consigna(
            "secret-x", "lote-1", "/api/v1/lotes/{id}/consigna/ack", body
        )
    assert capturado["url"].endswith("/api/v1/lotes/lote-1/consigna/ack")
    datos = capturado["data"]
    assert b"dec-9999" in datos
    assert b"temp_aplicado" in datos
    assert b"vel_aplicado" in datos


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