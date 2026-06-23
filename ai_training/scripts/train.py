import os
import shutil
import sys
import argparse

# 1. Definir argumentos de línea de comandos para permitir limpieza manual controlada
parser = argparse.ArgumentParser(description="Script de entrenamiento y limpieza para YOLO - Detección de Tostadas")
parser.add_argument("--clean-only", action="store_true", help="Solo realiza la limpieza de archivos temporales (dataset/ y runs/) y sale")
args = parser.parse_args()

MODEL_NAME = "yolo11n.pt"

# Definir función de limpieza reutilizable
def realizar_limpieza():
    print("\n" + "=" * 60)
    print("  INICIANDO LIMPIEZA DE ARCHIVOS TEMPORALES  ")
    print("=" * 60)
    
    confirmacion = input("¿Estás seguro de que deseas eliminar las imágenes del dataset y las carpetas de entrenamiento? (s/n): ")
    if confirmacion.lower() != 's':
        print("[i] Limpieza cancelada. Los archivos permanecen intactos.")
        return

    # Eliminar carpeta dataset/
    if os.path.exists("dataset"):
        shutil.rmtree("dataset")
        print("[✔] Carpeta temporal 'dataset/' eliminada.")
    else:
        print("[i] La carpeta 'dataset/' ya no existe.")
        
    # Eliminar carpeta runs/
    if os.path.exists("runs"):
        shutil.rmtree("runs")
        print("[✔] Carpeta temporal 'runs/' eliminada.")
    else:
        print("[i] La carpeta 'runs/' ya no existe.")
        
    # Eliminar el archivo de pesos base descargado
    if os.path.exists(MODEL_NAME):
        os.remove(MODEL_NAME)
        print(f"[✔] Archivo base '{MODEL_NAME}' eliminado.")
        
    print("[✔] Limpieza completada con éxito.")

# Si el usuario solo quiere limpiar los archivos temporales
if args.clean_only:
    realizar_limpieza()
    sys.exit(0)

# ==========================================
# FLUJO PRINCIPAL DE ENTRENAMIENTO
# ==========================================

print("=" * 60)
print("  PREPARANDO ENTRENAMIENTO DE YOLO - DETECCIÓN DE TOSTADAS  ")
print("=" * 60)

# Verificar dependencias
try:
    from ultralytics import YOLO
    print("[✔] Librería 'ultralytics' detectada correctamente.")
except ImportError:
    print("[❌] La librería 'ultralytics' no está instalada.")
    print("Por favor, ejecuta el siguiente comando en tu terminal para instalarla:")
    print("    pip install ultralytics")
    sys.exit(1)

# Definir rutas del dataset y configuración
DATA_YAML = os.path.abspath("dataset/data.yaml")
if not os.path.exists(DATA_YAML):
    print(f"[❌] No se encontró el archivo de configuración del dataset en: {DATA_YAML}")
    print("Asegúrate de haber copiado la carpeta del dataset correctamente.")
    sys.exit(1)

print(f"[✔] Configuración del dataset encontrada en: {DATA_YAML}")

# Cargar el modelo preentrenado (YOLO11 Nano)
print(f"[i] Cargando modelo base preentrenado: {MODEL_NAME}...")
model = YOLO(MODEL_NAME)

# Iniciar el entrenamiento
# epochs: Número de iteraciones completas sobre el dataset (50 para empezar)
# imgsz: Tamaño de la imagen (640 es el estándar de YOLO)
EPOCHS = 50
IMGSZ = 640
DEVICE = "cpu" # 'cpu' o 0 (para GPU NVIDIA)

print("\n" + "=" * 60)
print(f"  INICIANDO ENTRENAMIENTO ({EPOCHS} épocas en {DEVICE})  ")
print("=" * 60)
print("Esto puede tardar varios minutos dependiendo de tu procesador (CPU/GPU)...")

try:
    results = model.train(
        data=DATA_YAML,
        epochs=EPOCHS,
        imgsz=IMGSZ,
        device=DEVICE,
        workers=2,
        plots=True
    )
    print("\n[✔] ¡Entrenamiento completado con éxito!")
except Exception as e:
    print(f"\n[❌] Ocurrió un error durante el entrenamiento: {e}")
    sys.exit(1)

# Exportar el modelo entrenado a formato ONNX para usarlo con OpenCV DNN (detection.py)
print("\n" + "=" * 60)
print("  EXPORTANDO MODELO A FORMATO ONNX  ")
print("=" * 60)
print("[i] Convirtiendo el mejor modelo guardado (best.pt) a formato ONNX...")

try:
    # El modelo se guarda automáticamente en runs/detect/train/weights/best.pt
    onnx_path = model.export(format="onnx")
    print(f"[✔] Modelo exportado exitosamente a ONNX en: {onnx_path}")
    
    # Copiar los archivos resultantes al directorio principal para fácil acceso
    dest_model = "tostadas.onnx"
    shutil.copy(onnx_path, dest_model)
    print(f"[✔] Copiado modelo ONNX final a la raíz del proyecto como: {dest_model}")
    
    # Crear el archivo de nombres de clases (data/tostadas.names) automáticamente
    dest_names = "data/tostadas.names"
    clases = ['Tostada Quemada', 'tostadas ok']
    with open(dest_names, "w", encoding="utf-8") as f:
        for clase in clases:
            f.write(clase + "\n")
    print(f"[✔] Archivo de etiquetas creado en: {dest_names}")

    print("\n" + "=" * 60)
    print("  ¡TODO LISTO PARA VALIDAR TU MODELO!  ")
    print("=" * 60)
    print("Por seguridad, NO hemos borrado tus imágenes ni archivos temporales aún.")
    print("Primero, valida que tu modelo funcione en tiempo real ejecutando:")
    print(f"\npython detection.py --model {dest_model} --names {dest_names} --source <ruta_a_tu_video_o_imagen>")
    print("\nEjemplo con un video:")
    print(f"python detection.py --model {dest_model} --names {dest_names} --source data/videos/road.mp4")
    
    print("\n" + "-" * 60)
    print("Una vez que hayas validado que todo funciona y desees liberar espacio,")
    print("puedes ejecutar el siguiente comando en tu terminal para borrar carpetas temporales:")
    print("python train.py --clean-only")
    print("=" * 60)

except Exception as e:
    print(f"[❌] Error al exportar o copiar los resultados: {e}")
