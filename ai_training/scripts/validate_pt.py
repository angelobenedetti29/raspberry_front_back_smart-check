import os
from ultralytics import YOLO

print("=" * 60)
print("  VALIDANDO EL MODELO EN PYTORCH NATIVO (.PT)  ")
print("=" * 60)

MODEL_PATH = "runs/detect/train/weights/best.pt"
TEST_IMAGES_DIR = "dataset/test/images"
OUTPUT_IMAGE = "data/test_result_pt.jpg"

if not os.path.exists(MODEL_PATH):
    print(f"[❌] Error: No se encontraron los pesos en {MODEL_PATH}")
    exit(1)

if not os.path.exists(TEST_IMAGES_DIR):
    print(f"[❌] Error: No se encontró el directorio de test: {TEST_IMAGES_DIR}")
    exit(1)

# Buscar imágenes de prueba
test_files = [f for f in os.listdir(TEST_IMAGES_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
if not test_files:
    print("[❌] Error: No se encontraron imágenes de prueba.")
    exit(1)

test_image_path = os.path.join(TEST_IMAGES_DIR, test_files[0])
print(f"[✔] Imagen seleccionada para test: {test_image_path}")

# Cargar el modelo en PyTorch
model = YOLO(MODEL_PATH)

# Ejecutar inferencia
results = model(test_image_path)

# Guardar la imagen con las cajas de detección dibujadas
for r in results:
    r.save(filename=OUTPUT_IMAGE)
    print(f"[✔] Detección visual guardada exitosamente en: {OUTPUT_IMAGE}")
    
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
