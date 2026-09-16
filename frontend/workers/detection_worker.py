"""Worker de captura, inferencia y publicación del frontend.

``YOLODetectionThread`` es un ``QThread`` que abre la fuente de vídeo, ejecuta el
caso de uso de detección, mantiene las métricas del lote y publica cada frame
(RTSP + previsualización local).

El hilo es dueño de la captura y del publisher: nadie más los libera mientras él
pueda estar dentro de ``read()``. Las piezas que tocan hardware o red son
inyectables (``detect_use_case``, ``capture_factory``, ``publisher_factory`` y
``sensor_provider``), lo que permite probarlo sin cámara, sin FFmpeg y sin
modelo.
"""

import cv2
import numpy as np
import time
import threading

from datetime import datetime
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from streaming.config import StreamConfig
from streaming.publisher import FFmpegPublisher

from backend.app.config import get_settings
from backend.domain.entities.sensor_readings import SensorReadings
from backend.infrastructure.sensors.simulated_sensors import SimulatedSensorProvider
from backend.use_cases.build_lote_payload import DEFAULT_PRODUCT_ID, build_lote_payload

from frontend.services.streaming import validate_stream_config

# Segundos sin ver ninguna tostada tras los cuales se cierra el lote en curso.
INACTIVITY_SECONDS = 10.0
# Segundos mínimos entre dos alertas consecutivas de la ruta sin tracker.
MOCK_ALERT_THROTTLE_SECONDS = 5.0
# Estilo del recuadro y del texto dibujados sobre cada detección.
OVERLAY_BOX_THICKNESS = 2
OVERLAY_FONT_SCALE = 0.5
OVERLAY_TEXT_OFFSET = 10
# Colores BGR del recuadro.
OK_COLOR = (0, 255, 0)
BURNT_COLOR = (0, 0, 255)


def _is_burnt(detection) -> bool:
    """Indica si una detección corresponde a una tostada quemada.

    Cubre las dos rutas posibles:

    - Detecciones del tracker: traen ``state`` y una etiqueta normalizada
      (``TCQ`` / ``TCOK``). El tracker garantiza ``label == "TCQ"`` si y solo si
      ``state == "burnt"``, así que ambos criterios coinciden.
    - Detecciones sin tracker (por ejemplo con un modelo COCO): no traen
      ``state`` y el único criterio disponible es la etiqueta del modelo.
    """
    state = getattr(detection, "state", None)
    label = (getattr(detection, "label", "") or "").lower()
    return state == "burnt" or "quemada" in label or label == "tcq"


