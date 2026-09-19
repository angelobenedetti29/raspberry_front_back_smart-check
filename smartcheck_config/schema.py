"""Esquema de la configuración global de Smart-Check.

``AppConfig`` es la representación tipada e inmutable del ``config.json``
versionado en la raíz del repositorio. Las secciones son dataclasses
``frozen`` con validación en ``__post_init__``; cualquier dato inválido se
reporta como ``SchemaError``.

La forma en Python del ``stream`` es anidada 1:1 con ``config.json``
(``CaptureSettings``, ``PublisherSettings``, ``InferenceSettings``,
``StorageSettings`` y ``ReconnectSettings``), sin campos planos.
"""

from __future__ import annotations

import types
import typing
from dataclasses import (
    MISSING,
    asdict,
    dataclass,
    field,
    fields,
    replace,
)
from typing import Any, get_type_hints

__all__ = [
    "ApiSettings",
    "AppConfig",
    "CaptureSettings",
    "DeviceSettings",
    "InferenceSettings",
    "ModelEntry",
    "ModelsSettings",
    "PathsSettings",
    "PublisherSettings",
    "ReconnectSettings",
    "SchemaError",
    "StorageSettings",
    "StreamSettings",
]


class SchemaError(Exception):
    """Error de validación de la configuración."""


# ---------------------------------------------------------------------------
# Sección stream (anidada 1:1 con config.json)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptureSettings:
    """Parámetros de captura de video (``stream.capture``)."""

    source: str = "0"
    width: int = 1280
    height: int = 720
    fps: int = 30
    loop_video: bool = True
    buffer_size: int = 1
    stable_frames: int = 30
    read_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise SchemaError("width y height deben ser mayores que cero")
        if self.fps not in (20, 30):
            raise SchemaError("fps debe ser 20 o 30")
        if self.buffer_size < 1:
            raise SchemaError("buffer_size debe ser mayor que cero")
        if self.stable_frames < 1:
            raise SchemaError("stable_frames debe ser mayor que cero")
        if self.read_timeout_seconds <= 0:
            raise SchemaError("read_timeout_seconds debe ser mayor que cero")


@dataclass(frozen=True)
class PublisherSettings:
    """Parámetros de publicación RTSP (``stream.publisher``)."""

    output_url: str = "rtsp://smartcheck.duckdns.org:8554/entrada"
    bitrate: str = "2M"
    ffmpeg_executable: str = "ffmpeg"
    encoder: str = "libx264"
    pixel_format: str = "yuv420p"
    gop_seconds: int = 2
    b_frames: int = 0
    queue_size: int = 2
    write_timeout: float = 0.5
    stable_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not self.output_url:
            raise SchemaError("output_url no puede estar vacío")
        if self.pixel_format != "yuv420p":
            raise SchemaError(
                "pixel_format debe ser yuv420p para compatibilidad WebRTC"
            )
        if self.gop_seconds not in (1, 2):
            raise SchemaError("gop_seconds debe ser 1 o 2")
        if self.b_frames < 0:
            raise SchemaError("b_frames no puede ser negativo")
        if self.queue_size < 1:
            raise SchemaError("queue_size debe ser mayor que cero")
        if self.write_timeout <= 0:
            raise SchemaError("write_timeout debe ser mayor que cero")
        if self.stable_seconds < 0:
            raise SchemaError("stable_seconds no puede ser negativo")


@dataclass(frozen=True)
class InferenceSettings:
    """Parámetros de inferencia (``stream.inference``)."""

    enabled: bool = False
    require_hailo: bool = False
    model_path: str | None = None
    labels_path: str | None = None
    confidence_threshold: float = 0.6

    def __post_init__(self) -> None:
        if self.require_hailo and not self.enabled:
            raise SchemaError("require_hailo requiere enabled=True")


@dataclass(frozen=True)
class StorageSettings:
    """Persistencia de detecciones (``stream.storage``)."""

    path: str = "streaming/data/detections.jsonl"
    queue_size: int = 128
    max_bytes: int = 10485760
    max_files: int = 5
    persist_no_detection_every: int = 10

    def __post_init__(self) -> None:
        if self.queue_size < 1:
            raise SchemaError("queue_size debe ser mayor que cero")
        if self.max_bytes < 1:
            raise SchemaError("max_bytes debe ser mayor que cero")
        if self.max_files < 1:
            raise SchemaError("max_files debe ser mayor que cero")
        if self.persist_no_detection_every < 0:
            raise SchemaError("persist_no_detection_every no puede ser negativo")


