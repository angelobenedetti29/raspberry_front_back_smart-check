# Smart Check — Inspección de tostadas (Raspberry Pi)

Sistema de visión e IoT para una línea de tostadas: captura y procesa vídeo,
detecta tostadas (OK / quemadas), controla dispositivos IoT, publica el stream
por RTSP/WebRTC y registra lotes en un servidor central.

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
    entities/             # DetectionResult, IoTDevice, LoteRequest, SensorReadings
    interfaces/           # IImageDetector, IIoTController, IHttpClient, ISensorProvider
  use_cases/              # Casos de uso (orquestan dominio + infraestructura)
    detect_and_notify.py  # Detecta y acciona IoT / notifica
    control_device.py     # Enciende/apaga dispositivos
    send_lote_request.py  # Envía un lote al servidor central
    finalize_lote.py      # Valida y finaliza un lote
    build_lote_payload.py # Métricas de lote a partir de detecciones + sensores
    toast_tracker.py      # Seguimiento y estado de tostadas
  infrastructure/         # Adaptadores concretos
    ai/yolo_detector.py   # YOLO ONNX (CPU) o Hailo HEF (NPU)
    iot/mock_controller.py
    http/requests_client.py
    sensors/simulated_sensors.py
  app/                    # Composición FastAPI
    main.py               # create_app() + routers
    config.py             # Settings desde backend/.env
    dependencies.py       # Wiring de casos de uso (Dependency Injection)
    errors.py             # Traducción de errores de dominio a HTTP
    routers/              # status, devices, lotes, detection
  tests/                  # Tests de casos de uso, sensores y endpoints
```

Regla de dependencias: `app → use_cases → domain` y `infrastructure → domain`.
El dominio y los casos de uso **no** importan FastAPI ni infraestructura.

**Endpoints:** `GET /api/status`, `GET /api/devices`,
`POST /api/devices/{id}/turn-on|turn-off|toggle`,
`POST /api/lotes/finalizar`, `POST /api/detect`.

### Frontend — PySide6

```
frontend/
  main.py                 # Entrypoint delgado (argparse + QApplication)
  app.py                  # FactoryControlApp: composición y wiring
  config.py               # Constantes (ventana, catálogo de modelos, endpoints)
  services/
    paths.py              # resolve_path (resolución de rutas del proyecto)
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
    iot_panel.py          # Relés / buzzer
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

- Python 3.11+ (probado con 3.14).
- Dependencias del frontend/raíz: `pip install -r requirements.txt`
  (opencv-python, numpy, PySide6, requests).
- Dependencias del backend: `pip install -r backend/requirements.txt`
  (fastapi, uvicorn, python-multipart, numpy, opencv-python, requests).
- Opcional: FFmpeg + MediaMTX para publicar RTSP/WebRTC.
- Opcional: Hailo (`hailo_platform`) en Raspberry Pi para inferencia en NPU.

---

## Cómo ejecutar

### Backend

```bash
# desde la raíz del repositorio
python -m backend.app.main
# o con recarga:
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

Configuración en `backend/.env` (se carga automáticamente):

```
CENTRAL_LOTE_BASE_URL=https://servidor-central.example.com
CENTRAL_LOTE_API_KEY=...
```

Si no hay servidor central configurado, los endpoints de dispositivos y
detección siguen funcionando; `POST /api/lotes/finalizar` devolverá 502.

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
endpoints con `TestClient`) y el frontend (lógica del worker, ciclo de apagado y
construcción de la ventana en modo offscreen).

Entorno recomendado (el Python del sistema puede estar gestionado por el SO):

```bash
python -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt -r backend/requirements.txt httpx pytest
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

---

## Dónde tocar cada cosa

| Quiero… | Ir a… |
|---|---|
| Cambiar reglas/entidades de negocio | `backend/domain/` |
| Agregar o cambiar un caso de uso | `backend/use_cases/` |
| Cambiar un endpoint o su validación | `backend/app/routers/` |
| Cambiar la integración con IA / IoT / red | `backend/infrastructure/` |
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
`ISensorProvider` en `backend/infrastructure/sensors/`.
