import os
import sys
import subprocess
import numpy as np

def run_command(command_list):
    print(f"[CMD] Ejecutando: {' '.join(command_list)}")
    res = subprocess.run(command_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(f"[ERROR] El comando falló con código {res.returncode}")
        print(f"STDOUT:\n{res.stdout}")
        print(f"STDERR:\n{res.stderr}")
        return False
    print("[OK] Comando completado con éxito.")
    return True

def main():
    print("=" * 60)
    print("      COMPILADOR AUTOMÁTICO DE MODELO TOSTADAS V2 A HEF")
    print("=" * 60)

    # Agregar el directorio bin del intérprete de Python al PATH para que subprocess encuentre 'hailo'
    sys_exe_dir = os.path.dirname(sys.executable)
    if sys_exe_dir not in os.environ["PATH"]:
        os.environ["PATH"] = sys_exe_dir + os.path.pathsep + os.environ["PATH"]

    # Definir rutas relativas/absolutas
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # ai_training/
    models_dir = os.path.join(base_dir, "models")
    
    onnx_path = os.path.join(models_dir, "tostadas_v2.onnx")
    calib_path = os.path.join(models_dir, "calib_dataset.npy")
    
    har_path = os.path.join(models_dir, "tostadas_v2.har")
    quantized_har_path = os.path.join(models_dir, "tostadas_v2_quantized.har")
    hef_path = os.path.join(models_dir, "tostadas_v2.hef")

    # Validar que los archivos de origen existen
    if not os.path.exists(onnx_path):
        print(f"[ERROR] No se encuentra el archivo ONNX en: {onnx_path}")
        return
        
    if not os.path.exists(calib_path):
        print(f"[ERROR] No se encuentra el dataset de calibración en: {calib_path}")
        print("Asegúrate de haber corrido 'prepare_calibration.py' primero.")
        return

    # --- PASO 1: Parsear el modelo ONNX a HAR ---
    print("\n[PASO 1] Parseando archivo ONNX a formato HAR...")
    # comando: hailo parser onnx tostadas_v2.onnx --hw-arch hailo8l --net-name tostadas_v2 --har-path tostadas_v2.har -y
    parse_cmd = [
        "hailo", "parser", "onnx",
        onnx_path,
        "--hw-arch", "hailo8l",
        "--net-name", "tostadas_v2",
        "--har-path", har_path,
        "--end-node-names",
        "/model.23/cv2.0/cv2.0.2/Conv",
        "/model.23/cv3.0/cv3.0.2/Conv",
        "/model.23/cv2.1/cv2.1.2/Conv",
        "/model.23/cv3.1/cv3.1.2/Conv",
        "/model.23/cv2.2/cv2.2.2/Conv",
        "/model.23/cv3.2/cv3.2.2/Conv"
    ]
    if not run_command(parse_cmd):
        return

    if not os.path.exists(har_path):
        print(f"[ERROR] No se generó el archivo HAR esperado en: {har_path}")
        return

    # --- PASO 2: Cargar HAR y Cuantizar ---
    print("\n[PASO 2] Iniciando optimización/cuantización a INT8...")
    try:
        from hailo_sdk_client import ClientRunner
    except ImportError:
        print("[ERROR] No se pudo importar 'hailo_sdk_client'.")
        print("Asegúrate de estar ejecutando este script dentro del entorno virtual de Hailo (hailo_env).")
        return

    try:
        print(f"[INFO] Cargando HAR: {har_path}")
        runner = ClientRunner(har=har_path)
        
        print(f"[INFO] Cargando dataset de calibración: {calib_path}")
        calib_data = np.load(calib_path)
        
        print("[INFO] Ejecutando optimización (esto puede tardar unos minutos)...")
        runner.optimize(calib_data)
        
        print(f"[INFO] Guardando HAR optimizado en: {quantized_har_path}")
        runner.save_har(quantized_har_path)
        print("[OK] Optimización completada.")
    except Exception as e:
        print(f"[ERROR] Durante el proceso de cuantización: {e}")
        return

    # --- PASO 3: Compilar a HEF ---
    print("\n[PASO 3] Compilando el modelo a formato HEF final para Hailo-8L...")
    # comando: hailo compiler --hw-arch hailo8l tostadas_v2_quantized.har --output-dir models_dir --model-script tostadas_v2.alls
    alls_path = os.path.join(models_dir, "tostadas_v2.alls")
    compile_cmd = [
        "hailo", "compiler",
        "--hw-arch", "hailo8l",
        quantized_har_path,
        "--output-dir", models_dir,
        "--model-script", alls_path
    ]
    if not run_command(compile_cmd):
        return

    if not os.path.exists(hef_path):
        print(f"[ERROR] No se pudo generar el archivo HEF final en: {hef_path}")
        return

    print("\n" + "=" * 60)
    print("  ¡PROCESO DE MIGRACIÓN COMPLETADO CON ÉXITO!")
    print(f"  Modelo compilado: {hef_path}")
    print("=" * 60)

if __name__ == "__main__":
    main()
