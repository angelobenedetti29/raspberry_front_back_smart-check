"""Tests del serializador de detecciones de la capa API."""

from backend.app.serializers import detection_to_dict
from backend.domain.entities.detection import DetectionResult
from backend.use_cases.toast_tracker import TrackedToast


def test_serializa_tostada_trackeada_con_id_y_state():
    toast = TrackedToast(
        id=7, bbox=(1, 2, 3, 4), label="TCQ", confidence=0.91, state="burnt"
    )

    assert detection_to_dict(toast) == {
        "label": "TCQ",
        "confidence": 0.91,
        "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
        "id": 7,
        "state": "burnt",
    }


def test_serializa_deteccion_sin_id_ni_state():
    det = DetectionResult(label="TCOK", confidence=0.8, bbox=(5, 6, 7, 8))

    assert detection_to_dict(det) == {
        "label": "TCOK",
        "confidence": 0.8,
        "bbox": {"x": 5, "y": 6, "width": 7, "height": 8},
    }
