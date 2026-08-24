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
   `ffmpeg -encoders`; definir `STREAMING_ENCODER=h264_v4l2m2m` solo si existe.
   Usar `libx264` si no existe el encoder V4L2.
4. Crear `/etc/tesis/streaming.env`, sin secretos. El archivo es obligatorio
   para systemd. Debe contener `STREAMING_MODEL` con la ruta absoluta al
   `.hef` real instalado en esa Pi; no existe una ruta predeterminada válida.
   Un ejemplo de variables no relacionadas con la ruta concreta es:

   ```text
   STREAMING_SOURCE=0
   STREAMING_OUTPUT_URL=rtsp://127.0.0.1:8554/horno
   STREAMING_STORAGE=/var/lib/tesis-streaming/detections.jsonl
   STREAMING_INFERENCE=true
   STREAMING_REQUIRE_HAILO=true
   # STREAMING_MODEL debe apuntar al .hef real de esta instalación.
   STREAMING_ENCODER=libx264
   ```

   La unidad también fuerza mediante CLI `--inference --require-hailo`, por lo
   que esas flags no pueden ser anuladas por el EnvironmentFile. Un modelo que
   no sea `.hef` produce fallo rápido, sin fallback CPU/ONNX. Hailo es
   exclusivo: mantener una sola instancia que use la NPU.
   `STREAMING_CAPTURE_READ_TIMEOUT` permite ajustar el watchdog de `read()`;
   una lectura nativa bloqueada termina el proceso con código 1 y
   `Restart=always` lo reinicia.
5. Crear el directorio de almacenamiento con permisos para `tesis` y copiar
   las unidades:

   ```bash
   sudo install -d -o tesis -g tesis /var/lib/tesis-streaming
   sudo install -D -m 0644 mediamtx.service /etc/systemd/system/mediamtx.service
   sudo install -D -m 0644 streaming.service /etc/systemd/system/streaming.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now mediamtx.service streaming.service
   sudo systemctl status mediamtx.service streaming.service
   ```

6. Abrir solo 8889/TCP y 8189/UDP para WHEP/WebRTC. 8554/TCP es localhost y
   no debe exponerse. WHEP usa
   `http://<webrtcAdditionalHosts>:8889/horno/whep`; RTSP solo desde la Pi usa
   `rtsp://127.0.0.1:8554/horno`. En despliegues HTTPS, configurar también el
   proxy TLS y usar el origen HTTPS exacto del frontend, nunca CORS `*`.
