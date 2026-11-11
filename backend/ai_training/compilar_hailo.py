"""Compilación de un ONNX a HEF para Hailo-8L (Flujo YOLO26 End-to-End).

Orquesta el flujo del Dataflow Compiler (DFC):
1. Parseo directo ONNX -> HAR (sin recortar ramas intermedias).
2. Cuantización INT8 con el dataset de calibración.
3. Compilación final a HEF.

YOLO26 es nativamente NMS-free y no utiliza DFL, por lo que el grafo
se exporta completo y se ejecuta directamente sin decodificadores NMS de Hailo.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from backend.ai_training.rutas import asegurar_dir

HW_ARCH_DEFECTO = "hailo8l"
NIVEL_OPTIMIZACION = "0"


def generar_alls(
    destino: Path,
    *,
    nivel_optimizacion: str = NIVEL_OPTIMIZACION,
) -> Path:
    """Escribe el model script (.alls) para YOLO26 end-to-end."""
    contenido = (
        "normalization1 = normalization([0.0, 0.0, 0.0], [255.0, 255.0, 255.0])\n"
        f"performance_param(compiler_optimization_level={nivel_optimizacion})\n"
    )
    asegurar_dir(destino.parent)
    destino.write_text(contenido, encoding="utf-8")
    return destino


def _asegurar_hailo_en_path() -> None:
    """Deja el `hailo` del intérprete actual visible para subprocess."""
    exe_dir = os.path.dirname(sys.executable)
    if exe_dir and exe_dir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = exe_dir + os.pathsep + os.environ.get("PATH", "")


def ejecutar(comando: Sequence[str], *, aviso: Callable[[str], None] = print) -> None:
    """Corre un comando del toolchain y falla con el detalle si no sale 0."""
    aviso(f"[CMD] {' '.join(comando)}")
    try:
        resultado = subprocess.run(
            comando, capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"No se encontró el ejecutable '{comando[0]}' en el PATH. Ejecutá "
            "dentro del entorno de Hailo (Python 3.10 + Dataflow Compiler)."
        ) from exc
    if resultado.returncode != 0:
        detalle = (resultado.stdout or "") + (resultado.stderr or "")
        raise RuntimeError(f"El comando falló ({resultado.returncode}):\n{detalle}")


def parsear_har(
    onnx: Path,
    har: Path,
    *,
    net_name: str,
    end_nodes: Sequence[str] | None = None,
    hw_arch: str = HW_ARCH_DEFECTO,
    aviso: Callable[[str], None] = print,
) -> Path:
    """Paso 1: `hailo parser onnx` -> HAR. En YOLO26 end-to-end no se recortan nodos."""
    asegurar_dir(har.parent)
    comando = [
        "hailo",
        "parser",
        "onnx",
        str(onnx),
        "--hw-arch",
        hw_arch,
        "--net-name",
        net_name,
        "--har-path",
        str(har),
        "-y",
    ]
    if end_nodes:
        comando.extend(["--end-node-names", *end_nodes])

    ejecutar(comando, aviso=aviso)
    if not har.is_file():
        raise FileNotFoundError(f"No se generó el HAR esperado: {har}")
    return har


def verificar_calibracion(calib: Path, *, image_size: int) -> None:
    """Falla temprano si el calib set no coincide con la entrada del modelo."""
    import numpy as np

    if not calib.is_file():
        raise FileNotFoundError(f"No se encontró el dataset de calibración: {calib}")
    datos = np.load(str(calib), mmap_mode="r")
    if (
        datos.ndim != 4
        or datos.shape[1:3] != (image_size, image_size)
        or datos.shape[3] != 3
        or datos.dtype != np.uint8
    ):
        raise ValueError(
            f"El dataset de calibración tiene forma {tuple(datos.shape)} "
            f"({datos.dtype}); se esperaba (N, {image_size}, {image_size}, 3) "
            f"uint8. Regenerálo con "
            f"`prepare_calibration --image-size {image_size}`."
        )
    if datos.shape[0] < 1:
        raise ValueError("El dataset de calibración está vacío.")


def cuantizar(
    har: Path,
    alls: Path,
    calib: Path,
    quantized_har: Path,
    *,
    aviso: Callable[[str], None] = print,
) -> Path:
    """Paso 2: carga el .alls, optimiza/cuantiza con el calib set y guarda el HAR."""
    try:
        from hailo_sdk_client import ClientRunner
    except ImportError as exc:
        raise ImportError(
            "No se pudo importar 'hailo_sdk_client'. Ejecutá dentro del entorno "
            "de Hailo (Python 3.10 + Dataflow Compiler)."
        ) from exc

    import numpy as np

    if not calib.is_file():
        raise FileNotFoundError(f"No se encontró el dataset de calibración: {calib}")

    try:
        runner = ClientRunner(har=str(har))
        aviso(f"[INFO] Cargando model script: {alls}")
        runner.load_model_script(str(alls))
        aviso(f"[INFO] Optimizando/cuantizando con {calib} (puede tardar minutos)...")
        runner.optimize(np.load(str(calib)))
        runner.save_har(str(quantized_har))
    except Exception as exc:
        raise RuntimeError(f"Falló la cuantización con el DFC: {exc}") from exc
    if not quantized_har.is_file():
        raise FileNotFoundError(f"No se generó el HAR cuantizado: {quantized_har}")
    return quantized_har


def compilar_hef(
    quantized_har: Path,
    alls: Path,
    output_dir: Path,
    *,
    net_name: str,
    hw_arch: str = HW_ARCH_DEFECTO,
    aviso: Callable[[str], None] = print,
) -> Path:
    """Paso 3: `hailo compiler` -> HEF final."""
    asegurar_dir(output_dir)
    previos = {p: p.stat().st_mtime_ns for p in output_dir.glob("*.hef")}
    ejecutar(
        [
            "hailo",
            "compiler",
            "--hw-arch",
            hw_arch,
            str(quantized_har),
            "--output-dir",
            str(output_dir),
            "--model-script",
            str(alls),
        ],
        aviso=aviso,
    )
    nuevos = [p for p in output_dir.glob("*.hef") if previos.get(p) != p.stat().st_mtime_ns]
    exacto = output_dir / f"{net_name}.hef"
    if exacto.is_file() and exacto in nuevos:
        return exacto
    if len(nuevos) == 1:
        return nuevos[0]
    if not nuevos:
        raise FileNotFoundError(f"`hailo compiler` no generó ningún .hef nuevo en {output_dir}.")
    raise FileNotFoundError(
        f"HEFs nuevos ambiguos en {output_dir}: {[p.name for p in nuevos]}; "
        f"se esperaba {net_name}.hef."
    )


def compilar(
    onnx: Path,
    calib: Path,
    work_dir: Path,
    *,
    nombre: str,
    image_size: int,
    net_name: str | None = None,
    hw_arch: str = HW_ARCH_DEFECTO,
    end_nodes: Sequence[str] | None = None,
    aviso: Callable[[str], None] = print,
) -> Path:
    """Corre los 3 pasos y devuelve el HEF generado dentro de `work_dir`."""
    net = net_name or nombre
    if not onnx.is_file():
        raise FileNotFoundError(f"No se encontró el ONNX: {onnx}")
    verificar_calibracion(calib, image_size=image_size)

    asegurar_dir(work_dir)
    alls = generar_alls(work_dir / f"{nombre}.alls")

    _asegurar_hailo_en_path()
    aviso("[PASO 1] Parseando ONNX a HAR...")
    har = parsear_har(
        onnx,
        work_dir / f"{nombre}.har",
        net_name=net,
        end_nodes=end_nodes,
        hw_arch=hw_arch,
        aviso=aviso,
    )

    aviso("[PASO 2] Optimizando/cuantizando a INT8...")
    quant = cuantizar(har, alls, calib, work_dir / f"{nombre}_quantized.har", aviso=aviso)

    aviso("[PASO 3] Compilando a HEF...")
    return compilar_hef(quant, alls, work_dir, net_name=net, hw_arch=hw_arch, aviso=aviso)
