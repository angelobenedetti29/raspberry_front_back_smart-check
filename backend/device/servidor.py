"""Servidor HTTP mínimo para el push de renombre del backend Go a la Raspberry.

Hoy la Raspberry ya empuja su nombre al Go (`ClienteDispositivo.renombrar`).
Este módulo es el camino inverso: expone un único endpoint PUT autenticado con
el `rename_token` que el Go entrega al aprobar el alta (no con el `secret`
principal del dispositivo), para que el backend pueda renombrar el dispositivo.
Sólo stdlib (`http.server`), sin dependencias nuevas, igual que
`backend/api/cliente.py`.

Corre en un hilo daemon propio, aparte del hilo de `DeviceService`: un request
del Go no debe bloquear el polling de registro. `start()`/`stop()` son
idempotentes.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

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
METODO_RENOMBRE = "PUT"

# Tiempo máximo de espera del hilo al apagar el servidor.
TIEMPO_ESPERA_HILO = 5.0


class ServidorRenombre:
    """Servidor HTTP local del push de renombre (start/stop idempotentes)."""

    def __init__(
        self,
        device_service: "DeviceService",
        listen_address: str,
        listen_port: int,
    ) -> None:
        self._device_service = device_service
        self._listen_address = listen_address
        self._listen_port = listen_port
        self._server: ThreadingHTTPServer | None = None
        self._hilo: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Abre el socket y lanza el hilo daemon (idempotente)."""
        with self._lock:
            if self._server is not None:
                return
            handler = _crear_handler(self._device_service)
            try:
                server = ThreadingHTTPServer(
                    (self._listen_address, self._listen_port), handler
                )
            except OSError as exc:
                raise RuntimeError(
                    "No se pudo abrir el servidor de renombre en "
                    f"{self._listen_address}:{self._listen_port}: {exc}"
                ) from exc
            hilo = threading.Thread(
                target=server.serve_forever,
                name="device-renombre",
                daemon=True,
            )
            hilo.start()
            self._server = server
            self._hilo = hilo
            logger.info(
                "device: servidor de renombre escuchando en %s:%s",
                self._listen_address,
                self._listen_port,
            )

    def stop(self) -> None:
        """Corta el serve_forever, cierra el socket y espera al hilo (bloquea)."""
        with self._lock:
            server = self._server
            hilo = self._hilo
            self._server = None
            self._hilo = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if hilo is not None:
            hilo.join(timeout=TIEMPO_ESPERA_HILO)

    def is_alive(self) -> bool:
        """True si el servidor está arrancado y su hilo sigue vivo."""
        with self._lock:
            return (
                self._server is not None
                and self._hilo is not None
                and self._hilo.is_alive()
            )


