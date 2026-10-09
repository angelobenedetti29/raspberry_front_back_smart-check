"""Servidor HTTP general de la app (FastAPI + uvicorn embebido).

Cumple el Protocol `backend.service.Service`: `start()` no bloquea (lanza un
hilo daemon que corre `uvicorn.Server.run()`), `stop()` bloquea (señala
`server.should_exit` y espera al hilo). Recibe una secuencia de `APIRouter` y
los monta en una única app FastAPI: cada módulo del backend que necesite un
endpoint HTTP implementa su router y lo enchufa acá sin tocar este código.

El socket se bindea de forma síncrona en `start()` con
`uvicorn.Config.bind_socket()`, de modo que un puerto ocupado falla en el hilo
que llama (y no silenciosamente dentro del hilo de uvicorn). Así `run.py` puede
reaccionar a un `OSError` como lo hacía el `http.server` anterior.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

# Tiempo máximo de espera del hilo de uvicorn al apagar el servidor.
TIEMPO_ESPERA_HILO = 5.0


class ServidorHTTP:
    """Servidor HTTP general basado en FastAPI + uvicorn embebido.

    Cumple el Protocol `Service`: `start()` no bloquea (lanza un hilo daemon
    con `uvicorn.Server.run()`), `stop()` bloquea (`server.should_exit`
    + `join(timeout=...)`).

    Recibe una secuencia de `APIRouter` en el constructor y los monta en una
    sola app FastAPI. Cada módulo del backend que necesite un endpoint HTTP
    implementa su router y lo enchufa acá, sin tocar este código.

    `rebind(host, port)` para el servidor actual y arranca uno nuevo con los
    mismos routers en el bind indicado: es lo que llama `run.py` cuando el
    operador cambia `device.public_url` o `device.local_port` desde la sección
    Configuración (cambio de túnel). Se para antes de arrancar el nuevo a
    propósito: si el puerto no cambia (sólo cambia el host del bind), no se
    puede bindear el nuevo mientras el viejo siga escuchando. Si el bind nuevo
    falla con `OSError`, la excepción se propaga y `run.py` no sincroniza la
    URL con el backend Go.
    """

    name = "http"

    def __init__(
        self,
        routers: Sequence[APIRouter],
        bind_host: str,
        bind_port: int,
    ) -> None:
        self._routers = tuple(routers)
        self._bind_host = bind_host
        self._bind_port = bind_port
        self._server: uvicorn.Server | None = None
        self._hilo: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Bindea el socket y lanza el hilo de uvicorn (idempotente)."""
        with self._lock:
            if self._server is not None:
                return
            config = self._configurar(self._bind_host, self._bind_port)
            # Bind síncrono: un puerto ocupado levanta OSError acá, en el hilo
            # que llama, igual que el ThreadingHTTPServer anterior.
            socket_bindeado = config.bind_socket()
            server = uvicorn.Server(config)
            hilo = threading.Thread(
                target=server.run,
                args=([socket_bindeado],),
                name="http-uvicorn",
                daemon=True,
            )
            hilo.start()
            self._server = server
            self._hilo = hilo
            logger.info(
                "http: servidor escuchando en %s:%s", self._bind_host, self._bind_port
            )

    def stop(self) -> None:
        """Señala el cese y espera al hilo de uvicorn (bloquea, idempotente)."""
        with self._lock:
            server = self._server
            hilo = self._hilo
            self._server = None
            self._hilo = None
        if server is None:
            return
        server.should_exit = True
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

    def rebind(self, bind_host: str, bind_port: int) -> None:
        """Reemplaza el servidor actual por uno nuevo en el bind indicado.

        Para el actual y arranca uno nuevo con los mismos routers. Si el bind
        nuevo falla con `OSError` (puerto ocupado, sin permisos), `start()`
        propaga la excepción y el servidor queda detenido: `run.py` la captura
        y no sincroniza la URL nueva con el backend Go. El orden para-y-arranca
        es necesario para soportar cambios de host que conservan el puerto.
        """
        logger.info(
            "http: re-bindeando de %s:%s a %s:%s",
            self._bind_host,
            self._bind_port,
            bind_host,
            bind_port,
        )
        self.stop()
        with self._lock:
            self._bind_host = bind_host
            self._bind_port = bind_port
        self.start()
        logger.info("http: servidor re-bindado a %s:%s", bind_host, bind_port)

    def _configurar(self, bind_host: str, bind_port: int) -> uvicorn.Config:
        """Arma el `uvicorn.Config` para un host:puerto dado."""
        return uvicorn.Config(
            self._armar_app(),
            host=bind_host,
            port=bind_port,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )

    def _armar_app(self) -> FastAPI:
        """Monta los routers y unifica el shape de error en `{"error": ...}`."""
        app = FastAPI(
            title="SmartCheckLocal",
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        for router in self._routers:
            app.include_router(router)

        # Todo error HTTP del framework (404, 405, los HTTPException de los
        # routers) sale con `{"error": "..."}`, el mismo shape que consumía el
        # backend Go. Sin esto FastAPI devolvería `{"detail": "..."}`.
        @app.exception_handler(StarletteHTTPException)
        async def _error_http(
            _request: Request, exc: StarletteHTTPException
        ) -> JSONResponse:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": exc.detail},
                headers=exc.headers,
            )

        # Cualquier excepción no contemplada sale con 500 y `{"error": "..."}`
        # para mantener el shape de respuesta.
        @app.exception_handler(Exception)
        async def _error_generico(_request: Request, exc: Exception) -> JSONResponse:
            logger.warning("http: error no contemplado: %s", exc)
            return JSONResponse(
                status_code=500,
                content={"error": f"Error interno: {exc}"},
            )

        return app
