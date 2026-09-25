"""Entrenamiento YOLO y exportación a ONNX, adaptado al catálogo del proyecto.

Los nombres de clase se derivan de `model.names` (fuente de verdad del `.pt`),
nunca de una lista hardcodeada: invertir TCQ/TCOK hace que el detector etiquete
al revés. La exportación usa opset 11 + simplificado porque el parser de
Hailo-8L no digiere opsets modernos.
"""

from __future__ import annotations

import importlib.util
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

from backend.ai_training.rutas import DIR_RUNS, asegurar_dir, escribir_nombres

# Opset y simplificación que exige el toolchain de Hailo-8L.
OPSET_HAILO = 11


def nombres_de(
    nombres: Mapping[int, str] | list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    """Normaliza `model.names` a una tupla ordenada por índice.

    Ultralytics devuelve un dict {0: ..., 1: ...}; el orden es el de los índices
    del modelo y no se debe reordenar.
    """
    if isinstance(nombres, Mapping):
        return tuple(str(nombres[clave]) for clave in sorted(nombres))
    return tuple(str(nombre) for nombre in nombres)


def _verificar_exportacion(simplify: bool) -> None:
    """Falla temprano y con mensaje claro si faltan las deps de exportación."""
    faltantes = ["onnx"] if importlib.util.find_spec("onnx") is None else []
    if simplify and importlib.util.find_spec("onnxslim") is None:
        faltantes.append("onnxslim")
    if faltantes:
        raise ImportError(
            "Faltan dependencias de exportación: "
            + ", ".join(faltantes)
            + ". Instalá backend/ai_training/requirements-training.txt."
        )


def entrenar(
    data_yaml: Path,
    *,
    modelo_base: str = "yolo11n.pt",
    epochs: int = 100,
    image_size: int = 640,
    device: str = "0",
    paciencia: int = 15,
    workers: int = 4,
    batch: int | None = None,
    proyecto: Path = DIR_RUNS,
    nombre: str = "tostadas_v2",
    aviso: Callable[[str], None] = print,
) -> Path:
    """Entrena y devuelve la ruta del `best.pt` resultante."""
    from ultralytics import YOLO

    aviso(
        f"[INFO] Entrenando {modelo_base} desde {data_yaml} "
        f"({epochs} épocas, {image_size}px, device={device})."
    )
    modelo = YOLO(modelo_base)
    parametros: dict = dict(
        data=str(data_yaml),
        epochs=epochs,
        imgsz=image_size,
        device=device,
        patience=paciencia,
        workers=workers,
        plots=True,
        project=str(proyecto),
        name=nombre,
    )
    if batch is not None:
        parametros["batch"] = batch
    modelo.train(**parametros)

    trainer = getattr(modelo, "trainer", None)
    save_dir = Path(getattr(trainer, "save_dir", Path(proyecto) / nombre))
    best = save_dir / "weights" / "best.pt"
    if not best.is_file():
        raise FileNotFoundError(
            f"No se encontró best.pt tras el entrenamiento: {best}"
        )
    return best


def exportar_onnx(
    weights: Path,
    destino_onnx: Path,
    *,
    destino_names: Path | None = None,
    image_size: int = 640,
    opset: int = OPSET_HAILO,
    simplify: bool = True,
    aviso: Callable[[str], None] = print,
) -> Path:
    """Exporta `weights` a ONNX, lo copia a `destino_onnx` y escribe el `.names`."""
    weights = Path(weights)
    if not weights.is_file():
        raise FileNotFoundError(f"No se encontraron los pesos: {weights}")
    _verificar_exportacion(simplify)

    from ultralytics import YOLO

    modelo = YOLO(str(weights))
    aviso(
        f"[INFO] Exportando a ONNX (opset {opset}, simplify={simplify}, "
        f"{image_size}px)."
    )
    generado = Path(
        modelo.export(format="onnx", imgsz=image_size, opset=opset, simplify=simplify)
    )
    if not generado.is_file():
        raise FileNotFoundError(f"No se generó el ONNX esperado: {generado}")

    asegurar_dir(destino_onnx.parent)
    shutil.copy(generado, destino_onnx)
    aviso(f"[OK] ONNX copiado a {destino_onnx}")

    if destino_names is not None:
        nombres = nombres_de(modelo.names)
        escribir_nombres(destino_names, nombres)
        aviso(f"[OK] Nombres de clase escritos en {destino_names}: {', '.join(nombres)}")
    return destino_onnx
