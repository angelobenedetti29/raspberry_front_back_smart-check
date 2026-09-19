"""Secretos de la configuración global, resueltos desde ``.env``.

Solo dos valores son secretos y viven fuera de ``config.json``:
``DEVICE_ENROLLMENT_CODE`` y ``DEVICE_IDENTITY_DIR``. El entorno de proceso
tiene prioridad sobre el archivo ``.env``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from device_enrollment import DEFAULT_IDENTITY_DIR
from device_enrollment.envfile import load_env_values

from .paths import resolve_env_file

__all__ = ["Secrets", "load_secrets"]


@dataclass(frozen=True)
class Secrets:
    """Secretos del dispositivo cargados desde el entorno o el archivo env."""

    enrollment_code: str = ""
    identity_dir: str = DEFAULT_IDENTITY_DIR


def load_secrets(
    env_file: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
) -> Secrets:
    """Carga los secretos del dispositivo.

    La precedencia es entorno de proceso > archivo env > defaults. Si ``env``
    es ``None`` se usa ``os.environ``; si se inyecta un mapeo, este reemplaza
    al entorno del proceso (útil en tests). Un ``identity_dir`` vacío cae al
    valor por defecto.
    """
    file_values: dict[str, str] = {}
    path = resolve_env_file(env_file)
    if path is not None:
        try:
            file_values = load_env_values(path)
        except OSError:
            file_values = {}

    environ = os.environ if env is None else env

    def _get(name: str, default: str) -> str:
        value = environ.get(name)
        if value is None or value == "":
            value = file_values.get(name)
        if value is None or value == "":
            return default
        return value

    identity_dir = _get("DEVICE_IDENTITY_DIR", DEFAULT_IDENTITY_DIR)
    if not identity_dir:
        identity_dir = DEFAULT_IDENTITY_DIR

    return Secrets(
        enrollment_code=_get("DEVICE_ENROLLMENT_CODE", ""),
        identity_dir=identity_dir,
    )
