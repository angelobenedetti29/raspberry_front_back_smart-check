import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path

# Raíz del repositorio derivada de este archivo:
# <repo>/ai_training/scripts/export_onnx.py -> parents[2] == <repo>
REPO_ROOT = Path(__file__).resolve().parents[2]

# Pesos por defecto, anclados a la raíz del repo (ver guia_migracion_hailo.md).
DEFAULT_WEIGHTS = REPO_ROOT / "ai_training" / "runs" / "detect" / "train" / "weights" / "best.pt"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Exportación manual de best.pt a ONNX con resolución de rutas desde el repo root."
    )
    parser.add_argument(
        "--weights",
        default=str(DEFAULT_WEIGHTS),
        help="Ruta al archivo best.pt a exportar (default: %(default)s).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    print("=" * 60)
    print("  INICIANDO EXPORTACION MANUAL A ONNX CON PATH CORREGIDO  ")
    print("=" * 60)

    # 1. Verificar que las librerías ONNX están disponibles sin importarlas
    if importlib.util.find_spec("onnx") is None or importlib.util.find_spec("onnxslim") is None:
        print("[ERROR] No se encontraron las librerias 'onnx' y/o 'onnxslim'.")
        sys.exit(1)
    print("[OK] Librerias ONNX cargadas con exito.")

    # 2. Cargar modelo y exportar
    from ultralytics import YOLO

    WEIGHTS_PATH = args.weights
    if not os.path.exists(WEIGHTS_PATH):
        print(f"[ERROR] No se encontraron los pesos en: {WEIGHTS_PATH}")
        print("        Verificá la ruta con --weights o entrená primero el modelo.")
        sys.exit(1)

    try:
        print(f"[INFO] Cargando pesos desde {WEIGHTS_PATH}...")
        model = YOLO(WEIGHTS_PATH)

        print("[INFO] Exportando a formato ONNX...")
        # Ejecutar la exportación
        onnx_path = model.export(format="onnx")
        print(f"[OK] Modelo exportado exitosamente por YOLO a: {onnx_path}")

        # 3. Copiar archivo a la raíz (anclado al repo root)
        dest_model = REPO_ROOT / "tostadas.onnx"
        shutil.copy(onnx_path, str(dest_model))
        print(f"[OK] Modelo copiado a la raiz como: {dest_model}")

        # 4. Crear archivo de nombres de clase (anclado al repo root)
        dest_names = REPO_ROOT / "data" / "tostadas.names"
        os.makedirs(dest_names.parent, exist_ok=True)
        clases = ['Tostada Quemada', 'tostadas ok']
        with open(dest_names, "w", encoding="utf-8") as f:
            for clase in clases:
                f.write(clase + "\n")
        print(f"[OK] Archivo de etiquetas creado en: {dest_names}")

        print("\n" + "=" * 60)
        print("  EXPORTACION ONNX COMPLETADA CON EXITO!  ")
        print("=" * 60)

    except Exception as e:
        print(f"[ERROR] Durante el proceso de exportacion: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
