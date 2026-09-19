"""Configuración estática compartida por todos los módulos del frontend.

Desde la unificación, ``config.json`` (raíz del repositorio) es la única fuente
de verdad: este módulo deriva sus constantes de ``smartcheck_config.load_config``
al importarse, de modo que los consumidores siguen usando los mismos nombres
(``MODEL_CATALOG``, ``DEFAULT_SOURCE``, ``VIDEOS_DIR``, ...) sin conocer el
esquema nuevo.

La resolución de rutas se delega en ``smartcheck_config``: las rutas relativas
del catálogo y de los vídeos se convierten en absolutas a partir de la raíz del
repositorio, así que funcionan sin importar el directorio de trabajo.

Nota: las constantes se calculan una vez, al importar. Para leer la
configuración *fresca* (por ejemplo al abrir el diálogo de configuración) usá
``load()``, que devuelve un ``AppConfig`` nuevo.
"""

from smartcheck_config import (
    AppConfig,
    ModelEntry,
    load_config,
    resolve_project_path,
    resolve_project_root,
)

# ---------------------------------------------------------------------------
# Raíz del proyecto
# ---------------------------------------------------------------------------
# Se mantiene como ``str`` para no cambiar el contrato que ya consumían los
# módulos del frontend; la resolución real la hace smartcheck_config.
PROJECT_ROOT = str(resolve_project_root())

# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------
# No hay sección de UI en el esquema de config.json, así que estos valores
# siguen siendo constantes de código.
WINDOW_TITLE = "SISTEMA DE CONTROL INDUSTRIAL"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800

# Código de salida que pide un reinicio total del proceso. ``run.py`` lo
# reconoce y relanza la aplicación completa.
RESTART_EXIT_CODE = 75

# ---------------------------------------------------------------------------
# Configuración unificada (derivada al importar)
# ---------------------------------------------------------------------------
_LOADED = load_config()
_CONFIG = _LOADED.config


def load() -> AppConfig:
    """Devuelve un ``AppConfig`` fresco leído de ``config.json``.

    Se usa cuando hace falta releer la configuración en tiempo de ejecución
    (p. ej. al abrir el diálogo de configuración) sin depender de las constantes
    calculadas al importar.
    """
    return load_config().config


# ---------------------------------------------------------------------------
# Galería de vídeos
# ---------------------------------------------------------------------------
VIDEOS_DIR = _CONFIG.paths.videos_dir
VIDEO_EXTENSIONS = (".mp4",)

# ---------------------------------------------------------------------------
# Fuente de vídeo por defecto
# ---------------------------------------------------------------------------
# Nombre del vídeo de ejemplo preferido. Los vídeos no se versionan, así que si
# no está presente se cae al primer vídeo disponible de VIDEOS_DIR y, si no hay
# ninguno instalado, a "0" (cámara del dispositivo).
_PREFERRED_DEFAULT_SOURCE = "20260323_124058.mp4"


def _resolve_default_source() -> str:
    """Resuelve la fuente por defecto degradando a un vídeo disponible o a la cámara.

    Es un nombre de archivo, no una ruta: se busca dentro de VIDEOS_DIR. El valor
    "0" está reservado para la cámara del dispositivo.
    """
    import os

    videos_path = resolve_project_path(VIDEOS_DIR)
    if os.path.isfile(os.path.join(videos_path, _PREFERRED_DEFAULT_SOURCE)):
        return _PREFERRED_DEFAULT_SOURCE
    if os.path.isdir(videos_path):
        for name in sorted(os.listdir(videos_path)):
            if name.endswith(VIDEO_EXTENSIONS):
                return name
    return "0"


# Se resuelve al importar y se conserva como ``str`` para no cambiar el
# contrato que consumen app.py y main.py.
DEFAULT_SOURCE = _resolve_default_source()

# ---------------------------------------------------------------------------
# Integración con el backend local
# ---------------------------------------------------------------------------
# Endpoint del backend local que registra el lote al terminar la inspección.
LOCAL_LOTE_ENDPOINT = _CONFIG.api.lote_endpoint

# Dirección del backend local; la usa la notificación de recarga de config.
API_HOST = _CONFIG.api.host
API_PORT = _CONFIG.api.port

# Catálogo ordenado de modelos. Se conserva el orden de config.json; la posición
# de cada entrada es el índice que usa el selector de la UI (ver
# NPU_MODEL_INDEX y DEFAULT_MODEL_INDEX más abajo).
MODEL_CATALOG: tuple[ModelEntry, ...] = tuple(
    ModelEntry(
        label=entry.label,
        model_path=entry.model_path,
        names_path=entry.names_path,
        model_id=entry.model_id,
    )
    for entry in _CONFIG.models.catalog
)


def _model_index(model_id: str) -> int:
    """Devuelve la posición en MODEL_CATALOG del modelo con ese ``model_id``."""
    for index, entry in enumerate(MODEL_CATALOG):
        if entry.model_id == model_id:
            return index
    raise ValueError(f"model_id no encontrado en MODEL_CATALOG: {model_id!r}")


# Índices del catálogo que usa la detección de plataforma al arrancar:
# en Raspberry Pi con NPU se parte del modelo Hailo; en PC o simulador, de COCO.
# Ambos se derivan por identificador estable, de modo que reordenar las entradas
# no rompe la selección.
NPU_MODEL_INDEX = _model_index(_CONFIG.models.npu_model_id)
DEFAULT_MODEL_INDEX = _model_index(_CONFIG.models.default_model_id)
