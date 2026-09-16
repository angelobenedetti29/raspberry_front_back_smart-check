import argparse
import os
import shutil
import sys
from pathlib import Path

# Raíz del repositorio derivada de este archivo:
# <repo>/ai_training/scripts/train_non_interactive.py -> parents[2] == <repo>
REPO_ROOT = Path(__file__).resolve().parents[2]

# Variable de entorno alternativa para no pasar --data en cada ejecución.
DATA_YAML_ENV = "TRAIN_DATA_YAML"

# Orden de clases que usa el detector en runtime. DEBE coincidir exactamente,
# línea por línea, con ai_training/models/tostadas_v2.names (TCQ, TCOK).
CLASS_NAMES = ["TCQ", "TCOK"]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Entrenamiento no interactivo de YOLOv11 (V2) y exportación a ONNX."
    )
    parser.add_argument(
        "--data",
        default=None,
        help="Ruta al data.yaml del dataset de entrenamiento. Si se omite se usa la "
             f"variable de entorno {DATA_YAML_ENV}.",
    )
    parser.add_argument(
        "--weights",
        default=None,
        help="Ruta a un best.pt ya entrenado. Si se omite se buscan los pesos del "
             "entrenamiento bajo ai_training/runs/.",
    )
    return parser.parse_args(argv)


def resolve_data_yaml(cli_value):
    value = cli_value or os.environ.get(DATA_YAML_ENV)
    if not value:
        print("[ERROR] No se especificó el dataset de entrenamiento (data.yaml).")
        print(f"        Pasá --data /ruta/al/data.yaml o definí la variable de entorno {DATA_YAML_ENV}.")
        sys.exit(1)
    if not os.path.exists(value):
        print(f"[ERROR] No se encontró el dataset en la ruta indicada: {value}")
        sys.exit(1)
    return value


def find_trained_weights():
    candidates = [
        REPO_ROOT / "runs" / "detect" / "runs" / "train_non_interactive" / "weights" / "best.pt",
        REPO_ROOT / "runs" / "detect" / "train_non_interactive" / "weights" / "best.pt",
        REPO_ROOT / "runs" / "train_non_interactive" / "weights" / "best.pt",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return str(candidate)
    return str(candidates[-1])


def main(argv=None):
    args = parse_args(argv)

    print("=" * 60)
    print("  INICIANDO ENTRENAMIENTO NO INTERACTIVO DE YOLOv11 V2 (GPU)  ")
    print("=" * 60)

    # Rutas (se valida la configuración antes de exigir 'ultralytics')
    DATASET_YAML = resolve_data_yaml(args.data)
    MODEL_NAME = "yolo11n.pt"

    # Dependencia pesada importada recién después de parsear los argumentos para que
    # `--help` funcione aunque 'ultralytics' no esté instalado.
    try:
        from ultralytics import YOLO
    except ImportError:
        print("[ERROR] No se encontró el paquete 'ultralytics'.")
        print("        Instalá las dependencias de entrenamiento antes de correr este script.")
        sys.exit(1)

    print(f"[INFO] Dataset YAML detectado en: {DATASET_YAML}")

    # Cargar modelo base
    print(f"[INFO] Cargando modelo base: {MODEL_NAME}...")
    model = YOLO(MODEL_NAME)

    # Entrenar
    # epochs=100, imgsz=640, device=0 (GPU 4060 Laptop), patience=15 (Early stopping)
    EPOCHS = 100
    IMGSZ = 640
    DEVICE = 0
    PATIENCE = 15

    print(f"[INFO] Iniciando entrenamiento por {EPOCHS} épocas en GPU (dispositivo {DEVICE}) con paciencia de {PATIENCE}...")

    try:
        model.train(
            data=DATASET_YAML,
            epochs=EPOCHS,
            imgsz=IMGSZ,
            device=DEVICE,
            patience=PATIENCE,
            workers=4,
            plots=True,
            project=str(REPO_ROOT / "runs"),
            name="train_non_interactive"
        )
        print("[OK] Entrenamiento completado con éxito.")
    except Exception as e:
        print(f"[ERROR] Durante el entrenamiento: {e}")
        sys.exit(1)

    # Exportación
    print("\n" + "=" * 60)
    print("  EXPORTANDO MODELO A ONNX (V2)  ")
    print("=" * 60)

    # YOLOv11 suele estructurar como runs/detect/runs/train_non_interactive o runs/detect/train_non_interactive
    weights_path = args.weights or find_trained_weights()
    if not os.path.exists(weights_path):
        print(f"[ERROR] No se encontraron los pesos entrenados en ninguna de las rutas esperadas. Último intento: {weights_path}")
        sys.exit(1)

    try:
        print(f"[INFO] Cargando los mejores pesos desde: {weights_path}")
        trained_model = YOLO(weights_path)

        print("[INFO] Exportando modelo a formato ONNX...")
        # Exportar a ONNX. Ultralytics usará onnx, onnxslim si están instalados.
        onnx_file_path = trained_model.export(format="onnx")
        print(f"[OK] Modelo exportado por YOLO a: {onnx_file_path}")

        # Copiar modelos resultantes al backend con el nombre tostadas_v2.onnx
        target_onnx_dir = REPO_ROOT / "ai_training" / "models"
        os.makedirs(target_onnx_dir, exist_ok=True)

        dest_onnx = target_onnx_dir / "tostadas_v2.onnx"
        shutil.copy(onnx_file_path, str(dest_onnx))
        print(f"[OK] Modelo ONNX copiado a: {dest_onnx}")

        # Generar archivo de etiquetas en ai_training/models/tostadas_v2.names
        # IMPORTANTE: el orden de CLASS_NAMES debe coincidir con el que espera el
        # detector en runtime (backend/infrastructure/ai). No invertir TCQ/TCOK.
        dest_names = target_onnx_dir / "tostadas_v2.names"
        with open(dest_names, "w", encoding="utf-8") as f:
            for item in CLASS_NAMES:
                f.write(item + "\n")
        print(f"[OK] Archivo de etiquetas copiado/creado en: {dest_names}")

        print("[OK] Proceso completo de exportación y despliegue finalizado con éxito.")

    except Exception as e:
        print(f"[ERROR] Durante la exportación a ONNX: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
