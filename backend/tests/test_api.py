from dataclasses import dataclass
import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.dependencies import (
    get_detect_use_case,
    get_detector,
    get_finalize_lote_use_case,
    get_send_lote_inicio_use_case,
)
from backend.app.main import app
from backend.tests.fakes import FakeSendLoteUseCase, make_lote_payload
from backend.use_cases.finalize_lote import FinalizeLoteUseCase, LoteDeliveryError


class FakeDetector:
    @property
    def model_path(self):
        return "fake-model"

    def get_class_names(self):
        return ["TCOK", "TCQ"]


@dataclass
class FakeDetection:
    label: str
    confidence: float
    bbox: tuple
    id: int | None = None
    state: str = "ok"


class FakeDetectUseCase:
    def __init__(self, detections=None):
        self.detections = detections if detections is not None else []
        self.calls = []

    def execute(self, frame):
        self.calls.append(frame)
        return self.detections


class FakeSendLoteInicioUseCase:
    def __init__(
        self,
        success=True,
        status_code=None,
        error=None,
        response_text=None,
        target="http://central:9000/api/v1/lotes/inicio",
    ):
        self.success = success
        self.status_code = status_code
        self.error = error
        self.response_text = response_text
        self.target = target
        self.calls = []

    def execute(self, horno_id, producto_id):
        self.calls.append((horno_id, producto_id))
        return self.success

    def get_last_status_code(self):
        return self.status_code

    def get_last_error(self):
        return self.error

    def get_last_response_text(self):
        return self.response_text


def make_empty_settings() -> Settings:
    return Settings(
        device_api_base_url="",
        device_auth_audience="",
        device_identity_dir="/tmp/smart-check-test-identity",
        horno_id="",
        default_producto_id="",
        ping_interval_seconds=10.0,
    )


@pytest.fixture
def detect_use_case():
    return FakeDetectUseCase()


@pytest.fixture
def lote_inicio_use_case():
    return FakeSendLoteInicioUseCase()


@pytest.fixture
def client(detect_use_case, lote_inicio_use_case):
    app.dependency_overrides[get_detector] = lambda: FakeDetector()
    app.dependency_overrides[get_detect_use_case] = lambda: detect_use_case
    app.dependency_overrides[get_finalize_lote_use_case] = lambda: FinalizeLoteUseCase(
        FakeSendLoteUseCase(success=True), "http://central:9000/api/v1"
    )
    app.dependency_overrides[get_send_lote_inicio_use_case] = (
        lambda: lote_inicio_use_case
    )
    app.dependency_overrides[get_settings] = make_empty_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_status(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    assert response.json() == {
        "status": "online",
        "detector": {"model_path": "fake-model", "classes": ["TCOK", "TCQ"]},
    }


def test_finalizar_lote_success(client):
    response = client.post("/api/lotes/finalizar", json=make_lote_payload())
    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Lote enviado al servidor central.",
        "target": "http://central:9000/api/v1/lotes",
    }


def test_finalizar_lote_validation_returns_400(client):
    response = client.post("/api/lotes/finalizar", json=make_lote_payload(totalUnidades=99))
    assert response.status_code == 400
    assert "total de unidades" in response.json()["detail"]


def test_finalizar_lote_missing_field_returns_400(client):
    payload = make_lote_payload()
    payload.pop("turno")
    response = client.post("/api/lotes/finalizar", json=payload)
    assert response.status_code == 400
    assert "Faltan campos obligatorios" in response.json()["detail"]


def test_finalizar_lote_delivery_failure_returns_502(client):
    app.dependency_overrides[get_finalize_lote_use_case] = lambda: FinalizeLoteUseCase(
        FakeSendLoteUseCase(
            success=False, status_code=500, error="boom", response_text="server error"
        ),
        "http://central:9000/api/v1",
    )
    response = client.post("/api/lotes/finalizar", json=make_lote_payload())
    assert response.status_code == 502
    assert response.json()["detail"] == {
        "message": "No se pudo registrar el lote en el servidor central.",
        "target": "http://central:9000/api/v1/lotes",
        "status_code": 500,
        "error": "boom",
        "response": "server error",
    }


