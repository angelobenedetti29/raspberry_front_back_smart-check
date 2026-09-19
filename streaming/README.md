# Pipeline autónomo de streaming

Este paquete no depende de la GUI, FastAPI ni del backend central. El flujo es
`OpenCVFrameCapture` (BGR) → `FrameProcessor` → `JsonlDetectionStore` →
`FFmpegPublisher`.

## Configuración y modos

La configuración vive en `config.json` (raíz del repo) y se carga con
`smartcheck_config.load_config`; ya no se usan variables `STREAMING_*` ni flags
de CLI. Por defecto usa cámara `0`, `1280x720`, 30 fps, bitrate `2M`,
almacenamiento `streaming/data/detections.jsonl`, `ffmpeg` y encoder `libx264`.
Los fps admitidos son 20 y 30. La salida por defecto es el MediaMTX central,
`rtsp://smartcheck.duckdns.org:8554/entrada`. Para simular localmente, editar el
bloque `stream` de `config.json` (por ejemplo `output_url` a
`rtsp://127.0.0.1:8554/horno` e `inference.enabled` en `false`) y ejecutar
`python -m streaming.main`.

### Hailo en producción

Con `inference.enabled=true` se crea el
`backend.infrastructure.ai.YoloDetector` existente y se llama a
`detect_frame(frame)`. Con `inference.require_hailo=true`, el proceso falla
inmediatamente si el detector resultante no tiene `use_hailo=True`; no continúa
usando CPU/ONNX. En este modo `inference.model_path` debe apuntar a un archivo
`.hef` real compatible con la Raspberry Pi. No se proporciona ni se inventa una
ruta de modelo válida. `inference.labels_path` es opcional.

La NPU Hailo es exclusiva: no ejecutar dos procesos de streaming con inferencia
simultáneamente ni compartir el dispositivo con otro consumidor. Un modelo que
no sea Hailo hace que el servicio falle, en vez de degradar en silencio. Al
salir se llama a `release_hailo()`.

### Fuente y cámara

`stream.capture.source` acepta índice de cámara o ruta de vídeo. Los vídeos
locales se repiten por defecto (`stream.capture.loop_video`). Para un device
source se solicitan ancho, alto, fps y `CAP_PROP_BUFFERSIZE=1`; las propiedades
efectivas quedan disponibles en `OpenCVFrameCapture.effective_properties` para
diagnóstico. Los fallos de apertura, `read()` y excepciones del driver usan
backoff. El backoff solo vuelve al mínimo después de
`stream.capture.stable_frames` frames correctos, no únicamente al abrir.
`stream.capture.read_timeout_seconds` (5 segundos por defecto) supervisa el
heartbeat del hilo lector. Si V4L2/libcamera bloquea `read()` más tiempo, se
lanza `CaptureWatchdogError`, se detiene el bucle de forma controlada y el
ejecutable termina con código 1. No se intenta cancelar ni reemplazar el hilo
nativo bloqueado: `Restart=always` de systemd reinicia el proceso y el sistema
operativo libera la cámara.

## FFmpeg, RTSP y salud

FFmpeg recibe `rawvideo` BGR (`bgr24`) y publica H.264 por RTSP TCP. El input
del proceso se escribe mediante `os.write` y `select` sobre un descriptor no
bloqueante con timeout; el bucle principal solo hace `put_nowait` en una cola
acotada, descartando frames antiguos cuando está llena. Una pipe rota, proceso
muerto o falta de progreso cierra y reinicia FFmpeg con backoff exponencial
limitado. `stderr` se hereda para que systemd/journald conserve el diagnóstico
del encoder y RTSP.

La salida fija por defecto `yuv420p`, GOP de 2 segundos y `-bf 0`; se pueden
usar GOP de 1 o 2 segundos y encoder configurables desde
`stream.publisher.encoder`. Comprobar los encoders disponibles con
`ffmpeg -encoders` y editar `config.json` en consecuencia (`libx264` como
fallback CPU).

