"""Cota de tamaño de subida en POST /api/detect.

El endpoint es local y sin autenticación: sin límite, un body enorme puede
agotar la memoria del dispositivo. Se verifica que el payload sobredimensionado
se rechaza con 413 y que un upload normal conserva el camino existente.
"""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.dependencies import (
    get_detect_use_case,
    get_detector,
    get_finalize_lote_use_case,
)
from backend.app.main import app
from backend.app.routers import detection as detection_module


class _FakeDetector:
    @property
    def model_path(self):
        return "fake-model"

    def get_class_names(self):
        return ["TCOK", "TCQ"]


class _FakeDetectUseCase:
    def __init__(self):
        self.calls = []

    def execute(self, frame):
        self.calls.append(frame)
        return []


class _FakeFinalizeUseCase:
    def execute(self, payload):
        return {"status": "success"}


@pytest.fixture
def client():
    detect_use_case = _FakeDetectUseCase()
    app.dependency_overrides[get_detector] = lambda: _FakeDetector()
    app.dependency_overrides[get_detect_use_case] = lambda: detect_use_case
    app.dependency_overrides[get_finalize_lote_use_case] = (
        lambda: _FakeFinalizeUseCase()
    )
    app.dependency_overrides[get_settings] = lambda: Settings(
        device_api_base_url="",
        device_auth_audience="",
        device_identity_dir="/tmp/smart-check-test-identity",
        horno_id="",
        default_producto_id="",
        ping_interval_seconds=10.0,
    )
    with TestClient(app) as test_client:
        yield test_client, detect_use_case
    app.dependency_overrides.clear()


def _valid_png_bytes() -> bytes:
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return buffer.tobytes()


def test_upload_exceeding_limit_returns_413(client):
    test_client, detect_use_case = client
    oversized = b"\x00" * (detection_module.MAX_UPLOAD_BYTES + 1)

    response = test_client.post(
        "/api/detect",
        files={"file": ("big.png", oversized, "image/png")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "La imagen excede el tamaño máximo permitido."
    # El caso de uso de detección no debe ejecutarse con un payload rechazado.
    assert detect_use_case.calls == []


def test_upload_at_limit_is_not_rejected_by_size(client):
    test_client, detect_use_case = client
    # Un byte menos que el máximo no debe caer por la cota de tamaño (aquí el
    # contenido es basura, así que sigue el camino existente: falla como imagen).
    at_limit = b"\x00" * (detection_module.MAX_UPLOAD_BYTES - 1)

    response = test_client.post(
        "/api/detect",
        files={"file": ("at-limit.png", at_limit, "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Formato de imagen inválido."
    assert detect_use_case.calls == []


def test_small_valid_upload_follows_existing_path(client):
    test_client, detect_use_case = client

    response = test_client.post(
        "/api/detect",
        files={"file": ("toast.png", _valid_png_bytes(), "image/png")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "detections": [],
        "summary": {"total_detected": 0, "burned_toast_found": False},
    }
    assert len(detect_use_case.calls) == 1
