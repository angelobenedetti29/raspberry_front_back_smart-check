# AGENTS.md

App de escritorio para control de calidad en Raspberry Pi 5 (entorno industrial).
Frontend PySide6 + backend Python. Sin manifest de dependencias todavía: no asumas
gestor de paquetes ni comandos de build/lint.

## Reglas para agentes

- Mostrá el cambio propuesto y pedí aprobación antes de escribir cualquier archivo.
- Si el flujo no está claro, preguntá en vez de asumir.
- Comentarios y textos de UI en español.
- Al terminar de escribir o modificar un archivo, hacé una review de ese archivo antes
  de pasar al siguiente. Mostrá: (1) los cambios implementados en el archivo, (2) por
  qué deberían estar, (3) qué hace esa funcionalidad y qué parte del código la
  implementa. Cerrá pidiendo confirmación de aprobación para continuar.

## Ejecutar

- `python run.py` — entrypoint único. Carga `config.json`, arranca los servicios de
  backend en hilos y corre la GUI en el hilo principal (Qt lo exige).
- GUI sin display (verificación): `QT_QPA_PLATFORM=offscreen python run.py`
  (bloquea en el loop de Qt; usar `timeout`).
- Requiere Python 3.10+ (`str | None`, `slots=True`). Objetivo: Bookworm / 3.11.

## Arquitectura

- `backend/config.py` — loader. Única forma de leer config para arrancar:
  `load_config()`. `leer_config_cruda()` expone el JSON tal cual y
  `guardar_config()` lo valida antes de escribir (atómico): es lo que usa la
  sección Configuración para editar el archivo.
- `backend/service.py` — Protocol `Service`: `start()` no bloquea (hilo propio),
  `stop()` bloquea. Todo servicio de backend nuevo debe cumplirlo.
- `backend/streaming/` — captura → inferencia → publicación → persistencia.
  `pipeline.py` tiene los dos lazos (producción e inferencia) y `__init__.py` el
  `StreamingService` con los comandos que consume la UI. El lazo de producción
  **nunca** infiere y el de inferencia **nunca** publica: cargar un modelo tarda
  segundos y una inferencia en CPU ~25 ms, y hacerlo en el lazo de producción
  cortaría el stream.
- `backend/inference/` — motor de inferencia intercambiable. `detector.py` es el
  puerto; `catalogo.py` resuelve qué motor y qué archivo se cargan (con el
  auto-redirect `.onnx` → `.hef`); `yolo_opencv.py` (CPU, `cv2.dnn`) y
  `yolo_hailo.py` (NPU, sólo en la Pi) son los adaptadores.
- `backend/device/` — registro de la Raspberry contra el backend Go remoto:
  `cliente.py` (HTTP con `urllib`), `almacen.py` (`device.json` en la raíz) y
  `__init__.py` (`DeviceService`, hilo con polling; el alta la dispara el
  operador, nunca es automática).
- `backend/ai_training/` — bajo demanda; `run.py` no lo arranca.
- `frontend/nucleo/` — los `controlador*.py` son los puertos que consumen las
  secciones y los `adaptador*.py` los únicos que importan `backend.*`
  (`adaptador.py` → `backend.streaming`, `adaptador_config.py` → `backend.config`,
  `adaptador_monitor.py` → `backend.monitor`, `adaptador_dispositivo.py` →
  `backend.device`); `proveedor.py` también importa `backend.config` para el
  snapshot. `contrato.py` inyecta todo en `ContextoApp`. Las secciones **no**
  importan `backend.*`.
- `frontend/secciones/configuracion.py` — edita un subconjunto operativo de
  `config.json` por medio de `ControladorConfig`. El guardado se valida antes de
  escribir y **requiere reiniciar** la app: los servicios ya corren con la
  configuración del arranque, no se aplica en caliente.
- `frontend/app.py` → `run_gui(config, controlador)`. `frontend/main_window.py` →
  `MainWindow`.
- `run_gui` corre un `QTimer` de 200 ms como *pump*: sin él, Python no procesa
  señales bajo el loop de Qt y el proceso queda vivo. No lo quites.

## Config (gotchas)

