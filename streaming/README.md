# Pipeline autónomo de streaming

Este paquete no depende de la GUI, FastAPI ni del backend central. El flujo es
`OpenCVFrameCapture` (BGR) → `FrameProcessor` → `JsonlDetectionStore` →
`FFmpegPublisher`.

## Configuración y modos

Se puede usar CLI o variables `STREAMING_*`. Por defecto usa cámara `0`,
`1280x720`, 30 fps, bitrate `2M`, almacenamiento
`streaming/data/detections.jsonl`, `ffmpeg` y encoder `libx264`. Los fps
admitidos son 20 y 30. La salida por defecto es el MediaMTX central,
`rtsp://smartcheck.duckdns.org:8554/entrada`. La simulación local no activa
inferencia y puede redirigir la salida al MediaMTX local si se levanta:

```bash
python -m streaming.main --source multimedia/videos/test1.mp4 --no-inference \
  --output-url rtsp://127.0.0.1:8554/horno
```

También se puede configurar con `STREAMING_SOURCE`, `STREAMING_WIDTH`,
`STREAMING_HEIGHT`, `STREAMING_FPS`, `STREAMING_OUTPUT_URL`,
`STREAMING_BITRATE`, `STREAMING_INFERENCE`, `STREAMING_REQUIRE_HAILO`,
`STREAMING_MODEL`, `STREAMING_LABELS`, `STREAMING_STORAGE`, `STREAMING_FFMPEG`
y `STREAMING_ENCODER`. Los parámetros de estabilidad, colas, rotación,
pixel format y GOP tienen sus correspondientes variables visibles en `--help`.

### Hailo en producción

Con `STREAMING_INFERENCE=true` se crea el `backend.infrastructure.ai.YoloDetector`
existente y se llama a `detect_frame(frame)`. Con
`STREAMING_REQUIRE_HAILO=true`, el proceso falla inmediatamente si el detector
resultante no tiene `use_hailo=True`; no continúa usando CPU/ONNX. En este modo
`STREAMING_MODEL` debe ser definido por la instalación y apuntar a un archivo
`.hef` real compatible con la Raspberry Pi. No se proporciona ni se inventa
una ruta de modelo válida. `STREAMING_LABELS` es opcional.

La NPU Hailo es exclusiva: no ejecutar dos procesos de streaming con inferencia
simultáneamente ni compartir el dispositivo con otro consumidor. El servicio
systemd exige el EnvironmentFile y además ejecuta
`--inference --require-hailo`; esas flags tienen precedencia sobre cualquier
valor del EnvironmentFile. Un modelo que no sea Hailo hace que el servicio
falle, en vez de degradar en silencio. Al salir se llama a `release_hailo()`.

### Fuente y cámara

`--source` acepta índice de cámara o ruta de vídeo. Los vídeos locales se
repiten por defecto (`--no-loop-video`). Para un device source se solicitan
ancho, alto, fps y `CAP_PROP_BUFFERSIZE=1`; las propiedades efectivas quedan
disponibles en `OpenCVFrameCapture.effective_properties` para diagnóstico.
Los fallos de apertura, `read()` y excepciones del driver usan backoff. El
backoff solo vuelve al mínimo después de
`STREAMING_CAPTURE_STABLE_FRAMES` frames correctos, no únicamente al abrir.
`STREAMING_CAPTURE_READ_TIMEOUT` (5 segundos por defecto) supervisa el
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
usar GOP de 1 o 2 segundos y encoder configurables. Comprobar los encoders
disponibles con `ffmpeg -encoders`:

```bash
STREAMING_ENCODER=h264_v4l2m2m python -m streaming.main --source 0
# CPU:
STREAMING_ENCODER=libx264 python -m streaming.main --source 0
```

Verificar el resultado con `ffprobe -rtsp_transport tcp
rtsp://smartcheck.duckdns.org:8554/entrada` desde la Raspberry (o contra el
destino configurado en `STREAMING_OUTPUT_URL`) y después con un cliente
WHEP/browser. `FFmpegPublisher.health()` informa estado real del proceso,
worker, cola, último progreso y reinicios.

## Persistencia

`JsonlDetectionStore` escribe en un worker con colas acotadas, prioriza
registros con detecciones y no deja que un error de disco derribe el stream.
Los registros sin detecciones se pueden muestrear con
`STREAMING_PERSIST_NO_DETECTION_EVERY=10` (0 los omite); las detecciones siempre
se intentan en su cola prioritaria. El archivo rota por tamaño con
`STREAMING_STORAGE_MAX_BYTES` y conserva como máximo
`STREAMING_STORAGE_MAX_FILES` archivos. El apagado espera el drenaje limpio
de las colas dentro de un timeout.

## MediaMTX y destino de publicación

Por defecto el pipeline publica en el MediaMTX central:
`rtsp://smartcheck.duckdns.org:8554/entrada`. El ingest RTSP del central debe
aceptar publicación en el path `entrada`; el frontend lo consume por WHEP en
`https://smartcheck.duckdns.org:8889/entrada/whep` (usar el `whepUrl` del
dispositivo en el panel central). Ajustar el host si el despliegue cambia.

`STREAMING_OUTPUT_URL` (o `--output-url`) permite apuntar a otro destino. Para
desarrollo local sin central, `../mediamtx/mediamtx.yml` levanta un MediaMTX
local con el path `horno`; en ese caso publicar con
`STREAMING_OUTPUT_URL=rtsp://127.0.0.1:8554/horno` y consumir WHEP en
`http(s)://<host>:8889/horno/whep`. El ingest local está enlazado a
`127.0.0.1:8554`; no exponer ese puerto en red. WHEP/WebRTC usa normalmente TCP
8889 y UDP 8189; ajustar `webrtcAdditionalHosts` y los orígenes permitidos
(nunca CORS `*`) al host real.

## Pruebas sin hardware

Desde la raíz del repositorio:

```bash
python -m unittest discover -s streaming/tests -p 'test_*.py'
python -m compileall -q streaming
python -m streaming.main --help
```

Las pruebas usan detector, captura (incluida una captura bloqueada) y publisher
falsos; no requieren Hailo, ONNX, FFmpeg ni MediaMTX. La simulación manual local, si se dispone de
FFmpeg/MediaMTX en el mismo host, es:

```bash
python -m streaming.main --source multimedia/videos/test1.mp4 --no-inference
```

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
3. **RTSP/WHEP:** ejecutar `ffprobe` local sobre RTSP, abrir el WHEP en el
   frontend desde una máquina autorizada y verificar candidatos ICE por UDP
   8189/TCP 8889.
4. **Hailo:** usar el `.hef` real indicado por `STREAMING_MODEL`, arrancar una
   sola instancia y confirmar en logs que `require_hailo` no permite fallback
   CPU; comprobar que `release_hailo()` ocurre al parar.
5. **Disco:** llenar o montar en solo lectura el volumen de detecciones y
   confirmar logs de error, continuidad del vídeo, rotación y apagado limpio.