class YOLODetectionThread(QThread):
    """Hilo de captura + inferencia + publicación para una fuente de vídeo.

    Señales
    -------
    change_pixmap_signal(QImage): frame listo para la previsualización local.
    burned_toast_alert_signal(str): se confirmó una tostada quemada.
    lote_completed_signal(dict): hay un lote listo para registrar.
    """

    change_pixmap_signal = Signal(QImage)
    burned_toast_alert_signal = Signal(str)
    lote_completed_signal = Signal(dict)

    def __init__(self, source_file, detect_use_case, stream_config=None,
                 capture_factory=None, publisher_factory=None, sensor_provider=None,
                 producto_id=None):
        super().__init__()
        # La app entrega la ruta ya resuelta: absoluta, o "0" para la cámara.
        self.source_file = source_file
        self.detect_use_case = detect_use_case
        self.stream_config = validate_stream_config(
            stream_config if stream_config is not None else StreamConfig.from_env()
        )
        self.capture_factory = capture_factory or cv2.VideoCapture
        self.publisher_factory = publisher_factory or FFmpegPublisher
        self.sensor_provider = (
            sensor_provider if sensor_provider is not None else SimulatedSensorProvider()
        )
        self.producto_id = (
            producto_id
            if producto_id is not None
            else (get_settings().default_producto_id or DEFAULT_PRODUCT_ID)
        )

        # Recursos que posee el hilo; se crean dentro de run().
        self.capture = None
        self.publisher = None

        # Control de parada.
        self.running = True
        self._stop_event = threading.Event()

        # Filtros de dibujo activos.
        self.show_ok_toasts = True
        self.show_burnt_toasts = True

        # Estado de las alertas y del ritmo de frames.
        self.alerted_ids = set()
        self._last_mock_alert_time = 0.0
        self.last_toast_seen_time = 0.0
        # StreamConfig valida fps ∈ {20, 30}, así que no hay división por cero.
        self.frame_time = 1.0 / self.stream_config.fps

        # Reinicia el tracker y el registro de alertas al arrancar un stream nuevo.
        if hasattr(self.detect_use_case, "reset_tracker"):
            self.detect_use_case.reset_tracker()

        # Acumuladores de métricas del lote.
        self.inicio_at = datetime.now()
        self.seen_toasts = {}
        self.sensor_samples: list[SensorReadings] = []

    def run(self):
        """Bucle principal: abre la fuente, procesa frames y publica hasta parar."""
        cv_source = 0 if self.source_file == "0" else self.source_file
        cap = None
        publisher = None
        try:
            # Ambos recursos pertenecen al hilo. En particular, la GUI nunca
            # libera una captura mientras OpenCV pueda estar dentro de read().
            cap = self.capture = self.capture_factory(cv_source)
            publisher = self.publisher = self.publisher_factory(self.stream_config)

            if not cap.isOpened():
                print(f"No se pudo abrir la fuente de video: {cv_source}")
                return

            # El publisher no debe arrancar hasta que la fuente sea usable.
            if self._stop_event.is_set():
                return
            try:
                publisher.start()
            except Exception as exc:
                # La previsualización local sigue siendo útil aunque falle FFmpeg.
                print(f"[Thread] No se pudo iniciar el publisher RTSP: {exc}")

            self.last_toast_seen_time = time.time()

            while self.running and not self._stop_event.is_set():
                start_time = time.time()
                ret, frame = cap.read()
                if not ret:
                    break

                image = frame.copy()
                # Ejecutar el Caso de Uso de Detección.
                try:
                    detections = self.detect_use_case.execute(image)
                except Exception as e:
                    print(f"[Thread] Error al ejecutar inferencia YOLO: {e}")
                    detections = []

                # Registrar tostadas vistas y su estado final.
                has_visible_toasts = False
                for det in detections:
                    toast_id = getattr(det, "id", None)
                    if toast_id is not None:
                        has_visible_toasts = True
                        if toast_id not in self.seen_toasts:
                            self.seen_toasts[toast_id] = getattr(det, "state", "unknown")
                        elif _is_burnt(det):
                            # Una vez quemada, la tostada no vuelve a "ok".
                            self.seen_toasts[toast_id] = "burnt"

                # Simular lecturas de sensores en tiempo real.
                try:
                    self.sensor_samples.append(self.sensor_provider.read())
                except Exception as exc:
                    print(f"[Thread] Error al leer sensores: {exc}")

                # Cierre automático del lote por inactividad.
                if has_visible_toasts:
                    self.last_toast_seen_time = time.time()
                elif self.seen_toasts and (time.time() - self.last_toast_seen_time > INACTIVITY_SECONDS):
                    print("[Thread] Inactividad detectada (10s sin tostadas). Finalizando y enviando lote actual...")
                    self.emit_batch_metrics()
                    self.inicio_at = datetime.now()
                    self.seen_toasts.clear()
                    self.sensor_samples.clear()
                    self.last_toast_seen_time = time.time()

                # Avisar solo de las tostadas quemadas cuyo aviso no se emitió aún.
                for det in detections:
                    toast_id = getattr(det, "id", None)

                    if toast_id is not None:
                        if _is_burnt(det) and toast_id not in self.alerted_ids:
                            self.alerted_ids.add(toast_id)
                            self.burned_toast_alert_signal.emit(
                                f"¡ALERTA TOSTADA #{toast_id} QUEMADA! (Conf: {det.confidence:.2f})"
                            )
                    elif _is_burnt(det):
                        # Ruta sin tracker: se limita la frecuencia de avisos
                        # porque no hay identificador con el que deduplicar.
                        current_time = time.time()
                        if (current_time - self._last_mock_alert_time) > MOCK_ALERT_THROTTLE_SECONDS:
                            self._last_mock_alert_time = current_time
                            self.burned_toast_alert_signal.emit(
                                f"¡ALERTA TOSTADA QUEMADA! (Conf: {det.confidence:.2f})"
                            )

                self._draw_detections(image, detections)

                # Este es el frame canónico tanto para RTSP como para la preview.
                output = self._prepare_frame(image)

                try:
                    published = publisher.publish(output)
                    if published is False:
                        print("[Thread] Publisher RTSP rechazó un frame; continúa la vista local")
                except Exception as exc:
                    print(f"[Thread] Error publicando frame RTSP: {exc}")

                self.change_pixmap_signal.emit(self._to_qt_image(output))

                # Event.wait es interrumpible, a diferencia de time.sleep.
                elapsed = time.time() - start_time
                sleep_time = self.frame_time - elapsed
                if sleep_time > 0:
                    self._stop_event.wait(sleep_time)
        except Exception as exc:
            print(f"[Thread] Error en el procesamiento de video: {exc}")
        finally:
            # Conservar este orden: detener FFmpeg antes de liberar la entrada.
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

    # ------------------------------------------------------------------ frame
    def _draw_detections(self, image, detections):
        """Dibuja recuadro y etiqueta de cada detección que deba verse."""
        for det in detections:
            left, top, width, height = det.bbox
            label = det.label
            confidence = det.confidence
            toast_id = getattr(det, "id", None)

            is_burnt = _is_burnt(det)
            if is_burnt and not self.show_burnt_toasts:
                continue
            if not is_burnt and not self.show_ok_toasts:
                continue

            color = BURNT_COLOR if is_burnt else OK_COLOR
            cv2.rectangle(
                image,
                (left, top),
                (left + width, top + height),
                color,
                OVERLAY_BOX_THICKNESS,
            )
            if toast_id is not None:
                label_text = f"Tostada #{toast_id} ({label}) {confidence:.2f}"
            else:
                label_text = f"{label} {confidence:.2f}"
            cv2.putText(
                image,
                label_text,
                (left, top - OVERLAY_TEXT_OFFSET),
                cv2.FONT_HERSHEY_SIMPLEX,
                OVERLAY_FONT_SCALE,
                color,
                OVERLAY_BOX_THICKNESS,
            )
        return image

    def _prepare_frame(self, image):
        """Normaliza el frame a BGR uint8 contiguo de tres canales."""
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
        return np.ascontiguousarray(output)

    @staticmethod
    def _to_qt_image(frame):
        """Convierte un frame BGR en un QImage RGB con copia propia de los datos."""
        rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        return QImage(rgb_image.data, w, h, ch * w, QImage.Format_RGB888).copy()

    # ------------------------------------------------------------------ lote
    def emit_batch_metrics(self):
        """Emite el resumen del lote acumulado, si hay tostadas registradas."""
        if not self.seen_toasts:
            return

        fin_at = datetime.now()
        payload = build_lote_payload(
            self.inicio_at,
            fin_at,
            self.seen_toasts,
            self.sensor_samples,
            producto_id=self.producto_id,
        )

        self.lote_completed_signal.emit(payload)

    # ------------------------------------------------------------------ api
    def stop(self):
        """Pide al bucle que termine (no bloquea: run() hace la limpieza)."""
        self.running = False
        self._stop_event.set()