def test_detect_with_valid_image(client, detect_use_case):
    detect_use_case.detections = [
        FakeDetection(
            label="Tostada Quemada",
            confidence=0.91,
            bbox=(1, 2, 3, 4),
            id=7,
            state="burnt",
        )
    ]
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", image)
    assert ok

    response = client.post(
        "/api/detect",
        files={"file": ("toast.png", buffer.tobytes(), "image/png")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "detections": [
            {
                "label": "Tostada Quemada",
                "confidence": 0.91,
                "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                "id": 7,
                "state": "burnt",
            }
        ],
        "summary": {"total_detected": 1, "burned_toast_found": True},
    }
    assert len(detect_use_case.calls) == 1


def test_detect_without_detections(client):
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", image)
    assert ok

    response = client.post(
        "/api/detect", files={"file": ("toast.png", buffer.tobytes(), "image/png")}
    )

    assert response.status_code == 200
    assert response.json() == {
        "detections": [],
        "summary": {"total_detected": 0, "burned_toast_found": False},
    }


def test_detect_with_lote_payload(client):
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", image)
    assert ok

    response = client.post(
        "/api/detect",
        files={"file": ("toast.png", buffer.tobytes(), "image/png")},
        data={"lote_payload": json.dumps(make_lote_payload())},
    )

    assert response.status_code == 200
    assert response.json()["lote"] == {
        "status": "success",
        "message": "Lote enviado al servidor central.",
        "target": "http://central:9000/api/v1/lotes",
    }


def _valid_png_bytes():
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return buffer.tobytes()


class RaisingFinalizeUseCase:
    def execute(self, payload):
        raise LoteDeliveryError(
            status_code=500,
            error="boom",
            response_text="server error",
            target="http://central:9000/api/v1/lotes",
        )


def test_detect_invalid_image_returns_400(client):
    response = client.post(
        "/api/detect",
        files={"file": ("toast.png", b"not-an-image", "image/png")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Formato de imagen inválido."


def test_detect_malformed_lote_payload_returns_400(client):
    response = client.post(
        "/api/detect",
        files={"file": ("toast.png", _valid_png_bytes(), "image/png")},
        data={"lote_payload": "{not valid json"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "El campo lote_payload debe ser un JSON válido."


def test_detect_non_object_lote_payload_returns_400(client):
    response = client.post(
        "/api/detect",
        files={"file": ("toast.png", _valid_png_bytes(), "image/png")},
        data={"lote_payload": json.dumps(["not", "an", "object"])},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "El campo lote_payload debe ser un objeto JSON."


def test_detect_missing_lote_fields_returns_400(client):
    response = client.post(
        "/api/detect",
        files={"file": ("toast.png", _valid_png_bytes(), "image/png")},
        data={"lote_payload": json.dumps({})},
    )
    assert response.status_code == 400
    assert "Faltan campos obligatorios" in response.json()["detail"]


def test_detect_lote_delivery_failure_returns_502(client):
    app.dependency_overrides[get_finalize_lote_use_case] = lambda: RaisingFinalizeUseCase()
    response = client.post(
        "/api/detect",
        files={"file": ("toast.png", _valid_png_bytes(), "image/png")},
        data={"lote_payload": json.dumps(make_lote_payload())},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == {
        "message": "No se pudo registrar el lote en el servidor central.",
        "target": "http://central:9000/api/v1/lotes",
        "status_code": 500,
        "error": "boom",
        "response": "server error",
    }


def test_iniciar_lote_success_with_body(client, lote_inicio_use_case):
    response = client.post(
        "/api/lotes/iniciar",
        json={"hornoId": "horno-1", "productoId": "prod-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "message": "Inicio de lote enviado al servidor central.",
        "target": "http://central:9000/api/v1/lotes/inicio",
    }
    assert lote_inicio_use_case.calls == [("horno-1", "prod-1")]


def test_iniciar_lote_success_with_defaults(client, lote_inicio_use_case):
    def settings_with_defaults():
        return Settings(
            device_api_base_url="http://central:9000/api/v1",
            device_auth_audience="https://api.central/api/v1",
            device_identity_dir="/tmp/smart-check-test-identity",
            horno_id="horno-default",
            default_producto_id="prod-default",
            ping_interval_seconds=10.0,
        )

    app.dependency_overrides[get_settings] = settings_with_defaults

    response = client.post("/api/lotes/iniciar")

    assert response.status_code == 200
    assert lote_inicio_use_case.calls == [("horno-default", "prod-default")]


def test_iniciar_lote_missing_config_returns_400(client):
    response = client.post("/api/lotes/iniciar")

    assert response.status_code == 400
    assert "hornoId" in response.json()["detail"]


def test_iniciar_lote_delivery_failure_returns_502(client):
    app.dependency_overrides[get_send_lote_inicio_use_case] = (
        lambda: FakeSendLoteInicioUseCase(
            success=False, status_code=422, error="invalid", response_text="nope"
        )
    )

    response = client.post(
        "/api/lotes/iniciar",
        json={"hornoId": "horno-1", "productoId": "prod-1"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "message": "No se pudo iniciar el lote en el servidor central.",
        "target": "http://central:9000/api/v1/lotes/inicio",
        "status_code": 422,
        "error": "invalid",
        "response": "nope",
    }
