"""Tests unitarios del monitor de calidad y cliente de notificaciones."""

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from backend.config import NotificacionesConfig
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


class TestClienteNotificaciones:
    """Verifica el flujo HTTP contra el backend simulado."""

    @patch("urllib.request.urlopen")
    def test_flujo_completo_notificaciones(self, mock_urlopen: MagicMock) -> None:
        # Mock de 4 respuestas:
        # 1. /api/v1/auth/login -> { "data": { "token": "jwt-token-123" } }
        # 2. /api/v1/admin/notificaciones -> { "data": { "destinatarios": [{"email": "operador@fermar.com", "usuario_id": "usr-999"}] } }
        # 3. /api/v1/admin/notificaciones/test (EMAIL) -> status 200
        # 4. /api/v1/admin/notificaciones/test (TELEGRAM) -> status 200

        resp_login = MagicMock()
        resp_login.__enter__.return_value = resp_login
        resp_login.read.return_value = json.dumps({"data": {"token": "jwt-token-123"}}).encode()

        resp_destinatarios = MagicMock()
        resp_destinatarios.__enter__.return_value = resp_destinatarios
        resp_destinatarios.read.return_value = json.dumps(
            {
                "data": {
                    "destinatarios": [
                        {"email": "otro@fermar.com", "usuario_id": "usr-111"},
                        {"email": "operador@fermar.com", "usuario_id": "usr-999"},
                    ]
                }
            }
        ).encode()

        resp_test_email = MagicMock()
        resp_test_email.__enter__.return_value = resp_test_email
        resp_test_email.status = 200

        resp_test_tg = MagicMock()
        resp_test_tg.__enter__.return_value = resp_test_tg
        resp_test_tg.status = 200

        mock_urlopen.side_effect = [
            resp_login,
            resp_destinatarios,
            resp_test_email,
            resp_test_tg,
        ]

        cfg = NotificacionesConfig(
            habilitado=True,
            email_auth="admin@fermar.com.ar",
            password_auth="password123",
            destinatario_email="operador@fermar.com",
            umbral_anomalia=20,
            ventana_segundos=10.0,
            cooldown_segundos=60.0,
        )

        cliente = ClienteNotificaciones("http://localhost:8080", cfg)
        cliente.enviar_alerta_ambos_canales(TIPO_ALERTA_QUEMADOS)

        assert mock_urlopen.call_count == 4

        # Verificar headers y bodies
        llamadas = mock_urlopen.call_args_list

        # Llamada 1: login
        req1 = llamadas[0][0][0]
        assert req1.full_url == "http://localhost:8080/api/v1/auth/login"

        # Llamada 2: destinatarios con bearer token
        req2 = llamadas[1][0][0]
        assert req2.full_url == "http://localhost:8080/api/v1/admin/notificaciones"
        assert req2.headers["Authorization"] == "Bearer jwt-token-123"

        # Llamada 3: test EMAIL
        req3 = llamadas[2][0][0]
        assert req3.full_url == "http://localhost:8080/api/v1/admin/notificaciones/test"
        body3 = json.loads(req3.data.decode())
        assert body3["canal"] == "EMAIL"
        assert body3["usuario_id"] == "usr-999"
        assert body3["tipo"] == TIPO_ALERTA_QUEMADOS

        # Llamada 4: test TELEGRAM
        req4 = llamadas[3][0][0]
        assert req4.full_url == "http://localhost:8080/api/v1/admin/notificaciones/test"
        body4 = json.loads(req4.data.decode())
        assert body4["canal"] == "TELEGRAM"
        assert body4["usuario_id"] == "usr-999"
        assert body4["tipo"] == TIPO_ALERTA_QUEMADOS