@dataclass(frozen=True)
class ReconnectSettings:
    """Backoff de reconexión (``stream.reconnect``)."""

    initial_seconds: float = 1.0
    max_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.initial_seconds < 0:
            raise SchemaError("initial_seconds no puede ser negativo")
        if self.max_seconds < self.initial_seconds:
            raise SchemaError("max_seconds debe ser mayor o igual a initial_seconds")


@dataclass(frozen=True)
class StreamSettings:
    """Bloque ``stream`` anidado, con un dataclass por subsección."""

    capture: CaptureSettings = field(default_factory=CaptureSettings)
    publisher: PublisherSettings = field(default_factory=PublisherSettings)
    inference: InferenceSettings = field(default_factory=InferenceSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    reconnect: ReconnectSettings = field(default_factory=ReconnectSettings)


# ---------------------------------------------------------------------------
# Sección device
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceSettings:
    """Configuración del dispositivo que NO es secreta.

    ``identity_dir`` no vive aquí: es un secreto y se resuelve desde ``.env``
    en ``smartcheck_config.secrets``.
    """

    api_base_url: str = ""
    auth_audience: str = ""
    horno_id: str = ""
    producto_id: str = ""
    ping_interval_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.ping_interval_seconds <= 0:
            raise SchemaError("ping_interval_seconds debe ser mayor que cero")


# ---------------------------------------------------------------------------
# Sección models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelEntry:
    """Entrada del catálogo de modelos seleccionables."""

    label: str
    model_path: str
    names_path: str
    model_id: str


def _default_catalog() -> tuple[ModelEntry, ...]:
    """Catálogo por defecto, en el mismo orden que ``frontend.config``."""
    return (
        ModelEntry(
            "YOLOv11 Original (COCO)",
            "ai_training/models/yolo11n.onnx",
            "ai_training/models/class.names",
            "yolo11n-coco",
        ),
        ModelEntry(
            "YOLOv11 Tostadas V1 (Custom)",
            "ai_training/models/tostadas_v1.onnx",
            "ai_training/models/tostadas_v1.names",
            "tostadas-v1",
        ),
        ModelEntry(
            "YOLOv11 Tostadas V2 (Custom)",
            "ai_training/models/tostadas_v2.onnx",
            "ai_training/models/tostadas_v2.names",
            "tostadas-v2",
        ),
        ModelEntry(
            "YOLOv8s NPU (Hailo-8L COCO)",
            "/usr/share/hailo-models/yolov8s_h8l.hef",
            "ai_training/models/class.names",
            "yolov8s-hailo",
        ),
    )


@dataclass(frozen=True)
class ModelsSettings:
    """Catálogo de modelos y selección por defecto."""

    default_model_id: str = "yolo11n-coco"
    npu_model_id: str = "yolov8s-hailo"
    catalog: tuple[ModelEntry, ...] = field(default_factory=_default_catalog)

    def model_by_id(self, model_id: str) -> ModelEntry:
        """Devuelve la entrada del catálogo con ese ``model_id``.

        Raises:
            SchemaError: si el identificador no existe en el catálogo.
        """
        for entry in self.catalog:
            if entry.model_id == model_id:
                return entry
        raise SchemaError(f"model_id no encontrado en el catálogo: {model_id!r}")


# ---------------------------------------------------------------------------
# Otras secciones
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathsSettings:
    """Directorios de datos relativos a la raíz del repositorio."""

    videos_dir: str = "multimedia/videos"
    models_dir: str = "ai_training/models"
    data_dir: str = "streaming/data"


@dataclass(frozen=True)
class ApiSettings:
    """Configuración del backend HTTP local."""

    host: str = "127.0.0.1"
    port: int = 8000
    lote_endpoint: str = "http://localhost:8000/api/lotes/finalizar"

    def __post_init__(self) -> None:
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise SchemaError("port debe ser un entero")
        if not 1 <= self.port <= 65535:
            raise SchemaError("port debe estar entre 1 y 65535")


# ---------------------------------------------------------------------------
# Utilidades de parseo
# ---------------------------------------------------------------------------


def _type_names(expected: tuple[type, ...]) -> str:
    names: list[str] = []
    for item in expected:
        names.append("null" if item is type(None) else item.__name__)
    return " | ".join(names)


def _accepted_types(hint: Any) -> tuple[type, ...]:
    """Deriva los tipos aceptados de un type hint (resuelve ``X | None``)."""
    origin = typing.get_origin(hint)
    if origin is typing.Union or isinstance(hint, types.UnionType):
        accepted: list[type] = []
        for arg in typing.get_args(hint):
            accepted.extend(_accepted_types(arg))
        return tuple(accepted)
    if hint is None or hint is type(None):
        return (type(None),)
    return (hint,)


def _coerce(
    value: object, expected: tuple[type, ...], dotted: str, source: str
) -> Any:
    """Valida ``value`` contra ``expected`` o lanza ``SchemaError``."""
    if value is None:
        if type(None) in expected:
            return None
        raise SchemaError(
            f"Tipo inválido para '{dotted}' en {source}: "
            f"se esperaba {_type_names(expected)}, se recibió null"
        )
    if isinstance(value, bool) and bool not in expected:
        raise SchemaError(
            f"Tipo inválido para '{dotted}' en {source}: "
            f"se esperaba {_type_names(expected)}, se recibió bool"
        )
    if isinstance(value, int) and float in expected:
        return float(value)
    if not isinstance(value, expected):
        raise SchemaError(
            f"Tipo inválido para '{dotted}' en {source}: "
            f"se esperaba {_type_names(expected)}, "
            f"se recibió {type(value).__name__}"
        )
    return value


def _section(
    data: dict, key: str, source: str, dotted: str | None = None
) -> dict:
    """Devuelve la subsección ``key`` o ``{}`` si está ausente/null."""
    label = dotted or key
    if key not in data or data[key] is None:
        return {}
    value = data[key]
    if not isinstance(value, dict):
        raise SchemaError(
            f"La sección '{label}' en {source} debe ser un objeto JSON"
        )
    return value


def _build_section(
    cls: type,
    doc: dict,
    source: str,
    prefix: str,
    *,
    skip: frozenset[str] = frozenset(),
) -> Any:
    """Construye ``cls`` desde ``doc`` usando sus campos y type hints.

    Cada campo ausente usa su default (o ``default_factory``); las claves
    desconocidas se ignoran. Los tipos se validan con la clave punteada y el
    ``source``.
    """
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for item in fields(cls):
        if item.name in skip:
            continue
        dotted = f"{prefix}.{item.name}"
        if item.name in doc:
            kwargs[item.name] = _coerce(
                doc[item.name],
                _accepted_types(hints[item.name]),
                dotted,
                source,
            )
        elif item.default is not MISSING:
            kwargs[item.name] = item.default
        elif item.default_factory is not MISSING:
            kwargs[item.name] = item.default_factory()
        else:
            raise SchemaError(
                f"Falta la clave obligatoria '{dotted}' en {source}"
            )
    return cls(**kwargs)


def _parse_stream(stream_doc: dict, source: str) -> StreamSettings:
    """Construye ``StreamSettings`` desde la forma anidada de ``config.json``.

    No hay tolerancia a la antigua forma plana: las claves planas dentro de
    ``stream`` se ignoran.
    """
    capture = _build_section(
        CaptureSettings,
        _section(stream_doc, "capture", source, "stream.capture"),
        source,
        "stream.capture",
    )
    publisher = _build_section(
        PublisherSettings,
        _section(stream_doc, "publisher", source, "stream.publisher"),
        source,
        "stream.publisher",
    )
    inference = _build_section(
        InferenceSettings,
        _section(stream_doc, "inference", source, "stream.inference"),
        source,
        "stream.inference",
    )
    storage = _build_section(
        StorageSettings,
        _section(stream_doc, "storage", source, "stream.storage"),
        source,
        "stream.storage",
    )
    reconnect = _build_section(
        ReconnectSettings,
        _section(stream_doc, "reconnect", source, "stream.reconnect"),
        source,
        "stream.reconnect",
    )
    return StreamSettings(
        capture=capture,
        publisher=publisher,
        inference=inference,
        storage=storage,
        reconnect=reconnect,
    )


def _parse_catalog(raw: object, source: str) -> tuple[ModelEntry, ...]:
    if raw is None:
        return _default_catalog()
    if not isinstance(raw, list):
        raise SchemaError(
            f"Tipo inválido para 'models.catalog' en {source}: "
            f"se esperaba list, se recibió {type(raw).__name__}"
        )
    entries: list[ModelEntry] = []
    for index, item in enumerate(raw):
        prefix = f"models.catalog[{index}]"
        if not isinstance(item, dict):
            raise SchemaError(
                f"El elemento '{prefix}' en {source} debe ser un objeto JSON"
            )
        entries.append(
            ModelEntry(
                label=_require(item, "label", (str,), f"{prefix}.label", source),
                model_path=_require(
                    item, "model_path", (str,), f"{prefix}.model_path", source
                ),
                names_path=_require(
                    item, "names_path", (str,), f"{prefix}.names_path", source
                ),
                model_id=_require(
                    item, "model_id", (str,), f"{prefix}.model_id", source
                ),
            )
        )
    return tuple(entries)


def _require(
    section: dict,
    key: str,
    expected: tuple[type, ...],
    dotted: str,
    source: str,
) -> Any:
    if key not in section:
        raise SchemaError(f"Falta la clave obligatoria '{dotted}' en {source}")
    return _coerce(section[key], expected, dotted, source)


# ---------------------------------------------------------------------------
# Configuración raíz
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppConfig:
    """Documento completo de ``config.json`` (versión 1)."""

    schema_version: int = 1
    revision: int = 1
    stream: StreamSettings = field(default_factory=StreamSettings)
    device: DeviceSettings = field(default_factory=DeviceSettings)
    models: ModelsSettings = field(default_factory=ModelsSettings)
    paths: PathsSettings = field(default_factory=PathsSettings)
    api: ApiSettings = field(default_factory=ApiSettings)

    def to_dict(self) -> dict:
        """Serializa a la forma JSON exacta y con orden de claves estable.

        Se apoya en ``dataclasses.asdict`` (el orden de campos ya coincide con
        ``config.json``) y solo normaliza ``models.catalog`` a una lista de
        dicts con el orden de claves ``model_id, label, model_path,
        names_path``.
        """
        data = asdict(self)
        data["models"]["catalog"] = [
            {
                "model_id": entry.model_id,
                "label": entry.label,
                "model_path": entry.model_path,
                "names_path": entry.names_path,
            }
            for entry in self.models.catalog
        ]
        return data

    @classmethod
    def from_dict(cls, data: dict, *, source: str = "<memory>") -> "AppConfig":
        """Construye un ``AppConfig`` desde un documento v1 parcial.

        Cada sección ausente usa sus defaults; las claves desconocidas se
        ignoran silenciosamente para mantener compatibilidad futura. ``stream``
        solo acepta su forma anidada (1:1 con ``config.json``).

        Raises:
            SchemaError: si un tipo es incorrecto, indicando la clave punteada
                y el ``source``.
        """
        if not isinstance(data, dict):
            raise SchemaError(
                f"La raíz de la configuración en {source} debe ser un objeto JSON"
            )

        schema_version = 1
        if "schema_version" in data:
            schema_version = _coerce(
                data["schema_version"], (int,), "schema_version", source
            )
        revision = 1
        if "revision" in data:
            revision = _coerce(data["revision"], (int,), "revision", source)

        stream = _parse_stream(
            _section(data, "stream", source, "stream"), source
        )
        device = _build_section(
            DeviceSettings,
            _section(data, "device", source, "device"),
            source,
            "device",
        )
        paths = _build_section(
            PathsSettings,
            _section(data, "paths", source, "paths"),
            source,
            "paths",
        )
        api = _build_section(
            ApiSettings,
            _section(data, "api", source, "api"),
            source,
            "api",
        )
        models_doc = _section(data, "models", source, "models")
        models = _build_section(
            ModelsSettings,
            models_doc,
            source,
            "models",
            skip=frozenset({"catalog"}),
        )
        models = replace(
            models, catalog=_parse_catalog(models_doc.get("catalog"), source)
        )

        return cls(
            schema_version=schema_version,
            revision=revision,
            stream=stream,
            device=device,
            models=models,
            paths=paths,
            api=api,
        )
