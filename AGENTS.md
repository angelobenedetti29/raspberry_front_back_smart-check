# AGENTS.md

App de escritorio para control de calidad en Raspberry Pi 5 (entorno industrial).
Frontend PySide6 + backend Python.

Dependencias: `requirements.txt` (pip); las de entrenamiento y compilación van
aparte en `backend/ai_training/requirements-training.txt` y **no** se instalan en
la Raspberry. En la Raspberry, OpenCV (`cv2`) y
`hailo_platform` vienen del sistema y **no** se instalan por pip (rompen el build
con GStreamer/Hailo). Los secretos viven en `.env` (plantilla en `.env.example`),
nunca en `config.json`. No hay build ni linter configurados: no asumas comandos
de lint/build.

## Reglas para agentes

- Mostrá el cambio propuesto y pedí aprobación antes de escribir cualquier archivo.
- Si el flujo no está claro, preguntá en vez de asumir.
- Comentarios y textos de UI en español.
- Al terminar de escribir o modificar un archivo, hacé una review de ese archivo antes
  de pasar al siguiente. Mostrá: (1) los cambios implementados en el archivo, (2) por
  qué deberían estar, (3) qué hace esa funcionalidad y qué parte del código la
  implementa. Cerrá pidiendo confirmación de aprobación para continuar.

## Ejecutar

- Instalar dependencias: `pip install -r requirements.txt`; copiar `.env.example`
  a `.env` y completar (código de enrolamiento y carpeta de identidad).
- `python run.py` — entrypoint único. Carga `config.json`, arranca los cuatro
  servicios de backend en hilos (`StreamingService`, `DeviceService`,
  `MonitorService`, `LoteService`) y corre la GUI en el hilo principal (Qt lo
  exige).
- GUI sin display (verificación): `QT_QPA_PLATFORM=offscreen python run.py`
  (bloquea en el loop de Qt; usar `timeout`).
- Requiere Python 3.10+ (`str | None`, `slots=True`). Objetivo: Bookworm / 3.11
  (el venv de desarrollo corre 3.14).

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
  cortaría el stream. Las cajas las dibuja el lazo de producción (`anotador.py`)
  con las últimas detecciones que dejó el de inferencia.
  - `capture.py` — frames BGR normalizados a `capture.width × height`, de cámara o
    archivo; `publisher.py` — ffmpeg como subproceso hacia MediaMTX (hilo escritor
    + hilo supervisor); `storage.py` — JSONL por evento con rotación;
    `estabilizador.py` — latch anti-parpadeo por pista y eventos de ciclo de vida;
    `suscripcion.py` — buzón acotado por el que el streaming difunde eventos sin
    bloquear la inferencia; `estado.py` — instantáneas inmutables para la UI.
- `backend/inference/` — motor de inferencia intercambiable. `detector.py` es el
  puerto; `catalogo.py` resuelve qué motor y qué archivo se cargan (con el
  auto-redirect `.onnx` → `.hef`); `yolo_opencv.py` (CPU, `cv2.dnn`) y
  `yolo_hailo.py` (NPU, sólo en la Pi) son los adaptadores.
- `backend/device/` — registro de la Raspberry contra el backend Go remoto:
  `cliente.py` (HTTP con `urllib`), `almacen.py` (`device.json` en la raíz, guarda
  el secret), `tipos.py` (`TipoDispositivo`: ENTRADA_HORNO/SALIDA_HORNO) y
  `__init__.py` (`DeviceService`, hilo con polling; el alta la dispara el
  operador, nunca es automática).
- `backend/monitor/` — telemetría: `recolector.py` lee CPU/RAM/disco/temperatura
  con psutil y `acelerador.py` la utilización de la NPU (puerto
  `FuenteAceleradorIA`; hoy sólo Hailo). `MonitorService` envía por HTTP cada
  `device.ping_interval_seconds` y guarda el historial de envíos.
- `backend/lote/` — lote del sector. Consume los eventos de pista del streaming por
  el buzón, los traduce a productos con estado (`estado.py`) y los reporta al
  backend (`cliente.py`). `LoteService` corre en su hilo; el rol (ENTRADA abre el
  lote con el primer `alta`, SALIDA reporta y cierra por inactividad) sale de
  `TipoDispositivo`.
- `backend/ai_training/` — herramientas de entrenamiento/exportación/compilación,
  **bajo demanda**: `run.py` no lo arranca y la app no lo importa. `rutas.py`
  centraliza ROOT/config; `calibrar.py` arma `calib_dataset.npy`; `entrenar.py`
  entrena y exporta a ONNX + `.names`; `compilar_hailo.py` compila ONNX → HEF con
  el DFC; `validar.py` valida con el detector real del pipeline. Los
  `scripts/*.py` son los CLIs (`python -m backend.ai_training.scripts.<x>`, con la
  raíz del repo como CWD). Guía y flujo completo en
  `backend/ai_training/compile_instructions.md`.
