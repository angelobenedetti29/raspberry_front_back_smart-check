import cv2
import numpy as np
import time
import threading

from datetime import datetime
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from streaming.config import StreamConfig
from streaming.publisher import FFmpegPublisher

from backend.domain.entities.sensor_readings import SensorReadings
from backend.infrastructure.sensors.simulated_sensors import SimulatedSensorProvider
from backend.use_cases.build_lote_payload import build_lote_payload

from frontend.services.paths import resolve_path
from frontend.services.streaming import validate_stream_config


class YOLODetectionThread(QThread):
    change_pixmap_signal = Signal(QImage)
    iot_status_changed_signal = Signal()
    burned_toast_alert_signal = Signal(str)
    lote_completed_signal = Signal(dict)

    def __init__(self, source_file, detect_use_case, stream_config=None,
                 capture_factory=None, publisher_factory=None, sensor_provider=None):
        super().__init__()
        self.source_file = resolve_path(source_file)
        self.detect_use_case = detect_use_case
        self.stream_config = validate_stream_config(
            stream_config if stream_config is not None else StreamConfig.from_env()
        )
        self.capture_factory = capture_factory or cv2.VideoCapture
        self.publisher_factory = publisher_factory or FFmpegPublisher
        self.sensor_provider = (
            sensor_provider if sensor_provider is not None else SimulatedSensorProvider()
        )
        self.capture = None
        self.publisher = None
        self.running = True
        self._stop_event = threading.Event()
        self.show_ok_toasts = True
        self.show_burnt_toasts = True
        
        # Reset tracker and alert records when starting a new stream
        if hasattr(self.detect_use_case, "reset_tracker"):
            self.detect_use_case.reset_tracker()
        self.alerted_ids = set()

        # Acumuladores de métricas del lote
        self.inicio_at = datetime.now()
        self.seen_toasts = {}
        self.sensor_samples: list[SensorReadings] = []

    def run(self):
        cv_source = 0 if self.source_file == "0" else self.source_file
        cap = None
        publisher = None
        try:
            # Both resources belong to the worker.  In particular, the GUI never
            # releases a capture while OpenCV may still be inside read().
            cap = self.capture = self.capture_factory(cv_source)
            publisher = self.publisher = self.publisher_factory(self.stream_config)

            if not cap.isOpened():
                print(f"No se pudo abrir la fuente de video: {cv_source}")
                return

            # The publisher must not be started until the source is usable.
            if self._stop_event.is_set():
                return
            try:
                publisher.start()
            except Exception as exc:
                # A local preview is still useful when FFmpeg is unavailable.
                print(f"[Thread] No se pudo iniciar el publisher RTSP: {exc}")

            self.frame_time = 1.0 / self.stream_config.fps

            # Generar colores de clases de forma determinista
            try:
                class_names = self.detect_use_case.detector.get_class_names()
            except Exception:
                class_names = []

            colors = {name: (0, 255, 0) for name in class_names}  # Todas las tostadas OK en verde

            self.last_toast_seen_time = time.time()

            while self.running and not self._stop_event.is_set():
                start_time = time.time()
                ret, frame = cap.read()
                if not ret:
                    break

                image = frame.copy()
                # Ejecutar el Caso de Uso de Detección e IoT
                try:
                    detections = self.detect_use_case.execute(image)
                except Exception as e:
                    print(f"[Thread] Error al ejecutar inferencia YOLO: {e}")
                    detections = []

                # Registrar tostadas vistas y su estado final
                has_visible_toasts = False
                for det in detections:
                    toast_id = getattr(det, "id", None)
                    state = getattr(det, "state", "unknown")
                    if toast_id is not None:
                        has_visible_toasts = True
                        if toast_id not in self.seen_toasts:
                            self.seen_toasts[toast_id] = state
                        elif state == "burnt":
                            self.seen_toasts[toast_id] = "burnt"

                # Simular lecturas de sensores en tiempo real
                try:
                    self.sensor_samples.append(self.sensor_provider.read())
                except Exception as exc:
                    print(f"[Thread] Error al leer sensores: {exc}")

                # Lógica de cierre automático por inactividad
                if has_visible_toasts:
                    self.last_toast_seen_time = time.time()
                elif self.seen_toasts and (time.time() - self.last_toast_seen_time > 10.0):
                    print("[Thread] Inactividad detectada (10s sin tostadas). Finalizando y enviando lote actual...")
                    self.emit_batch_metrics()
                    self.inicio_at = datetime.now()
                    self.seen_toasts.clear()
                    self.sensor_samples.clear()
                    self.last_toast_seen_time = time.time()

                # Filtrar tostadas quemadas activas cuya alerta no ha sido emitida por este hilo
                new_alerts = False
                for det in detections:
                    toast_id = getattr(det, "id", None)
                    state = getattr(det, "state", "unknown")

                    if toast_id is not None:
                        if state == "burnt" and toast_id not in self.alerted_ids:
                            self.alerted_ids.add(toast_id)
                            self.burned_toast_alert_signal.emit(f"¡ALERTA TOSTADA #{toast_id} QUEMADA! (Conf: {det.confidence:.2f})")
                            new_alerts = True
                    else:
                        is_burnt = "quemada" in det.label.lower() or det.label.lower() == "tcq"
                        if is_burnt:
                            current_time = time.time()
                            if not hasattr(self, "_last_mock_alert_time") or (current_time - self._last_mock_alert_time) > 5.0:
                                self._last_mock_alert_time = current_time
                                self.burned_toast_alert_signal.emit(f"¡ALERTA TOSTADA QUEMADA! (Conf: {det.confidence:.2f})")
                                new_alerts = True

                if new_alerts:
                    self.iot_status_changed_signal.emit()

                # Dibujar rectángulos y etiquetas
                for det in detections:
                    left, top, width, height = det.bbox
                    label = det.label
                    confidence = det.confidence
                    toast_id = getattr(det, "id", None)
                    state = getattr(det, "state", "unknown")

                    is_burnt = state == "burnt" or "quemada" in label.lower() or label.lower() == "tcq"
                    if is_burnt and not self.show_burnt_toasts:
                        continue
                    if not is_burnt and not self.show_ok_toasts:
                        continue

                    color = (0, 0, 255) if is_burnt else colors.get(label, (0, 255, 0))
                    cv2.rectangle(image, (left, top), (left + width, top + height), color, 2)
                    if toast_id is not None:
                        label_text = f"Tostada #{toast_id} ({label}) {confidence:.2f}"
                    else:
                        label_text = f"{label} {confidence:.2f}"
                    cv2.putText(image, label_text, (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

                # This is the one canonical frame for both RTSP and the local preview.
                output = cv2.resize(
                    image,
                    (self.stream_config.width, self.stream_config.height),
                    interpolation=cv2.INTER_AREA,
                )
                if output.ndim == 2:
                    output = cv2.cvtColor(output, cv2.COLOR_GRAY2BGR)
                elif output.ndim != 3 or output.shape[2] != 3:
                    raise ValueError("la salida de vídeo no tiene tres canales BGR")
                if output.dtype != np.uint8:
                    output = output.astype(np.uint8)
                output = np.ascontiguousarray(output)

                try:
                    published = publisher.publish(output)
                    if published is False:
                        print("[Thread] Publisher RTSP rechazó un frame; continúa la vista local")
                except Exception as exc:
                    print(f"[Thread] Error publicando frame RTSP: {exc}")

                rgb_image = cv2.cvtColor(output, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb_image.shape
                qt_image = QImage(
                    rgb_image.data, w, h, ch * w, QImage.Format_RGB888
                ).copy()
                self.change_pixmap_signal.emit(qt_image)

                # Event.wait is interruptible, unlike time.sleep.
                elapsed = time.time() - start_time
                sleep_time = self.frame_time - elapsed
                if sleep_time > 0:
                    self._stop_event.wait(sleep_time)
        except Exception as exc:
            print(f"[Thread] Error en el procesamiento de video: {exc}")
        finally:
            # Keep this order: stop any FFmpeg activity before releasing input.
            if publisher is not None:
                try:
                    publisher.stop()
                except Exception as exc:
                    print(f"[Thread] Error al detener el publisher RTSP: {exc}")
            if cap is not None:
                try:
                    cap.release()
                except Exception as exc:
                    print(f"[Thread] Error al liberar la captura: {exc}")

        if self.seen_toasts:
            self.emit_batch_metrics()

    def emit_batch_metrics(self):
        if not self.seen_toasts:
            return

        fin_at = datetime.now()
        payload = build_lote_payload(
            self.inicio_at, fin_at, self.seen_toasts, self.sensor_samples
        )

        self.lote_completed_signal.emit(payload)

    def stop(self):
        self.running = False
        self._stop_event.set()
