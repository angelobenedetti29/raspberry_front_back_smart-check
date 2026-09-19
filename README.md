# Smart Check — Inspección de tostadas (Raspberry Pi)

Sistema de visión para una línea de tostadas: captura y procesa vídeo, detecta
tostadas (OK / quemadas), publica el stream por RTSP/WebRTC y registra lotes en
un servidor central.

El repositorio contiene tres componentes independientes más los recursos de IA:

- **Backend** (`backend/`): API REST en FastAPI con Arquitectura Limpia.
- **Frontend** (`frontend/`): aplicación de escritorio PySide6 (Qt) para el operador.
- **Streaming** (`streaming/`): pipeline autónomo captura → inferencia → publicación RTSP.

---

## Arquitectura

### Backend — Arquitectura Limpia

```
backend/
  domain/                 # Reglas de negocio puras (sin frameworks)
    entities/             # DetectionResult, LoteRequest, DeviceMetrics, SensorReadings
    interfaces/           # IImageDetector
  use_cases/              # Casos de uso (orquestan dominio + infraestructura)
    detect_and_notify.py  # Detecta tostadas y actualiza el tracker
    send_lote_request.py  # Envía un lote firmado (DeviceProof) al servidor central
    send_lote_inicio.py   # Inicia un lote firmado (DeviceProof) en el servidor central
    send_ping_request.py  # Envía telemetría periódica firmada (DeviceProof)
    finalize_lote.py      # Valida y finaliza un lote
    build_lote_payload.py # Métricas de lote a partir de detecciones + sensores
    toast_tracker.py      # Seguimiento y estado de tostadas
  infrastructure/         # Adaptadores concretos
    ai/yolo_detector.py   # YOLO ONNX (CPU) o Hailo HEF (NPU)
    http/requests_client.py
    sensors/simulated_sensors.py
    system/system_metrics.py  # CPU/RAM/disco/temperatura de Linux (Raspberry)
  app/                    # Composición FastAPI
    main.py               # create_app() + routers
    config.py             # Traduce config.json + secretos a Settings planos
    config_store.py       # Cache de config.json con recarga por mtime
    dependencies.py       # Wiring de casos de uso (Dependency Injection)
    telemetry.py          # TelemetryLoop: ping periódico en hilo daemon
    errors.py             # Traducción de errores de dominio a HTTP
    routers/              # status, lotes, detection, config
  tests/                  # Tests de casos de uso, sensores y endpoints
```

Regla de dependencias: `app → use_cases → domain` y `infrastructure → domain`.
El dominio y los casos de uso **no** importan FastAPI ni infraestructura.

### Identidad de dispositivo — `device_enrollment/`

Paquete liviano y compartido (sin FastAPI, AI/YOLO/PySide6 ni hardware) que
implementa la identidad Ed25519 del nodo y el transporte firmado:

```
device_enrollment/
  __main__.py         # python -m device_enrollment enroll|recover|reset
  cli.py              # Parseo de flags, env-file y prompt sin eco
  identity.py         # private-key.pem PKCS#8 + identity.json (0700/0600, lock, atómico)
  proof.py            # JWS compacto EdDSA (PyJWT + cryptography), thumbprint RFC7638
  transport.py        # POST firmado (Authorization: DeviceProof <jws>), sin redirects
  client.py           # Flujo recover-first + provisión y manejo de revocación
  envfile.py          # Lectura y limpieza quirúrgica de DEVICE_ENROLLMENT_CODE
  atomic.py           # tempfile + fsync + os.replace + fsync del directorio
```

El backend reutiliza `SignedTransport` en los tres emisores (`ping`, `lotes`,
`lotes/inicio`); ya no existe la API key compartida (`X-API-Key`).

**Endpoints:** `GET /api/status`, `POST /api/lotes/finalizar`,
`POST /api/lotes/iniciar`, `POST /api/detect`, `GET /api/config`,
`POST /api/config/reload`.

### Frontend — PySide6

