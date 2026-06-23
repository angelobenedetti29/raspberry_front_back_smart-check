import os
import shutil
import sys
from ultralytics import YOLO

def main():
    print("=" * 60)
    print("  INICIANDO ENTRENAMIENTO NO INTERACTIVO DE YOLOv11 V2 (GPU)  ")
    print("=" * 60)
    
    # Rutas
    DATASET_YAML = r"C:\Users\angel\Downloads\Deteccion de tostadas normales.v4-tostada_normal-1.0.2.yolov11\data.yaml"
    MODEL_NAME = "yolo11n.pt"
    
    if not os.path.exists(DATASET_YAML):
        print(f"[ERROR] No se encontró el dataset en la ruta absoluta: {DATASET_YAML}")
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
        results = model.train(
            data=DATASET_YAML,
            epochs=EPOCHS,
            imgsz=IMGSZ,
            device=DEVICE,
            patience=PATIENCE,
            workers=4,
            plots=True,
            project="runs",
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
    weights_path = os.path.join("runs", "detect", "runs", "train_non_interactive", "weights", "best.pt")
    if not os.path.exists(weights_path):
        weights_path = os.path.join("runs", "detect", "train_non_interactive", "weights", "best.pt")
        if not os.path.exists(weights_path):
            weights_path = os.path.join("runs", "train_non_interactive", "weights", "best.pt")
            
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
        target_onnx_dir = os.path.join("ai_training", "models")
        os.makedirs(target_onnx_dir, exist_ok=True)
        
        dest_onnx = os.path.join(target_onnx_dir, "tostadas_v2.onnx")
        shutil.copy(onnx_file_path, dest_onnx)
        print(f"[OK] Modelo ONNX copiado a: {dest_onnx}")
        
        # Generar archivo de etiquetas en ai_training/models/tostadas_v2.names
        dest_names = os.path.join(target_onnx_dir, "tostadas_v2.names")
        classes = ['TCOK', 'TCQ']
        with open(dest_names, "w", encoding="utf-8") as f:
            for item in classes:
                f.write(item + "\n")
        print(f"[OK] Archivo de etiquetas copiado/creado en: {dest_names}")
        
        print("[OK] Proceso completo de exportación y despliegue finalizado con éxito.")
        
    except Exception as e:
        print(f"[ERROR] Durante la exportación a ONNX: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
