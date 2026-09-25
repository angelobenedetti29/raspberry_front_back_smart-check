"""Validación visual de modelos: corre el detector real y anota una imagen.

Reusa el mismo detector que el pipeline (`backend.inference`), así lo que se
valida es lo que corre en producción: preprocesado, decode, NMS y umbrales por
clase. No reimplementa la decodificación de `cv2.dnn` como la copia vieja.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from backend.ai_training.calibrar import listar_imagenes
from backend.ai_training.rutas import ROOT, asegurar_dir

if TYPE_CHECKING:
    import numpy as np

    from backend.config import AppConfig, ModelEntry
    from backend.inference.detector import Detector, ResultadoDeteccion

# Salida de imágenes anotadas (ya ignorada por .gitignore: multimedia/output/).
SALIDA_DIR = ROOT / "multimedia" / "output"

_PALETA = (
    (56, 56, 255),
    (255, 157, 56),
    (56, 196, 56),
    (196, 56, 196),
    (56, 196, 196),
    (255, 255, 56),
)


def color_clase(indice: int) -> tuple[int, int, int]:
    """Color BGR estable para una clase, ciclando la paleta."""
    return _PALETA[indice % len(_PALETA)]


def resumen_por_clase(
    detecciones: Sequence["ResultadoDeteccion"],
) -> dict[str, int]:
    """Cuenta detecciones por etiqueta."""
    conteo: dict[str, int] = {}
    for deteccion in detecciones:
        conteo[deteccion.label] = conteo.get(deteccion.label, 0) + 1
    return conteo


def anotar(
    frame: "np.ndarray",
    detecciones: Sequence["ResultadoDeteccion"],
    nombres: Sequence[str],
) -> "np.ndarray":
    """Dibuja cajas y etiquetas sobre el frame (in place) y lo devuelve."""
    import cv2

    for deteccion in detecciones:
        left, top, ancho, alto = deteccion.bbox
        try:
            indice = nombres.index(deteccion.label)
        except ValueError:
            indice = 0
        color = color_clase(indice)
        cv2.rectangle(frame, (left, top), (left + ancho, top + alto), color, 2)
        cv2.putText(
            frame,
            deteccion.etiqueta,
            (left, max(0, top - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
        )
    return frame


def anotar_imagen(
    detector: "Detector",
    imagen_path: Path,
    destino: Path,
    *,
    nombres: Sequence[str] | None = None,
    aviso=print,
) -> dict[str, int]:
    """Corre el detector, guarda la imagen anotada y devuelve el resumen."""
    import cv2

    frame = cv2.imread(str(imagen_path))
    if frame is None:
        raise FileNotFoundError(f"No se pudo leer la imagen: {imagen_path}")

    detecciones = detector.detectar_frame(frame)
    etiquetas = tuple(nombres) if nombres is not None else tuple(detector.nombres)
    anotar(frame, detecciones, etiquetas)

    asegurar_dir(destino.parent)
    if not cv2.imwrite(str(destino), frame):
        raise RuntimeError(f"No se pudo escribir la imagen: {destino}")
    aviso(f"[OK] Imagen anotada: {destino}")
    return resumen_por_clase(detecciones)


def validar_pt(
    weights: Path,
    imagen_path: Path,
    destino: Path,
    *,
    aviso=print,
) -> dict[str, int]:
    """Corre un `.pt` de Ultralytics, guarda el resultado y devuelve el resumen."""
    from ultralytics import YOLO

    modelo = YOLO(str(weights))
    conteo: dict[str, int] = {}
    for resultado in modelo(str(imagen_path)):
        asegurar_dir(destino.parent)
        resultado.save(filename=str(destino))
        nombres = resultado.names
        for clase in resultado.boxes.cls.tolist():
            etiqueta = nombres[int(clase)]
            conteo[etiqueta] = conteo.get(etiqueta, 0) + 1
    aviso(f"[OK] Imagen anotada: {destino}")
    return conteo


def entrada_catalogo(cfg: "AppConfig", model_id: str | None) -> "ModelEntry":
    """Busca la entrada del catálogo por id, o usa la default de la config."""
    buscado = model_id or cfg.models.default_model_id
    for entrada in cfg.models.catalog:
        if entrada.model_id == buscado:
            return entrada
    raise ValueError(f"Modelo no encontrado en el catálogo: {buscado}")


def construir_detector_catalogo(
    cfg: "AppConfig", model_id: str | None = None
) -> tuple["Detector", "ModelEntry"]:
    """Construye el detector real de un modelo del catálogo."""
    from backend.inference.catalogo import construir_detector

    entrada = entrada_catalogo(cfg, model_id)
    return construir_detector(entrada, cfg.stream.inference), entrada


def resolver_imagen(imagen: str | None, images_dir: str | None) -> Path:
    """Resuelve la imagen de prueba: `--imagen` o la primera de `--images-dir`."""
    if imagen:
        path = Path(imagen).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"No se encontró la imagen: {path}")
        return path
    if images_dir:
        directorio = Path(images_dir).expanduser()
        imagenes = listar_imagenes(directorio)
        if not imagenes:
            raise FileNotFoundError(
                f"No se encontraron imágenes JPG/PNG en: {directorio}"
            )
        return imagenes[0]
    raise ValueError("Indicá --imagen o --images-dir con al menos una imagen.")


def advertir_thresholds(
    cfg: "AppConfig",
    names_path: Path,
    nombres: Sequence[str],
    *,
    aviso=print,
) -> tuple[str, ...]:
    """Avisa si algún `class_thresholds` del catálogo no matchea los `.names`.

    Un desajuste (típico tras re-exportar con otros nombres de clase) hace que
    el umbral por clase se ignore en silencio y todo caiga al umbral global.
    Las claves del catálogo ya vienen normalizadas a minúscula.
    """
    objetivo = Path(names_path).resolve()
    conocidas = {nombre.lower() for nombre in nombres}
    desajustadas = [
        f"{entrada.model_id}:{clase}"
        for entrada in cfg.models.catalog
        if entrada.names_path.resolve() == objetivo
        for clase in entrada.class_thresholds
        if clase not in conocidas
    ]
    if desajustadas:
        aviso(
            f"[WARN] Los class_thresholds de {names_path.name} no coinciden con "
            f"las clases {list(nombres)}: {', '.join(desajustadas)}. "
            "Actualizá config.json o esos umbrales por clase no se aplicarán."
        )
    return tuple(desajustadas)
