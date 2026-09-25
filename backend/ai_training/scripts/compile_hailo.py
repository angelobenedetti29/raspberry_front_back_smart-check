"""CLI: compila `models/<nombre>.onnx` a `models/<nombre>.hef` para Hailo-8L.

Sólo en el host de compilación (Linux/WSL2 + DFC). Antes hay que generar el
dataset de calibración:

    python -m backend.ai_training.scripts.compile_hailo --nombre tostadas_v2
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from backend.ai_training import rutas
from backend.ai_training.compilar_hailo import (
    HW_ARCH_DEFECTO,
    NODOS_SALIDA_DEFECTO,
    compilar,
)
from backend.ai_training.rutas import DIR_TRABAJO, leer_nombres
from backend.config import ConfigError


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Compila un ONNX a HEF (parseo -> cuantización -> HEF) para "
        "Hailo-8L, generando el _nms.json y el .alls con rutas locales."
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
        help="Dónde quedan har/_nms.json/.alls (default: %(default)s).",
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
        "--clases",
        type=int,
        default=None,
        help="Cantidad de clases. Default: contar <models-dir>/<nombre>.names.",
    )
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
        help="Nodo de salida (repetible). Default: los 6 de YOLO11 "
        "(/model.23/...); YOLOv8 usa /model.22/... y exige verificar las conv.",
    )
    parser.add_argument(
        "--reg-layer",
        action="append",
        default=None,
        help="Capa de regresión del NMS (repetible). Default: conv51/62/77.",
    )
    parser.add_argument(
        "--cls-layer",
        action="append",
        default=None,
        help="Capa de clasificación del NMS (repetible). Default: conv54/65/80.",
    )
    return parser.parse_args(argv)


def _resolver_clases(cli_value, names_path: Path) -> int:
    en_names = len(leer_nombres(names_path)) if names_path.is_file() else 0
    if not en_names:
        print(
            f"[WARN] No se encontró {names_path.name}: el HEF se compila igual, "
            "pero el runtime no podrá cargarlo sin ese archivo."
        )
    if cli_value is not None:
        if en_names and cli_value != en_names:
            print(
                f"[ERROR] --clases {cli_value} no coincide con "
                f"{names_path.name} ({en_names} clases)."
            )
            sys.exit(1)
        return cli_value
    if not en_names:
        print("[ERROR] No se pudo determinar la cantidad de clases.")
        print(f"        Pasá --clases N o asegurá que exista {names_path}.")
        sys.exit(1)
    return en_names


def main(argv=None):
    args = parse_args(argv)

    try:
        models_dir = rutas.resolver_models_dir(args.models_dir)
        image_size = rutas.resolver_image_size(args.imgsz)
    except ConfigError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    nombre = args.nombre
    work_dir = Path(args.work_dir).expanduser()
    onnx = models_dir / f"{nombre}.onnx"
    nombres = models_dir / f"{nombre}.names"
    calib = (
        Path(args.calib).expanduser() if args.calib else work_dir / "calib_dataset.npy"
    )
    hef_dest = (
        Path(args.hef_dest).expanduser()
        if args.hef_dest
        else models_dir / f"{nombre}.hef"
    )

    clases = _resolver_clases(args.clases, nombres)

    print("=" * 60)
    print("      COMPILACIÓN DE MODELO A HEF PARA HAILO-8L")
    print("=" * 60)

    try:
        hef = compilar(
            onnx,
            calib,
            work_dir,
            nombre=nombre,
            clases=clases,
            image_size=image_size,
            net_name=args.net_name,
            hw_arch=args.hw_arch,
            end_nodes=tuple(args.end_node) if args.end_node else NODOS_SALIDA_DEFECTO,
            reg_layers=args.reg_layer,
            cls_layers=args.cls_layer,
        )
        if hef.resolve() != hef_dest.resolve():
            shutil.copy(hef, hef_dest)
    except (FileNotFoundError, ImportError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print("=" * 60)
    print("[OK] Compilación completada.")
    print(f"[OK] HEF: {hef_dest}")
    print("=" * 60)


if __name__ == "__main__":
    main()
