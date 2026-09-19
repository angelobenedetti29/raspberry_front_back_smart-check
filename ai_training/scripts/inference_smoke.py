import os
import cv2
import random
from backend.infrastructure.ai.yolo_detector import YoloDetector

def main():
    print("=" * 60)
    print("      PRUEBA DE INFERENCIA DEL MODELO TOSTADAS V2")
    print("=" * 60)
    
    # 1. Inicializar el detector (cargará tostadas_v2.onnx por defecto)
    try:
        detector = YoloDetector()
        print(f"[OK] Modelo cargado: {detector.model_path}")
        print(f"[OK] Clases registradas: {detector.names}")
    except Exception as e:
        print(f"[ERROR] No se pudo inicializar el detector: {e}")
        return

    # 2. Definir imagen de prueba
    image_path = os.path.join("multimedia", "images", "tostadas_test.jpg")
    output_path = os.path.join("multimedia", "output", "test_result_inference.jpg")
    
    if not os.path.exists(image_path):
        print(f"[ERROR] No se encuentra la imagen de prueba en: {image_path}")
        return

    # 3. Leer la imagen
    image = cv2.imread(image_path)
    if image is None:
        print(f"[ERROR] No se pudo leer la imagen {image_path}")
        return
        
    print(f"[INFO] Ejecutando inferencia sobre {image_path}...")
    
    # 4. Obtener detecciones
    try:
        results = detector.detect(image_path)
    except Exception as e:
        print(f"[ERROR] Durante la inferencia: {e}")
        return

    print(f"[OK] Inferencia completada. Se encontraron {len(results)} detecciones:")
    
    # Generar colores aleatorios para cada clase de forma reproducible
    random.seed(42)
    colors = [[random.randint(0, 255) for _ in range(3)] for _ in detector.names]

    # 5. Dibujar resultados en la imagen
    for i, res in enumerate(results):
        label = res.label
        confidence = res.confidence
        bbox = res.bbox # (left, top, width, height)
        left, top, width, height = bbox
        
        print(f"  - [{i+1}] {label} ({confidence:.2f}) en [x={left}, y={top}, w={width}, h={height}]")
        
        # Obtener clase para el color
        try:
            class_idx = detector.names.index(label)
        except ValueError:
            class_idx = 0
            
        color = colors[class_idx]
        
        # Dibujar rectangulo
        cv2.rectangle(image, (left, top), (left + width, top + height), color, 2)
        
        # Escribir etiqueta
        caption = f"{label} {confidence:.2f}"
        cv2.putText(image, caption, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    # 6. Guardar imagen resultante
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cv2.imwrite(output_path, image)
    print(f"[OK] Imagen con detecciones guardada en: {output_path}")
    print("=" * 60)

if __name__ == "__main__":
    main()
