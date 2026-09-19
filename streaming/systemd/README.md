# Instalación de servicios

Los paths de `WorkingDirectory`, usuario y binario son ejemplos: ajustarlos a
la instalación de la Raspberry Pi.

1. Instalar MediaMTX desde su release para la arquitectura de la Raspberry Pi
   y copiar el binario a `/usr/local/bin/mediamtx`. El RTSP de ingest queda
   enlazado a localhost; no abrir 8554 en el firewall.
2. Crear el usuario `mediamtx`, copiar `mediamtx/mediamtx.yml` a
   `/etc/mediamtx/mediamtx.yml`, y sustituir el origen permitido y
   `webrtcAdditionalHosts` por valores reales.
3. Instalar el checkout del proyecto y FFmpeg. Confirmar aceleración con
   `ffmpeg -encoders`; definir `encoder` en `config.json`
   (`h264_v4l2m2m` solo si existe, `libx264` si no).
4. Editar el `config.json` versionado en la raíz del repositorio. Es la única
   fuente de configuración: ya no hay `EnvironmentFile` ni flags de CLI. Ajustar
   sobre todo:

   ```json
   "stream": {
     "capture": { "source": "0" },
     "publisher": {
       "output_url": "rtsp://smartcheck.duckdns.org:8554/entrada",
       "encoder": "libx264"
     },
     "inference": {
       "enabled": true,
       "require_hailo": true,
       "model_path": "/ruta/absoluta/real/modelo.hef"
     },
     "storage": { "path": "/var/lib/tesis-streaming/detections.jsonl" }
   }
   ```

   `model_path` debe apuntar al `.hef` real instalado en esa Pi; no existe una
   ruta predeterminada válida. Un modelo que no sea `.hef` produce fallo rápido,
   sin fallback CPU/ONNX. Hailo es exclusivo: mantener una sola instancia que
   use la NPU. `read_timeout_seconds` permite ajustar el watchdog de `read()`;
   una lectura nativa bloqueada termina el proceso con código 1 y
   `Restart=always` lo reinicia.
5. Crear el directorio de almacenamiento con permisos para `tesis` y copiar
   las unidades:

   ```bash
   sudo install -d -o tesis -g tesis /var/lib/tesis-streaming
   sudo install -D -m 0644 mediamtx.service /etc/systemd/system/mediamtx.service
   sudo install -D -m 0644 streaming.service /etc/systemd/system/streaming.service
   sudo install -D -m 0644 smartcheck-config.path /etc/systemd/system/smartcheck-config.path
   sudo install -D -m 0644 smartcheck-config-reload.service /etc/systemd/system/smartcheck-config-reload.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now mediamtx.service streaming.service smartcheck-config.path
   sudo systemctl status mediamtx.service streaming.service smartcheck-config.path
   ```

   `smartcheck-config.path` observa `config.json` con `PathModified`: como el
   guardado es atómico (`os.replace`), systemd detecta el reemplazo del path y
   dispara `smartcheck-config-reload.service`, que reinicia `streaming.service`
   como proceso nuevo.

6. Con el destino central por defecto
   (`output_url=rtsp://smartcheck.duckdns.org:8554/entrada`), el WHEP
   para un cliente externo (navegador/reproductor WebRTC) es
   `https://smartcheck.duckdns.org:8889/entrada/whep` (el `whepUrl` que publica
   el dispositivo en el panel central). El frontend de escritorio PySide6 no
   implementa WHEP/WebRTC. El MediaMTX local de los pasos 1-2 solo es
   necesario si se publica localmente
   (`output_url=rtsp://127.0.0.1:8554/horno`); en ese caso abrir solo
   8889/TCP y 8189/UDP para WHEP/WebRTC, sin exponer 8554/TCP. En despliegues
   HTTPS, configurar el proxy TLS y usar el origen HTTPS exacto del frontend,
   nunca CORS `*`.
