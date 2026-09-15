import os

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QScrollArea,
    QFrame,
)
from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QImage

from streaming.config import StreamConfig

# Backend imports (Clean Architecture)
from backend.infrastructure.ai.yolo_detector import YoloDetector
from backend.infrastructure.iot.mock_controller import MockIoTController
from backend.infrastructure.http.requests_client import RequestsHttpClient
from backend.use_cases.detect_and_notify import DetectAndNotifyUseCase
from backend.use_cases.control_device import ControlDeviceUseCase

from frontend.config import (
    DEFAULT_SOURCE,
    LOCAL_LOTE_ENDPOINT,
    MODEL_CATALOG,
    VIDEO_EXTENSIONS,
    VIDEOS_DIR_PSEUDO_PATH,
    WINDOW_HEIGHT,
    WINDOW_TITLE,
    WINDOW_WIDTH,
)
from frontend.services.models import (
    default_model_index,
    detect_platform,
    model_for_index,
)
from frontend.services.paths import resolve_path
from frontend.services.streaming import PreviewOnlyPublisher, validate_stream_config
from frontend.ui.alerts_panel import AlertsPanel
from frontend.ui.filters_panel import FiltersPanel
from frontend.ui.gallery_panel import GalleryPanel
from frontend.ui.iot_panel import IoTPanel
from frontend.ui.sidebar import Sidebar
from frontend.ui.theme import (
    RIGHT_COLUMN_MIN_WIDTH,
    SIDEBAR_MAX_WIDTH,
    SIDEBAR_MIN_WIDTH,
    build_stylesheet,
)
from frontend.ui.video_panel import VideoPanel
from frontend.workers.detection_worker import YOLODetectionThread


# Below this width the three-column layout reflows into a single stacked,
# vertically scrollable column (targets the 800x480 Raspberry Pi panel).
COMPACT_BREAKPOINT = 1024
MIN_WINDOW_WIDTH = 640
MIN_WINDOW_HEIGHT = 420


class _FallbackDetector:
    """ESTA CLASE SIMULA UN DETECTOR DE OBJETOS CUANDO NO SE PUEDE CARGAR EL MODELO YOLO REAL.
    Se utiliza para permitir que la aplicación siga funcionando incluso si el modelo YOLO no se puede inicializar correctamente.
    """

    def __init__(self, error=None):
        self.error = "" if error is None else str(error)
        self.use_hailo = False

    def detect_frame(self, frame):
        return []

    def get_class_names(self):
        return ["Tostada Quemada", "tostadas ok"]


def _detector_pill_state(detector):
    """Le pasamos el detector y nos devuelve si es hailo, """
    if getattr(detector, "use_hailo", False):
        return "Hailo NPU Activo", "on"
    if isinstance(detector, _FallbackDetector):
        return f"Simulado (Error: {detector.error[:25]})", "warning"
    return "ONNX Activo", "info"


