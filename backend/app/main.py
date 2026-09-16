from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.dependencies import get_detector, get_telemetry_loop
from backend.app.routers import detection, lotes, status


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_telemetry_loop().start()
    yield
    get_telemetry_loop().stop()
    # Release a lazily-loaded Hailo device (no-op if the detector never loaded).
    get_detector().release_hailo()


def create_app() -> FastAPI:
    app = FastAPI(
        title="YOLOv11 Toast Detection API",
        description="Backend en Arquitectura Limpia para la inferencia YOLO de tostadas",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.include_router(status.router)
    app.include_router(lotes.router)
    app.include_router(detection.router)

    return app


app = create_app()
