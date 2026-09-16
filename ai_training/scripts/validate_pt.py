import argparse
import os
import sys
from pathlib import Path

# Raíz del repositorio derivada de este archivo:
# <repo>/ai_training/scripts/validate_pt.py -> parents[2] == <repo>
REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Valida el modelo PyTorch nativo (.pt) de tostadas con Ultralytics."
    )
    parser.add_argument(
        "--model",
        default=str(REPO_ROOT / "ai_training" / "runs" / "detect" / "train" / "weights" / "best.pt"),
        help="Ruta a los pesos .pt (default: %(default)s).",
    )
    parser.add_argument(
        "--images-dir",
        default=str(REPO_ROOT / "dataset" / "test" / "images"),
        help="Directorio con imágenes de prueba (default: %(default)s).",
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "data" / "test_result_pt.jpg"),
        help="Ruta de la imagen de salida (default: %(default)s).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    print("=" * 60)
    print("  VALIDANDO EL MODELO EN PYTORCH NATIVO (.PT)  ")
    print("=" * 60)

    MODEL_PATH = args.model
    TEST_IMAGES_DIR = args.images_dir
    OUTPUT_IMAGE = args.output

    if not os.path.exists(MODEL_PATH):
        print(f"[ERROR] No se encontraron los pesos en {MODEL_PATH}")
        print("        Pasá --model /ruta/a/best.pt si están en otra ubicación.")
        sys.exit(1)

    if not os.path.isdir(TEST_IMAGES_DIR):
        print(f"[ERROR] No se encontró el directorio de test: {TEST_IMAGES_DIR}")
        print("        Pasá --images-dir /ruta/a/test/images apuntando al dataset.")
        sys.exit(1)

    # Buscar imágenes de prueba
    test_files = [f for f in os.listdir(TEST_IMAGES_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    if not test_files:
        print("[ERROR] No se encontraron imágenes de prueba.")
        sys.exit(1)

    test_image_path = os.path.join(TEST_IMAGES_DIR, test_files[0])
    print(f"[OK] Imagen seleccionada para test: {test_image_path}")

    # Dependencia pesada importada recién después de validar las rutas para que
    # `--help` funcione aunque 'ultralytics' no esté instalado.
    try:
        from ultralytics import YOLO
    except ImportError:
        print("[ERROR] No se encontró el paquete 'ultralytics'.")
        print("        Instalá las dependencias de inferencia antes de correr este script.")
        sys.exit(1)

    # Cargar el modelo en PyTorch
    model = YOLO(MODEL_PATH)

    # Ejecutar inferencia
    results = model(test_image_path)

    # Asegurar que el directorio de salida existe antes de guardar
    output_dir = os.path.dirname(OUTPUT_IMAGE)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # Guardar la imagen con las cajas de detección dibujadas
    for r in results:
        r.save(filename=OUTPUT_IMAGE)
        print(f"[OK] Detección visual guardada exitosamente en: {OUTPUT_IMAGE}")

        # Imprimir un resumen de los objetos detectados
        print("\n" + "=" * 60)
        print("  RESUMEN DE DETECCIONES  ")
        print("=" * 60)
        names = r.names
        classes_detected = r.boxes.cls.tolist()
        counts = {}
        for c in classes_detected:
            name = names[int(c)]
            counts[name] = counts.get(name, 0) + 1

        for name, count in counts.items():
            print(f"   * {name}: {count} detectados")
        if not counts:
            print("   * No se detectó ninguna tostada en esta imagen.")
        print("=" * 60)


if __name__ == "__main__":
    main()
