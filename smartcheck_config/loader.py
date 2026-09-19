"""Carga y guardado de ``config.json`` con guardado atómico y revisión.

El guardado toma un lock exclusivo (``fcntl.flock``) sobre ``<config>.lock``
que cubre la lectura de la revisión en disco y la escritura, de modo que dos
procesos concurrentes no pueden pisarse ni reutilizar la misma revisión.
"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from device_enrollment.atomic import write_atomic

from .paths import resolve_config_path
from .schema import AppConfig

__all__ = [
    "ConfigError",
    "LoadedConfig",
    "RevisionConflictError",
    "load_config",
    "save_config",
]


class ConfigError(Exception):
    """Error al cargar o guardar la configuración."""


class RevisionConflictError(ConfigError):
    """La revisión esperada no coincide con la revisión en disco."""


@dataclass(frozen=True)
class LoadedConfig:
    """Configuración cargada junto con la ruta de la que provino."""

    config: AppConfig
    path: Path


def load_config(
    config_path: str | os.PathLike[str] | None = None, *, strict: bool = False
) -> LoadedConfig:
    """Carga ``config.json``.

    Si el archivo no existe y ``strict`` es ``False`` se devuelven los valores
    por defecto con la ruta ya resuelta; con ``strict=True`` se lanza
    ``ConfigError``. Un JSON inválido siempre lanza ``ConfigError``.

    Raises:
        ConfigError: archivo ausente en modo estricto, JSON inválido o error
            de lectura.
    """
    path = resolve_config_path(config_path)
    if not path.exists():
        if strict:
            raise ConfigError(f"No existe el archivo de configuración: {path}")
        # Sin archivo la revisión base es 0: ``_read_revision`` también devuelve
        # 0 para un archivo ausente, así que ``save_config`` con
        # ``expected_revision=0`` (la revisión recién cargada) funciona y escribe
        # la revisión 1. Ver ``test_save_config_tras_archivo_ausente``.
        return LoadedConfig(config=replace(AppConfig(), revision=0), path=path)

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"No se pudo leer {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON inválido en {path}: {exc}") from exc

    config = AppConfig.from_dict(data, source=str(path))
    return LoadedConfig(config=config, path=path)


def _read_revision(path: Path) -> int:
    """Lee la revisión en disco; 0 si el archivo no existe."""
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"No se pudo leer la revisión de {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(
            f"La raíz de la configuración en {path} debe ser un objeto JSON"
        )
    revision = data.get("revision", 1)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ConfigError(f"La revisión en {path} debe ser un entero")
    return revision


def save_config(
    config_or_dict: AppConfig | dict,
    *,
    config_path: str | os.PathLike[str] | None = None,
    expected_revision: int | None = None,
) -> AppConfig:
    """Guarda ``config.json`` de forma atómica e incrementa la revisión.

    Toma un lock exclusivo sobre ``<config>.lock`` que cubre la lectura de la
    revisión actual y la escritura final. Si ``expected_revision`` no es
    ``None`` y no coincide con la revisión en disco se lanza
    ``RevisionConflictError``.

    Acepta un ``AppConfig`` ya construido o un ``dict`` (que se valida con el
    esquema). La revisión escrita es siempre ``revision_en_disco + 1`` (o ``1``
    si el archivo es nuevo).

    Returns:
        El ``AppConfig`` efectivamente guardado, con la revisión actualizada.

    Raises:
        ConfigError: diccionario inválido o imposible de leer el estado actual.
        RevisionConflictError: conflicto de revisión.
    """
    path = resolve_config_path(config_path)
    lock_path = path.with_name(path.name + ".lock")

    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            exists = path.exists()
            current_revision = _read_revision(path)

            if (
                expected_revision is not None
                and current_revision != expected_revision
            ):
                raise RevisionConflictError(
                    "Revisión desactualizada para "
                    f"{path}: se esperaba {expected_revision}, "
                    f"el disco tiene {current_revision}"
                )

            config = _as_config(config_or_dict, path)
            new_revision = current_revision + 1 if exists else 1
            saved = replace(config, revision=new_revision)

            payload = json.dumps(
                saved.to_dict(), indent=2, ensure_ascii=False
            ) + "\n"
            write_atomic(path, payload.encode("utf-8"), mode=0o644)
            return saved
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _as_config(config_or_dict: AppConfig | dict, path: Path) -> AppConfig:
    """Normaliza la entrada de ``save_config`` a un ``AppConfig`` validado."""
    if isinstance(config_or_dict, AppConfig):
        return config_or_dict
    if isinstance(config_or_dict, dict):
        return AppConfig.from_dict(config_or_dict, source=str(path))
    raise ConfigError(
        "save_config espera un AppConfig o un dict, "
        f"se recibió {type(config_or_dict).__name__}"
    )