```
frontend/
  main.py                 # Entrypoint delgado (argparse + QApplication)
  app.py                  # FactoryControlApp: composición y wiring
  config.py               # Constantes y resolución de rutas del proyecto
  services/
    streaming.py          # validate_stream_config, PreviewOnlyPublisher
    models.py             # Detección de plataforma + catálogo de modelos
  workers/
    detection_worker.py   # YOLODetectionThread (captura, inferencia, publicación)
  ui/
    theme.py              # Design system: tokens + build_stylesheet()
    components.py         # Card, SectionTitle, StatusPill, ActionButton, ...
    sidebar.py            # Navegación, selector de modelo, estado del detector
    video_panel.py        # Superficie de vídeo + estados
    gallery_panel.py      # Galería de vídeos locales
    alerts_panel.py       # Historial de alertas
    filters_panel.py      # Visibilidad de etiquetas
    assets/               # Recursos (chevron del combo, etc.)
```

`ui/` no importa backend ni streaming. La composición de la app vive en `app.py`;
`workers/detection_worker.py` y `services/models.py` son los otros puntos que
tocan backend/streaming. Todos los colores/espaciados viven en `ui/theme.py`.

### Streaming — Pipeline autónomo

`OpenCVFrameCapture` → `FrameProcessor` → `JsonlDetectionStore` → `FFmpegPublisher`.
No depende de la GUI ni de FastAPI. Ver `streaming/README.md`.

---

## Requisitos

- Python 3.11+ para el runtime del proyecto (probado con 3.14). El toolchain de
  compilación Hailo DFC es independiente y exige Python 3.10 (ver
  `guia_migracion_hailo.md`).
- Dependencias del frontend/raíz: `pip install -r requirements.txt`
  (opencv-python, numpy, PySide6, requests, cryptography, PyJWT).
- Dependencias del backend: `pip install -r backend/requirements.txt`
  (fastapi, uvicorn, python-multipart, numpy, opencv-python, requests,
  cryptography, PyJWT).
- Opcional: FFmpeg + MediaMTX para publicar RTSP/WebRTC.
- Opcional: Hailo (`hailo_platform`) en Raspberry Pi para inferencia en NPU.

---

## Configuración

Toda la configuración funcional vive en **`config.json`**, versionado en la raíz
del repositorio. Es la fuente única para el backend, el frontend, el pipeline de
`streaming/` y el CLI `device_enrollment`: ningún componente lee variables de
entorno para *valores* de configuración. Secciones del documento:

- `stream.capture` — fuente, resolución, fps, loop, buffer, frames estables y
  timeout de lectura.
- `stream.publisher` — `output_url` RTSP, bitrate, encoder, formato, GOP/B-frames
  y colas.
- `stream.inference` — `enabled`, `require_hailo`, `model_path`, `labels_path` y
  `confidence_threshold`.
- `stream.storage` — ruta y rotación del JSONL de detecciones.
- `stream.reconnect` — backoff de reconexión.
- `device` — `api_base_url`, `auth_audience`, `horno_id`, `producto_id` y
  `ping_interval_seconds`.
- `models` — `default_model_id`, `npu_model_id` y el `catalog` de modelos
  seleccionables.
- `paths` — `videos_dir`, `models_dir`, `data_dir`.
- `api` — `host`, `port` y `lote_endpoint` del backend local.

`schema_version` y `revision` son metadatos: `revision` se incrementa en cada
guardado y permite detectar escrituras concurrentes (`save_config(expected_revision=...)`).

### Edición desde la UI (app en marcha)

La configuración se edita desde la UI del frontend con la aplicación corriendo.
Al guardar, el frontend escribe `config.json` de forma atómica y notifica al
backend local (`POST /api/config/reload`). Cada hoja modificada se clasifica
según lo que exige aplicarla (`frontend/services/config_apply.py`, `plan_changes`):

