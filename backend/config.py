"""Carga, resolución y validación de la configuración de la aplicación."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"


class ConfigError(Exception):
    """Error de configuración: archivo ausente, JSON inválido o valores incorrectos."""


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    source: str
    width: int
    height: int
    fps: int
    loop_video: bool
    buffer_size: int
    stable_frames: int
    read_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class PublisherConfig:
    output_url: str
    bitrate: str
    ffmpeg_executable: str
    encoder: str
    preset: str
    tune: str
    pixel_format: str
    rtsp_transport: str
    gop_seconds: int
    b_frames: int
    queue_size: int
    write_timeout: float
    stable_seconds: float


@dataclass(frozen=True, slots=True)
class InferenceConfig:
    image_size: int
    confidence_threshold: float
    nms_threshold: float


@dataclass(frozen=True, slots=True)
class PreviewConfig:
    fps: int


@dataclass(frozen=True, slots=True)
class StorageConfig:
    path: Path
    queue_size: int
    max_bytes: int
    max_files: int
    persist_no_detection_every: int


@dataclass(frozen=True, slots=True)
class ReconnectConfig:
    initial_seconds: float
    max_seconds: float


@dataclass(frozen=True, slots=True)
class CountingConfig:
    enabled: bool
    line_y: int


@dataclass(frozen=True, slots=True)
class StreamConfig:
    capture: CaptureConfig
    publisher: PublisherConfig
    inference: InferenceConfig
    preview: PreviewConfig
    storage: StorageConfig
    reconnect: ReconnectConfig
    counting: CountingConfig


@dataclass(frozen=True, slots=True)
class ModelEntry:
    model_id: str
    label: str
    model_path: Path
    names_path: Path
    # Umbrales por clase del modelo. Las claves se normalizan a minúscula porque
    # el detector las busca con label.lower(); lo que no figure acá usa el
    # umbral global de stream.inference.confidence_threshold.
    class_thresholds: Mapping[str, float]
    # Producto del catálogo del backend que este modelo detecta. None hasta que
    # se elija desde Configuración (selector poblado con GET /api/v1/productos).
    producto_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModelsConfig:
    default_model_id: str
    catalog: tuple[ModelEntry, ...]


@dataclass(frozen=True, slots=True)
class PathsConfig:
    root: Path
    videos_dir: Path
    models_dir: Path
    data_dir: Path


@dataclass(frozen=True, slots=True)
class ApiConfig:
    # base_url es sólo esquema + host + puerto (ej. "http://192.168.1.50:8080").
    # Las rutas de cada recurso van como endpoints nombrados en el config, que se
    # van agregando a medida que el backend las expone.
    base_url: str
    registration_requests_endpoint: str
    dispositivos_ping_endpoint: str
    dispositivos_nombre_endpoint: str
    productos_endpoint: str
    dispositivos_sector_endpoint: str
    lotes_endpoint: str


@dataclass(frozen=True, slots=True)
class DeviceConfig:
    # hostname lógico con el que la Raspberry se identifica al registrarse.
    hostname: str
    ping_interval_seconds: float
    registration_poll_interval_seconds: float


@dataclass(frozen=True, slots=True)
class LoteConfig:
    # Coordinación de lotes por sector contra el backend Go. Todos los tiempos en
    # segundos.
    habilitado: bool
    # Inactividad necesaria para cerrar el lote (salida local y sector servidor).
    cierre_sin_detecciones_segundos: float
    # Cadencia de drenado del buzón y envío de eventos en vivo.
    flush_segundos: float
    # Tope de eventos por POST (el contrato del backend acepta hasta 100).
    max_eventos_por_envio: int
    # Capacidad del buzón entre el lazo de inferencia y el LoteService.
    cola_eventos: int
    # Capacidad de la outbox de reintentos.
    max_pendientes: int
    # Cada cuánto se re-consulta el catálogo de productos y el sector.
    refresco_catalogo_segundos: float
    # Backoff exponencial ante errores reintentables.
    reintento_inicial_segundos: float
    reintento_maximo_segundos: float


@dataclass(frozen=True, slots=True)
class AppConfig:
    paths: PathsConfig
    stream: StreamConfig
    models: ModelsConfig
    api: ApiConfig
    device: DeviceConfig
    lote: LoteConfig


def resolve_path(value: str | Path, base: Path) -> Path:
    """Convierte una ruta del config en una ruta absoluta anclada a base.

    Si el valor ya es absoluto se respeta tal cual (p. ej. la ruta del
    modelo Hailo en /usr/share/...). Si es relativo se interpreta
    contra base (el ROOT del proyecto), nunca contra el CWD.
    """
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _read_json(path: Path) -> dict:
    """Lee y parsea el archivo de configuración en JSON.

    Convierte cualquier fallo de lectura o de parseo en un ConfigError
    con contexto, y exige que la raíz del archivo sea un objeto JSON.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"No se encontró el archivo de configuración: {path}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON inválido en {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(
            f"La raíz del config debe ser un objeto JSON, no {type(data).__name__}"
        )

    return data


