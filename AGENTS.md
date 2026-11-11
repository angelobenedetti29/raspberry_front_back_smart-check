# AGENTS.md

App de escritorio para control de calidad en Raspberry Pi 5 (entorno industrial).
Frontend PySide6 + backend Python.

Dependencias: `requirements.txt` (pip); las de entrenamiento y compilación van
aparte en `backend/ai_training/requirements-training.txt` y **no** se instalan en
la Raspberry. En la Raspberry, OpenCV (`cv2`) y
`hailo_platform` vienen del sistema y **no** se instalan por pip (rompen el build
con GStreamer/Hailo). El único secreto (el `secret` del dispositivo) no vive en
`config.json` sino en `device.json` (ignorado por git). No hay build. La config de
lint/format vive en `pyproject.toml` (ruff y black, line-length 100, target py310)
y la CI la usa con `ruff check .` y `black --check .`, sin flags inline; `pytest`
no está en `requirements.txt` y se instala aparte (ver `.github/workflows/ci.yml`).

## Reglas para agentes

- Mostrá el cambio propuesto y pedí aprobación antes de escribir cualquier archivo.
- Si el flujo no está claro, preguntá en vez de asumir.
- Comentarios y textos de UI en español.
- Al terminar de escribir o modificar un archivo, hacé una review de ese archivo antes
  de pasar al siguiente. Mostrá: (1) los cambios implementados en el archivo, (2) por
  qué deberían estar, (3) qué hace esa funcionalidad y qué parte del código la
  implementa. Cerrá pidiendo confirmación de aprobación para continuar.

## Ejecutar

- Instalar dependencias: `pip install -r requirements.txt`. El alta del
  dispositivo se dispara desde la UI; el `secret` que devuelve el backend queda
  en `device.json`.
- `python run.py` — entrypoint único. Carga `config.json`, arranca los servicios
  de backend en hilos (`StreamingService`, `DeviceService`, `MonitorService`,
  `LoteService`, `ControlService` y `ServidorHTTP`) y corre la GUI en el hilo
  principal (Qt lo exige).
- GUI sin display (verificación): `QT_QPA_PLATFORM=offscreen python run.py`
  (bloquea en el loop de Qt; usar `timeout`).
- Requiere Python 3.10+ (`str | None`, `slots=True`). Objetivo: Bookworm / 3.11
  (el venv de desarrollo corre 3.14).
- Tests: `pytest` **no** está en `requirements.txt`; instalalo aparte
  (`pip install pytest`). Backend: `python -m pytest tests/backend -q`; suite
  completa desde la raíz: `pytest -q` (incluye `tests/frontend/`).
- Instalación paso a paso (venv, doble instancia ENTRADA/SALIDA):
  `MANUAL_INSTALACION.md`.

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
    + hilo supervisor); `storage_sqlite.py` — SQLite (WAL) por evento con rotación
    por tamaño; `estabilizador.py` — latch anti-parpadeo por pista y eventos de
    ciclo de vida;
    `suscripcion.py` — buzón acotado por el que el streaming difunde eventos sin
    bloquear la inferencia; `estado.py` — instantáneas inmutables para la UI.
- `backend/inference/` — motor de inferencia intercambiable. `detector.py` es el
  puerto; `catalogo.py` resuelve qué motor y qué archivo se cargan (con el
  auto-redirect `.onnx` → `.hef`); `yolo_opencv.py` (CPU, `cv2.dnn`) y
  `yolo_hailo.py` (NPU, sólo en la Pi) son los adaptadores.
- `backend/http/` — servidor HTTP general de la app, basado en FastAPI + uvicorn
  embebido. `servidor.py` expone `ServidorHTTP` (cumple el Protocol `Service`):
  bindea el socket, monta los `APIRouter` que recibe y los corre en un hilo
  daemon; `stop()` señaliza `should_exit` y espera al hilo; `rebind(host, port)`
  lo reemplaza por uno nuevo en otro bind (lo usa `run.py` cuando cambia
  `device.public_url` o `device.local_port` desde Configuración). `routers/`
  trae los routers enchufables: hoy sólo `renombre.py` (push de renombre del
  backend Go a la Pi); mañana cualquier módulo puede sumar el suyo sin tocar
  `ServidorHTTP`.