| Categoría | Ejemplos | Aplicación |
|---|---|---|
| **hot** | `device.*`, `stream.storage.*`, `stream.reconnect.*` y colas/timeouts del publisher | En caliente: el backend recarga sin reiniciar la app |
| **detector** | `stream.inference.*` | Se reconstruye el detector de inferencia |
| **pipeline** | `stream.capture.source/width/height/fps` y `stream.publisher.output_url/bitrate/encoder/...` | Se reinicia el pipeline de captura/publicación |
| **restart_app** | `models.*`, `paths.*` | Reinicio total del proceso |

El reinicio total se pide saliendo con el código de salida `75`; `run.py` lo
reconoce y relanza el frontend. `api.host`/`api.port` los toma `run.py` al
arrancar, así que para re-vincular el socket hay que reiniciar `run.py`.

### Secretos — `.env` en la raíz

Solo dos valores son secretos y viven fuera de `config.json`, en el `.env` de la
**raíz** del repositorio (o en el archivo que indique `SMARTCHECK_ENV_FILE`):

- `DEVICE_ENROLLMENT_CODE`: código de invitación de un solo uso; solo lo consume
  el aprovisionamiento.
- `DEVICE_IDENTITY_DIR`: directorio de la identidad Ed25519 (default
  `/var/lib/smart-check/device`).

Plantilla: `.env.example` (raíz). No hay `backend/.env` ni
`backend/.env.example`.

### Variables de bootstrap

Solo las *rutas* se controlan por entorno; el resto se ignora:

- `SMARTCHECK_ROOT`: raíz del repositorio (si no se define, se busca
  `pyproject.toml` subiendo desde `smartcheck_config/`).
- `SMARTCHECK_CONFIG`: ruta alternativa a `config.json`.
- `SMARTCHECK_ENV_FILE`: ruta alternativa al `.env` de secretos.

### Endpoints de configuración

- `GET /api/config`: snapshot efectivo del runtime (revisión, estado de
  enrolamiento y settings, sin secretos).
- `POST /api/config/reload`: fuerza la recarga de `config.json` y aplica los
  cambios hot. Responde `200` aunque el JSON sea inválido (`ok=false`; conserva
  el último valor bueno).

---

## Cómo ejecutar

### Frontend + Backend juntos

`run.py` levanta el backend (FastAPI) y el frontend (PySide6) al mismo tiempo.
Toma el host y el puerto de `config.api`; si ya hay un backend activo ahí, lo
reutiliza; si no, lo inicia y lo detiene al cerrar el frontend (o con `Ctrl+C`).
Si el frontend pide un reinicio total (código de salida `75`), lo relanza. Si el
intérprete actual no tiene las dependencias pero existe `.venv/`, se re-ejecuta
solo con `.venv/bin/python`.

```bash
python run.py   # host/puerto y fuente de vídeo salen de config.json
```

### Backend

```bash
# desde la raíz del repositorio (host/puerto deben coincidir con config.api)
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
# o con recarga:
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
```

`backend/app/main.py` expone la app como `backend.app.main:app` (resultado de
`create_app()`); no define bloque `__main__`, así que se arranca con Uvicorn.
`run.py` levanta el backend con este mismo comando usando `config.api.host` y
`config.api.port`.

