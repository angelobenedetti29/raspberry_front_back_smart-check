import os
import sys
import shutil

# 1. Agregar la carpeta de librerías locales al path de Python para que pueda importar 'onnx'
ONNX_LIB_PATH = os.path.abspath("onnx_lib")
if ONNX_LIB_PATH not in sys.path:
    sys.path.insert(0, ONNX_LIB_PATH)

print("=" * 60)
print("  INICIANDO EXPORTACION MANUAL A ONNX CON PATH CORREGIDO  ")
print("=" * 60)

try:
    import onnx
    import onnxslim
    print(f"[OK] Libreria ONNX cargada con exito desde: {ONNX_LIB_PATH}")
except ImportError as e:
    print(f"[ERROR] Al importar ONNX desde la libreria local: {e}")
    sys.exit(1)

# 2. Cargar modelo y exportar
from ultralytics import YOLO

WEIGHTS_PATH = "runs/detect/train/weights/best.pt"
if not os.path.exists(WEIGHTS_PATH):
    print(f"[ERROR] No se encontraron los pesos en: {WEIGHTS_PATH}")
    sys.exit(1)

try:
    print(f"[INFO] Cargando pesos desde {WEIGHTS_PATH}...")
    model = YOLO(WEIGHTS_PATH)
    
    print("[INFO] Exportando a formato ONNX...")
    # Ejecutar la exportación usando nuestro sys.path modificado
    onnx_path = model.export(format="onnx")
    print(f"[OK] Modelo exportado exitosamente por YOLO a: {onnx_path}")
    
    # 3. Copiar archivo a la raíz
    dest_model = "tostadas.onnx"
    shutil.copy(onnx_path, dest_model)
    print(f"[OK] Modelo copiado a la raiz como: {dest_model}")
    
    # 4. Crear archivo de nombres de clase
    dest_names = "data/tostadas.names"
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
