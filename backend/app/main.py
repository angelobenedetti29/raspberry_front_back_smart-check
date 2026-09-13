from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.dependencies import get_detector, get_telemetry_loop
from backend.app.routers import detection, devices, lotes, status


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_telemetry_loop().start()
    yield
    get_telemetry_loop().stop()
    # Release a lazily-loaded Hailo device (no-op if the detector never loaded).
    get_detector().release_hailo()


def create_app() -> FastAPI:
    app = FastAPI(
        title="YOLOv11 IoT Toast Detection API",
        description="Backend en Arquitectura Limpia para control de IoT e inferencia YOLO",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS middleware for local frontend communication
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(status.router)
    app.include_router(devices.router)
    app.include_router(lotes.router)
    app.include_router(detection.router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
