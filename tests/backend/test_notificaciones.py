"""Tests unitarios del monitor de calidad y cliente de notificaciones."""

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from backend.config import ApiConfig, NotificacionesConfig
from backend.lote.monitor_calidad import (
    TIPO_ALERTA_CRUDOS,
    TIPO_ALERTA_QUEMADOS,
    MonitorCalidad,
)
from backend.lote.tipos import EstadoProducto
from backend.notificaciones.cliente import ClienteNotificaciones


class TestMonitorCalidad:
    """Verifica la lógica de la ventana deslizante y buffers separados."""

    def test_productos_ok_no_disparan_alerta(self) -> None:
        monitor = MonitorCalidad(umbral_anomalia=20, ventana_segundos=10.0)
        for i in range(50):
            alerta = monitor.registrar(EstadoProducto.OK, ahora=float(i))
            assert alerta is None

    def test_umbral_alcanzado_en_ventana_dispara_quemados(self) -> None:
        monitor = MonitorCalidad(umbral_anomalia=20, ventana_segundos=10.0, cooldown_segundos=60.0)
        # 19 productos quemados en 5 segundos -> ninguno dispara
        for i in range(19):
            alerta = monitor.registrar(EstadoProducto.QUEMADO, ahora=float(i) * 0.2)
            assert alerta is None

        # El producto 20 dentro de los 10 segundos dispara
        alerta = monitor.registrar(EstadoProducto.QUEMADO, ahora=4.0)
        assert alerta == TIPO_ALERTA_QUEMADOS

    def test_umbral_alcanzado_en_ventana_dispara_crudos(self) -> None:
        monitor = MonitorCalidad(umbral_anomalia=20, ventana_segundos=10.0, cooldown_segundos=60.0)
        for i in range(19):
            alerta = monitor.registrar(EstadoProducto.CRUDO, ahora=float(i) * 0.2)
            assert alerta is None

        alerta = monitor.registrar(EstadoProducto.CRUDO, ahora=4.0)
        assert alerta == TIPO_ALERTA_CRUDOS

    def test_buffers_independientes(self) -> None:
        monitor = MonitorCalidad(umbral_anomalia=20, ventana_segundos=10.0)
        # 15 quemados y 15 crudos en 5 segundos (total 30 defectuosos, pero ninguno llega a 20)
        for i in range(15):
            assert monitor.registrar(EstadoProducto.QUEMADO, ahora=float(i) * 0.2) is None
            assert monitor.registrar(EstadoProducto.CRUDO, ahora=float(i) * 0.2) is None

        # Sumar 5 quemados más llega a 20 quemados
        for i in range(4):
            assert monitor.registrar(EstadoProducto.QUEMADO, ahora=3.5 + (i * 0.1)) is None

        alerta = monitor.registrar(EstadoProducto.QUEMADO, ahora=4.0)
        assert alerta == TIPO_ALERTA_QUEMADOS

    def test_eventos_vencidos_no_acumulan(self) -> None:
        monitor = MonitorCalidad(umbral_anomalia=20, ventana_segundos=10.0)
        # 19 quemados espaciados cada 2 segundos (cubren 38 segundos)
        for i in range(19):
            alerta = monitor.registrar(EstadoProducto.QUEMADO, ahora=float(i * 2))
            assert alerta is None

        # Al segundo 40, los primeros ya vencieron (> 10s de antigüedad)
        alerta = monitor.registrar(EstadoProducto.QUEMADO, ahora=40.0)
        assert alerta is None

    def test_cooldown_evita_spam(self) -> None:
        monitor = MonitorCalidad(umbral_anomalia=20, ventana_segundos=10.0, cooldown_segundos=60.0)
        for _ in range(20):
            alerta = monitor.registrar(EstadoProducto.QUEMADO, ahora=1.0)

        # El vigésimo disparó
        assert alerta == TIPO_ALERTA_QUEMADOS

        # Siguientes en t=2.0 no disparan por cooldown
        assert monitor.registrar(EstadoProducto.QUEMADO, ahora=2.0) is None
        # En t=50.0 todavía está en cooldown (< 60s)
        assert monitor.registrar(EstadoProducto.QUEMADO, ahora=50.0) is None

        # Pasados los 60s (t=62.0), si se vuelven a acumular 20, vuelve a disparar
        for _ in range(19):
            monitor.registrar(EstadoProducto.QUEMADO, ahora=62.0)
        alerta = monitor.registrar(EstadoProducto.QUEMADO, ahora=62.5)
        assert alerta == TIPO_ALERTA_QUEMADOS


