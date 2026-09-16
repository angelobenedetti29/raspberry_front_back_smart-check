import argparse
import os
import subprocess
import sys
from pathlib import Path

# Raíz del repositorio derivada de este archivo:
# <repo>/ai_training/scripts/compile_hailo.py -> parents[2] == <repo>
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS_DIR = REPO_ROOT / "ai_training" / "models"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Compila ai_training/models/tostadas_v2.onnx a HEF para Hailo-8L."
    )
    parser.add_argument(
        "--models-dir",
        default=str(DEFAULT_MODELS_DIR),
        help="Directorio con tostadas_v2.onnx, calib_dataset.npy y tostadas_v2.alls "
             "(default: %(default)s).",
    )
    return parser.parse_args(argv)


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


def main(argv=None):
    args = parse_args(argv)

    print("=" * 60)
    print("      COMPILADOR AUTOMÁTICO DE MODELO TOSTADAS V2 A HEF")
    print("=" * 60)

    # Agregar el directorio bin del intérprete de Python al PATH para que subprocess encuentre 'hailo'
    sys_exe_dir = os.path.dirname(sys.executable)
    if sys_exe_dir and sys_exe_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = sys_exe_dir + os.path.pathsep + os.environ.get("PATH", "")

    # Rutas ancladas al repo root (o al --models-dir indicado)
    models_dir = Path(args.models_dir)

    onnx_path = str(models_dir / "tostadas_v2.onnx")
    calib_path = str(models_dir / "calib_dataset.npy")
    alls_path = str(models_dir / "tostadas_v2.alls")

    har_path = str(models_dir / "tostadas_v2.har")
    quantized_har_path = str(models_dir / "tostadas_v2_quantized.har")
    hef_path = str(models_dir / "tostadas_v2.hef")

    # Validar que los archivos de origen existen
    if not os.path.exists(onnx_path):
        print(f"[ERROR] No se encuentra el archivo ONNX en: {onnx_path}")
        print("        Exportalo primero (ai_training/scripts/export_onnx.py) o copialo a esa carpeta.")
        sys.exit(1)

    if not os.path.exists(calib_path):
        print(f"[ERROR] No se encuentra el dataset de calibración en: {calib_path}")
        print("        Generálo con:")
        print("        python ai_training/scripts/prepare_calibration.py --images-dir /ruta/a/train/images")
        sys.exit(1)

    if not os.path.exists(alls_path):
        print(f"[ERROR] No se encuentra el model script (.alls) en: {alls_path}")
        print("        Verificá la estructura indicada en ai_training/compile_instructions.md.")
        sys.exit(1)

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
        sys.exit(1)

    if not os.path.exists(har_path):
        print(f"[ERROR] No se generó el archivo HAR esperado en: {har_path}")
        sys.exit(1)

    # --- PASO 2: Cargar HAR y Cuantizar ---
    print("\n[PASO 2] Iniciando optimización/cuantización a INT8...")
    try:
        from hailo_sdk_client import ClientRunner
    except ImportError:
        print("[ERROR] No se pudo importar 'hailo_sdk_client'.")
        print("Asegúrate de estar ejecutando este script dentro del entorno virtual de Hailo (hailo_env).")
        sys.exit(1)

    # NumPy solo se necesita en esta etapa; se importa acá para no exigirlo en `--help`.
    import numpy as np

    try:
        print(f"[INFO] Cargando HAR: {har_path}")
        runner = ClientRunner(har=har_path)

        # El .alls ya fue validado antes de empezar; se inyecta NMS y Normalización.
        print(f"[INFO] Cargando script de modelo (.alls): {alls_path}")
        runner.load_model_script(alls_path)

        print(f"[INFO] Cargando dataset de calibración: {calib_path}")
        calib_data = np.load(calib_path)

        print("[INFO] Ejecutando optimización (esto puede tardar unos minutos)...")
        runner.optimize(calib_data)

        print(f"[INFO] Guardando HAR optimizado en: {quantized_har_path}")
        runner.save_har(quantized_har_path)
        print("[OK] Optimización completada.")
    except Exception as e:
        print(f"[ERROR] Durante el proceso de cuantización: {e}")
        sys.exit(1)

    # --- PASO 3: Compilar a HEF ---
    print("\n[PASO 3] Compilando el modelo a formato HEF final para Hailo-8L...")
    # comando: hailo compiler --hw-arch hailo8l tostadas_v2_quantized.har --output-dir models_dir --model-script tostadas_v2.alls
    compile_cmd = [
        "hailo", "compiler",
        "--hw-arch", "hailo8l",
        quantized_har_path,
        "--output-dir", str(models_dir),
        "--model-script", alls_path
    ]
    if not run_command(compile_cmd):
        sys.exit(1)

    if not os.path.exists(hef_path):
        print(f"[ERROR] No se pudo generar el archivo HEF final en: {hef_path}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  ¡PROCESO DE MIGRACIÓN COMPLETADO CON ÉXITO!")
    print(f"  Modelo compilado: {hef_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