- `backend/device/` — registro de la Raspberry contra el backend Go remoto:
  `cliente.py` (HTTP con `urllib`), `almacen.py` (`device.json` en la raíz, guarda
  el secret), `tipos.py` (`TipoDispositivo`: ENTRADA_HORNO/SALIDA_HORNO) y
  `__init__.py` (`DeviceService`, hilo con polling; el alta la dispara el
  operador, nunca es automática). El servidor HTTP local del push de renombre
  **no** vive acá: lo levanta `run.py` con `backend.http.ServidorHTTP` y el
  router `backend.http.routers.renombre`; este módulo sólo expone
  `derivar_bind(...)` (público) para que `run.py` calcule el bind y
  `aplicar_renombre_externo` para que el router aplique el cambio.
- `backend/monitor/` — telemetría: `recolector.py` lee CPU/RAM/disco/temperatura
  con psutil y `acelerador.py` la utilización de la NPU (puerto
  `FuenteAceleradorIA`; hoy sólo Hailo). `MonitorService` envía por HTTP cada
  `device.ping_interval_seconds` y guarda el historial de envíos.
- `backend/lote/` — lote del sector. Consume los eventos de pista del streaming por
  el buzón, los traduce a productos con estado (`estado.py`) y los reporta al
  backend (`cliente.py`). `LoteService` corre en su hilo; el rol (ENTRADA abre el
  lote con el primer `alta`, SALIDA reporta y cierra por inactividad) sale de
  `TipoDispositivo`. La SALIDA descarta los eventos de un producto distinto al del
  lote abierto, porque el Go rechaza el batch completo.
- `backend/etiquetas.py` — resolución de etiquetas a `(producto, estado)`.
  `config.json` → `etiquetas.clases` mapea los códigos cortos del modelo (`tc`,
  `tcok`, `tcq`, `cha`, `fajita`, `chaok`, `chaq`, ...) a producto normalizado y
  estado opcional; `fajita` y `cha` resuelven a Chalitas. Sin coincidencia en el
  mapa se mantiene la convención `<producto>_<estado>`: el estado es el sufijo
  tras el último `_` y sólo puede ser `crudo`|`ok`|`quemado`; sin estado válido el
  evento no cuenta para el lote. `CLASES_POR_DEFECTO` es el fallback para configs
  viejas y tests, no el mapa principal. `backend/lote/` usa `separar_etiqueta` con
  el resolutor del config.
- `backend/control/` — ControlService (SCA-400, sólo ENTRADA): publica telemetría
  del horno y aplica consignas. Pollea
  `GET /api/v1/lotes/{id}/consigna-propuesta` cada
  `control.poll_consigna_propuesta_segundos`: en AUTOMATICO aplica las que llegan
  con `autorizada=true`; en SUPERVISION espera la aprobación del operario; en
  MANUAL el MPC no propone, pero sí se sirve un override manual ya aprobado
  (`manual_override=True`) que se aplica sin el Δmáx por ciclo, sólo con el rango
  del producto. El resto de las propuestas se clampea al Δmáx del producto antes
  de escribir el PLC/variador. La propuesta (`SetpointPropuesto`) llega con
  `temp1_objetivo`/`vel_objetivo`, `autorizada`, `decision_id`, `limites` y
  `manual_override`; la Pi confirma lo aplicado con `consigna/ack`. Sensores y
  actuadores se eligen por config (`control.sensores/actuadores.driver`):
  `stub` (sin hardware), `simulado` (`simulado.py`: planta en memoria dentro del
  proceso, para probar lógica y UI) o `modbus_rtu/tcp` (`modbus.py`, el driver
  real). Para probar la ruta Modbus sin PLC está `tools/simulador_modbus.py`
  (standalone, sin imports de la app; espeja el mapa de registros de `modbus.py`,
  así que si cambiás uno cambiá el otro; con `driver: "modbus_tcp"` apuntando a
  él). El simulador trae un panel web de pruebas en `http://127.0.0.1:8091/`
  (flags `--ui-host`, `--ui-puerto`, `--sin-ui`) con `GET /api/estado` y
  `POST /api/consignas`, `/api/parametros`, `/api/reiniciar` y `/api/rechazar`;
  permite mover la planta y cambiar parámetros en caliente. **El panel no tiene
  autenticación**: por defecto escucha en loopback. Usa `pymodbus.simulator` y
  necesita `pymodbus >= 3.15`; `requirements.txt` declara `>=3.11,<4`, así que
  pip resuelve la última 3.x.
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
  `backend.device`, `adaptador_lotes.py` → `backend.lote`, `adaptador_control.py`
  → `backend.control`). `contrato.py` define
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
  escribir. El par `device.public_url` + `device.local_port` se aplica en
  caliente: re-bindea el `ServidorHTTP` y sincroniza la `rename_url` con el Go.
  El resto de la configuración **requiere reiniciar** la app: los servicios ya
  corren con la configuración del arranque.
