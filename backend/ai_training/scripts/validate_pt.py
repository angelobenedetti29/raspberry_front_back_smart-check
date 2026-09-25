"""CLI: valida un modelo PyTorch (.pt) con Ultralytics sobre una imagen.

    python -m backend.ai_training.scripts.validate_pt --imagen /ruta/frame.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.ai_training import validar
from backend.ai_training.rutas import DIR_RUNS

NOMBRE_DEFECTO = "tostadas_v2"
PESOS_DEFECTO = DIR_RUNS / NOMBRE_DEFECTO / "weights" / "best.pt"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Corre un .pt de Ultralytics sobre una imagen y guarda el "
        "resultado con el resumen por clase."
    )
    parser.add_argument(
        "--weights",
        default=str(PESOS_DEFECTO),
        help="best.pt a validar (default: %(default)s).",
    )
    parser.add_argument("--imagen", default=None)
    parser.add_argument("--images-dir", default=None)
    parser.add_argument(
        "--output",
        default=None,
        help="Salida. Default: multimedia/output/validacion_pt.jpg.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        imagen = validar.resolver_imagen(args.imagen, args.images_dir)
        salida = (
            Path(args.output).expanduser()
            if args.output
            else validar.SALIDA_DIR / "validacion_pt.jpg"
        )
        conteo = validar.validar_pt(Path(args.weights).expanduser(), imagen, salida)
    except (FileNotFoundError, ImportError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print("=" * 60)
    print("  RESUMEN DE DETECCIONES (.pt)")
    print("=" * 60)
    for nombre, cantidad in sorted(conteo.items()):
        print(f"   * {nombre}: {cantidad}")
    if not conteo:
        print("   * Sin detecciones.")
    print("=" * 60)


if __name__ == "__main__":
    main()
