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
    config.py             # Settings desde backend/.env
    dependencies.py       # Wiring de casos de uso (Dependency Injection)
    telemetry.py          # TelemetryLoop: ping periódico en hilo daemon
    errors.py             # Traducción de errores de dominio a HTTP
    routers/              # status, lotes, detection
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
`POST /api/lotes/iniciar`, `POST /api/detect`.

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

## Cómo ejecutar

### Frontend + Backend juntos

`run.py` levanta el backend (FastAPI) y el frontend (PySide6) al mismo tiempo.
Si ya hay un backend activo en `http://localhost:8000`, lo reutiliza; si no, lo
inicia y lo detiene al cerrar el frontend (o con `Ctrl+C`). Si el intérprete
actual no tiene las dependencias pero existe `.venv/`, se re-ejecuta solo con
`.venv/bin/python`.

```bash
python run.py              # frontend con su fuente por defecto
python run.py --source 0   # los argumentos extra se pasan al frontend
```

### Backend

```bash
# desde la raíz del repositorio
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
# o con recarga:
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

`backend/app/main.py` expone la app como `backend.app.main:app` (resultado de
`create_app()`); no define bloque `__main__`, así que se arranca con Uvicorn.
`run.py` levanta el backend con este mismo comando.

Configuración en `backend/.env` (se carga automáticamente). Plantilla segura en
`backend/.env.example`:

```
DEVICE_API_BASE_URL=https://servidor-central.example.com/api/v1
DEVICE_AUTH_AUDIENCE=https://servidor-central.example.com/api/v1
DEVICE_ENROLLMENT_CODE=
DEVICE_IDENTITY_DIR=/var/lib/smart-check/device
HORNO_ID=...
PRODUCTO_ID=...
PING_INTERVAL_SECONDS=10
```

- `DEVICE_API_BASE_URL`: servidor central Go; el código normaliza la URL y le
  agrega `/api/v1` si falta, así que terminar en `/api/v1` no es un requisito.
  El backend ignora los alias legacy `CENTRAL_BASE_URL` y `CENTRAL_LOTE_BASE_URL`,
  que sólo lee el CLI de aprovisionamiento (`device_enrollment/cli.py`) como
  fallback (el primero tiene prioridad sobre el segundo).
- `DEVICE_AUTH_AUDIENCE`: audiencia compartida con el verificador Go.
- `DEVICE_ENROLLMENT_CODE`: código de invitación de un solo uso. Sólo se
  consume durante el aprovisionamiento y se elimina al completarse.
- `DEVICE_IDENTITY_DIR`: directorio de identidad (default
  `/var/lib/smart-check/device`; dueño del servicio, `0700`).
- `HORNO_ID` / `PRODUCTO_ID`: defaults usados por `POST /api/lotes/iniciar`
  cuando el body no los incluye.
- `PING_INTERVAL_SECONDS`: periodo de telemetría (default 10; valores inválidos
  o <= 0 se ignoran).

La telemetría y los emisores firmados sólo se habilitan cuando existe una
identidad **enrolled** cargada desde `DEVICE_IDENTITY_DIR`. Ya **no** se usa
`X-API-Key`: los POST a `/api/v1/dispositivos/ping`, `/api/v1/lotes` y
`/api/v1/lotes/inicio` llevan `Authorization: DeviceProof <jws>`. Si no hay
servidor central configurado, los endpoints de detección siguen
funcionando; `POST /api/lotes/finalizar` e `POST /api/lotes/iniciar` (los
endpoints locales del backend, distintos de los `/api/v1/...` del servidor
central) devolverán 502.

> **Restricción operativa — la identidad se carga una sola vez al arrancar.**
> `backend/app/dependencies.py` instancia `IdentityStore` y llama a
> `try_load_enrolled()` en tiempo de import. La telemetría y los emisores firmados
> quedan cableados con esa identidad (y con `dispositivoId`, `DEVICE_API_BASE_URL`
> y `DEVICE_AUTH_AUDIENCE` de ese momento). Por lo tanto, enrolar, recuperar,
> revocar o re-provisionar un nodo **mientras el servicio está corriendo no surte
> efecto hasta reiniciarlo**:
>
> 1. Ejecutar `python -m device_enrollment enroll` (o `recover`) y esperar el
>    resultado `phase=enrolled`.
> 2. Reiniciar el backend (por ejemplo `systemctl restart smart-check-backend`,
>    o relanzar `python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000`).
>
> Tras una revocación, el camino es `reset` → nueva invitación → `enroll`
> (clave nueva) → reinicio. El cambio de `DEVICE_API_BASE_URL`/audiencia también
> requiere reinicio, aunque el `apiBaseUrl` persistido en `identity.json` se
> reescribe con el valor realmente usado en el próximo `recover`/`enroll`.

### Aprovisionamiento de identidad (CLI)

El nodo se enrola una sola vez contra el servidor central. El CLI es liviano
(no importa FastAPI, YOLO/PySide6 ni hardware) y **nunca** acepta el código por
argumento:

```bash
# Enrolar (recover-first; el código se lee del env/archivo o se pide sin eco)
python -m device_enrollment enroll --env-file /etc/smart-check/device.env
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
`--identity-dir`. El código sale de `DEVICE_ENROLLMENT_CODE` (env o archivo
seleccionado) o de un prompt sin eco. Flujo: al iniciar con una clave pendiente
se llama primero a `/api/v1/dispositivos/enrollments/recover`; con `404` se
redime la invitación en `/api/v1/dispositivos/provision` con la misma clave. Un
resultado ambiguo (timeout/5xx) se reintenta vía recover y **nunca** rota la
clave. `credential_revoked` es un alto definitivo que requiere `reset` y una
invitación nueva (clave nueva). Tras un enrolamiento durable el código se
elimina del proceso y del archivo de entorno seleccionado, preservando el resto
del contenido. Un shell/contenedor padre no puede borrar su propio entorno: si
exportó el código, conviene limpiarlo aparte.

### Frontend

```bash
# cámara local (índice 0) o un vídeo de multimedia/videos/
python -m frontend.main --source 0
python -m frontend.main --source road.mp4
# también: python frontend/main.py --source road.mp4
```

### Streaming

```bash
python -m streaming.main --source 0 --no-inference
STREAMING_ENCODER=h264_v4l2m2m python -m streaming.main --source 0
```

Variables y detalles en `streaming/README.md`.

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
> con `--require-hailo`/`STREAMING_REQUIRE_HAILO=true` necesita el `.hef`
> presente en disco (`STREAMING_MODEL`); si falta, el proceso falla en vez de
> degradar a CPU/ONNX.