- `visible_si` — mecanismo de campos condicionales: `CampoConfigUI` lleva
  `visible_si=(clave, valor)` (`frontend/nucleo/controlador_config.py`),
  `AdaptadorConfig` lo resuelve en cada `campos()` y `Configuracion` muestra u
  oculta la celda según el valor actual del otro campo. Al guardar, los campos
  ocultos **no se envían**; las claves que ya existan en el JSON no se borran.
  Hoy se usa para `control.sensores/actuadores.host/port`, visibles sólo con
  driver `modbus_tcp`; si la clave falta, el adaptador ofrece el default
  `127.0.0.1:5020`.
- `frontend/app.py` → `run_gui(config, controlador…)`. `frontend/main_window.py` →
  `MainWindow` (shell con rail lateral y `QStackedWidget`).
- `run_gui` corre un `QTimer` de 200 ms como *pump*: sin él, Python no procesa
  señales bajo el loop de Qt y el proceso queda vivo. No lo quites.

## Config (gotchas)

- `config.json` vive en la raíz y se versiona. Las rutas relativas se resuelven contra
  el ROOT del proyecto (derivado de `backend/config.py`), **nunca** contra el CWD.
- El `secret` del dispositivo **no** va acá: lo devuelve el backend al aprobar el
  alta y se guarda en `device*.json` (ignorado por git).
- No dupliques rutas en el JSON: el loader compone `catalog[].model_path/names_path`
  desde `paths.models_dir`, y `stream.storage.path` queda fijo en
  `paths.data_dir/eventos.db`. `api.base_url` es sólo esquema + host + puerto (sin
  `/` final); las rutas de los recursos van como endpoints nombrados
  (`api.*_endpoint`, todos empezando con `/`): registro, ping, nombre, rename-url,
  productos, sector y lotes.
- `paths.models_dir` (`models/`) debe existir o el loader falla, y es la base de
  `catalog[].file/names_file`. `paths.data_dir` (`data/`) es salida en runtime (el log
  de eventos) y se crea bajo demanda; `paths.videos_dir` (`multimedia/videos/`) es la
  base de los archivos que puede tomar `stream.capture.source`.
- `data/eventos.db` es la DB SQLite (WAL) **por evento**, no por frame: una fila
  por cada `alta`/`quemada`/`cambio`/`baja` de pista, más un `latido` periódico.
  Contar productos es agrupar los `baja` por su `label` final. Rota por tamaño
  (`stream.storage.max_bytes` × `stream.storage.max_files`), y
  `stream.storage.persist_no_detection_every` es la cadencia del latido en frames.
- El loader valida integridad referencial: `models.default_model_id`,
  `models.default_entrada_model_id` y `models.default_salida_model_id` deben
  existir en `catalog`. `confidence_threshold` ∈ [0,1], `nms_threshold` ∈ (0,1],
  `image_size > 0`, `fps > 0`, `rtsp_transport` ∈ {`tcp`, `udp`}. Error de
  dominio: `ConfigError`.
- El modelo inicial de cada Raspberry sale del `TipoDispositivo` persistido en
  `device.json` (`ModelsConfig.modelo_para`): `ENTRADA_HORNO` usa
  `default_entrada_model_id`, `SALIDA_HORNO` usa `default_salida_model_id` y sin
  tipo (o sin la clave) cae a `default_model_id`, que es el fallback. La elección
  ocurre al arrancar: si la Pi se registra con la app ya iniciada, hay que
  reiniciar para que tome el modelo de su rol.