def _get(section: dict[str, Any], key: str, expected: type, where: str) -> Any:
    """Extrae y valida un campo tipado de una sección del config."""
    if key not in section:
        raise ConfigError(f"Falta el campo '{where}.{key}'")

    value = section[key]

    # JSON no distingue 1 de 1.0; aceptamos int donde se espera float.
    if expected is float and isinstance(value, int) and not isinstance(value, bool):
        return float(value)

    # bool es subclase de int; sin esto, True pasaría como int válido.
    if expected is int and isinstance(value, bool):
        raise ConfigError(f"El campo '{where}.{key}' debe ser int, no bool")

    if not isinstance(value, expected):
        raise ConfigError(
            f"El campo '{where}.{key}' debe ser {expected.__name__}, "
            f"no {type(value).__name__}"
        )

    return value


def _parse_config(data: dict, root: Path) -> AppConfig:
    """Construye un AppConfig validado a partir del dict crudo del JSON."""

    # --- paths (primero: hace falta para resolver todo lo demás) ---
    paths_raw = _get(data, "paths", dict, "config")
    paths = PathsConfig(
        root=root,
        videos_dir=resolve_path(_get(paths_raw, "videos_dir", str, "paths"), root),
        models_dir=resolve_path(_get(paths_raw, "models_dir", str, "paths"), root),
        data_dir=resolve_path(_get(paths_raw, "data_dir", str, "paths"), root),
    )

    if not paths.models_dir.is_dir():
        raise ConfigError(
            f"El directorio de modelos no existe: {paths.models_dir} "
            f"(definido en 'paths.models_dir')"
        )

    stream_raw = _get(data, "stream", dict, "config")

    # --- stream.capture ---
    capture_raw = _get(stream_raw, "capture", dict, "stream")
    capture = CaptureConfig(
        source=_get(capture_raw, "source", str, "stream.capture"),
        width=_get(capture_raw, "width", int, "stream.capture"),
        height=_get(capture_raw, "height", int, "stream.capture"),
        fps=_get(capture_raw, "fps", int, "stream.capture"),
        loop_video=_get(capture_raw, "loop_video", bool, "stream.capture"),
        buffer_size=_get(capture_raw, "buffer_size", int, "stream.capture"),
        stable_frames=_get(capture_raw, "stable_frames", int, "stream.capture"),
        read_timeout_seconds=_get(capture_raw, "read_timeout_seconds", float, "stream.capture"),
    )
    if not capture.source:
        raise ConfigError("'stream.capture.source' no puede estar vacío")
    if capture.width <= 0 or capture.height <= 0:
        raise ConfigError("'stream.capture.width' y 'stream.capture.height' deben ser > 0")
    if capture.width % 2 or capture.height % 2:
        raise ConfigError(
            "'stream.capture.width' y 'stream.capture.height' deben ser pares: "
            "la conversión a I420 y H.264 yuv420p lo requieren"
        )
    if capture.fps <= 0:
        raise ConfigError("'stream.capture.fps' debe ser > 0")

    # --- stream.publisher ---
    publisher_raw = _get(stream_raw, "publisher", dict, "stream")
    publisher = PublisherConfig(
        output_url=_get(publisher_raw, "output_url", str, "stream.publisher"),
        bitrate=_get(publisher_raw, "bitrate", str, "stream.publisher"),
        ffmpeg_executable=_get(publisher_raw, "ffmpeg_executable", str, "stream.publisher"),
        encoder=_get(publisher_raw, "encoder", str, "stream.publisher"),
        preset=_get(publisher_raw, "preset", str, "stream.publisher"),
        tune=_get(publisher_raw, "tune", str, "stream.publisher"),
        pixel_format=_get(publisher_raw, "pixel_format", str, "stream.publisher"),
        rtsp_transport=_get(publisher_raw, "rtsp_transport", str, "stream.publisher"),
        gop_seconds=_get(publisher_raw, "gop_seconds", int, "stream.publisher"),
        b_frames=_get(publisher_raw, "b_frames", int, "stream.publisher"),
        queue_size=_get(publisher_raw, "queue_size", int, "stream.publisher"),
        write_timeout=_get(publisher_raw, "write_timeout", float, "stream.publisher"),
        stable_seconds=_get(publisher_raw, "stable_seconds", float, "stream.publisher"),
    )
    if publisher.rtsp_transport not in {"tcp", "udp"}:
        raise ConfigError(
            "'stream.publisher.rtsp_transport' debe ser 'tcp' o 'udp', "
            f"no {publisher.rtsp_transport!r}"
        )

    # --- stream.inference ---
    inference_raw = _get(stream_raw, "inference", dict, "stream")
    inference = InferenceConfig(
        image_size=_get(inference_raw, "image_size", int, "stream.inference"),
        confidence_threshold=_get(inference_raw, "confidence_threshold", float, "stream.inference"),
        nms_threshold=_get(inference_raw, "nms_threshold", float, "stream.inference"),
    )
    if inference.image_size <= 0:
        raise ConfigError("'stream.inference.image_size' debe ser > 0")
    if not 0.0 <= inference.confidence_threshold <= 1.0:
        raise ConfigError("'stream.inference.confidence_threshold' debe estar entre 0 y 1")
    if not 0.0 < inference.nms_threshold <= 1.0:
        raise ConfigError("'stream.inference.nms_threshold' debe estar en (0, 1]")

    # --- stream.preview ---
    preview_raw = _get(stream_raw, "preview", dict, "stream")
    preview = PreviewConfig(
        fps=_get(preview_raw, "fps", int, "stream.preview"),
    )
    if preview.fps <= 0:
        raise ConfigError("'stream.preview.fps' debe ser > 0")

    # --- stream.storage (path derivado de data_dir + file) ---
    storage_raw = _get(stream_raw, "storage", dict, "stream")
    filename = _get(storage_raw, "file", str, "stream.storage")
    if not filename:
        raise ConfigError("'stream.storage.file' no puede estar vacío")
    storage = StorageConfig(
        path=paths.data_dir / filename,
        queue_size=_get(storage_raw, "queue_size", int, "stream.storage"),
        max_bytes=_get(storage_raw, "max_bytes", int, "stream.storage"),
        max_files=_get(storage_raw, "max_files", int, "stream.storage"),
        persist_no_detection_every=_get(storage_raw, "persist_no_detection_every", int, "stream.storage"),
    )

    # --- stream.reconnect ---
    reconnect_raw = _get(stream_raw, "reconnect", dict, "stream")
    reconnect = ReconnectConfig(
        initial_seconds=_get(reconnect_raw, "initial_seconds", float, "stream.reconnect"),
        max_seconds=_get(reconnect_raw, "max_seconds", float, "stream.reconnect"),
    )
    if reconnect.initial_seconds <= 0 or reconnect.max_seconds < reconnect.initial_seconds:
        raise ConfigError(
            "'stream.reconnect': initial_seconds debe ser > 0 y max_seconds >= initial_seconds"
        )

    # --- stream.counting ---
    counting_raw = stream_raw.get("counting")
    if counting_raw is not None:
        if not isinstance(counting_raw, dict):
            raise ConfigError("'stream.counting' debe ser un objeto JSON")
        counting = CountingConfig(
            enabled=_get(counting_raw, "enabled", bool, "stream.counting"),
            line_y=_get(counting_raw, "line_y", int, "stream.counting"),
        )
    else:
        counting = CountingConfig(enabled=True, line_y=round(capture.height * 0.75))
    if counting.line_y <= 0 or counting.line_y >= capture.height:
        raise ConfigError(
            f"'stream.counting.line_y' ({counting.line_y}) debe estar dentro de la altura del frame (0, {capture.height})"
        )

    stream = StreamConfig(
        capture=capture,
        publisher=publisher,
        inference=inference,
        preview=preview,
        storage=storage,
        reconnect=reconnect,
        counting=counting,
    )

    # --- models.catalog ---
    models_raw = _get(data, "models", dict, "config")
    catalog_raw = _get(models_raw, "catalog", list, "models")
    if not catalog_raw:
        raise ConfigError("'models.catalog' no puede estar vacío")

    entries: list[ModelEntry] = []
    seen_ids: set[str] = set()
    for index, entry_raw in enumerate(catalog_raw):
        where = f"models.catalog[{index}]"
        if not isinstance(entry_raw, dict):
            raise ConfigError(f"'{where}' debe ser un objeto JSON")
        entry_id = _get(entry_raw, "model_id", str, where)
        if entry_id in seen_ids:
            raise ConfigError(f"'{where}.model_id' está duplicado: {entry_id}")
        seen_ids.add(entry_id)

        thresholds_raw = entry_raw.get("class_thresholds", {})
        if not isinstance(thresholds_raw, dict):
            raise ConfigError(f"'{where}.class_thresholds' debe ser un objeto JSON")
        thresholds: dict[str, float] = {}
        for clase, valor in thresholds_raw.items():
            if not isinstance(clase, str) or not clase.strip():
                raise ConfigError(f"'{where}.class_thresholds' tiene una clase vacía")
            if isinstance(valor, bool) or not isinstance(valor, (int, float)):
                raise ConfigError(
                    f"'{where}.class_thresholds[\"{clase}\"]' debe ser numérico"
                )
            if not 0.0 <= float(valor) <= 1.0:
                raise ConfigError(
                    f"'{where}.class_thresholds[\"{clase}\"]' debe estar entre 0 y 1"
                )
            thresholds[clase.strip().lower()] = float(valor)

        producto_id_raw = entry_raw.get("producto_id")
        if producto_id_raw is None:
            producto_id: str | None = None
        elif isinstance(producto_id_raw, str) and producto_id_raw.strip():
            producto_id = producto_id_raw.strip()
        else:
            raise ConfigError(
                f"'{where}.producto_id' debe ser un string no vacío o null"
            )

        entries.append(
            ModelEntry(
                model_id=entry_id,
                label=_get(entry_raw, "label", str, where),
                model_path=resolve_path(_get(entry_raw, "file", str, where), paths.models_dir),
                names_path=resolve_path(_get(entry_raw, "names_file", str, where), paths.models_dir),
                class_thresholds=MappingProxyType(thresholds),
                producto_id=producto_id,
            )
        )
    catalog = tuple(entries)

    # --- models (referencias) ---
    default_model_id = _get(models_raw, "default_model_id", str, "models")
    if default_model_id not in seen_ids:
        raise ConfigError(
            f"'models.default_model_id' referencia un modelo inexistente: {default_model_id}"
        )

    models = ModelsConfig(
        default_model_id=default_model_id,
        catalog=catalog,
    )

    # --- api (base_url sin path; los endpoints son rutas nombradas) ---
    api_raw = _get(data, "api", dict, "config")
    base_url = _get(api_raw, "base_url", str, "api")
    registration_requests_endpoint = _get(
        api_raw, "registration_requests_endpoint", str, "api"
    )
    dispositivos_ping_endpoint = _get(
        api_raw, "dispositivos_ping_endpoint", str, "api"
    )
    dispositivos_nombre_endpoint = _get(
        api_raw, "dispositivos_nombre_endpoint", str, "api"
    )
    productos_endpoint = _get(api_raw, "productos_endpoint", str, "api")
    dispositivos_sector_endpoint = _get(
        api_raw, "dispositivos_sector_endpoint", str, "api"
    )
    lotes_endpoint = _get(api_raw, "lotes_endpoint", str, "api")
    if not base_url.startswith(("http://", "https://")):
        raise ConfigError(
            "'api.base_url' debe empezar con 'http://' o 'https://', "
            f"no {base_url!r}"
        )
    if base_url.endswith("/"):
        raise ConfigError("'api.base_url' no debe terminar en '/'")
    if not registration_requests_endpoint.startswith("/"):
        raise ConfigError(
            "'api.registration_requests_endpoint' debe empezar con '/', "
            f"no {registration_requests_endpoint!r}"
        )
    if not dispositivos_ping_endpoint.startswith("/"):
        raise ConfigError(
            "'api.dispositivos_ping_endpoint' debe empezar con '/', "
            f"no {dispositivos_ping_endpoint!r}"
        )
    if not dispositivos_nombre_endpoint.startswith("/"):
        raise ConfigError(
            "'api.dispositivos_nombre_endpoint' debe empezar con '/', "
            f"no {dispositivos_nombre_endpoint!r}"
        )
    if not productos_endpoint.startswith("/"):
        raise ConfigError(
            "'api.productos_endpoint' debe empezar con '/', "
            f"no {productos_endpoint!r}"
        )
    if not dispositivos_sector_endpoint.startswith("/"):
        raise ConfigError(
            "'api.dispositivos_sector_endpoint' debe empezar con '/', "
            f"no {dispositivos_sector_endpoint!r}"
        )
    if not lotes_endpoint.startswith("/"):
        raise ConfigError(
            "'api.lotes_endpoint' debe empezar con '/', "
            f"no {lotes_endpoint!r}"
        )
    api = ApiConfig(
        base_url=base_url,
        registration_requests_endpoint=registration_requests_endpoint,
        dispositivos_ping_endpoint=dispositivos_ping_endpoint,
        dispositivos_nombre_endpoint=dispositivos_nombre_endpoint,
        productos_endpoint=productos_endpoint,
        dispositivos_sector_endpoint=dispositivos_sector_endpoint,
        lotes_endpoint=lotes_endpoint,
    )

    # --- device ---
    device_raw = _get(data, "device", dict, "config")
    hostname = _get(device_raw, "hostname", str, "device")
    if not hostname.strip():
        raise ConfigError("'device.hostname' no puede estar vacío")
    ping_interval_seconds = _get(device_raw, "ping_interval_seconds", float, "device")
    registration_poll_interval_seconds = _get(
        device_raw, "registration_poll_interval_seconds", float, "device"
    )
    if ping_interval_seconds <= 0:
        raise ConfigError("'device.ping_interval_seconds' debe ser > 0")
    if registration_poll_interval_seconds <= 0:
        raise ConfigError(
            "'device.registration_poll_interval_seconds' debe ser > 0"
        )
    device = DeviceConfig(
        hostname=hostname.strip(),
        ping_interval_seconds=ping_interval_seconds,
        registration_poll_interval_seconds=registration_poll_interval_seconds,
    )

    # --- lote ---
    lote_raw = _get(data, "lote", dict, "config")
    lote = LoteConfig(
        habilitado=_get(lote_raw, "habilitado", bool, "lote"),
        cierre_sin_detecciones_segundos=_get(
            lote_raw, "cierre_sin_detecciones_segundos", float, "lote"
        ),
        flush_segundos=_get(lote_raw, "flush_segundos", float, "lote"),
        max_eventos_por_envio=_get(
            lote_raw, "max_eventos_por_envio", int, "lote"
        ),
        cola_eventos=_get(lote_raw, "cola_eventos", int, "lote"),
        max_pendientes=_get(lote_raw, "max_pendientes", int, "lote"),
        refresco_catalogo_segundos=_get(
            lote_raw, "refresco_catalogo_segundos", float, "lote"
        ),
        reintento_inicial_segundos=_get(
            lote_raw, "reintento_inicial_segundos", float, "lote"
        ),
        reintento_maximo_segundos=_get(
            lote_raw, "reintento_maximo_segundos", float, "lote"
        ),
    )
    if lote.cierre_sin_detecciones_segundos <= 0:
        raise ConfigError("'lote.cierre_sin_detecciones_segundos' debe ser > 0")
    if lote.flush_segundos <= 0:
        raise ConfigError("'lote.flush_segundos' debe ser > 0")
    if lote.flush_segundos >= lote.cierre_sin_detecciones_segundos:
        raise ConfigError(
            "'lote.flush_segundos' debe ser menor que "
            "'lote.cierre_sin_detecciones_segundos'"
        )
    if not 1 <= lote.max_eventos_por_envio <= 100:
        raise ConfigError("'lote.max_eventos_por_envio' debe estar entre 1 y 100")
    if lote.cola_eventos < 1:
        raise ConfigError("'lote.cola_eventos' debe ser >= 1")
    if lote.max_pendientes < lote.max_eventos_por_envio:
        raise ConfigError(
            "'lote.max_pendientes' debe ser >= 'lote.max_eventos_por_envio'"
        )
    if lote.refresco_catalogo_segundos <= 0:
        raise ConfigError("'lote.refresco_catalogo_segundos' debe ser > 0")
    if lote.reintento_inicial_segundos <= 0:
        raise ConfigError("'lote.reintento_inicial_segundos' debe ser > 0")
    if lote.reintento_maximo_segundos < lote.reintento_inicial_segundos:
        raise ConfigError(
            "'lote.reintento_maximo_segundos' debe ser >= "
            "'lote.reintento_inicial_segundos'"
        )

    return AppConfig(
        paths=paths,
        stream=stream,
        models=models,
        api=api,
        device=device,
        lote=lote,
    )


