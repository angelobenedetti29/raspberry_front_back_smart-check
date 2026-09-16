import argparse
import os
import random
import sys
from pathlib import Path

# Raíz del repositorio derivada de este archivo:
# <repo>/ai_training/scripts/validate.py -> parents[2] == <repo>
REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Valida el modelo ONNX de tostadas (v2) con OpenCV DNN."
    )
    parser.add_argument(
        "--model",
        default=str(REPO_ROOT / "ai_training" / "models" / "tostadas_v2.onnx"),
        help="Ruta al modelo ONNX (default: %(default)s).",
    )
    parser.add_argument(
        "--names",
        default=str(REPO_ROOT / "ai_training" / "models" / "tostadas_v2.names"),
        help="Ruta al archivo de nombres de clase (default: %(default)s).",
    )
    parser.add_argument(
        "--images-dir",
        default=str(REPO_ROOT / "dataset" / "test" / "images"),
        help="Directorio con imágenes de prueba (default: %(default)s).",
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "data" / "test_result.jpg"),
        help="Ruta de la imagen de salida (default: %(default)s).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    print("=" * 60)
    print("  VALIDANDO EL MODELO ENTRENADO - PRUEBA DE INFERENCIA  ")
    print("=" * 60)

    # Dependencias pesadas importadas recién después de parsear los argumentos para
    # que `--help` funcione aunque OpenCV/NumPy no estén instalados.
    import cv2
    import numpy as np

    # 1. Definir rutas (ancladas al repo root por defecto, override por CLI)
    MODEL_PATH = args.model
    NAMES_PATH = args.names
    TEST_IMAGES_DIR = args.images_dir
    OUTPUT_IMAGE = args.output

    if not os.path.exists(MODEL_PATH):
        print(f"[ERROR] No se encontro el modelo entrenado en {MODEL_PATH}")
        print("        Pasá --model /ruta/a/tostadas_v2.onnx si está en otra ubicación.")
        sys.exit(1)

    if not os.path.exists(NAMES_PATH):
        print(f"[ERROR] No se encontro el archivo de etiquetas en {NAMES_PATH}")
        print("        Pasá --names /ruta/a/tostadas_v2.names si está en otra ubicación.")
        sys.exit(1)

    # 2. Cargar nombres de clases y generar colores
    with open(NAMES_PATH, "r", encoding="utf-8") as f:
        NAMES = [line.strip() for line in f.readlines()]
    COLORS = [[random.randint(0, 255) for _ in range(3)] for _ in NAMES]

    print(f"[OK] Clases cargadas: {NAMES}")

    # 3. Cargar el modelo ONNX en OpenCV DNN
    print("[INFO] Cargando el modelo ONNX en OpenCV DNN...")
    net = cv2.dnn.readNet(MODEL_PATH)
    IMAGE_SIZE = 640

    # 4. Buscar una imagen de prueba en el dataset
    if not os.path.isdir(TEST_IMAGES_DIR):
        print(f"[ERROR] No se encontro el directorio de imagenes de prueba: {TEST_IMAGES_DIR}")
        print("        Pasá --images-dir /ruta/a/test/images apuntando al dataset.")
        sys.exit(1)

    test_files = [f for f in os.listdir(TEST_IMAGES_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    if not test_files:
        print("[ERROR] No se encontraron imagenes de prueba en la carpeta de test.")
        sys.exit(1)

    # Elegir la primera imagen disponible
    test_image_name = test_files[0]
    test_image_path = os.path.join(TEST_IMAGES_DIR, test_image_name)
    print(f"[OK] Imagen seleccionada para validacion: {test_image_path}")

    # 5. Cargar y preparar la imagen
    image = cv2.imread(test_image_path)
    if image is None:
        print("[ERROR] Al cargar la imagen con OpenCV.")
        sys.exit(1)

    image_height, image_width, _ = image.shape
    blob = cv2.dnn.blobFromImage(image, 1/255.0, (IMAGE_SIZE, IMAGE_SIZE), swapRB=True, crop=False)

    # 6. Realizar la prediccion
    net.setInput(blob)
    preds = net.forward()
    preds = preds.transpose((0, 2, 1))

    # 7. Procesar las detecciones
    class_ids, confs, boxes = [], [], []
    x_factor = image_width / IMAGE_SIZE
    y_factor = image_height / IMAGE_SIZE
    rows = preds[0].shape[0]

    for i in range(rows):
        row = preds[0][i]
        classes_score = row[4:]
        # np.argmax no depende de la versión de OpenCV. cv2.minMaxLoc devuelve el
        # pico como Point(x, y) y para un array 1D su semántica cambia entre 4.x y
        # 5.x, por lo que leer max_idx[1] fijaba la clase 0 en OpenCV 5.
        class_id = int(np.argmax(classes_score))

        # Umbral de confianza del 25% para visualización
        if classes_score[class_id] > 0.25:
            confs.append(float(classes_score[class_id]))
            class_ids.append(class_id)

            # Coordenadas
            x, y, w, h = row[0].item(), row[1].item(), row[2].item(), row[3].item()
            left = int((x - 0.5 * w) * x_factor)
            top = int((y - 0.5 * h) * y_factor)
            width = int(w * x_factor)
            height = int(h * y_factor)
            boxes.append([left, top, width, height])

    # Aplicar Non-Maximum Suppression (NMS)
    indexes = cv2.dnn.NMSBoxes(boxes, confs, 0.25, 0.45)

    print(f"[INFO] Se detectaron {len(indexes)} objetos potenciales.")

    # 8. Dibujar detecciones y reportar
    detections_count = {name: 0 for name in NAMES}
    for i in indexes:
        box = boxes[i]
        class_id = class_ids[i]
        score = confs[i]
        label = NAMES[class_id]
        detections_count[label] += 1

        left, top, width, height = box
        cv2.rectangle(image, (left, top), (left + width, top + height), COLORS[class_id], 2)

        caption = f"{label} {score:.2f}"
        cv2.putText(image, caption, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLORS[class_id], 2)
        print(f"    - Detectado '{label}' con confianza {score:.2f} en x:{left}, y:{top}")

    # Guardar la imagen resultante
    output_dir = os.path.dirname(OUTPUT_IMAGE)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    cv2.imwrite(OUTPUT_IMAGE, image)
    print(f"[OK] Imagen con detecciones guardada con exito en: {OUTPUT_IMAGE}")

    print("\n" + "=" * 60)
    print("  REPORTE FINAL DE DETECCIONES EN LA IMAGEN DE PRUEBA  ")
    print("=" * 60)
    for k, v in detections_count.items():
        print(f"   * {k}: {v} detectados")
    print("=" * 60)


if __name__ == "__main__":
    main()
