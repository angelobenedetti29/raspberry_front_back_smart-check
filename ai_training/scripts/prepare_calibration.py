import os
import cv2
import numpy as np
import glob

def main():
    print("=" * 60)
    print("        CREACIÓN DEL DATASET DE CALIBRACIÓN HAILO")
    print("=" * 60)

    # 1. Definir la ruta de las imágenes
    default_path = r"C:\Users\angel\Downloads\Deteccion de tostadas normales.v4-tostada_normal-1.0.2.yolov11\train\images"
    
    if not os.path.exists(default_path):
        print(f"[WARN] No se encontró la ruta por defecto: {default_path}")
        img_dir = input("Introduce la ruta de la carpeta de imágenes: ").strip()
    else:
        img_dir = default_path

    if not os.path.exists(img_dir):
        print(f"[ERROR] La carpeta de imágenes no existe: {img_dir}")
        return

    # Buscar imágenes JPG, JPEG y PNG
    search_patterns = [os.path.join(img_dir, "*.jpg"), os.path.join(img_dir, "*.jpeg"), os.path.join(img_dir, "*.png")]
    img_paths = []
    for pattern in search_patterns:
        img_paths.extend(glob.glob(pattern))

    print(f"[INFO] Se encontraron {len(img_paths)} imágenes en {img_dir}")
    
    if not img_paths:
        print("[ERROR] No se encontraron imágenes en el directorio especificado.")
        return

    # Limitar a un lote óptimo para la calibración (entre 50 y 100 imágenes)
    max_images = min(100, len(img_paths))
    selected_paths = img_paths[:max_images]
    print(f"[INFO] Seleccionando {max_images} imágenes para la calibración...")

    images = []
    success_count = 0

    for i, path in enumerate(selected_paths):
        img = cv2.imread(path)
        if img is not None:
            # Redimensionar a 640x640 como requiere YOLOv8/v11
            img = cv2.resize(img, (640, 640))
            # Convertir de BGR (OpenCV) a RGB
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            images.append(img)
            success_count += 1
            if (i + 1) % 10 == 0 or (i + 1) == max_images:
                print(f"  Procesadas {i + 1}/{max_images} imágenes...")
        else:
            print(f"  [WARN] No se pudo leer la imagen: {path}")

    if not images:
        print("[ERROR] No se pudo procesar ninguna imagen con éxito.")
        return

    # Convertir a NumPy array de tipo uint8
    calib_dataset = np.array(images, dtype=np.uint8)
    
    # Directorio de salida
    output_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # En la raíz de ai_training o en el directorio actual
    output_path = os.path.join(output_dir, "models", "calib_dataset.npy")
    
    # Asegurar que la carpeta de destino existe
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Guardar archivo .npy
    np.save(output_path, calib_dataset)
    
    print("\n" + "=" * 60)
    print(f"[OK] ¡Dataset de calibración creado con éxito!")
    print(f"[OK] Archivo guardado en: {output_path}")
    print(f"[OK] Dimensiones del dataset: {calib_dataset.shape}")
    print("=" * 60)

if __name__ == "__main__":
    main()
