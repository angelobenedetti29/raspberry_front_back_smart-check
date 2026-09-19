"""Store de ``config.json`` con recarga por mtime y conservación del último valor bueno.

El store es la capa de cache entre ``smartcheck_config`` y el runtime del
backend:

- ``get()`` es el camino caliente: hace un ``os.stat`` y solo relee el archivo
  si cambió ``(st_mtime_ns, st_size)``.
- ``refresh(force=True)`` relee siempre (lo usa ``POST /api/config/reload``).
- Si el JSON deja de ser válido se loguea, se guarda ``last_error`` y se
  devuelve el último ``Settings`` bueno. El error solo se propaga cuando nunca
  hubo un valor bueno (primer arranque).
- Los snapshots son inmutables (``AppConfig``/``Secrets``/``Settings`` son
  dataclasses ``frozen``): nunca se muta el objeto anterior, se reemplaza.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from smartcheck_config import (
    ConfigError,
    LoadedConfig,
    SchemaError,
    Secrets,
)
from smartcheck_config import load_config as _load_config
from smartcheck_config import load_secrets as _load_secrets
from smartcheck_config import resolve_config_path, resolve_env_file

from backend.app.config import Settings, settings_from_config

logger = logging.getLogger(__name__)

# Intentos de lectura antes de rendirse ante un reemplazo concurrente continuo.
_MAX_READ_ATTEMPTS = 5


class ConfigStore:
    """Cache thread-safe de ``config.json`` + ``.env`` para el runtime."""

    def __init__(
        self,
        config_path: str | os.PathLike[str] | None = None,
        env_file: str | os.PathLike[str] | None = None,
    ):
        self._path = resolve_config_path(config_path)
        self._env_file = env_file
        self._lock = threading.Lock()

        self._loaded: LoadedConfig | None = None
        self._secrets = Secrets()
        self._settings: Settings | None = None
        self._stat: tuple[int, int] | None = None
        # Stamp en el que ya se intentó leer y falló: evita releer+parsear+
        # loguear en cada request mientras el archivo no cambie.
        self._error_stat: tuple[int, int] | None = None
        self._last_error: str | None = None
        self._last_ok = False
        self._changed = False

    # -- introspección ---------------------------------------------------------
    @property
    def path(self) -> Path:
        """Ruta resuelta del ``config.json`` observado."""
        return self._path

    @property
    def revision(self) -> int:
        """Revisión del último ``config.json`` bueno (0 si nunca se cargó)."""
        if self._loaded is None:
            return 0
        return self._loaded.config.revision

    @property
    def last_error(self) -> str | None:
        """Mensaje del último fallo de recarga, o ``None`` si el último fue bueno."""
        return self._last_error

    @property
    def last_ok(self) -> bool:
        """True si la última lectura del archivo fue válida."""
        return self._last_ok

    @property
    def changed(self) -> bool:
        """True si la última recarga cambió la configuración efectiva."""
        return self._changed

    @property
    def secrets(self) -> Secrets:
        """Últimos secretos cargados desde ``.env``."""
        return self._secrets

    def settings(self) -> Settings:
        """Settings del último valor bueno; intenta cargar si aún no hay."""
        if self._settings is None:
            return self.refresh()
        return self._settings

    def snapshot(self) -> LoadedConfig:
        """``LoadedConfig`` del último valor bueno (cargando si hace falta)."""
        if self._loaded is None:
            self.refresh()
        assert self._loaded is not None
        return self._loaded

    # -- carga -----------------------------------------------------------------
    def load_config(self) -> LoadedConfig:
        """Lee ``config.json`` desde disco sin tocar la cache."""
        return _load_config(self._path)

    def load_secrets(self) -> Secrets:
        """Resuelve los secretos de ``.env`` (o del entorno) sin tocar la cache."""
        return _load_secrets(self._env_file)

    # -- camino caliente y recarga --------------------------------------------
    def get(self) -> Settings:
        """Devuelve el snapshot actual, releyendo solo si el archivo cambió."""
        with self._lock:
            return self._refresh_locked(force=False)

    def refresh(self, force: bool = False) -> Settings:
        """Recarga ``config.json``.

        Con ``force=False`` no relee si ``(mtime, size)`` no cambió. Devuelve
        siempre el último valor bueno; si nunca hubo uno y el archivo es
        inválido, propaga ``ConfigError``.
        """
        return self.refresh_with_changed(force=force)[0]

    def refresh_with_changed(self, force: bool = False) -> tuple[Settings, bool]:
        """Igual que ``refresh`` pero devuelve ``(settings, changed)`` de forma atómica.

        El flag ``changed`` se lee bajo el mismo lock que la recarga, de modo que
        dos refrescos concurrentes no pueden pisarse el flag y perder la
        aplicación del cambio.
        """
        with self._lock:
            settings = self._refresh_locked(force=force)
            return settings, self._changed

    # -- implementación --------------------------------------------------------
    def _stat_file(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self._path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _refresh_locked(self, force: bool) -> Settings:
        stat_before = self._stat_file()

        if (
            not force
            and self._loaded is not None
            and stat_before == self._stat
            and (self._last_error is None or stat_before == self._error_stat)
        ):
            # Sin cambios en disco: si la última lectura falló para este mismo
            # stamp, se devuelve el último valor bueno sin releer ni loguear.
            self._changed = False
            return self._settings  # type: ignore[return-value]

        loaded: LoadedConfig | None = None
        last_exc: Exception | None = None
        stat_after = stat_before

        for _ in range(_MAX_READ_ATTEMPTS):
            before = self._stat_file()
            try:
                candidate = _load_config(self._path)
            except (ConfigError, SchemaError) as exc:
                # JSON ilegible o esquema inválido: se conserva el último bueno.
                last_exc = exc
                stat_after = self._stat_file()
                break
            after = self._stat_file()
            # Re-stat tras leer: si el archivo fue reemplazado durante la
            # lectura, se reintenta para no fijar como cache una lectura vieja.
            if before == after:
                loaded = candidate
                stat_after = after
                break
            stat_after = after

        if loaded is None:
            self._last_error = (
                str(last_exc)
                if last_exc is not None
                else "config.json cambió durante la lectura"
            )
            self._last_ok = False
            self._changed = False
            self._stat = stat_after
            self._error_stat = stat_after
            logger.warning(
                "No se pudo recargar %s; se conserva el último valor bueno: %s",
                self._path,
                self._last_error,
            )
            if self._settings is None:
                # Nunca hubo un valor bueno: propagar (primer arranque).
                raise last_exc if last_exc is not None else ConfigError(
                    self._last_error
                )
            return self._settings

        secrets = _load_secrets(self._env_file)
        new_settings = settings_from_config(loaded.config, secrets)
        changed = self._loaded is None or loaded.config != self._loaded.config

        self._loaded = loaded
        self._secrets = secrets
        self._settings = new_settings
        self._stat = stat_after
        self._error_stat = None
        self._last_error = None
        self._last_ok = True
        self._changed = changed
        return new_settings
