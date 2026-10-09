"""CLI: compila `models/<nombre>.onnx` a `models/<nombre>.hef` para Hailo-8L.

Sólo en el host de compilación (Linux/WSL2 + DFC). Antes hay que generar el
dataset de calibración:

    python -m backend.ai_training.scripts.compile_hailo --nombre tostadas_v2

El flujo es YOLO26 end-to-end NMS-free: el HEF sale con la salida cruda del
modelo y el NMS se hace en CPU del lado del runtime (mismo criterio que
`yolo_opencv.py`).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from backend.ai_training import rutas
from backend.ai_training.compilar_hailo import (
    HW_ARCH_DEFECTO,
    compilar,
)
from backend.ai_training.rutas import DIR_TRABAJO
from backend.config import ConfigError


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Compila un ONNX a HEF (parseo -> cuantización -> HEF) para "
        "Hailo-8L. Flujo YOLO26 end-to-end NMS-free: el .alls sólo lleva "
        "normalización y perf params, y el HEF sale con la salida cruda del "
        "modelo (NMS se hace en CPU en el runtime)."
    )
    parser.add_argument(
        "--nombre",
        default="tostadas_v2",
        help="Base del modelo: models/<nombre>.onnx/.names (default: %(default)s).",
    )
    parser.add_argument(
        "--net-name",
        default=None,
        help="Nombre de red dentro del HAR (default: --nombre).",
    )
    parser.add_argument(
        "--models-dir",
        default=None,
        help="Dónde viven .onnx/.names. Default: paths.models_dir de config.json.",
    )
    parser.add_argument(
        "--work-dir",
        default=str(DIR_TRABAJO),
        help="Dónde quedan .har/.alls (default: %(default)s).",
    )
    parser.add_argument(
        "--calib",
        default=None,
        help="calib_dataset.npy (default: <work-dir>/calib_dataset.npy).",
    )
    parser.add_argument(
        "--hef-dest",
        default=None,
        help="Destino del HEF (default: <models-dir>/<nombre>.hef).",
    )
    parser.add_argument("--hw-arch", default=HW_ARCH_DEFECTO)
    parser.add_argument(
        "--imgsz",
        type=int,
        default=None,
        help="Tamaño de entrada. Default: stream.inference.image_size.",
    )
    parser.add_argument(
        "--end-node",
        action="append",
        default=None,
        help="Nodo de salida para recortar el grafo (repetible). Por defecto "
        "se parsea entero; pasarlos sólo si el parser necesita recorte manual.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        models_dir = rutas.resolver_models_dir(args.models_dir)
        image_size = rutas.resolver_image_size(args.imgsz)
    except ConfigError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1) from exc

    nombre = args.nombre
    work_dir = Path(args.work_dir).expanduser()
    onnx = models_dir / f"{nombre}.onnx"
    calib = (
        Path(args.calib).expanduser() if args.calib else work_dir / "calib_dataset.npy"
    )
    hef_dest = (
        Path(args.hef_dest).expanduser()
        if args.hef_dest
        else models_dir / f"{nombre}.hef"
    )

    print("=" * 60)
    print("      COMPILACIÓN DE MODELO A HEF PARA HAILO-8L")
    print("=" * 60)

    try:
        hef = compilar(
            onnx,
            calib,
            work_dir,
            nombre=nombre,
            image_size=image_size,
            net_name=args.net_name,
            hw_arch=args.hw_arch,
            end_nodes=tuple(args.end_node) if args.end_node else None,
        )
        if hef.resolve() != hef_dest.resolve():
            shutil.copy(hef, hef_dest)
    except (FileNotFoundError, ImportError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1) from exc

    print("=" * 60)
    print("[OK] Compilación completada.")
    print(f"[OK] HEF: {hef_dest}")
    print("=" * 60)


if __name__ == "__main__":
    main()
