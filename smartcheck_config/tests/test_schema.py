"""Tests del esquema y la serialización de ``AppConfig``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smartcheck_config import load_config
from smartcheck_config.schema import (
    ApiSettings,
    AppConfig,
    CaptureSettings,
    DeviceSettings,
    InferenceSettings,
    ModelEntry,
    ModelsSettings,
    PathsSettings,
    PublisherSettings,
    ReconnectSettings,
    SchemaError,
    StorageSettings,
    StreamSettings,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_JSON = _REPO_ROOT / "config.json"


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults_de_cada_subseccion():
    stream = AppConfig().stream
    assert stream.capture == CaptureSettings()
    assert stream.publisher == PublisherSettings()
    assert stream.inference == InferenceSettings()
    assert stream.storage == StorageSettings()
    assert stream.reconnect == ReconnectSettings()
    assert stream.capture.width == 1280
    assert stream.capture.height == 720
    assert stream.capture.buffer_size == 1
    assert stream.capture.read_timeout_seconds == 5.0
    assert stream.publisher.queue_size == 2
    assert stream.publisher.pixel_format == "yuv420p"
    assert stream.inference.enabled is False
    assert stream.inference.model_path is None
    assert stream.storage.queue_size == 128
    assert stream.storage.path == "streaming/data/detections.jsonl"
    assert stream.reconnect.initial_seconds == 1.0
    assert stream.reconnect.max_seconds == 30.0


def test_api_default_es_loopback():
    """El backend local no debe escuchar en todas las interfaces por defecto."""
    assert ApiSettings().host == "127.0.0.1"
    assert AppConfig().api.host == "127.0.0.1"
    assert AppConfig.from_dict({}).api.host == "127.0.0.1"


def test_default_sections():
    cfg = AppConfig()
    assert cfg.device == DeviceSettings()
    assert cfg.paths == PathsSettings()
    assert cfg.api == ApiSettings()
    assert len(cfg.models.catalog) == 4
    assert cfg.models.catalog[0] == ModelEntry(
        "YOLOv11 Original (COCO)",
        "ai_training/models/yolo11n.onnx",
        "ai_training/models/class.names",
        "yolo11n-coco",
    )
    assert cfg.models.default_model_id == "yolo11n-coco"
    assert cfg.models.npu_model_id == "yolov8s-hailo"


# ---------------------------------------------------------------------------
# Serialización
# ---------------------------------------------------------------------------


def test_to_dict_shape_and_key_order():
    data = AppConfig().to_dict()
    assert list(data.keys()) == [
        "schema_version",
        "revision",
        "stream",
        "device",
        "models",
        "paths",
        "api",
    ]
    stream = data["stream"]
    assert list(stream.keys()) == [
        "capture",
        "publisher",
        "inference",
        "storage",
        "reconnect",
    ]
    assert list(stream["capture"].keys()) == [
        "source",
        "width",
        "height",
        "fps",
        "loop_video",
        "buffer_size",
        "stable_frames",
        "read_timeout_seconds",
    ]
    assert list(stream["publisher"].keys()) == [
        "output_url",
        "bitrate",
        "ffmpeg_executable",
        "encoder",
        "pixel_format",
        "gop_seconds",
        "b_frames",
        "queue_size",
        "write_timeout",
        "stable_seconds",
    ]
    assert list(stream["inference"].keys()) == [
        "enabled",
        "require_hailo",
        "model_path",
        "labels_path",
        "confidence_threshold",
    ]
    assert list(stream["storage"].keys()) == [
        "path",
        "queue_size",
        "max_bytes",
        "max_files",
        "persist_no_detection_every",
    ]
    assert list(stream["reconnect"].keys()) == [
        "initial_seconds",
        "max_seconds",
    ]
    assert stream["capture"]["buffer_size"] == 1
    assert stream["publisher"]["queue_size"] == 2
    assert stream["storage"]["path"] == "streaming/data/detections.jsonl"
    assert stream["reconnect"]["initial_seconds"] == 1.0
    assert list(data["models"]["catalog"][0].keys()) == [
        "model_id",
        "label",
        "model_path",
        "names_path",
    ]


def test_to_dict_es_igual_a_config_json():
    """``load_config().config.to_dict()`` reproduce el ``config.json`` real."""
    loaded = load_config(_CONFIG_JSON)
    actual = loaded.config.to_dict()
    expected = json.loads(_CONFIG_JSON.read_text(encoding="utf-8"))

    # ``config.json`` no versiona la revisión en el archivo del repo.
    actual.pop("revision", None)
    expected.pop("revision", None)

    assert actual == expected
    # Mismo orden de claves también en el nivel raíz.
    assert list(actual.keys()) == list(expected.keys())


def test_round_trip_to_dict_from_dict():
    original = AppConfig()
    restored = AppConfig.from_dict(original.to_dict())
    assert restored == original


# ---------------------------------------------------------------------------
# Parseo tolerante
# ---------------------------------------------------------------------------


def test_from_dict_partial_uses_defaults():
    cfg = AppConfig.from_dict({"device": {"horno_id": "horno-1"}})
    assert cfg.device.horno_id == "horno-1"
    assert cfg.device.api_base_url == ""
    assert cfg.stream == StreamSettings()
    assert cfg.paths == PathsSettings()


def test_from_dict_seccion_parcial_usa_defaults():
    cfg = AppConfig.from_dict({"stream": {"capture": {"width": 640}}})
    assert cfg.stream.capture.width == 640
    assert cfg.stream.capture.height == 720
    assert cfg.stream.capture.fps == 30
    assert cfg.stream.publisher == PublisherSettings()


def test_from_dict_unknown_keys_ignored():
    cfg = AppConfig.from_dict(
        {
            "schema_version": 1,
            "futuro": {"algo": 1},
            "stream": {"capture": {"width": 640}, "desconocido": 42},
        }
    )
    assert cfg.stream.capture.width == 640
    assert cfg.stream.capture.height == 720


def test_from_dict_flat_stream_keys_no_longer_accepted():
    """La antigua forma plana ya no setea campos anidados."""
    cfg = AppConfig.from_dict(
        {
            "stream": {
                "output_url": "rtsp://example/out",
                "capture_buffer_size": 3,
                "publisher_queue_size": 7,
                "inference_enabled": True,
                "storage_max_bytes": 2048,
            }
        }
    )
    assert cfg.stream.publisher.output_url == PublisherSettings().output_url
    assert cfg.stream.capture.buffer_size == 1
    assert cfg.stream.publisher.queue_size == 2
    assert cfg.stream.inference.enabled is False
    assert cfg.stream.storage.max_bytes == 10485760


def test_from_dict_wrong_type_reports_dotted_key_and_source():
    with pytest.raises(SchemaError) as excinfo:
        AppConfig.from_dict(
            {"stream": {"capture": {"width": "ancho"}}},
            source="prueba.json",
        )
    message = str(excinfo.value)
    assert "stream.capture.width" in message
    assert "prueba.json" in message


def test_from_dict_none_en_union_se_acepta():
    cfg = AppConfig.from_dict(
        {"stream": {"inference": {"model_path": None}}}
    )
    assert cfg.stream.inference.model_path is None


def test_from_dict_bool_no_es_numero():
    with pytest.raises(SchemaError) as excinfo:
        AppConfig.from_dict(
            {"stream": {"capture": {"buffer_size": True}}},
            source="cfg.json",
        )
    assert "stream.capture.buffer_size" in str(excinfo.value)


def test_from_dict_int_a_float():
    cfg = AppConfig.from_dict(
        {"stream": {"capture": {"read_timeout_seconds": 3}}}
    )
    assert cfg.stream.capture.read_timeout_seconds == 3.0
    assert isinstance(cfg.stream.capture.read_timeout_seconds, float)


def test_from_dict_seccion_debe_ser_objeto():
    with pytest.raises(SchemaError) as excinfo:
        AppConfig.from_dict(
            {"stream": {"capture": 5}}, source="cfg.json"
        )
    assert "stream.capture" in str(excinfo.value)


def test_from_dict_root_must_be_object():
    with pytest.raises(SchemaError):
        AppConfig.from_dict([])  # type: ignore[arg-type]


def test_from_dict_models_catalog_custom():
    cfg = AppConfig.from_dict(
        {
            "models": {
                "default_model_id": "custom",
                "catalog": [
                    {
                        "model_id": "custom",
                        "label": "Custom",
                        "model_path": "a.onnx",
                        "names_path": "a.names",
                    }
                ],
            }
        }
    )
    assert cfg.models.default_model_id == "custom"
    assert cfg.models.catalog == (
        ModelEntry("Custom", "a.onnx", "a.names", "custom"),
    )


def test_from_dict_models_catalog_missing_key():
    with pytest.raises(SchemaError) as excinfo:
        AppConfig.from_dict(
            {"models": {"catalog": [{"model_id": "x", "label": "X"}]}},
            source="cfg.json",
        )
    assert "models.catalog[0].model_path" in str(excinfo.value)


def test_model_by_id_found_and_missing():
    models = ModelsSettings()
    assert models.model_by_id("tostadas-v1").model_path.endswith("tostadas_v1.onnx")
    with pytest.raises(SchemaError):
        models.model_by_id("no-existe")


# ---------------------------------------------------------------------------
# Validaciones por sección
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"width": 0},
        {"height": -1},
        {"fps": 25},
        {"buffer_size": 0},
        {"stable_frames": 0},
        {"read_timeout_seconds": 0},
    ],
)
def test_capture_validation_errors(overrides):
    with pytest.raises(SchemaError):
        CaptureSettings(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"output_url": ""},
        {"pixel_format": "nv12"},
        {"gop_seconds": 3},
        {"b_frames": -1},
        {"queue_size": 0},
        {"write_timeout": 0},
        {"stable_seconds": -1},
    ],
)
def test_publisher_validation_errors(overrides):
    with pytest.raises(SchemaError):
        PublisherSettings(**overrides)


def test_inference_require_hailo_implies_enabled():
    with pytest.raises(SchemaError):
        InferenceSettings(require_hailo=True, enabled=False)
    assert InferenceSettings(require_hailo=True, enabled=True).require_hailo


@pytest.mark.parametrize(
    "overrides",
    [
        {"queue_size": 0},
        {"max_bytes": 0},
        {"max_files": 0},
        {"persist_no_detection_every": -1},
    ],
)
def test_storage_validation_errors(overrides):
    with pytest.raises(SchemaError):
        StorageSettings(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"initial_seconds": -1},
        {"initial_seconds": 5, "max_seconds": 1},
    ],
)
def test_reconnect_validation_errors(overrides):
    with pytest.raises(SchemaError):
        ReconnectSettings(**overrides)


@pytest.mark.parametrize("port", [0, 65536, -1, "8000", True])
def test_api_port_validation(port):
    with pytest.raises(SchemaError):
        ApiSettings(port=port)


def test_device_ping_validation():
    with pytest.raises(SchemaError):
        DeviceSettings(ping_interval_seconds=0)