- `config.json` vive en la raíz. Las rutas relativas se resuelven contra el ROOT del
  proyecto (derivado de `backend/config.py`), **nunca** contra el CWD.
- No dupliques rutas en el JSON: el loader compone `stream.storage.path` y
  `catalog[].model_path/names_path`. `api.base_url` es sólo esquema + host +
  puerto; las rutas de los recursos van como endpoints nombrados (`api.*_endpoint`).
- `paths.models_dir` (`models/`) debe existir o el loader falla. `data/` es salida en
  runtime (log de eventos `eventos.jsonl`) y se crea bajo demanda.
- `data/eventos.jsonl` es un log **por evento**, no por frame: una línea por cada
  `alta`/`quemada`/`cambio`/`baja` de pista, más un `latido` periódico. Contar
  productos es agrupar los `baja` por su `label` final. Rota por tamaño
  (`stream.storage.max_bytes` × `stream.storage.max_files`), y
  `stream.storage.persist_no_detection_every` es la cadencia del latido en frames.
- El loader valida integridad referencial: `models.default_model_id` debe existir en
  `catalog`. `confidence_threshold` ∈ [0,1], `nms_threshold` ∈ (0,1], `image_size > 0`,
  `fps > 0`, `rtsp_transport` ∈ {`tcp`, `udp`}. Error de dominio: `ConfigError`.
- `catalog[].class_thresholds` son umbrales por clase del modelo; las claves se
  normalizan a minúscula (el detector las busca con `label.lower()`) y lo que no figure
  usa `stream.inference.confidence_threshold`. El loader los expone inmutables
  (`MappingProxyType`), coherente con el `frozen=True` de los dataclasses.
- `stream.publisher.write_timeout`: tiempo máximo que puede tardar un frame en entrar
  al pipe de ffmpeg antes de dar el proceso por trabado y reiniciarlo. Un ffmpeg sano
  acepta un frame de 720p en pocos milisegundos, así que un valor chico no afecta el
  caso normal. Si la Pi no da abasto, el backpressure sostenido se resuelve bajando
  resolución o fps (config), no subiendo este valor.
- `stream.capture.source`: un índice numérico (`"0"`) es cámara; cualquier otro valor es
  el nombre de un archivo dentro de `paths.videos_dir`. Es la fuente **por defecto**; la
  UI puede cambiarla en runtime.

## Verificación en la Raspberry (pendiente, no verificable en dev)

En la máquina de desarrollo no existe `hailo_platform` ni cámara, así que la ruta
NPU y la captura real **no** están verificadas. Antes de dar por buena la puesta
en marcha en la Pi:

1. **`hailo_platform` a nivel sistema.** Viene del paquete de Hailo, no de pip. Si
   la app corre en un venv, tiene que estar creado con `--system-site-packages` o
   el import falla y el motor NPU aparece como no disponible.
2. **Estrés de la NPU (el más importante).** Cambiar de modelo NPU → CPU → NPU
   varias veces seguidas y confirmar que no falla con
   `HAILO_OUT_OF_PHYSICAL_DEVICES` y que RSS y handles se mantienen estables. El
   `VDevice` es de por vida del proceso y al cambiar de modelo sólo se desmonta el
   network group; si el re-montaje resulta inestable, la política pasa a ser no
   recargar un HEF sin reiniciar la app.
3. **Formas reales de cada HEF.** El detector valida la salida con una inferencia
   en negro y rechaza el modelo si no tiene forma post-NMS. `yolov8s-hailo` viene
   del model zoo y no de la receta propia: si lo rechaza, revisar orden de tensores.
4. **Sostenido:** fps, CPU y térmica durante ~30 min con inferencia NPU + x264.
5. **Stream desde el otro frontend:** confirmar que se ve, y que conmutar
   plano ↔ inferencia **no** corta la sesión RTSP (el id de sesión de MediaMTX no
   debe cambiar).

Ojo con confundir ritmos: la vista local de la app va a `stream.preview.fps` (15),
el stream publicado a `stream.capture.fps` (30).

## Pendiente / no asumir

- No hay tests, linter ni build configurados.
- `api` sigue siendo un stub con hilo; su lógica real está pendiente.
- La ruta NPU y la captura real no están verificadas en dev (ver la sección
  anterior).