- `etiquetas.clases`: etiqueta → `{producto, estado}`. Claves y productos se
  normalizan con `normalizar_clave` (minúsculas, sin tildes, separadores → `_`);
  el `estado` es opcional y sólo acepta `crudo`|`ok`|`quemado` o `null`; el loader
  rechaza etiquetas duplicadas al normalizar. Los alias viejos
  (`tostada_quemada`, `tostadas_ok`) siguen en el mapa mientras se usen los
  modelos viejos. Sin `etiquetas` en el config se usa `CLASES_POR_DEFECTO`.
- `catalog[].class_thresholds` son umbrales por clase; las claves se normalizan a
  minúscula (el detector las busca con `label.lower()`) y lo que no figure usa
  `stream.inference.confidence_threshold`. El loader los expone inmutables
  (`MappingProxyType`), coherente con el `frozen=True` de los dataclasses. Las
  claves son las clases del `.names` tal cual (p. ej. `tcok`, `chaq` o
  `tostada_comun_quemado`), no necesariamente compuestas; el producto se resuelve
  con `etiquetas.clases`/`backend/etiquetas.py`, sin `producto_id` en el catálogo.
- `device.*`: `hostname` lógico (no vacío), `ping_interval_seconds` (telemetría del
  monitor) y `registration_poll_interval_seconds` (polling del alta), ambos > 0.
  `public_url` (opcional, default `""`) es la URL completa (esquema + host + puerto)
  que el Go usa para enviar los renombres a esta Pi: la Pi la manda como
  `rename_url` en el alta y de ahí deriva el host:puerto de bind de su servidor
  HTTP (`DeviceService.derivar_bind`). Regla: URL vacía →
  `listen_address:local_port`; host loopback → `127.0.0.1`; hostname de túnel (no
  IP) → `127.0.0.1`; IP literal de la LAN → `0.0.0.0`. Puerto: el de la URL si
  viene; si no, 80/443 para host loopback y `local_port` para el resto.
  `listen_address`/`local_port` son el bind de respaldo cuando `public_url` está
  vacía. El anuncio al Go es la `public_url` tal cual. El par
  `public_url`+`local_port` se puede cambiar desde Configuración y re-bindea en
  caliente; el resto de `device.*` requiere reinicio. El alta usa la URL del
  arranque (`DeviceService._url`): el cambio en caliente sirve para rotar una Pi
  ya aprobada, no para habilitar el alta si la app arrancó con `public_url`
  vacía.
- `control.sensores`/`control.actuadores`: `driver` ∈ {`stub`, `simulado`,
  `modbus_rtu`, `modbus_tcp`}. `modbus_rtu` exige `serial_port`; `modbus_tcp`
  exige `host` y `port` (1–65535). `unit_id` ∈ [1,247]. `consigna_reg_temp1`/
  `consigna_reg_vel` (default 100/104) deben coincidir con el mapa del PLC
  (`backend/control/modbus.py`); la Pi es la única que escribe.
- `lote.*`: `habilitado`, `cierre_sin_detecciones_segundos` (inactividad para cerrar),
  `flush_segundos` (debe ser < cierre), `max_eventos_por_envio` ∈ [1,100],
  `cola_eventos` ≥ 1, `max_pendientes` ≥ `max_eventos_por_envio`,
  `refresco_catalogo_segundos` y el backoff de reintentos.
- `stream.publisher.server_url` es sólo la base RTSP (esquema + host + puerto, sin
  `/` final). El path de publicación se deriva como
  `<slug(nombre)>-<entrada|salida>` (`backend/streaming/url.py`) con el nombre y
  el tipo vivos del dispositivo; el front web arma el WHEP con el mismo path. Si
  el tipo todavía no se conoce (dispositivo sin registrar), no se publica hasta
  que el alta lo provea.
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

- Hay tests en `tests/backend/` y `tests/frontend/`. `pytest` no está en
  `requirements.txt`: instalalo aparte (`pip install pytest`) y corré
  `python -m pytest tests/backend -q` (o `pytest -q` para todo). La config de
  lint/format está en `pyproject.toml` y la CI la usa (`ruff check .` y
  `black --check .`).
- La ruta NPU y la captura real no están verificadas en dev (ver la sección anterior).
- El entrenamiento real (Ultralytics/torch) y la compilación HEF (DFC) tampoco son
  verificables en dev: ver `backend/ai_training/compile_instructions.md`.
- La verificación con PLC, cámara y Hailo reales sigue pendiente; el detalle está
  en `RECORDATORIOS.md` y en la sección anterior.