La configuración del backend es `config.json` (ver **[Configuración](#configuración)**)
y sus secretos se resuelven desde el `.env` de la raíz; no hay `backend/.env`.
`backend/app/config_store.py` cachea el documento, lo relee por `mtime` y
conserva el último valor bueno si el JSON queda inválido. Los valores que antes
eran variables de entorno ahora viven en `config.device`: `api_base_url`
(servidor central Go; el código normaliza la URL y le agrega `/api/v1` si falta,
así que terminar en `/api/v1` no es un requisito), `auth_audience`, `horno_id`,
`producto_id` y `ping_interval_seconds`. `horno_id` / `producto_id` son los
defaults de `POST /api/lotes/iniciar` cuando el body no los incluye;
`ping_interval_seconds` es el periodo de telemetría (el esquema rechaza valores
<= 0).

La telemetría y los emisores firmados sólo se habilitan cuando existe una
identidad **enrolled** cargada desde `DEVICE_IDENTITY_DIR` (`.env`). Ya **no** se
usa `X-API-Key`: los POST a `/api/v1/dispositivos/ping`, `/api/v1/lotes` y
`/api/v1/lotes/inicio` llevan `Authorization: DeviceProof <jws>`. Si no hay
servidor central configurado, los endpoints de detección siguen funcionando;
`POST /api/lotes/finalizar` e `POST /api/lotes/iniciar` (los endpoints locales
del backend, distintos de los `/api/v1/...` del servidor central) devolverán 502.

> **Identidad y enrolamiento.** Enrolar, recuperar, revocar o re-provisionar un
> nodo con el servicio en marcha no se adopta solo: tras el enrolamiento, forzá
> la recarga (`POST /api/config/reload`, que relee la identidad) o reiniciá el
> backend.
>
> 1. Ejecutar `python -m device_enrollment enroll` (o `recover`) y esperar el
>    resultado `phase=enrolled`.
> 2. Forzar la recarga (`curl -X POST http://127.0.0.1:8000/api/config/reload`)
>    o reiniciar el backend (`systemctl restart smart-check-backend`, o relanzar
>    `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000`).
>
> Tras una revocación, el camino es `reset` → nueva invitación → `enroll`
> (clave nueva) → reinicio. El cambio de `device.api_base_url`/`auth_audience`
> sí se aplica en caliente al guardar `config.json`, aunque el `apiBaseUrl`
> persistido en `identity.json` se reescribe con el valor realmente usado en el
> próximo `recover`/`enroll`.

### Aprovisionamiento de identidad (CLI)

El nodo se enrola una sola vez contra el servidor central. El CLI es liviano
(no importa FastAPI, YOLO/PySide6 ni hardware) y **nunca** acepta el código por
argumento. La red (`device.api_base_url` y `device.auth_audience`) sale de
`config.json`; los secretos (`DEVICE_ENROLLMENT_CODE`, `DEVICE_IDENTITY_DIR`)
salen del env-file (por defecto el `.env` de la raíz, o `SMARTCHECK_ENV_FILE`).
Los flags de red e identidad son overrides:

```bash
# Enrolar (recover-first; el código se lee del env/archivo o se pide sin eco)
python -m device_enrollment enroll
# o con un env-file explícito:
python -m device_enrollment enroll --env-file /etc/smart-check/device.env
# overrides puntuales de red/identidad:
python -m device_enrollment enroll \
  --api-base-url https://api.example.com/api/v1 \
  --audience https://api.example.com/api/v1 \
  --identity-dir /var/lib/smart-check/device

# Recuperar una identidad existente (sólo prueba firmada, sin código)
python -m device_enrollment recover --env-file /etc/smart-check/device.env

# Reset deliberado (necesario tras una revocación): borra la clave local
python -m device_enrollment reset --identity-dir /var/lib/smart-check/device --yes
```

Flags disponibles: `--env-file /ruta/absoluta`, `--api-base-url`, `--audience`,
`--identity-dir` (los tres últimos son overrides de `config.json`/`.env`). El
código sale de `DEVICE_ENROLLMENT_CODE` (env o archivo seleccionado) o de un
prompt sin eco. Flujo: al iniciar con una clave pendiente
se llama primero a `/api/v1/dispositivos/enrollments/recover`; con `404` se
redime la invitación en `/api/v1/dispositivos/provision` con la misma clave. Un
resultado ambiguo (timeout/5xx) se reintenta vía recover y **nunca** rota la
clave. `credential_revoked` es un alto definitivo que requiere `reset` y una
invitación nueva (clave nueva). Tras un enrolamiento durable el código se
elimina del proceso y del archivo de entorno seleccionado, preservando el resto
del contenido. Un shell/contenedor padre no puede borrar su propio entorno: si
exportó el código, conviene limpiarlo aparte.

### Frontend

La fuente de vídeo inicial sale de `config.json` (galería `paths.videos_dir` y
la degradación documentada en `frontend/config.py`). `--source` se acepta por
compatibilidad pero se ignora: la configuración es la única fuente de verdad.

```bash
python -m frontend.main
# también: python frontend/main.py
```

### Streaming

```bash
python -m streaming.main
```

No hay flags ni variables de entorno: el pipeline lee `config.json`. Detalles en
`streaming/README.md`.

---

## Tests

Los tests cubren el pipeline de streaming, el backend (casos de uso, sensores y
endpoints con `TestClient`), la identidad firmada de dispositivo (permisos,
escritura atómica, reinicio, flujo recover/provisión, detección de tampering y
logging sin secretos) y el frontend (lógica del worker, ciclo de apagado y
construcción de la ventana en modo offscreen).

Evidencia cross-language: `device_enrollment/tests/vectors/device_proof_vector.json`
es un vector determinístico (clave semilla fija, JWS exacto, body/method/path y
`bhash`/fingerprint esperados) generado por el código de firma real
(`device_enrollment.proof`). El padre puede verificarlo con el verificador Go
real; `device_enrollment/tests/test_device_proof_vector.py` regenera y valida el archivo.

Entorno recomendado (el Python del sistema puede estar gestionado por el SO):

```bash
python -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt -r backend/requirements.txt -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

---

## Dónde tocar cada cosa

| Quiero… | Ir a… |
|---|---|
| Cambiar reglas/entidades de negocio | `backend/domain/` |
| Agregar o cambiar un caso de uso | `backend/use_cases/` |
| Cambiar un endpoint o su validación | `backend/app/routers/` |
| Cambiar la integración con IA / red | `backend/infrastructure/` |
| Cambiar la configuración global | `config.json` + `smartcheck_config/` |
| Cambiar la aplicación de config en la UI | `frontend/services/config_apply.py` |
| Cambiar identidad/firma/CLI del dispositivo | `device_enrollment/` |
| Cambiar la apariencia o tokens visuales | `frontend/ui/theme.py` |
| Cambiar un panel o componente de UI | `frontend/ui/` |
| Cambiar comportamiento de la ventana | `frontend/app.py` |
| Cambiar el worker de vídeo/inferencia | `frontend/workers/` |
| Ajustar el pipeline de streaming | `streaming/` |
| Reentrenar/exportar el modelo | `ai_training/` |

---

## Hardware

- **Cámara:** se detecta automáticamente en Raspberry Pi; el modelo se elige en
  la UI (YOLOv11 COCO / Tostadas V1 / V2 / YOLOv8s Hailo-8L).
- **NPU Hailo:** si `hailo_platform` está disponible y hay `.hef`, `YoloDetector`
  redirige automáticamente de `.onnx` a `.hef`. La NPU es exclusiva: no ejecutar
  dos procesos con inferencia a la vez.
- **Video en red:** MediaMTX (`mediamtx/mediamtx.yml`) y unidades systemd en
  `streaming/systemd/`.

Las lecturas de sensores (temperaturas de horno/combustión y velocidad de cinta)
provienen hoy de un `SimulatedSensorProvider`; para hardware real, implementar
un proveedor concreto en `backend/infrastructure/sensors/`.

> **Binarios de modelos y vídeos de muestra.** Por decisión del repositorio, los
> pesos y artefactos binarios (`.onnx`, `.hef`, `.pt`) y los vídeos de muestra
> ya no se versionan (ver `.gitignore`). Genéralos o descárgalos desde
> `ai_training/` (entrenamiento/exportación y compilación Hailo; pasos en
> `guia_migracion_hailo.md`) y colócalos en `ai_training/models/`. `streaming`
> con `stream.inference.require_hailo=true` en `config.json` necesita que
> `stream.inference.model_path` apunte a un `.hef` presente en disco; si falta o
> no es Hailo, el proceso falla en vez de degradar a CPU/ONNX.
