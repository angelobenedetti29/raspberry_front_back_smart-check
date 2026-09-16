"""Configuración estática compartida por todos los módulos del frontend.

El módulo contiene los valores constantes de la aplicación y la resolución de las
rutas del proyecto. Las rutas de modelos y de vídeos se declaran relativas a la
raíz del repositorio; ``resolve_project_path`` las convierte en absolutas a
partir de ``PROJECT_ROOT``, de modo que funcionan sin importar el directorio de
trabajo desde el que se lance la aplicación.
"""

import os
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Raíz del proyecto
# ---------------------------------------------------------------------------
# Este archivo vive en <raíz>/frontend/config.py, así que la raíz está dos
# niveles más arriba. Derivarla de __file__ (y no del CWD) hace que la
# resolución sea determinista.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_project_path(path):
    """Devuelve la ruta absoluta de un recurso declarado en este módulo.

    Las rutas absolutas (por ejemplo el modelo ``.hef`` de la NPU Hailo) se
    devuelven tal cual. No comprueba que el recurso exista: de esa validación se
    encargan los llamadores, que pueden dar un mensaje de error más útil.
    """
    if not path or os.path.isabs(path):
        return path
    return os.path.join(PROJECT_ROOT, path)


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------
WINDOW_TITLE = "SISTEMA DE CONTROL INDUSTRIAL"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800

# ---------------------------------------------------------------------------
# Galería de vídeos
# ---------------------------------------------------------------------------
# Carpeta de vídeos, relativa a la raíz del repositorio.
VIDEOS_DIR = "multimedia/videos"
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
# Se puede sobrescribir con la variable de entorno SMARTCHECK_LOTE_ENDPOINT.
LOCAL_LOTE_ENDPOINT = os.environ.get(
    "SMARTCHECK_LOTE_ENDPOINT",
    "http://localhost:8000/api/lotes/finalizar",
)


class ModelEntry(NamedTuple):
    """Entrada del catálogo de modelos que el operador puede seleccionar.

    ``model_path`` y ``names_path`` son rutas relativas a la raíz del
    repositorio (salvo el ``.hef`` de Hailo, que es una ruta absoluta del
    sistema); ``resolve_project_path`` las convierte en rutas utilizables.
    ``model_id`` es un identificador estable e independiente del orden del
    catálogo, usado para localizar entradas concretas sin depender de su índice.
    """

    label: str         # Texto que se muestra en el selector de la barra lateral.
    model_path: str    # Pesos del modelo: .onnx para CPU o .hef para la NPU Hailo.
    names_path: str    # Archivo .names con los nombres de las clases detectables.
    model_id: str      # Identificador estable de la entrada.


# Catálogo ordenado de modelos. La posición de cada entrada es el índice que usa
# el selector de la UI; ver NPU_MODEL_INDEX y DEFAULT_MODEL_INDEX más abajo.
MODEL_CATALOG: tuple[ModelEntry, ...] = (
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

# Identificador estable del modelo NPU dentro del catálogo.
NPU_MODEL_ID = "yolov8s-hailo"


def _model_index(model_id: str) -> int:
    """Devuelve la posición en MODEL_CATALOG del modelo con ese ``model_id``."""
    for index, entry in enumerate(MODEL_CATALOG):
        if entry.model_id == model_id:
            return index
    raise ValueError(f"model_id no encontrado en MODEL_CATALOG: {model_id!r}")


# Índices del catálogo que usa la detección de plataforma al arrancar:
# en Raspberry Pi con NPU se parte del modelo Hailo; en PC o simulador, de COCO.
# NPU_MODEL_INDEX se deriva del catálogo por identificador, de modo que reordenar
# las entradas no rompe la selección.
NPU_MODEL_INDEX = _model_index(NPU_MODEL_ID)
DEFAULT_MODEL_INDEX = 0
