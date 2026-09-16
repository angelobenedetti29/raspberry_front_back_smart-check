import argparse
import glob
import os
import sys
from pathlib import Path

# Raíz del repositorio derivada de este archivo:
# <repo>/ai_training/scripts/prepare_calibration.py -> parents[2] == <repo>
REPO_ROOT = Path(__file__).resolve().parents[2]

# Variable de entorno alternativa para no pasar --images-dir en cada ejecución.
IMAGES_DIR_ENV = "CALIB_IMAGES_DIR"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Genera ai_training/models/calib_dataset.npy para la calibración de Hailo."
    )
    parser.add_argument(
        "--images-dir",
        default=None,
        help="Carpeta con las imágenes de calibración. Si se omite se usa la "
             f"variable de entorno {IMAGES_DIR_ENV}.",
    )
    return parser.parse_args(argv)


def resolve_images_dir(cli_value):
    value = cli_value or os.environ.get(IMAGES_DIR_ENV)
    if not value:
        print("[ERROR] No se especificó el directorio de imágenes de calibración.")
        print(f"        Pasá --images-dir /ruta/a/train/images o definí la variable de entorno {IMAGES_DIR_ENV}.")
        sys.exit(1)
    if not os.path.isdir(value):
        print(f"[ERROR] La carpeta de imágenes no existe: {value}")
        sys.exit(1)
    return value


def main(argv=None):
    args = parse_args(argv)

    print("=" * 60)
    print("        CREACIÓN DEL DATASET DE CALIBRACIÓN HAILO")
    print("=" * 60)

    # Dependencias pesadas importadas recién después de parsear los argumentos para
    # que `--help` funcione aunque OpenCV/NumPy no estén instalados.
    import cv2
    import numpy as np

    # 1. Definir la ruta de las imágenes (CLI o variable de entorno, nunca interactivo)
    img_dir = resolve_images_dir(args.images_dir)

    # Buscar imágenes JPG, JPEG y PNG
    search_patterns = [os.path.join(img_dir, "*.jpg"), os.path.join(img_dir, "*.jpeg"), os.path.join(img_dir, "*.png")]
    img_paths = []
    for pattern in search_patterns:
        img_paths.extend(glob.glob(pattern))

    print(f"[INFO] Se encontraron {len(img_paths)} imágenes en {img_dir}")

    if not img_paths:
        print("[ERROR] No se encontraron imágenes en el directorio especificado.")
        sys.exit(1)

    # Limitar a un lote óptimo para la calibración (entre 50 y 500 imágenes)
    max_images = min(500, len(img_paths))
    selected_paths = img_paths[:max_images]
    print(f"[INFO] Seleccionando {max_images} imágenes para la calibración...")

    images = []

    for i, path in enumerate(selected_paths):
        img = cv2.imread(path)
        if img is not None:
            # Redimensionar a 640x640 como requiere YOLOv8/v11
            img = cv2.resize(img, (640, 640))
            # Convertir de BGR (OpenCV) a RGB
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            images.append(img)
            if (i + 1) % 10 == 0 or (i + 1) == max_images:
                print(f"  Procesadas {i + 1}/{max_images} imágenes...")
        else:
            print(f"  [WARN] No se pudo leer la imagen: {path}")

    if not images:
        print("[ERROR] No se pudo procesar ninguna imagen con éxito.")
        sys.exit(1)

    # Convertir a NumPy array de tipo uint8
    calib_dataset = np.array(images, dtype=np.uint8)

    # Directorio de salida anclado a la raíz del repo (independiente del CWD)
    output_path = REPO_ROOT / "ai_training" / "models" / "calib_dataset.npy"

    # Asegurar que la carpeta de destino existe
    os.makedirs(output_path.parent, exist_ok=True)

    # Guardar archivo .npy
    np.save(str(output_path), calib_dataset)

    print("\n" + "=" * 60)
    print(f"[OK] ¡Dataset de calibración creado con éxito!")
    print(f"[OK] Archivo guardado en: {output_path}")
    print(f"[OK] Dimensiones del dataset: {calib_dataset.shape}")
    print("=" * 60)


if __name__ == "__main__":
    main()