Verificar el resultado con `ffprobe -rtsp_transport tcp
rtsp://smartcheck.duckdns.org:8554/entrada` desde la Raspberry (o contra el
destino configurado en `stream.publisher.output_url`) y después con un cliente
WHEP/browser. `FFmpegPublisher.health()` informa estado real del proceso,
worker, cola, último progreso y reinicios.

## Persistencia

`JsonlDetectionStore` escribe en un worker con colas acotadas, prioriza
registros con detecciones y no deja que un error de disco derribe el stream.
Los registros sin detecciones se pueden muestrear con
`stream.storage.persist_no_detection_every` (10 por defecto, 0 los omite); las
detecciones siempre se intentan en su cola prioritaria. El archivo rota por
tamaño con `stream.storage.max_bytes` y conserva como máximo
`stream.storage.max_files` archivos. El apagado espera el drenaje limpio
de las colas dentro de un timeout.

## MediaMTX y destino de publicación

Por defecto el pipeline publica en el MediaMTX central:
`rtsp://smartcheck.duckdns.org:8554/entrada`. El ingest RTSP del central debe
aceptar publicación en el path `entrada`. El WHEP está disponible en
`https://smartcheck.duckdns.org:8889/entrada/whep` para un cliente externo
(navegador u otro reproductor WebRTC) que use el `whepUrl` publicado por el
dispositivo en el panel central; el frontend de escritorio PySide6 **no**
implementa WHEP/WebRTC: sólo publica por RTSP y previsualiza localmente.
Ajustar el host si el despliegue cambia.

`stream.publisher.output_url` permite apuntar a otro destino. Para desarrollo
local sin central, `../mediamtx/mediamtx.yml` levanta un MediaMTX local con el
path `horno`; en ese caso configurar
`output_url=rtsp://127.0.0.1:8554/horno` y consumir WHEP en
`http(s)://<host>:8889/horno/whep`. El ingest local está enlazado a
`127.0.0.1:8554`; no exponer ese puerto en red. WHEP/WebRTC usa normalmente TCP
8889 y UDP 8189; ajustar `webrtcAdditionalHosts` y los orígenes permitidos
(nunca CORS `*`) al host real.

## Pruebas sin hardware

Desde la raíz del repositorio:

```bash
python -m pytest -q streaming/tests
python -m compileall -q streaming
```

Las pruebas usan detector, captura (incluida una captura bloqueada) y publisher
falsos; no requieren Hailo, ONNX, FFmpeg ni MediaMTX. La simulación manual local
edita `config.json` (fuente de vídeo e inferencia en `false`) y luego ejecuta
`python -m streaming.main`.

## Validación de estrés y recuperación en hardware

Esta parte requiere todavía la Raspberry Pi, una cámara real, NPU Hailo y
MediaMTX/FFmpeg instalados:

1. **Cámara:** comprobar con `v4l2-ctl --list-formats-ext`, iniciar el servicio,
   revisar `effective_properties` y desenchufar/reconectar la cámara; confirmar
   recuperación después del umbral de frames estables. Simular además un
   `read()` bloqueado y confirmar el error de watchdog, salida 1 y reinicio de
   systemd, no una acumulación de hilos.
2. **FFmpeg/MediaMTX:** observar `journalctl -u streaming -f -u mediamtx`,
   detener FFmpeg o MediaMTX y confirmar que el pipeline sigue capturando y
   reinicia con backoff; inspeccionar `health()` y la ausencia de bloqueo al
   apagar.
3. **RTSP/WHEP:** ejecutar `ffprobe` local sobre RTSP, abrir el WHEP en un
   navegador/cliente WebRTC externo desde una máquina autorizada y verificar
   candidatos ICE por UDP 8189/TCP 8889. El frontend de escritorio no consume
   WHEP.
4. **Hailo:** usar el `.hef` real indicado por `inference.model_path`, arrancar
   una sola instancia y confirmar en logs que `require_hailo` no permite
   fallback CPU; comprobar que `release_hailo()` ocurre al parar.
5. **Disco:** llenar o montar en solo lectura el volumen de detecciones y
   confirmar logs de error, continuidad del vídeo, rotación y apagado limpio.
