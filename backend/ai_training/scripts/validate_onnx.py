"""CLI: valida un modelo del catálogo sobre una imagen (motor real del pipeline).

    python -m backend.ai_training.scripts.validate_onnx --imagen /ruta/frame.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.ai_training import rutas, validar
from backend.config import ConfigError
from backend.inference.detector import ErrorCargaModelo


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Corre el detector real sobre una imagen y guarda el "
        "resultado anotado con el resumen por clase."
    )
    parser.add_argument(
        "--modelo",
        default=None,
        help="model_id del catálogo. Default: models.default_model_id.",
    )
    parser.add_argument("--imagen", default=None, help="Imagen de prueba.")
    parser.add_argument(
        "--images-dir",
        default=None,
        help="Carpeta de imágenes; se usa la primera si no se pasa --imagen.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Salida. Default: multimedia/output/validacion_<modelo>.jpg.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        cfg = rutas.cargar_config()
        imagen = validar.resolver_imagen(args.imagen, args.images_dir)
        detector, entrada = validar.construir_detector_catalogo(cfg, args.modelo)
    except (ConfigError, FileNotFoundError, ValueError, ErrorCargaModelo) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    salida = (
        Path(args.output).expanduser()
        if args.output
        else validar.SALIDA_DIR / f"validacion_{entrada.model_id}.jpg"
    )

    print("=" * 60)
    print("  VALIDACIÓN DE MODELO (motor real del pipeline)")
    print("=" * 60)
    print(f"[INFO] Modelo:  {entrada.model_id} ({entrada.label})")
    print(f"[INFO] Motor:   {detector.motor.etiqueta}")
    print(f"[INFO] Archivo: {detector.modelo_path}")
    print(f"[INFO] Clases:  {', '.join(detector.nombres)}")
    print(f"[INFO] Imagen:  {imagen}")

    try:
        conteo = validar.anotar_imagen(detector, imagen, salida)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)
    finally:
        detector.liberar()

    print("=" * 60)
    print("  RESUMEN DE DETECCIONES")
    print("=" * 60)
    for nombre, cantidad in sorted(conteo.items()):
        print(f"   * {nombre}: {cantidad}")
    if not conteo:
        print("   * Sin detecciones.")
    print("=" * 60)


if __name__ == "__main__":
    main()