class TestPorcentajeVentana:
    """El porcentaje se mide sobre todos los eventos de la ventana (OK incluidos)."""

    def test_porcentaje_sobre_total_de_la_ventana(self) -> None:
        monitor = MonitorCalidad(umbral_anomalia=2, ventana_segundos=10.0, cooldown_segundos=60.0)
        for i in range(6):
            monitor.registrar(EstadoProducto.OK, ahora=float(i) * 0.1)
        monitor.registrar(EstadoProducto.QUEMADO, ahora=1.0)
        alerta = monitor.registrar(EstadoProducto.QUEMADO, ahora=1.1)
        assert alerta == TIPO_ALERTA_QUEMADOS
        # 2 quemados de 8 eventos en la ventana -> 25 %
        assert monitor.porcentaje(TIPO_ALERTA_QUEMADOS, ahora=1.1) == pytest.approx(25.0)
        assert monitor.porcentaje(TIPO_ALERTA_CRUDOS, ahora=1.1) == 0.0

    def test_porcentaje_ignora_eventos_vencidos(self) -> None:
        monitor = MonitorCalidad(umbral_anomalia=2, ventana_segundos=10.0)
        monitor.registrar(EstadoProducto.OK, ahora=0.0)
        monitor.registrar(EstadoProducto.QUEMADO, ahora=20.0)
        # El OK de t=0 ya venció: 1 quemado de 1 evento -> 100 %
        assert monitor.porcentaje(TIPO_ALERTA_QUEMADOS, ahora=20.0) == pytest.approx(100.0)

    def test_porcentaje_sin_eventos_es_cero(self) -> None:
        monitor = MonitorCalidad()
        assert monitor.porcentaje(TIPO_ALERTA_QUEMADOS, ahora=5.0) == 0.0


def _api() -> ApiConfig:
    return ApiConfig(
        base_url="http://localhost:8080",
        registration_requests_endpoint="/api/v1/registration-requests/",
        dispositivos_ping_endpoint="/api/v1/dispositivos/ping",
        dispositivos_nombre_endpoint="/api/v1/dispositivos/nombre",
        dispositivos_rename_url_endpoint="/api/v1/dispositivos/rename-url",
        productos_endpoint="/api/v1/productos",
        dispositivos_sector_endpoint="/api/v1/dispositivos/sector",
        lotes_endpoint="/api/v1/lotes",
    )


def _respuesta(cuerpo: dict) -> MagicMock:
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.read.return_value = json.dumps(cuerpo).encode()
    return resp


class TestClienteNotificaciones:
    """La alerta va al lote abierto con el secret del dispositivo, sin reintentos."""

    def _cliente(self, habilitado: bool = True) -> ClienteNotificaciones:
        cfg = NotificacionesConfig(
            habilitado=habilitado,
            umbral_anomalia=10,
            ventana_segundos=10.0,
            cooldown_segundos=60.0,
        )
        return ClienteNotificaciones(_api(), cfg)

    @patch("urllib.request.urlopen")
    def test_alerta_creada(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _respuesta({"success": True, "data": {"creada": True}})

        ok = self._cliente().enviar_alerta("secret-xyz", "lote-1", TIPO_ALERTA_QUEMADOS, 8.4567)

        assert ok is True
        assert mock_urlopen.call_count == 1
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:8080/api/v1/lotes/lote-1/alertas-calidad"
        assert req.get_method() == "POST"
        assert req.headers["Authorization"] == "Bearer secret-xyz"
        assert json.loads(req.data.decode()) == {
            "tipo": TIPO_ALERTA_QUEMADOS,
            "porcentaje": 8.46,
            "umbral": 10,
            "ventana_segundos": 10,
        }
        # Sin ".0": el JSON crudo lleva enteros exactos, no floats.
        assert b'"umbral": 10, "ventana_segundos": 10}' in req.data

    @patch("urllib.request.urlopen")
    def test_alerta_ya_activa_es_exito_sin_reintento(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _respuesta({"success": True, "data": {"creada": False}})

        ok = self._cliente().enviar_alerta("secret-xyz", "lote-1", TIPO_ALERTA_CRUDOS, 30.0)

        assert ok is True
        assert mock_urlopen.call_count == 1

    @patch("urllib.request.urlopen")
    def test_error_http_no_reintenta(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://localhost:8080", 409, "Conflict", {}, io.BytesIO(b'{"error":"cerrado"}')
        )

        ok = self._cliente().enviar_alerta("secret-xyz", "lote-1", TIPO_ALERTA_QUEMADOS, 12.0)

        assert ok is False
        assert mock_urlopen.call_count == 1

    @patch("urllib.request.urlopen")
    def test_deshabilitado_no_envia(self, mock_urlopen: MagicMock) -> None:
        self._cliente(habilitado=False).disparar_alerta_asincrona(
            "secret-xyz", "lote-1", TIPO_ALERTA_QUEMADOS, 12.0
        )

        assert mock_urlopen.call_count == 0
