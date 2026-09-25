"""CLI: genera el dataset de calibración de Hailo (`calib_dataset.npy`).

    python -m backend.ai_training.scripts.prepare_calibration \
        --images-dir /ruta/al/dataset/train/images
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.ai_training import rutas
from backend.ai_training.calibrar import preparar_calibracion

ENV_IMAGENES = "CALIB_IMAGES_DIR"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Genera un calib_dataset.npy a partir de imágenes reales "
        "para la cuantización INT8 de Hailo."
    )
    parser.add_argument(
        "--images-dir",
        default=None,
        help=f"Carpeta con las imágenes de calibración. Si se omite se usa "
        f"la variable de entorno {ENV_IMAGENES}.",
    )
    parser.add_argument(
        "--output",
        default=str(rutas.DIR_TRABAJO / "calib_dataset.npy"),
        help="Ruta de salida del .npy (default: %(default)s).",
    )
    parser.add_argument(
        "--image-size",
        type=int,
        default=None,
        help="Lado de entrada del modelo. Default: stream.inference.image_size "
        "de config.json.",
    )
    parser.add_argument(
        "--max-imagenes",
        type=int,
        default=100,
        help="Máximo de imágenes a incluir (default: %(default)s).",
    )
    return parser.parse_args(argv)


def _resolver_imagenes(cli_value):
    valor = cli_value or rutas.valor_entorno(ENV_IMAGENES)
    if not valor:
        print("[ERROR] No se especificó la carpeta de imágenes de calibración.")
        print(
            f"        Pasá --images-dir /ruta/a/train/images o definí {ENV_IMAGENES}."
        )
        sys.exit(1)
    return Path(valor).expanduser()


def main(argv=None):
    args = parse_args(argv)

    print("=" * 60)
    print("        CREACIÓN DEL DATASET DE CALIBRACIÓN HAILO")
    print("=" * 60)

    images_dir = _resolver_imagenes(args.images_dir)
    image_size = rutas.resolver_image_size(args.image_size)
    salida = Path(args.output).expanduser()

    try:
        forma = preparar_calibracion(
            images_dir,
            salida,
            image_size=image_size,
            max_imagenes=args.max_imagenes,
        )
    except (FileNotFoundError, ImportError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print("=" * 60)
    print("[OK] Dataset de calibración generado con éxito.")
    print(f"[OK] Archivo: {salida}")
    print(f"[OK] Forma:   {forma}")
    print("=" * 60)


if __name__ == "__main__":
    main()