def _crear_handler(device_service: "DeviceService") -> type[BaseHTTPRequestHandler]:
    """Arma la clase handler cerrando sobre el `DeviceService`."""

    class HandlerRenombre(BaseHTTPRequestHandler):
        server_version = "SmartCheckRenombre/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            # Un print por request inunda stdout; el logging lo maneja la app.
            return

        def do_PUT(self) -> None:
            # El método lo garantiza el dispatch; la ruta es lo único a validar.
            # Formato esperado: /api/v1/dispositivos/<device_id>/nombre.
            device_id_url = self._extraer_device_id(self.path)
            if device_id_url is None:
                self._responder(404, {"error": "Ruta no encontrada"})
                return
            device_id_local = device_service.device_id()
            if device_id_local is None or device_id_local != device_id_url:
                # Defensa en profundidad: el rename_token ya autentica, pero
                # no aceptar un push dirigido a otro UUID evita confusiones si
                # en algún momento hay más de un device por instancia.
                self._responder(
                    403,
                    {"error": "El dispositivo de la URL no está registrado en esta Pi"},
                )
                return
            try:
                self._procesar_renombre()
            except (BrokenPipeError, ConnectionResetError):
                # El cliente cerró la conexión: no hay a quién responder.
                return

        @staticmethod
        def _extraer_device_id(path: str) -> str | None:
            """Devuelve el UUID del path o None si el formato no matchea."""
            if not path.startswith(PREFIJO_RENOMBRE) or not path.endswith(SUFIJO_RENOMBRE):
                return None
            interior = path[len(PREFIJO_RENOMBRE):-len(SUFIJO_RENOMBRE)]
            # El device_id tiene que ser no vacío y no tener '/' (sería otra
            # ruta). La Pi no valida que sea un UUID estricto: si el Go
            # manda otra cosa pero coincide, lo aceptamos.
            if not interior or "/" in interior:
                return None
            return interior

        def __getattr__(self, nombre: str) -> Any:
            # Cualquier método distinto de PUT (GET, POST, ...) devuelve 405.
            if nombre.startswith("do_"):
                return self._metodo_no_permitido
            raise AttributeError(nombre)

        def _metodo_no_permitido(self) -> None:
            self._responder(
                405,
                {"error": f"Método no permitido; usá {METODO_RENOMBRE}"},
                extra_headers={"Allow": METODO_RENOMBRE},
            )

        def _procesar_renombre(self) -> None:
            if not self._autorizado():
                return
            nombre = self._leer_nombre()
            if nombre is None:
                return
            try:
                # Hace todo el trabajo: memoria del DeviceService + config.json
                # atómico y validado. Si falla, NO se responde 200.
                device_service.aplicar_renombre_externo(nombre)
            except Exception as exc:  # noqa: BLE001 - se traduce a 500 con detalle
                # El detalle del error no contiene el secret.
                logger.warning("device: fallo al aplicar renombre externo: %s", exc)
                self._responder(
                    500,
                    {"error": f"No se pudo aplicar el renombre: {exc}"},
                )
                return
            self._responder(200, {"nombre": nombre})

        def _autorizado(self) -> bool:
            rename_token = device_service.rename_token()
            if rename_token is None:
                # Sin token la Pi no está aprobada: ningún request autentica.
                self._responder(403, {"error": "El dispositivo no está registrado"})
                return False
            cabecera = self.headers.get("Authorization", "")
            # Comparación en tiempo constante contra el Bearer esperado. Se
            # compara bytes porque los headers pueden traer latín-1 y
            # compare_digest rechaza str con caracteres no ASCII.
            if not hmac.compare_digest(
                cabecera.encode("utf-8"), f"Bearer {rename_token}".encode("utf-8")
            ):
                self._responder(401, {"error": "Credencial inválida"})
                return False
            return True

        def _leer_nombre(self) -> str | None:
            """Lee y valida el body. Devuelve el nombre o None (ya respondió)."""
            crudo = self.headers.get("Content-Length")
            try:
                largo = int(crudo)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                self._responder(
                    400, {"error": "Falta Content-Length o no es un entero"}
                )
                return None
            if largo <= 0:
                self._responder(
                    400, {"error": "Content-Length debe ser un entero positivo"}
                )
                return None

            cuerpo = self.rfile.read(largo)
            try:
                datos = json.loads(cuerpo.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._responder(400, {"error": "El cuerpo no es JSON válido"})
                return None
            if not isinstance(datos, dict):
                self._responder(400, {"error": "El cuerpo debe ser un objeto JSON"})
                return None
            nombre = datos.get("nombre")
            if not isinstance(nombre, str) or not nombre.strip():
                self._responder(
                    400,
                    {"error": "El campo 'nombre' debe ser un string no vacío"},
                )
                return None
            return nombre.strip()

        def _responder(
            self,
            codigo: int,
            payload: dict,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            cuerpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(codigo)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(cuerpo)))
                if extra_headers:
                    for clave, valor in extra_headers.items():
                        self.send_header(clave, valor)
                self.end_headers()
                self.wfile.write(cuerpo)
            except (BrokenPipeError, ConnectionResetError):
                # El cliente se fue antes de leer la respuesta.
                return

    return HandlerRenombre
