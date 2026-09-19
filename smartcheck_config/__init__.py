"""Configuración global unificada de Smart-Check.

Este paquete es la fuente única del ``config.json`` versionado en la raíz del
repositorio: define el esquema tipado, resuelve sus rutas de bootstrap, lo
carga y lo guarda de forma atómica con control de revisión, y expone los
secretos que viven en ``.env``.

No importa FastAPI, PySide6, cv2 ni ningún otro stack pesado: solo stdlib y
los helpers de ``device_enrollment``.
"""

from __future__ import annotations

from .loader import (
    ConfigError,
    LoadedConfig,
    RevisionConflictError,
    load_config,
    save_config,
)
from .paths import (
    ConfigPathError,
    resolve_config_path,
    resolve_env_file,
    resolve_project_path,
    resolve_project_root,
)
from .schema import (
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
from .secrets import Secrets, load_secrets
from device_enrollment import DEFAULT_IDENTITY_DIR

__version__ = "1.0.0"

__all__ = [
    "ApiSettings",
    "AppConfig",
    "CaptureSettings",
    "ConfigError",
    "ConfigPathError",
    "DEFAULT_IDENTITY_DIR",
    "DeviceSettings",
    "InferenceSettings",
    "LoadedConfig",
    "ModelEntry",
    "ModelsSettings",
    "PathsSettings",
    "PublisherSettings",
    "ReconnectSettings",
    "RevisionConflictError",
    "SchemaError",
    "Secrets",
    "StorageSettings",
    "StreamSettings",
    "load_config",
    "load_secrets",
    "resolve_config_path",
    "resolve_env_file",
    "resolve_project_path",
    "resolve_project_root",
    "save_config",
]