def load_config(path: Path | None = None) -> AppConfig:
    """Carga la configuración de la aplicación desde disco.

    Es la función pública del módulo: lee el archivo, lo parsea, lo valida
    y devuelve un AppConfig con rutas absolutas ancladas al ROOT del proyecto.
    """
    config_path = path if path is not None else CONFIG_PATH
    data = _read_json(config_path)
    return _parse_config(data, ROOT)


def leer_config_cruda(path: Path | None = None) -> dict:
    """Devuelve el dict JSON crudo del archivo de configuración.

    Es la vista editable que consume la UI: el contenido tal como está en
    disco, sin validar ni transformar (no convierte rutas ni aplica valores
    por defecto). Si path es None se usa el CONFIG_PATH del proyecto.
    """
    config_path = path if path is not None else CONFIG_PATH
    return _read_json(config_path)


def guardar_config(data: dict, path: Path | None = None) -> AppConfig:
    """Valida y guarda la configuración de la aplicación en disco.

    Primero valida el dict con _parse_config; si es inválido levanta
    ConfigError y no toca el disco (ni siquiera crea el temporal). Recién
    cuando el contenido es válido escribe de forma atómica: vuelca a un
    archivo temporal y lo reemplaza con os.replace, de modo que el config
    nunca queda a medias. Devuelve el AppConfig ya validado.
    """
    config_path = path if path is not None else CONFIG_PATH
    config = _parse_config(data, ROOT)

    temporal = config_path.with_name(config_path.name + ".tmp")
    try:
        temporal.write_text(
            json.dumps(data, indent=4, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporal, config_path)
    except OSError as exc:
        temporal.unlink(missing_ok=True)
        raise ConfigError(
            f"No se pudo escribir la configuración en {config_path}: {exc}"
        ) from exc

    return config
