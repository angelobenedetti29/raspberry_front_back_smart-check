from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from backend.app.dependencies import (
    get_detector,
    refresh_runtime,
    shutdown_runtime,
    start_telemetry,
    stop_telemetry,
)
from backend.app.routers import config, detection, lotes, status


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Ciclo de vida de la app: arranca y detiene la telemetría.

    Al apagar libera la NPU si el detector llegó a cargarse.
    """
    start_telemetry()
    try:
        yield
    finally:
        stop_telemetry()
        # Libera el dispositivo Hailo cargado perezosamente (no-op si el detector
        # nunca se cargó).
        get_detector().release_hailo()
        # Cierra los transportes retirados que quedaron pendientes de recargas.
        shutdown_runtime()


def create_app() -> FastAPI:
    """Construye la app FastAPI y registra los routers de la API."""
    app = FastAPI(
        title="YOLOv11 Toast Detection API",
        description="Backend en Arquitectura Limpia para la inferencia YOLO de tostadas",
        version="1.0.0",
        lifespan=lifespan,
        dependencies=[Depends(refresh_runtime)],
    )

    app.include_router(status.router)
    app.include_router(lotes.router)
    app.include_router(detection.router)
    app.include_router(config.router)

    return app


app = create_app()
