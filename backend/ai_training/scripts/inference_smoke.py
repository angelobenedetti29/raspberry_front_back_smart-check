"""CLI: smoke test de inferencia (resuelve el catálogo, carga y, opcional, infiere).

Sin --imagen/--images-dir sólo valida resolución y carga del detector:

    python -m backend.ai_training.scripts.inference_smoke --imagen /ruta/frame.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.ai_training import rutas, validar
from backend.config import ConfigError
from backend.inference.catalogo import catalogo_resuelto
from backend.inference.detector import ErrorCargaModelo


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Smoke test del pipeline: resolución de catálogo, carga del "
        "detector y, si hay imagen, una inferencia."
    )
    parser.add_argument(
        "--modelo",
        default=None,
        help="model_id del catálogo. Default: models.default_model_id.",
    )
    parser.add_argument("--imagen", default=None)
    parser.add_argument("--images-dir", default=None)
    parser.add_argument("--output", default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        cfg = rutas.cargar_config()
        entrada = validar.entrada_catalogo(cfg, args.modelo)
        resuelto = next(
            r
            for r in catalogo_resuelto(cfg.models.catalog)
            if r.model_id == entrada.model_id
        )
    except (ConfigError, ValueError, StopIteration) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print("=" * 60)
    print("  SMOKE TEST DE INFERENCIA")
    print("=" * 60)
    print(f"[INFO] Modelo:     {resuelto.model_id} ({resuelto.label})")
    motor = resuelto.motor.etiqueta if resuelto.motor else "-"
    print(f"[INFO] Motor:      {motor}")
    print(f"[INFO] Archivo:    {resuelto.modelo_path}")
    print(f"[INFO] Disponible: {resuelto.disponible} {resuelto.motivo}".rstrip())
    if not resuelto.disponible:
        print(f"[ERROR] {resuelto.motivo}")
        sys.exit(1)

    try:
        detector, _ = validar.construir_detector_catalogo(cfg, entrada.model_id)
    except (ValueError, ErrorCargaModelo) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print(f"[OK] Detector cargado: {detector.modelo_path.name}")
    print(f"[INFO] Clases: {', '.join(detector.nombres)}")

    try:
        if args.imagen or args.images_dir:
            imagen = validar.resolver_imagen(args.imagen, args.images_dir)
            salida = (
                Path(args.output).expanduser()
                if args.output
                else validar.SALIDA_DIR / f"smoke_{entrada.model_id}.jpg"
            )
            conteo = validar.anotar_imagen(detector, imagen, salida)
            print(f"[OK] Detecciones: {conteo or 'ninguna'}")
        else:
            print("[INFO] Sin imagen: sólo se validó resolución y carga.")
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
    finally:
        detector.liberar()

    print("=" * 60)
    print("[OK] Smoke test finalizado.")


if __name__ == "__main__":
    main()
