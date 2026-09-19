"""Resolución de rutas de bootstrap de la configuración global.

Solo las *rutas* se controlan con variables de entorno
(``SMARTCHECK_ROOT``, ``SMARTCHECK_CONFIG`` y ``SMARTCHECK_ENV_FILE``). Los
valores de configuración viven exclusivamente en ``config.json``; este módulo
no lee ninguna otra variable de entorno.
"""

from __future__ import annotations

import os
from pathlib import Path

# Nombre del marcador que identifica la raíz del repositorio.
_ROOT_MARKER = "pyproject.toml"


class ConfigPathError(Exception):
    """Error al resolver una ruta de bootstrap de la configuración."""


def resolve_project_root() -> Path:
    """Devuelve la raíz del proyecto.

    Si ``SMARTCHECK_ROOT`` está definida se usa ese directorio (resuelto y
    validado como directorio real). En caso contrario se sube desde la
    ubicación de este archivo buscando el ``pyproject.toml`` del repositorio.

    Raises:
        ConfigPathError: si la variable apunta a algo que no es un directorio
            o si no se encuentra el marcador subiendo desde ``__file__``.
    """
    env_root = os.getenv("SMARTCHECK_ROOT")
    if env_root:
        root = Path(env_root).expanduser().resolve()
        if not root.is_dir():
            raise ConfigPathError(
                f"SMARTCHECK_ROOT no apunta a un directorio válido: {env_root!r}"
            )
        return root

    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / _ROOT_MARKER).is_file():
            return candidate
    raise ConfigPathError(
        f"No se encontró {_ROOT_MARKER!r} subiendo desde {start}"
    )


def resolve_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Resuelve la ruta de ``config.json``.

    Precedencia: ``explicit`` > ``SMARTCHECK_CONFIG`` > ``<root>/config.json``.
    """
    if explicit is not None:
        return Path(explicit).expanduser().resolve()

    env_config = os.getenv("SMARTCHECK_CONFIG")
    if env_config:
        return Path(env_config).expanduser().resolve()

    return resolve_project_root() / "config.json"


def resolve_project_path(path: str) -> str:
    """Convierte una ruta relativa al repositorio en una ruta utilizable.

    Las rutas vacías o absolutas se devuelven tal cual; el resto se une a la
    raíz del proyecto.
    """
    if not path:
        return path
    candidate = Path(path)
    if candidate.is_absolute():
        return path
    return str(resolve_project_root() / candidate)


def resolve_env_file(
    explicit: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Resuelve el archivo ``.env`` de secretos.

    Precedencia: ``explicit`` (aunque no exista) > ``SMARTCHECK_ENV_FILE`` >
    ``<root>/.env`` si existe > ``None``.
    """
    if explicit is not None:
        return Path(explicit).expanduser().resolve()

    env_file = os.getenv("SMARTCHECK_ENV_FILE")
    if env_file:
        return Path(env_file).expanduser().resolve()

    default = resolve_project_root() / ".env"
    if default.is_file():
        return default
    return None
