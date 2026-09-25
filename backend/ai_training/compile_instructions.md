# Guía de entrenamiento, exportación y compilación (ai_training)

Este módulo vive en `backend/ai_training/` y es **on-demand**: `run.py` no lo
arranca y la app no lo importa. Se corre con la raíz del repo como CWD, usando
`python -m backend.ai_training.scripts.<script>`.

Las dependencias pesadas de entrenamiento (Ultralytics/torch) y el compilador de
Hailo (DFC) **no** van a la Raspberry: se instalan sólo en la PC de entrenamiento
y en el host de compilación.

## 0. Entornos

- **PC de entrenamiento (GPU):** Python 3.10+ y
  `pip install -r backend/ai_training/requirements-training.txt`.
- **Host de compilación (Linux/WSL2):** Python **3.10** y el Dataflow Compiler
  de Hailo (`.whl` de la Developer Zone), más `numpy==1.26.4`. El DFC no corre en
  la Raspberry.
- **Raspberry (runtime):** sólo necesita `models/<nombre>.hef` y
  `models/<nombre>.names`; no instala nada de este módulo.

## 1. Dataset

El dataset YOLO (`data.yaml` + `images/` + `labels/`) **no se versiona**: vive
fuera del repo y se pasa por ruta. Calibración y validación también toman
carpetas/rutas explícitas.

## 2. Entrenar y exportar a ONNX

```bash
python -m backend.ai_training.scripts.train \
  --data /ruta/al/dataset/data.yaml \
  --nombre tostadas_v2
```

Hace `train` (100 épocas por defecto, `--device 0`) y exporta a
`models/tostadas_v2.onnx` + `models/tostadas_v2.names`. Los pesos quedan en
`backend/ai_training/runs/<nombre>/weights/best.pt`.

- El `.names` se escribe desde `model.names` (orden real de los índices): no hay
  lista de clases hardcodeada.
- La exportación fuerza `opset 11` + simplificado, que es lo que digiere el
  parser de Hailo-8L.
- Si ya tenés un `best.pt`: `--weights ruta/best.pt` (se salta el entrenamiento),
  o usá `python -m backend.ai_training.scripts.export_onnx --weights <pt>`.

## 3. Generar el dataset de calibración

```bash
python -m backend.ai_training.scripts.prepare_calibration \
  --images-dir /ruta/al/dataset/train/images
```

Genera `backend/ai_training/calib_dataset.npy` (uint8, RGB, 640×640, sin dividir
por 255) usando `stream.inference.image_size` como lado. Por defecto toma hasta
100 imágenes. Necesita OpenCV (`cv2`), así que corrélo donde esté disponible; el
host de compilación lo **lee** de `<work-dir>/calib_dataset.npy` (por defecto
`backend/ai_training/calib_dataset.npy`), no lo regenera.

## 4. Validar antes de compilar (CPU)

```bash
python -m backend.ai_training.scripts.validate_onnx \
  --modelo tostadas-v2 --imagen /ruta/a/una/foto.jpg

# smoke completo (resolución + carga + inferencia opcional):
python -m backend.ai_training.scripts.inference_smoke --imagen /ruta/a/una/foto.jpg
```

Corre el detector real del pipeline y guarda la imagen anotada en
`multimedia/output/`. También existe `validate_pt.py` para el `.pt` con
Ultralytics.

## 5. Compilar a HEF (host con DFC)

```bash
python -m backend.ai_training.scripts.compile_hailo --nombre tostadas_v2
```

Pasos internos (los tres automatizados):

1. `hailo parser onnx models/tostadas_v2.onnx --hw-arch hailo8l --net-name
   tostadas_v2 --har-path ... --end-node-names <6 convs> -y` → `.har`
   (los 6 convs por defecto son de YOLO11, `/model.23/...`; para YOLOv8 pasá
   `--end-node /model.22/...` y verificá los índices de conv del NMS)
2. `ClientRunner(har).load_model_script(<nombre>.alls)` **antes** de
   `optimize(calib_dataset.npy)` → `_quantized.har`
3. `hailo compiler --hw-arch hailo8l <quantized.har> --output-dir ...
   --model-script <nombre>.alls` → `tostadas_v2.hef`

El `_nms.json` y el `.alls` se generan por corrida en `backend/ai_training/` con
rutas locales (no se versionan). El HEF final se copia a `models/tostadas_v2.hef`.
El `calib_dataset.npy` debe estar en el `--work-dir` (por defecto
`backend/ai_training/`); el host no necesita OpenCV, sólo el DFC.

### Por qué así (no tocar)

- `nms_postprocess(..., engine=cpu)`: el NMS de YOLOv8 en `nn_core` **no existe**
  en Hailo-8L.
- El `.alls` **debe** cargarse en el runner antes de `optimize`; si se pasa sólo
  al comando `hailo compiler`, el HEF sale con las 6 salidas crudas y sin NMS.
- El JSON de NMS usa `bbox_decoders` (obligatorio) apuntando a las convoluciones
  internas (`conv51/54`, `conv62/65`, `conv77/80` para YOLO11), no a los
  `output_layer`. Otra arquitectura/toolchain puede cambiar esos índices: si el
  parser falla con `Invalid scope name`, revisalos antes de reusar los defaults.

## 6. Registrar el modelo en el catálogo

Agregar/editar en `config.json` → `models.catalog`:

```json
{
  "model_id": "tostadas-v2",
  "producto_id": null,
  "label": "YOLOv11 Tostadas V2 (Custom)",
  "file": "tostadas_v2.onnx",
  "names_file": "tostadas_v2.names",
  "class_thresholds": { "tcq": 0.3, "tcok": 0.6 }
}
```

Con el `.hef` hermano en `models/`, la app lo redirige solo a la NPU cuando
`hailo_platform` está disponible; si no, usa el `.onnx` en CPU.

### Verificación pendiente en la Raspberry

La ruta NPU y la captura real no están verificadas en dev (ver `AGENTS.md`):
estrés de la NPU cambiando de modelo, formas reales de cada HEF y sostenido
~30 min.
