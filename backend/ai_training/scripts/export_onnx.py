"""CLI: exporta un `best.pt` a ONNX + `.names` para el catálogo.

    python -m backend.ai_training.scripts.export_onnx \
        --weights backend/ai_training/runs/tostadas_v2/weights/best.pt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.ai_training import rutas, validar
from backend.ai_training.entrenar import OPSET_HAILO, exportar_onnx
from backend.ai_training.rutas import DIR_RUNS, leer_nombres
from backend.config import ConfigError

NOMBRE_DEFECTO = "tostadas_v2"
PESOS_DEFECTO = DIR_RUNS / NOMBRE_DEFECTO / "weights" / "best.pt"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Exporta un best.pt a ONNX (opset para Hailo) y su .names."
    )
    parser.add_argument(
        "--weights",
        default=str(PESOS_DEFECTO),
        help="best.pt a exportar (default: %(default)s).",
    )
    parser.add_argument(
        "--nombre",
        default=NOMBRE_DEFECTO,
        help="Nombre del modelo de salida (default: %(default)s).",
    )
    parser.add_argument(
        "--models-dir",
        default=None,
        help="Destino de .onnx/.names. Default: paths.models_dir de config.json.",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=None,
        help="Tamaño de entrada. Default: stream.inference.image_size.",
    )
    parser.add_argument("--opset", type=int, default=OPSET_HAILO)
    parser.add_argument(
        "--sin-simplify",
        action="store_true",
        help="No simplificar el grafo ONNX (por defecto se simplifica).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        cfg = rutas.cargar_config()
    except ConfigError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
    models_dir = (
        Path(args.models_dir).expanduser() if args.models_dir else cfg.paths.models_dir
    )
    image_size = (
        args.imgsz if args.imgsz is not None else cfg.stream.inference.image_size
    )

    try:
        destino_names = models_dir / f"{args.nombre}.names"
        exportar_onnx(
            Path(args.weights).expanduser(),
            models_dir / f"{args.nombre}.onnx",
            destino_names=destino_names,
            image_size=image_size,
            opset=args.opset,
            simplify=not args.sin_simplify,
        )
        validar.advertir_thresholds(cfg, destino_names, leer_nombres(destino_names))
    except (FileNotFoundError, ImportError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