class FactoryControlApp(QMainWindow):

    # CONSTRUCTOR
    def __init__(self, default_source=DEFAULT_SOURCE):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.setStyleSheet(build_stylesheet())

        # Inicializar componentes del Backend 
        self.iot_controller = MockIoTController()
        self.http_client = RequestsHttpClient()

        # Detección automática de plataforma (Raspberry Pi con chip Hailo)
        platform = detect_platform()
        self.is_running_on_npu = platform.is_npu

        # Rutas iniciales de modelos según la plataforma
        self.current_model, self.current_names = model_for_index(
            default_model_index(self.is_running_on_npu)
        )

        # Instanciar el detector de YOLO (Capa de infraestructura)
        try:
            model_path = resolve_path(self.current_model)
            names_path = resolve_path(self.current_names)
            self.detector = YoloDetector(model_path=model_path, names_path=names_path)
        except Exception as e:
            print(f"[GUI App] Error al inicializar detector YOLO: {e}")
            self.detector = _FallbackDetector(e)

        # Instanciar Casos de Uso
        self.detect_use_case = DetectAndNotifyUseCase(self.detector, self.iot_controller, self.http_client)
        self.control_device_use_case = ControlDeviceUseCase(self.iot_controller)

        # Filtrado de clases visible por defecto
        self.show_ok_toasts = True
        self.show_burnt_toasts = True

        # Construcción de la UI y primer refresco de estados
        self._build_ui()
        self.update_filter_button_styles()
        self.update_iot_status_labels()

        self.yolo_thread = None
        self._shutdown_thread = None
        self._pending_action = None
        self._closing_requested = False
        self._recovery_required = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)
        self.play_internal_target(default_source)

    # ------------------------------------------------------------------ ui
    def _build_ui(self):
        """Compose the panels; all widget construction lives in frontend.ui."""
        central_widget = QWidget()
        central_widget.setObjectName("Root")
        self.setCentralWidget(central_widget)

        outer_layout = QVBoxLayout(central_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # One global scroll surface; it only shows bars when the stage is
        # larger than the viewport (compact/reflow mode).
        self.global_scroll = QScrollArea()
        self.global_scroll.setWidgetResizable(True)
        self.global_scroll.setFrameShape(QFrame.NoFrame)
        self.global_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer_layout.addWidget(self.global_scroll)

        self.stage = QWidget()
        self.stage.setObjectName("Root")
        self.stage_layout = QGridLayout(self.stage)
        self.stage_layout.setContentsMargins(16, 16, 16, 16)
        self.stage_layout.setSpacing(16)
        self.global_scroll.setWidget(self.stage)

        # --- Sidebar (camera toggle + model selector) ---
        self.sidebar = Sidebar(
            [entry[0] for entry in MODEL_CATALOG],
            current_index=default_model_index(self.is_running_on_npu),
        )
        #self.sidebar.set_detector_pill(*_detector_pill_state(self.detector))
        self.sidebar.camera_toggled.connect(self.toggle_camera)
        self.sidebar.model_changed.connect(self.change_model)

        self.nav_buttons = self.sidebar.nav_buttons
        self.model_selector = self.sidebar.model_selector

        # --- Center column: live video + video gallery ---
        self.center_column = QWidget()
        self.center_column.setObjectName("Column")
        center_layout = QVBoxLayout(self.center_column)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(16)

        self.video_panel = VideoPanel()
        self.video_label = self.video_panel.video_label
        center_layout.addWidget(self.video_panel, stretch=7)

        self.gallery_panel = GalleryPanel()
        self.gallery_panel.video_selected.connect(self.play_internal_target)
        self.videos_layout = self.gallery_panel.list_layout
        center_layout.addWidget(self.gallery_panel, stretch=3)

        # --- Right column: alerts, filters, IoT ---
        self.right_column = QWidget()
        self.right_column.setObjectName("Column")
        self.right_column.setMinimumWidth(RIGHT_COLUMN_MIN_WIDTH)
        right_layout = QVBoxLayout(self.right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(16)

        self.alerts_panel = AlertsPanel()
        self.alerts_log_layout = self.alerts_panel.list_layout
        right_layout.addWidget(self.alerts_panel, stretch=2)

        self.filters_panel = FiltersPanel()
        self.filters_panel.filter_ok_toggled.connect(self.toggle_filter_ok)
        self.filters_panel.filter_burnt_toggled.connect(self.toggle_filter_burnt)
        self.btn_filter_ok = self.filters_panel.btn_filter_ok
        self.btn_filter_burnt = self.filters_panel.btn_filter_burnt
        right_layout.addWidget(self.filters_panel, stretch=1)

        self.iot_panel = IoTPanel()
        self.iot_panel.toaster_toggled.connect(self.toggle_toaster)
        self.iot_panel.alarm_toggled.connect(self.toggle_alarm)
        self.toaster_btn = self.iot_panel.toaster_btn
        self.alarm_btn = self.iot_panel.alarm_btn
        self.toaster_lbl = self.iot_panel.toaster_lbl
        self.alarm_lbl = self.iot_panel.alarm_lbl
        right_layout.addWidget(self.iot_panel, stretch=1)

        # Place the three blocks according to the initial window width.
        self._compact = None
        self._apply_breakpoint(self.width() < COMPACT_BREAKPOINT)

        self._load_gallery()

    def _apply_breakpoint(self, compact):
        """Arrange panels for the current width without rebuilding widgets.

        Wide: sidebar | (video + gallery) | (alerts + filters + IoT).
        Compact: everything stacked in one column inside the global scroll.
        """
        grid = self.stage_layout
        for widget in (self.sidebar, self.center_column, self.right_column):
            grid.removeWidget(widget)
        for column in range(3):
            grid.setColumnStretch(column, 0)
        for row in range(3):
            grid.setRowStretch(row, 0)

        if compact:
            self.sidebar.setMinimumWidth(0)
            self.sidebar.setMaximumWidth(16777215)
            grid.addWidget(self.sidebar, 0, 0, 1, 1)
            grid.addWidget(self.center_column, 1, 0, 1, 1)
            grid.addWidget(self.right_column, 2, 0, 1, 1)
            grid.setColumnStretch(0, 1)
            grid.setRowStretch(1, 1)
        else:
            self.sidebar.setMinimumWidth(SIDEBAR_MIN_WIDTH)
            self.sidebar.setMaximumWidth(SIDEBAR_MAX_WIDTH)
            grid.addWidget(self.sidebar, 0, 0, 1, 1)
            grid.addWidget(self.center_column, 0, 1, 1, 1)
            grid.addWidget(self.right_column, 0, 2, 1, 1)
            grid.setColumnStretch(1, 7)
            grid.setColumnStretch(2, 3)
            grid.setRowStretch(0, 1)

        self._compact = compact

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "stage_layout"):
            return
        compact = event.size().width() < COMPACT_BREAKPOINT
        if compact != self._compact:
            self._apply_breakpoint(compact)

    def _load_gallery(self):
        videos_path = resolve_path(VIDEOS_DIR_PSEUDO_PATH)
        names = []
        if os.path.exists(videos_path):
            names = sorted(
                f for f in os.listdir(videos_path) if f.endswith(VIDEO_EXTENSIONS)
            )
        if names:
            self.gallery_panel.set_videos(names)
        else:
            self.gallery_panel.show_empty(
                "Sin vídeos", f"No se encontró la ruta: {videos_path}"
            )

    # -------------------------------------------------------------- filters
    def toggle_filter_ok(self, checked=None):
        self.show_ok_toasts = not self.show_ok_toasts
        self.update_filter_button_styles()
        if self.yolo_thread is not None:
            self.yolo_thread.show_ok_toasts = self.show_ok_toasts

    def toggle_filter_burnt(self, checked=None):
        self.show_burnt_toasts = not self.show_burnt_toasts
        self.update_filter_button_styles()
        if self.yolo_thread is not None:
            self.yolo_thread.show_burnt_toasts = self.show_burnt_toasts

    def update_filter_button_styles(self):
        self.filters_panel.update_state(self.show_ok_toasts, self.show_burnt_toasts)

    # ------------------------------------------------------------------- iot
    def toggle_toaster(self, checked=None):
        is_on = self.iot_controller.get_status("rele_tostadora")
        if is_on:
            self.control_device_use_case.turn_off_device("rele_tostadora")
        else:
            self.control_device_use_case.turn_on_device("rele_tostadora")
        self.update_iot_status_labels()

    def toggle_alarm(self, checked=None):
        is_on = self.iot_controller.get_status("alarma_buzzer")
        if is_on:
            self.control_device_use_case.turn_off_device("alarma_buzzer")
        else:
            self.control_device_use_case.turn_on_device("alarma_buzzer")
        self.update_iot_status_labels()

    def update_iot_status_labels(self):
        toaster_on = self.iot_controller.get_status("rele_tostadora")
        alarm_on = self.iot_controller.get_status("alarma_buzzer")
        self.iot_panel.update_state(toaster_on, alarm_on)

    # ---------------------------------------------------------------- alerts
    def add_alert_log(self, message, tone="danger"):
        self.alerts_panel.add_alert(message, tone=tone)

    # Lógica de cambio de modelo dinámico
    def change_model(self, index):
        if self._recovery_required:
            self._show_recovery_required()
            return

        active_worker = (
            (self.yolo_thread is not None and self.yolo_thread.isRunning())
            or self._shutdown_thread is not None
        )
        stream_config, publisher_factory = self._stream_setup(
            allow_preview_fallback=not active_worker
        )
        if stream_config is None:
            return

        if index == 0:
            self.current_model, self.current_names = model_for_index(index)
            print("[INFO] Frente cambiado al Modelo Original (YOLOv11 COCO)")
        elif index == 1:
            self.current_model, self.current_names = model_for_index(index)
            print("[INFO] Frente cambiado al Modelo de Tostadas V1 (Personalizado)")
        elif index == 2:
            self.current_model, self.current_names = model_for_index(index)
            print("[INFO] Frente cambiado al Modelo de Tostadas V2 (Personalizado)")
        elif index == 3:
            self.current_model, self.current_names = model_for_index(index)
            print("[INFO] Frente cambiado al Modelo Acelerado por NPU (YOLOv8s Hailo-8L COCO)")

        model_path = resolve_path(self.current_model)
        names_path = resolve_path(self.current_names)

        # Keep the exact active source, rather than reconstructing a gallery
        # filename from it after the asynchronous shutdown.
        active_source = None
        if self.yolo_thread is not None and self.yolo_thread.isRunning():
            active_source = self.yolo_thread.source_file
            if active_source != "0":
                active_source = os.path.abspath(active_source)

        if active_source is not None:
            self._request_thread_shutdown(
                pending_action=lambda: self._finish_model_change(
                    model_path, names_path, active_source,
                    stream_config, publisher_factory,
                )
            )
            return

        self._finish_model_change(
            model_path, names_path, active_source,
            stream_config, publisher_factory,
        )

    def _finish_model_change(self, model_path, names_path, active_source,
                             stream_config, publisher_factory):
        """Apply a model change only after the previous worker has finished."""
        # This method is only used as the finished callback for a running
        # worker, or directly when no worker is active.  A timeout clears the
        # pending action, so this release cannot race a live capture.
        if hasattr(self, 'detector') and self.detector is not None:
            print("[GUI App] Liberando recursos del detector anterior...")
            if hasattr(self.detector, 'release_hailo'):
                try:
                    self.detector.release_hailo()
                except Exception as e:
                    print(f"[GUI App] Error al liberar NPU: {e}")
            del self.detector
            self.detector = None

        try:
            self.detector = YoloDetector(model_path=model_path, names_path=names_path)
        except Exception as e:
            print(f"[GUI App] Error al cambiar detector YOLO: {e}")
            self.detector = _FallbackDetector(e)

        self.detect_use_case = DetectAndNotifyUseCase(self.detector, self.iot_controller, self.http_client)
        if hasattr(self, "sidebar"):
            self.sidebar.set_detector_pill(*_detector_pill_state(self.detector))

        if active_source is not None:
            self._start_internal_target(
                active_source,
                source_path_override=active_source,
                stream_config=stream_config,
                publisher_factory=publisher_factory,
                model_path=model_path,
                names_path=names_path,
            )

    def _disconnect_worker_signals(self, thread):
        """Disconnect UI consumers while a worker is winding down."""
        for signal in (
            thread.change_pixmap_signal,
            thread.lote_completed_signal,
            thread.iot_status_changed_signal,
            thread.burned_toast_alert_signal,
        ):
            try:
                signal.disconnect()
            except (TypeError, RuntimeError):
                pass

    def _show_recovery_required(self):
        self._recovery_required = True
        message = "Se requiere recuperación: no se pudo detener el vídeo anterior"
        print(f"[GUI App] {message}")
        if hasattr(self, "video_panel"):
            self.video_panel.show_recovery(message)
        if hasattr(self, "add_alert_log") and hasattr(self, "alerts_log_layout"):
            self.add_alert_log(message)

    def _on_shutdown_timeout(self):
        thread = self._shutdown_thread
        if thread is None:
            return
        # A finished signal can be queued behind this timer event.
        if not thread.isRunning():
            self._on_worker_finished()
            return

        # Do not release or terminate a potentially blocked native capture.
        # Keeping yolo_thread is deliberate: no replacement may be created
        # until the operator recovers this still-running worker.
        try:
            thread.finished.disconnect(self._on_worker_finished)
        except (TypeError, RuntimeError):
            pass
        self._pending_action = None
        self._shutdown_thread = None
        self._show_recovery_required()

    @Slot()
    def _on_worker_finished(self):
        thread = self._shutdown_thread
        if thread is None:
            return
        self._shutdown_timer.stop()
        try:
            thread.finished.disconnect(self._on_worker_finished)
        except (TypeError, RuntimeError):
            pass
        self._disconnect_worker_signals(thread)
        self._shutdown_thread = None
        if self.yolo_thread is thread:
            self.yolo_thread = None

        pending_action = self._pending_action
        self._pending_action = None
        closing = self._closing_requested
        self._closing_requested = False
        if closing:
            # The second closeEvent is accepted only after QThread has emitted
            # finished, never while its native capture may still be running.
            self.close()
        elif pending_action is not None and not self._recovery_required:
            pending_action()

    def _request_thread_shutdown(self, pending_action=None, closing=False):
        """Request stop and queue work until the old QThread has finished."""
        if self._recovery_required and pending_action is not None:
            self._show_recovery_required()
            return False

        if self._shutdown_thread is not None:
            self._pending_action = pending_action
            self._closing_requested = self._closing_requested or closing
            return False

        thread = self.yolo_thread
        if thread is None:
            if pending_action is not None and not closing:
                pending_action()
            return True

        self._disconnect_worker_signals(thread)
        if not thread.isRunning():
            if self.yolo_thread is thread:
                self.yolo_thread = None
            if pending_action is not None and not closing:
                pending_action()
            return True

        self._shutdown_thread = thread
        self._pending_action = pending_action
        self._closing_requested = closing
        thread.finished.connect(self._on_worker_finished)
        thread.stop()  # Nonblocking; capture.release remains in worker.run().
        self._shutdown_timer.start(5000)
        return False

    # Lógica Botón Cámara (Manejo de estado)
    def toggle_camera(self, checked):
        if checked:
            self.play_internal_target("0")
            return

        self.video_panel.show_off("Cámara apagada")
        self._request_thread_shutdown()

    def play_internal_target(self, video_name):
        if self._recovery_required:
            self._show_recovery_required()
            return

        active_worker = (
            (self.yolo_thread is not None and self.yolo_thread.isRunning())
            or self._shutdown_thread is not None
        )
        stream_config, publisher_factory = self._stream_setup(
            allow_preview_fallback=not active_worker
        )
        if stream_config is None:
            return

        def start_target():
            self._start_internal_target(
                video_name,
                stream_config=stream_config,
                publisher_factory=publisher_factory,
            )

        self._request_thread_shutdown(pending_action=start_target)

    def _show_video_start_error(self, message):
        print(f"[GUI App] {message}")
        self.video_panel.show_error(message)

    def _show_streaming_disabled(self, error, preview_only):
        if preview_only:
            message = (
                "Streaming deshabilitado (configuración inválida); "
                "vista local en modo preview"
            )
        else:
            message = (
                "Streaming deshabilitado: configuración inválida; "
                "se conserva la fuente activa"
            )
        print(f"[GUI App] {message}: {error}")
        if hasattr(self, "video_panel") and preview_only:
            self._show_video_start_error(message)
        if hasattr(self, "add_alert_log") and hasattr(self, "alerts_log_layout"):
            self.add_alert_log(message, tone="warning")

    def _stream_setup(self, allow_preview_fallback):
        try:
            config = validate_stream_config(StreamConfig.from_env())
            return config, None
        except Exception as exc:
            self._show_streaming_disabled(exc, allow_preview_fallback)
            if not allow_preview_fallback:
                return None, None
            # StreamConfig() is a known-good, even-dimension preview config.
            return validate_stream_config(StreamConfig()), PreviewOnlyPublisher

    def _validated_stream_config(self, stream_config=None):
        try:
            config = stream_config if stream_config is not None else StreamConfig.from_env()
            return validate_stream_config(config)
        except Exception as exc:
            self._show_video_start_error(f"Error de configuración de vídeo: {exc}")
            return None

    def _start_internal_target(self, video_name, source_path_override=None,
                               stream_config=None, model_path=None,
                               names_path=None, publisher_factory=None):
        if source_path_override is not None:
            source_path = source_path_override
            if source_path != "0":
                self.sidebar.set_camera_checked(False)
        elif video_name != "0":
            self.sidebar.set_camera_checked(False)
            videos_dir = resolve_path(VIDEOS_DIR_PSEUDO_PATH)
            if os.path.isabs(video_name) or video_name.startswith("multimedia/videos") or video_name.startswith("yolov11-python/"):
                source_path = resolve_path(video_name)
            else:
                source_path = os.path.join(videos_dir, video_name)
        else:
            source_path = "0"

        stream_config = self._validated_stream_config(stream_config)
        if stream_config is None:
            return

        resolved_model = model_path if model_path is not None else resolve_path(self.current_model)
        resolved_names = names_path if names_path is not None else resolve_path(self.current_names)
        if not os.path.exists(resolved_model) or not os.path.exists(resolved_names):
            self._show_video_start_error(
                f"Error: No se encontró el modelo o las etiquetas\nCargar: {os.path.basename(resolved_model)}"
            )
            return

        self.video_panel.show_connecting()
        self.video_panel.set_session_meta(os.path.basename(source_path) if source_path != "0" else "Cámara del dispositivo")

        self.yolo_thread = YOLODetectionThread(
            source_path,
            self.detect_use_case,
            stream_config=stream_config,
            publisher_factory=publisher_factory,
        )
        self.yolo_thread.show_ok_toasts = self.show_ok_toasts
        self.yolo_thread.show_burnt_toasts = self.show_burnt_toasts
        self.yolo_thread.change_pixmap_signal.connect(self.update_image)
        self.yolo_thread.iot_status_changed_signal.connect(self.update_iot_status_labels)
        self.yolo_thread.burned_toast_alert_signal.connect(self.add_alert_log)
        self.yolo_thread.lote_completed_signal.connect(self.handle_lote_completed)
        self.yolo_thread.start()

    @Slot(dict)
    def handle_lote_completed(self, payload):
        print(f"[GUI App] Lote completado. Enviando POST con payload: {payload}")
        url = LOCAL_LOTE_ENDPOINT

        # Enviar petición HTTP POST al backend local
        success = self.http_client.post(url, payload)
        if success:
            print("[GUI App] Lote registrado exitosamente en el servidor central a través del backend.")
            self.add_alert_log(f"¡LOTE REGISTRADO! Unidades: {payload['totalUnidades']} (OK: {payload['correctos']}, Q: {payload['quemados']}, C: {payload['crudas']})", tone="info")
        else:
            print(f"[GUI App] Error al registrar el lote: {self.http_client.last_error}")
            self.add_alert_log(f"Error al enviar lote: {str(self.http_client.last_error)[:50]}")

    @Slot(QImage)
    def update_image(self, qt_image):
        self.video_panel.show_image(qt_image)

    def closeEvent(self, event):
        # A close request is asynchronous: QThread must finish before Qt may
        # destroy the window and its worker-owned capture.
        if self.yolo_thread is not None and self.yolo_thread.isRunning():
            self._request_thread_shutdown(closing=True)
            event.ignore()
            return
        self._request_thread_shutdown(closing=True)
        event.accept()
