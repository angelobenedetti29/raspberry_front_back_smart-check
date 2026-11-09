"""Cliente para el envío de notificaciones y alertas al backend central.

Implementa autenticación vía JWT (/api/v1/auth/login), resolución de usuarioId
por email (/api/v1/admin/notificaciones) y despacho de alertas de prueba / test
(/api/v1/admin/notificaciones/test) hacia EMAIL y TELEGRAM.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any

from backend.config import NotificacionesConfig

logger = logging.getLogger(__name__)

_TIMEOUT_HTTP = 10.0


class ClienteNotificaciones:
    """Gestiona la comunicación con la API de notificaciones del backend central."""

    def __init__(self, base_url: str, config: NotificacionesConfig) -> None:
        self._base_url = base_url.rstrip("/")
        self._config = config
        self._token: str | None = None
        self._usuario_id: str | None = None
        self._lock = threading.Lock()

    def disparar_alerta_asincrona(self, tipo_alerta: str) -> None:
        """Lanza el envío de notificaciones por EMAIL y TELEGRAM en un hilo daemon."""
        if not self._config.habilitado:
            return

        hilo = threading.Thread(
            target=self._enviar_canales_seguro,
            args=(tipo_alerta,),
            name=f"notificacion-{tipo_alerta}",
            daemon=True,
        )
        hilo.start()

    def _enviar_canales_seguro(self, tipo_alerta: str) -> None:
        """Envía la alerta por Email y Telegram manejando excepciones."""
        try:
            self.enviar_alerta_ambos_canales(tipo_alerta)
        except Exception:
            logger.exception("Error al enviar alerta de calidad: %s", tipo_alerta)

    def enviar_alerta_ambos_canales(self, tipo_alerta: str) -> None:
        """Envía la alerta secuencialmente a EMAIL y TELEGRAM."""
        with self._lock:
            if not self._token:
                self._login()

            if not self._usuario_id:
                self._resolver_usuario_id()

            usuario_id = self._usuario_id
            if not usuario_id:
                logger.warning(
                    "No se pudo resolver usuario_id para %s. Alerta abortada.",
                    self._config.destinatario_email,
                )
                return

        # Envío a ambos canales
        for canal in ("EMAIL", "TELEGRAM"):
            self._enviar_test(canal, usuario_id, tipo_alerta)

    def _login(self) -> None:
        """Autentica contra /api/v1/auth/login y almacena el token Bearer/cookie."""
        url = f"{self._base_url}/api/v1/auth/login"
        payload_dict = {
            "email": self._config.email_auth,
            "password": self._config.password_auth,
        }
        payload = json.dumps(payload_dict).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        logger.info("[HTTP REQUEST] POST %s | payload: {email: %s, password: ***}", url, self._config.email_auth)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_HTTP) as resp:
                resp_text = resp.read().decode("utf-8")
                logger.info("[HTTP RESPONSE] Status %s from %s | body: %s", resp.status, url, resp_text)
                
                token = None
                # 1. Intentar extraer de cookie Set-Cookie: session_token=...
                set_cookie = resp.headers.get("Set-Cookie") or ""
                for part in set_cookie.split(";"):
                    part = part.strip()
                    if part.startswith("session_token="):
                        token = part.split("=", 1)[1]
                        break

                # 2. Si no vino en la cookie, buscar en el body JSON
                if not token:
                    try:
                        data = json.loads(resp_text)
                        token = data.get("token") or (data.get("data") or {}).get("token")
                    except Exception:
                        pass

                if not token:
                    raise ValueError(f"Respuesta de login sin token en cookie ni body: {resp_text}")

                self._token = token
                logger.info("Autenticación exitosa para notificaciones.")
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            logger.error("[HTTP ERROR] Status %s from %s | body: %s", err.code, url, err_body)
            self._token = None
            raise
        except Exception as exc:
            logger.error("Fallo al autenticar contra el backend (%s): %s", url, exc)
            self._token = None
            raise

    def _resolver_usuario_id(self) -> None:
        """Consulta /api/v1/admin/notificaciones para hallar el usuarioId del email configurado."""
        if not self._token:
            return

        url = f"{self._base_url}/api/v1/admin/notificaciones"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Cookie": f"session_token={self._token}",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(
            url,
            headers=headers,
            method="GET",
        )
        logger.info("[HTTP REQUEST] GET %s | Auth: Bearer ...", url)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_HTTP) as resp:
                resp_text = resp.read().decode("utf-8")
                logger.info("[HTTP RESPONSE] Status %s from %s | body: %s", resp.status, url, resp_text)
                data = json.loads(resp_text)
                destinatarios = (data.get("data") or {}).get("destinatarios") or []
                email_objetivo = self._config.destinatario_email.strip().lower()

                for dest in destinatarios:
                    if (dest.get("email") or "").strip().lower() == email_objetivo:
                        self._usuario_id = dest.get("usuario_id")
                        logger.info(
                            "Usuario ID encontrado para %s: %s",
                            email_objetivo,
                            self._usuario_id,
                        )
                        return

                logger.warning(
                    "El email %s no figura entre los destinatarios de notificaciones: %s",
                    email_objetivo,
                    [d.get("email") for d in destinatarios],
                )
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            logger.error("[HTTP ERROR] Status %s from %s | body: %s", err.code, url, err_body)
            if err.code == 401:
                self._token = None
            raise

    def _enviar_test(self, canal: str, usuario_id: str, tipo_alerta: str) -> None:
        """Dispara la llamada a /api/v1/admin/notificaciones/test."""
        url = f"{self._base_url}/api/v1/admin/notificaciones/test"
        payload_dict = {
            "canal": canal,
            "usuario_id": usuario_id,
            "tipo": tipo_alerta,
        }
        payload = json.dumps(payload_dict).encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Cookie": f"session_token={self._token}",
            "Content-Type": "application/json",
        }

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        logger.info("[HTTP REQUEST] POST %s | Canal: %s | UsuarioId: %s | Tipo: %s", url, canal, usuario_id, tipo_alerta)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_HTTP) as resp:
                resp_text = resp.read().decode("utf-8")
                logger.info("[HTTP RESPONSE] Status %s from %s | Canal %s | body: %s", resp.status, url, canal, resp_text)
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            logger.error("[HTTP ERROR] Status %s from %s | Canal %s | body: %s", err.code, url, canal, err_body)
            if err.code == 401:
                self._token = None
        except Exception as exc:
            logger.error("Error inesperado enviando notificación por %s: %s", canal, exc)