- `frontend/nucleo/` — los `controlador*.py` son los puertos que consumen las
  secciones y los `adaptador*.py` los únicos que importan `backend.*`
  (`adaptador.py` → `backend.streaming`, `adaptador_config.py` → `backend.config`,
  `adaptador_monitor.py` → `backend.monitor`, `adaptador_dispositivo.py` →
  `backend.device`, `adaptador_lotes.py` → `backend.lote`). `contrato.py` define
  `ContextoApp` y los grupos de sección (`GrupoSeccion`: COMUN/ENTRADA/SALIDA) e
  inyecta todo en cada sección. `bus.py` es el bus de señales Qt, `registro.py` el
  orden y la visibilidad de las secciones, `estado.py` el modelo del estado del
  sistema, `proveedor.py` arma el snapshot combinando config y estado runtime, y
  `tema.py`/`iconos.py` el look (tokens + QSS y SVG tintados). Las secciones **no**
  importan `backend.*`.
- `frontend/secciones/` — una sección por pantalla (`inicio`, `en_vivo`,
  `metricas`, `lotes`, `horno_cinta`, `configuracion`). Todas heredan de
  `SeccionBase` y leen por `ContextoApp`.
- `frontend/secciones/configuracion.py` — edita un subconjunto operativo de
  `config.json` por medio de `ControladorConfig`. El guardado se valida antes de
  escribir y **requiere reiniciar** la app: los servicios ya corren con la
  configuración del arranque, no se aplica en caliente.
- `frontend/app.py` → `run_gui(config, controlador…)`. `frontend/main_window.py` →
  `MainWindow` (shell con rail lateral y `QStackedWidget`).
- `run_gui` corre un `QTimer` de 200 ms como *pump*: sin él, Python no procesa
  señales bajo el loop de Qt y el proceso queda vivo. No lo quites.

## Config (gotchas)

- `config.json` vive en la raíz y se versiona. Las rutas relativas se resuelven contra
  el ROOT del proyecto (derivado de `backend/config.py`), **nunca** contra el CWD.
- Los secretos **no** van acá: el código de enrolamiento y la carpeta de identidad
  salen de `.env`, y el secret que devuelve el backend se guarda en `device*.json`
  (ambos ignorados por git).
- No dupliques rutas en el JSON: el loader compone `stream.storage.path` (=
  `paths.data_dir` + `stream.storage.file`) y `catalog[].model_path/names_path`.
  `api.base_url` es sólo esquema + host + puerto (sin `/` final); las rutas de los
  recursos van como endpoints nombrados (`api.*_endpoint`, todos empezando con `/`):
  registro, ping, nombre, productos, sector y lotes.
- `paths.models_dir` (`models/`) debe existir o el loader falla, y es la base de
  `catalog[].file/names_file`. `paths.data_dir` (`data/`) es salida en runtime (el log
  de eventos) y se crea bajo demanda; `paths.videos_dir` (`multimedia/videos/`) es la
  base de los archivos que puede tomar `stream.capture.source`.
- `data/eventos.jsonl` es un log **por evento**, no por frame: una línea por cada
  `alta`/`quemada`/`cambio`/`baja` de pista, más un `latido` periódico. Contar
  productos es agrupar los `baja` por su `label` final. Rota por tamaño
  (`stream.storage.max_bytes` × `stream.storage.max_files`), y
  `stream.storage.persist_no_detection_every` es la cadencia del latido en frames.
- El loader valida integridad referencial: `models.default_model_id` debe existir en
  `catalog`. `confidence_threshold` ∈ [0,1], `nms_threshold` ∈ (0,1], `image_size > 0`,
  `fps > 0`, `rtsp_transport` ∈ {`tcp`, `udp`}. Error de dominio: `ConfigError`.
- `catalog[].producto_id` (opcional, `null` o string no vacío) vincula el modelo a un
  producto del backend. `catalog[].class_thresholds` son umbrales por clase; las claves
  se normalizan a minúscula (el detector las busca con `label.lower()`) y lo que no
  figure usa `stream.inference.confidence_threshold`. El loader los expone inmutables
  (`MappingProxyType`), coherente con el `frozen=True` de los dataclasses.
- `device.*`: `hostname` lógico (no vacío), `ping_interval_seconds` (telemetría del
  monitor) y `registration_poll_interval_seconds` (polling del alta), ambos > 0.
- `lote.*`: `habilitado`, `cierre_sin_detecciones_segundos` (inactividad para cerrar),
  `flush_segundos` (debe ser < cierre), `max_eventos_por_envio` ∈ [1,100],
  `cola_eventos` ≥ 1, `max_pendientes` ≥ `max_eventos_por_envio`,
  `refresco_catalogo_segundos` y el backoff de reintentos.
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

- No hay tests, linter ni build configurados: existen las carpetas `tests/backend` y
  `tests/frontend` (vacías) y el `.gitignore` contempla `.pytest_cache/`/`.ruff_cache/`,
  pero no hay archivos de test ni config de pytest/ruff.
- La ruta NPU y la captura real no están verificadas en dev (ver la sección anterior).
- El entrenamiento real (Ultralytics/torch) y la compilación HEF (DFC) tampoco son
  verificables en dev: ver `backend/ai_training/compile_instructions.md`.
