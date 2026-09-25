"""Rutas y utilidades compartidas por las herramientas de ai_training.

Estas herramientas corren tanto en la PC de entrenamiento como en el host de
compilación (WSL2 + Hailo DFC), así que el módulo:

- ancla la raíz del repo al archivo, no al CWD;
- resuelve `models/` y `data/` desde backend.config (la única fuente de verdad);
- no importa dependencias pesadas (torch, ultralytics, cv2, hailo) al cargarse.

Se ejecutan como módulo, con la raíz del repo como CWD:

    python -m backend.ai_training.scripts.prepare_calibration --help
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.config import AppConfig

# <repo>/backend/ai_training/rutas.py -> parents[2] == <repo>
ROOT = Path(__file__).resolve().parents[2]

# Intermedios del toolchain (runs/, .har, .npy, _nms.json, .alls). Todo ignorado
# por git; sólo lo desplegable se copia a `models/`.
DIR_TRABAJO = ROOT / "backend" / "ai_training"
DIR_RUNS = DIR_TRABAJO / "runs"


def cargar_config() -> "AppConfig":
    """Carga la config validada del proyecto (rutas absolutas ancladas al ROOT)."""
    # Import perezoso: mantiene liviano el arranque y el `--help`.
    from backend.config import load_config

    return load_config()


def asegurar_dir(path: Path) -> Path:
    """Crea el directorio (y sus padres) si no existe y lo devuelve."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def leer_nombres(path: Path) -> tuple[str, ...]:
    """Lee un `.names`: una clase por línea, ignorando líneas vacías."""
    contenido = path.read_text(encoding="utf-8")
    return tuple(linea.strip() for linea in contenido.splitlines() if linea.strip())


def escribir_nombres(path: Path, nombres: Sequence[str]) -> Path:
    """Escribe un `.names` de forma atómica, una clase por línea.

    El orden de `nombres` es el orden de los índices del modelo: no reordenar.
    """
    asegurar_dir(path.parent)
    temporal = path.with_name(path.name + ".tmp")
    temporal.write_text("".join(f"{nombre}\n" for nombre in nombres), encoding="utf-8")
    os.replace(temporal, path)
    return path


def valor_entorno(nombre: str) -> str | None:
    """Devuelve la variable de entorno no vacía, o None."""
    valor = os.environ.get(nombre)
    if valor is None or not valor.strip():
        return None
    return valor.strip()


def resolver_models_dir(cli_value: str | None) -> Path:
    """`--models-dir` si se pasó; si no, `paths.models_dir` de config.json."""
    if cli_value:
        return Path(cli_value).expanduser()
    return cargar_config().paths.models_dir


def resolver_image_size(cli_value: int | None) -> int:
    """`--imgsz`/`--image-size` si se pasó; si no, `stream.inference.image_size`."""
    if cli_value is not None:
        return cli_value
    return cargar_config().stream.inference.image_size
