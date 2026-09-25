"""Compilación de un ONNX a HEF para Hailo-8L.

Orquesta el flujo del Dataflow Compiler (DFC): parseo ONNX->HAR, carga del model
script (.alls) *antes* de optimizar (si no, el NMS no queda horneado en el HEF),
cuantización INT8 con el dataset de calibración y compilación final.

Sólo corre en el host de compilación (Linux/WSL2, Python 3.10, DFC instalado),
no en la Raspberry ni en dev. El `.alls` y el `_nms.json` se generan por corrida
con rutas locales: así no se repite la ruta absoluta de Windows de la referencia.

Los nodos de salida y las capas NMS por defecto son de YOLO11 (`/model.23/...`).
Para YOLOv8 hay que pasar `--end-node /model.22/...` y verificar las conv del HAR.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from backend.ai_training.rutas import asegurar_dir

HW_ARCH_DEFECTO = "hailo8l"
META_ARCH = "yolov8"
# Hailo-8L no soporta NMS en nn_core: el NMS corre en CPU vía metadata del HEF.
ENGINE_NMS = "cpu"
NIVEL_OPTIMIZACION = "max"

# Nodos del cabezal de detección de YOLO11 (3 regresión + 3 clase). YOLOv8 usa
# `/model.22/...` en lugar de `/model.23/...`.
NODOS_SALIDA_DEFECTO: tuple[str, ...] = (
    "/model.23/cv2.0/cv2.0.2/Conv",
    "/model.23/cv3.0/cv3.0.2/Conv",
    "/model.23/cv2.1/cv2.1.2/Conv",
    "/model.23/cv3.1/cv3.1.2/Conv",
    "/model.23/cv2.2/cv2.2.2/Conv",
    "/model.23/cv3.2/cv3.2.2/Conv",
)

# Capas internas del HAR (prefijadas por el net-name) por escala. Deben ser las
# convoluciones inmediatamente anteriores a las salidas (no los output_layer).
_CONV_REGRESION = (51, 62, 77)
_CONV_CLASES = (54, 65, 80)
_STRIDES = (8, 16, 32)


def generar_nms_json(
    destino: Path,
    *,
    clases: int,
    image_size: int,
    net_name: str,
    reg_layers: Sequence[str] | None = None,
    cls_layers: Sequence[str] | None = None,
) -> Path:
    """Escribe el JSON de NMS que consume el DFC (`bbox_decoders` es obligatorio)."""
    if clases < 1:
        raise ValueError("El NMS necesita al menos una clase.")
    regs = (
        tuple(reg_layers)
        if reg_layers
        else tuple(f"{net_name}/conv{i}" for i in _CONV_REGRESION)
    )
    clss = (
        tuple(cls_layers)
        if cls_layers
        else tuple(f"{net_name}/conv{i}" for i in _CONV_CLASES)
    )
    if len(regs) != len(_STRIDES) or len(clss) != len(_STRIDES):
        raise ValueError(
            f"Se esperan {len(_STRIDES)} escalas (strides {list(_STRIDES)}): "
            f"reg_layers={len(regs)}, cls_layers={len(clss)}."
        )

    contenido = {
        "nms_scores_th": 0.001,
        "nms_iou_th": 0.7,
        "max_proposals_per_class": 100,
        "classes": int(clases),
        "background_removal": False,
        "image_dims": [int(image_size), int(image_size)],
        "bbox_decoders": [
            {"stride": stride, "reg_layer": reg, "cls_layer": cls}
            for stride, (reg, cls) in zip(_STRIDES, zip(regs, clss))
        ],
    }
    asegurar_dir(destino.parent)
    destino.write_text(json.dumps(contenido, indent=2) + "\n", encoding="utf-8")
    return destino


def generar_alls(
    nms_json: Path,
    destino: Path,
    *,
    nivel_optimizacion: str = NIVEL_OPTIMIZACION,
) -> Path:
    """Escribe el model script (.alls) apuntando al JSON de NMS local."""
    ruta = nms_json.resolve()
    if '"' in str(ruta):
        raise ValueError(f"La ruta del NMS no puede contener comillas: {ruta}")
    contenido = (
        "normalization1 = normalization([0.0, 0.0, 0.0], [255.0, 255.0, 255.0])\n"
        f'nms_postprocess("{ruta}", meta_arch={META_ARCH}, engine={ENGINE_NMS})\n'
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
    end_nodes: Sequence[str],
    hw_arch: str = HW_ARCH_DEFECTO,
    aviso: Callable[[str], None] = print,
) -> Path:
    """Paso 1: `hailo parser onnx` -> HAR, recortando en los nodos de salida."""
    asegurar_dir(har.parent)
    ejecutar(
        [
            "hailo", "parser", "onnx", str(onnx),
            "--hw-arch", hw_arch,
            "--net-name", net_name,
            "--har-path", str(har),
            "--end-node-names", *end_nodes,
            "-y",
        ],
        aviso=aviso,
    )
    if not har.is_file():
        raise FileNotFoundError(f"No se generó el HAR esperado: {har}")
    return har


def verificar_calibracion(calib: Path, *, image_size: int) -> None:
    """Falla temprano si el calib set no coincide con la entrada del modelo."""
    import numpy as np

    if not calib.is_file():
        raise FileNotFoundError(f"No se encontró el dataset de calibración: {calib}")
    # mmap: sólo se lee la forma, sin traer las imágenes a memoria.
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
        # El model script va ANTES de optimize: ahí se hornean normalización y NMS.
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
    """Paso 3: `hailo compiler` -> HEF final.

    No confía en que el archivo sea `<net_name>.hef`: se queda con los `.hef`
    nuevos/actualizados respecto de antes de compilar, para no devolver un HEF
    viejo ni el de otro modelo del work-dir compartido.
    """
    asegurar_dir(output_dir)
    previos = {p: p.stat().st_mtime_ns for p in output_dir.glob("*.hef")}
    ejecutar(
        [
            "hailo", "compiler",
            "--hw-arch", hw_arch,
            str(quantized_har),
            "--output-dir", str(output_dir),
            "--model-script", str(alls),
        ],
        aviso=aviso,
    )
    nuevos = [
        p
        for p in output_dir.glob("*.hef")
        if previos.get(p) != p.stat().st_mtime_ns
    ]
    exacto = output_dir / f"{net_name}.hef"
    if exacto.is_file() and exacto in nuevos:
        return exacto
    if len(nuevos) == 1:
        return nuevos[0]
    if not nuevos:
        raise FileNotFoundError(
            f"`hailo compiler` no generó ningún .hef nuevo en {output_dir}."
        )
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
    clases: int,
    image_size: int,
    net_name: str | None = None,
    hw_arch: str = HW_ARCH_DEFECTO,
    end_nodes: Sequence[str] = NODOS_SALIDA_DEFECTO,
    reg_layers: Sequence[str] | None = None,
    cls_layers: Sequence[str] | None = None,
    aviso: Callable[[str], None] = print,
) -> Path:
    """Corre los 3 pasos y devuelve el HEF generado dentro de `work_dir`."""
    net = net_name or nombre
    if not onnx.is_file():
        raise FileNotFoundError(f"No se encontró el ONNX: {onnx}")
    verificar_calibracion(calib, image_size=image_size)

    asegurar_dir(work_dir)
    nms_json = generar_nms_json(
        work_dir / f"{nombre}_nms.json",
        clases=clases,
        image_size=image_size,
        net_name=net,
        reg_layers=reg_layers,
        cls_layers=cls_layers,
    )
    alls = generar_alls(nms_json, work_dir / f"{nombre}.alls")

    _asegurar_hailo_en_path()
    aviso("[PASO 1] Parseando ONNX a HAR...")
    har = parsear_har(
        onnx, work_dir / f"{nombre}.har",
        net_name=net, end_nodes=end_nodes, hw_arch=hw_arch, aviso=aviso,
    )

    aviso("[PASO 2] Optimizando/cuantizando a INT8...")
    quant = cuantizar(
        har, alls, calib, work_dir / f"{nombre}_quantized.har", aviso=aviso
    )

    aviso("[PASO 3] Compilando a HEF...")
    return compilar_hef(
        quant, alls, work_dir, net_name=net, hw_arch=hw_arch, aviso=aviso
    )
