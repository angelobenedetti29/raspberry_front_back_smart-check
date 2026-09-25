"""CLI: entrena YOLO y exporta a ONNX + .names listos para el catálogo.

    python -m backend.ai_training.scripts.train \
        --data /ruta/al/dataset/data.yaml --nombre tostadas_v2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.ai_training import rutas, validar
from backend.ai_training.entrenar import OPSET_HAILO, entrenar, exportar_onnx
from backend.ai_training.rutas import DIR_RUNS, leer_nombres
from backend.config import ConfigError

ENV_DATA = "TRAIN_DATA_YAML"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Entrena YOLO y exporta el resultado a ONNX + .names "
        "para el catálogo de modelos."
    )
    parser.add_argument(
        "--data",
        default=None,
        help=f"data.yaml del dataset. Si se omite se usa {ENV_DATA}.",
    )
    parser.add_argument(
        "--weights",
        default=None,
        help="best.pt ya entrenado: si se indica, se omite el entrenamiento.",
    )
    parser.add_argument(
        "--nombre",
        default="tostadas_v2",
        help="Nombre del run y base de los archivos de salida (default: %(default)s).",
    )
    parser.add_argument(
        "--modelo-base",
        default="yolo11n.pt",
        help="Pesos base de Ultralytics (default: %(default)s).",
    )
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument(
        "--imgsz",
        type=int,
        default=None,
        help="Tamaño de entrada. Default: stream.inference.image_size.",
    )
    parser.add_argument(
        "--device",
        default="0",
        help="Device de PyTorch, ej. '0' o 'cpu' (default: %(default)s).",
    )
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch", type=int, default=None)
    parser.add_argument("--opset", type=int, default=OPSET_HAILO)
    parser.add_argument(
        "--models-dir",
        default=None,
        help="Destino de .onnx/.names. Default: paths.models_dir de config.json.",
    )
    parser.add_argument(
        "--sin-export",
        action="store_true",
        help="Sólo entrenar, sin exportar a ONNX.",
    )
    return parser.parse_args(argv)


def _resolver_data(cli_value) -> Path:
    valor = cli_value or rutas.valor_entorno(ENV_DATA)
    if not valor:
        print("[ERROR] No se especificó el dataset de entrenamiento (data.yaml).")
        print(f"        Pasá --data /ruta/al/data.yaml o definí {ENV_DATA}.")
        sys.exit(1)
    path = Path(valor).expanduser()
    if not path.is_file():
        print(f"[ERROR] No se encontró el dataset: {path}")
        sys.exit(1)
    return path


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
        if args.weights:
            pesos = Path(args.weights).expanduser()
        else:
            pesos = entrenar(
                _resolver_data(args.data),
                modelo_base=args.modelo_base,
                epochs=args.epochs,
                image_size=image_size,
                device=args.device,
                paciencia=args.patience,
                workers=args.workers,
                batch=args.batch,
                proyecto=DIR_RUNS,
                nombre=args.nombre,
            )

        if args.sin_export:
            print(f"[OK] Entrenamiento finalizado. Pesos: {pesos}")
            return

        destino_names = models_dir / f"{args.nombre}.names"
        exportar_onnx(
            pesos,
            models_dir / f"{args.nombre}.onnx",
            destino_names=destino_names,
            image_size=image_size,
            opset=args.opset,
        )
        validar.advertir_thresholds(cfg, destino_names, leer_nombres(destino_names))
    except (FileNotFoundError, ImportError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
