"""Metadata de /api/status y carga perezosa del detector (sin modelo real)."""

import backend.app.dependencies as dependencies
from backend.app.dependencies import LazyYoloDetector
from backend.app.routers.status import get_status


class StubYoloDetector:
    def __init__(self):
        self.model_path = "stub.onnx"

    def detect_frame(self, frame):
        return []

    def get_class_names(self):
        return ["TCQ", "TCOK"]

    def release_hailo(self):
        pass


def _yolo_que_no_debe_construirse(*_args, **_kwargs):
    raise AssertionError("no se debe cargar el detector para consultar metadata")


def test_metadata_no_dispara_la_carga(monkeypatch):
    monkeypatch.setattr(dependencies, "YoloDetector", _yolo_que_no_debe_construirse)
    detector = LazyYoloDetector()

    assert detector.loaded is False
    assert detector.model_path is None
    assert detector.error == ""

    assert get_status(detector=detector) == {
        "status": "online",
        "detector": {"loaded": False, "model_path": None, "classes": [], "error": ""},
    }


def test_status_con_detector_cargado(monkeypatch):
    monkeypatch.setattr(dependencies, "YoloDetector", StubYoloDetector)
    detector = LazyYoloDetector()
    detector.detect_frame(None)  # única vía de carga perezosa

    assert detector.loaded is True
    assert detector.error == ""
    assert get_status(detector=detector) == {
        "status": "online",
        "detector": {
            "loaded": True,
            "model_path": "stub.onnx",
            "classes": ["TCQ", "TCOK"],
            "error": "",
        },
    }


def test_status_reporta_fallback(monkeypatch):
    def yolo_roto(*_args, **_kwargs):
        raise RuntimeError("sin modelo")

    monkeypatch.setattr(dependencies, "YoloDetector", yolo_roto)
    detector = LazyYoloDetector()
    detector.detect_frame(None)

    body = get_status(detector=detector)["detector"]
    assert body["loaded"] is True
    assert body["error"] == "sin modelo"
    assert body["model_path"] == "Mocked (Error)"
    assert body["classes"] == ["Tostada Quemada", "tostadas ok"]


def test_status_tolera_dobles_sin_metadata():
    class DobleMinimo:
        @property
        def model_path(self):
            return "fake-model"

        def get_class_names(self):
            return ["TCOK", "TCQ"]

    assert get_status(detector=DobleMinimo()) == {
        "status": "online",
        "detector": {
            "loaded": True,
            "model_path": "fake-model",
            "classes": ["TCOK", "TCQ"],
            "error": "",
        },
    }
