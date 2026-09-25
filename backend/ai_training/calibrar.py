"""Generación del dataset de calibración para la cuantización INT8 de Hailo.

Toma imágenes reales, las lleva al mismo espacio que ve el detector en runtime
(BGR->RGB, `image_size`×`image_size`, uint8 sin dividir) y las empaqueta en un
único `.npy`, que es lo que consume `ClientRunner.optimize`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from backend.ai_training.rutas import asegurar_dir

if TYPE_CHECKING:
    import numpy as np

_EXTENSIONES = (".jpg", ".jpeg", ".png")


def listar_imagenes(images_dir: Path) -> list[Path]:
    """Devuelve las imágenes soportadas del directorio, ordenadas."""
    return sorted(
        p
        for p in images_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _EXTENSIONES
    )


def preparar_calibracion(
    images_dir: Path,
    destino: Path,
    *,
    image_size: int,
    max_imagenes: int = 100,
    aviso: Callable[[str], None] = print,
) -> tuple[int, ...]:
    """Construye el `.npy` de calibración y devuelve su forma (N, H, W, C).

    `aviso` recibe los mensajes de progreso/advertencia; por defecto imprime.
    """
    import cv2
    import numpy as np

    if not images_dir.is_dir():
        raise FileNotFoundError(f"No existe la carpeta de imágenes: {images_dir}")

    rutas_imagenes = listar_imagenes(images_dir)
    if not rutas_imagenes:
        raise FileNotFoundError(
            f"No se encontraron imágenes JPG/PNG en: {images_dir}"
        )

    seleccion = rutas_imagenes[: max(1, max_imagenes)]
    aviso(
        f"[INFO] {len(rutas_imagenes)} imágenes en {images_dir}; "
        f"usando {len(seleccion)}"
    )

    imagenes: list["np.ndarray"] = []
    for i, ruta in enumerate(seleccion, start=1):
        imagen = cv2.imread(str(ruta))
        if imagen is None:
            aviso(f"[WARN] No se pudo leer: {ruta}")
            continue
        imagen = cv2.resize(imagen, (image_size, image_size))
        imagen = cv2.cvtColor(imagen, cv2.COLOR_BGR2RGB)
        imagenes.append(imagen)
        if i % 10 == 0 or i == len(seleccion):
            aviso(f"  procesadas {i}/{len(seleccion)}")

    if not imagenes:
        raise RuntimeError("No se pudo procesar ninguna imagen con éxito.")

    calib = np.array(imagenes, dtype=np.uint8)
    asegurar_dir(destino.parent)
    np.save(str(destino), calib)
    return tuple(calib.shape)
