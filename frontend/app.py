"""Composición de la aplicación de escritorio Factory Control.

``FactoryControlApp`` es el punto donde se unen las tres capas: los casos de uso
del backend, el panel de UI (``frontend.ui``) y el worker de captura e
inferencia. La app no construye widgets (de eso se encargan los paneles) ni
resuelve los detalles de vídeo (de eso se encarga el worker): solo cablea
señales, mantiene el estado de la sesión y coordina el ciclo de vida del hilo.
"""

import os
import logging

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QScrollArea,
    QFrame,
)
from PySide6.QtCore import (
    Qt,
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QImage

from streaming.config import StreamConfig

# Componentes del backend (Arquitectura Limpia).
from backend.infrastructure.ai.fallback_detector import FallbackDetector
from backend.infrastructure.ai.yolo_detector import YoloDetector
from backend.infrastructure.http.requests_client import (
    TIMEOUT_UNCERTAIN,
    RequestsHttpClient,
)
from backend.use_cases.detect_and_notify import DetectAndNotifyUseCase

from frontend.config import (
    DEFAULT_SOURCE,
    LOCAL_LOTE_ENDPOINT,
    MODEL_CATALOG,
    VIDEO_EXTENSIONS,
    VIDEOS_DIR,
    WINDOW_HEIGHT,
    WINDOW_TITLE,
    WINDOW_WIDTH,
    resolve_project_path,
)
from frontend.services.models import (
    default_model_index,
    detect_platform,
    model_for_index,
)
from frontend.services.streaming import PreviewOnlyPublisher, validate_stream_config
from frontend.ui.alerts_panel import AlertsPanel
from frontend.ui.filters_panel import FiltersPanel
from frontend.ui.gallery_panel import GalleryPanel
from frontend.ui.sidebar import Sidebar
from frontend.ui.theme import (
    RIGHT_COLUMN_MIN_WIDTH,
    SIDEBAR_MAX_WIDTH,
    SIDEBAR_MIN_WIDTH,
    build_stylesheet,
)
from frontend.ui.video_panel import VideoPanel
from frontend.workers.detection_worker import YOLODetectionThread

logger = logging.getLogger(__name__)


# Por debajo de este ancho, el layout de tres columnas se reordena en una sola
# columna apilada y desplazable (pensado para el panel 800x480 de la Raspberry).
COMPACT_BREAKPOINT = 1024
MIN_WINDOW_WIDTH = 640
MIN_WINDOW_HEIGHT = 420

# Cota de espera al cerrar para drenar los POST de lote en vuelo: no debe
# bloquear el apagado indefinidamente si el backend no responde.
LOTE_POST_DRAIN_MS = 3000

# Tiempo máximo que se espera a que el QThread de vídeo termine antes de darlo
# por abandonado y pedir recuperación manual.
WORKER_SHUTDOWN_TIMEOUT_MS = 5000

# Ancho máximo de Qt (QWIDGETSIZE_MAX) usado para "sin límite" en modo compacto.
SIDEBAR_UNCONSTRAINED_MAX_WIDTH = 16777215


def _detector_pill_state(detector):
    """Devuelve ``(texto, tono)`` para la píldora de estado del detector."""
    if getattr(detector, "use_hailo", False):
        return "Hailo NPU Activo", "on"
    if isinstance(detector, FallbackDetector):
        return f"Simulado (Error: {detector.error[:25]})", "warning"
    return "ONNX Activo", "info"


class _LotePostSignals(QObject):
    """Portador de señales del POST de lote; vive en el hilo de la GUI.

    El ``QRunnable`` emite desde el pool y Qt encola la entrega en el hilo de la
    GUI, así que el slot que actualiza las alertas nunca corre en segundo plano.
    """

    completed = Signal(dict, bool, str)


class _LotePostTask(QRunnable):
    """Envía un lote al backend fuera del hilo de la GUI.

    ``RequestsHttpClient.post`` bloquea hasta el timeout; ejecutarlo aquí evita
    que la ventana se congele mientras se registra el lote.
    """

    def __init__(self, client, url, payload, signals):
        super().__init__()
        self._client = client
        self._url = url
        self._payload = payload
        self._signals = signals

    @Slot()
    def run(self):
        success = self._client.post(self._url, self._payload)
        # Captura el error aquí, en el hilo del pool, inmediatamente después del
        # POST: ``last_error`` pertenece al request que acaba de terminar y otro
        # POST podría sobrescribirlo, así que se envía con la señal en vez de
        # que el slot lea estado compartido del cliente.
        error = getattr(self._client, "last_error", None)
        self._signals.completed.emit(self._payload, success, error)


class FactoryControlApp(QMainWindow):
    """Ventana principal: cablea paneles, casos de uso y worker de vídeo."""

    def __init__(self, default_source=DEFAULT_SOURCE):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.setStyleSheet(build_stylesheet())

        # Componentes del backend.
        self.http_client = RequestsHttpClient()

        # POST de lotes en segundo plano: el pool y su emisor se conservan en la
        # app para que las señales sigan vivas hasta que se entreguen.
        self._lote_post_signals = _LotePostSignals(self)
        self._lote_post_signals.completed.connect(self._on_lote_post_finished)
        self._lote_pool = QThreadPool(self)
        # Un solo hilo serializa los POST de lote: ``RequestsHttpClient`` guarda
        # ``last_error`` en estado compartido y cada POST puede tardar hasta su
        # timeout, así que dos POST solapados reportarían el error ajeno. También
        # conserva el orden FIFO de registro de lotes.
        self._lote_pool.setMaxThreadCount(1)

        # Detección automática de plataforma (Raspberry Pi con chip Hailo).
        platform = detect_platform()
        self.is_running_on_npu = platform.is_npu

        # Rutas iniciales de modelos según la plataforma.
        self.current_model, self.current_names = model_for_index(
            default_model_index(self.is_running_on_npu)
        )

        # Instanciar el detector (capa de infraestructura).
        try:
            self.detector = YoloDetector(
                model_path=resolve_project_path(self.current_model),
                names_path=resolve_project_path(self.current_names),
            )
        except Exception as e:
            logger.error("[GUI App] Error al inicializar detector YOLO: %s", e)
            self.detector = FallbackDetector(e)

        # Casos de uso.
        self.detect_use_case = DetectAndNotifyUseCase(self.detector)

        # Filtros de clases visibles por defecto.
        self.show_ok_toasts = True
        self.show_burnt_toasts = True

        # Estado del layout, inicializado antes de construir la UI porque
        # resizeEvent puede dispararse durante la propia construcción.
        self._compact = None

        # Construcción de la UI y primer refresco de estados.
        self._build_ui()
        self.update_filter_button_styles()

        # Estado del ciclo de vida del worker de vídeo.
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
        """Compone los paneles; la construcción de widgets vive en frontend.ui."""
        central_widget = QWidget()
        central_widget.setObjectName("Root")
        self.setCentralWidget(central_widget)

        outer_layout = QVBoxLayout(central_widget)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Una única superficie de scroll global; solo muestra barras cuando el
        # escenario es más grande que el viewport (modo compacto).
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

        # --- Columna izquierda: cámara y selector de modelo ---
        self.sidebar = Sidebar(
            [entry.label for entry in MODEL_CATALOG],
            current_index=default_model_index(self.is_running_on_npu),
        )
        self.sidebar.set_detector_pill(*_detector_pill_state(self.detector))
        self.sidebar.camera_toggled.connect(self.toggle_camera)
        self.sidebar.model_changed.connect(self.change_model)

        # --- Columna central: vídeo en vivo + galería ---
        self.center_column = QWidget()
        self.center_column.setObjectName("Column")
        center_layout = QVBoxLayout(self.center_column)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(16)

        self.video_panel = VideoPanel()
        center_layout.addWidget(self.video_panel, stretch=7)

        self.gallery_panel = GalleryPanel()
        self.gallery_panel.video_selected.connect(self.play_internal_target)
        center_layout.addWidget(self.gallery_panel, stretch=3)

        # --- Columna derecha: alertas y filtros ---
        self.right_column = QWidget()
        self.right_column.setObjectName("Column")
        self.right_column.setMinimumWidth(RIGHT_COLUMN_MIN_WIDTH)
        right_layout = QVBoxLayout(self.right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(16)

        self.alerts_panel = AlertsPanel()
        right_layout.addWidget(self.alerts_panel, stretch=2)

        self.filters_panel = FiltersPanel()
        self.filters_panel.filter_ok_toggled.connect(self.toggle_filter_ok)
        self.filters_panel.filter_burnt_toggled.connect(self.toggle_filter_burnt)
        right_layout.addWidget(self.filters_panel, stretch=1)

        # Coloca los tres bloques según el ancho inicial de la ventana.
        self._apply_breakpoint(self.width() < COMPACT_BREAKPOINT)

        self._load_gallery()

    def _apply_breakpoint(self, compact):
        """Ordena los paneles para el ancho actual sin reconstruir widgets.

        Ancho: sidebar | (vídeo + galería) | (alertas + filtros).
        Compacto: todo apilado en una columna dentro del scroll global.
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
            self.sidebar.setMaximumWidth(SIDEBAR_UNCONSTRAINED_MAX_WIDTH)
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
        """Reordena los paneles al cruzar el punto de ruptura compacto."""
        super().resizeEvent(event)
        # Qt puede entregar un resize durante __init__, antes de _build_ui().
        if not hasattr(self, "stage_layout"):
            return
        compact = event.size().width() < COMPACT_BREAKPOINT
        if compact != self._compact:
            self._apply_breakpoint(compact)

    def _load_gallery(self):
        """Carga en la galería los vídeos disponibles en VIDEOS_DIR."""
        videos_path = resolve_project_path(VIDEOS_DIR)
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
    def toggle_filter_ok(self):
        """Alterna la visibilidad de las etiquetas de tostadas OK."""
        self.show_ok_toasts = not self.show_ok_toasts
        self.update_filter_button_styles()
        if self.yolo_thread is not None:
            self.yolo_thread.show_ok_toasts = self.show_ok_toasts

    def toggle_filter_burnt(self):
        """Alterna la visibilidad de las etiquetas de tostadas quemadas."""
        self.show_burnt_toasts = not self.show_burnt_toasts
        self.update_filter_button_styles()
        if self.yolo_thread is not None:
            self.yolo_thread.show_burnt_toasts = self.show_burnt_toasts

    def update_filter_button_styles(self):
        """Sincroniza los chips de filtro con el estado de la aplicación."""
        self.filters_panel.update_state(self.show_ok_toasts, self.show_burnt_toasts)

    # ---------------------------------------------------------------- alerts
    def add_alert_log(self, message, tone="danger"):
        """Agrega un aviso al historial de alertas."""
        self.alerts_panel.add_alert(message, tone=tone)

    # ---------------------------------------------------------------- modelo
    def change_model(self, index):
        """Cambia el modelo activo y reinicia el vídeo en curso, si lo hay.

        Un índice fuera del catálogo no cambia nada: el selector solo emite
        posiciones válidas.
        """
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

        if 0 <= index < len(MODEL_CATALOG):
            self.current_model, self.current_names = model_for_index(index)
            logger.info(
                "[INFO] Modelo cambiado a: %s", MODEL_CATALOG[index].label
            )

        model_path = resolve_project_path(self.current_model)
        names_path = resolve_project_path(self.current_names)

        # Conservar la fuente exacta que está activa en vez de reconstruir un
        # nombre de la galería después del apagado asíncrono.
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
        """Aplica el cambio de modelo una vez terminó el worker anterior.

        Solo se invoca como callback de un worker en ejecución o directamente
        cuando no hay ninguno activo. El timeout limpia la acción pendiente, así
        que esta liberación no puede competir con una captura viva.
        """
        if self.detector is not None:
            logger.info("[GUI App] Liberando recursos del detector anterior...")
            try:
                self.detector.release_hailo()
            except Exception as e:
                logger.error("[GUI App] Error al liberar NPU: %s", e)
            self.detector = None

        try:
            self.detector = YoloDetector(model_path=model_path, names_path=names_path)
        except Exception as e:
            logger.error("[GUI App] Error al cambiar detector YOLO: %s", e)
            self.detector = FallbackDetector(e)

        self.detect_use_case = DetectAndNotifyUseCase(self.detector)
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

    # ------------------------------------------------- ciclo de vida worker
    def _disconnect_worker_signals(self, thread):
        """Desconecta los consumidores de UI mientras un worker se apaga."""
        for signal in (
            thread.change_pixmap_signal,
            thread.lote_completed_signal,
            thread.burned_toast_alert_signal,
        ):
            try:
                signal.disconnect()
            except (TypeError, RuntimeError):
                pass

    def _show_recovery_required(self):
        """Deja la app en un estado seguro que exige intervención del operador."""
        self._recovery_required = True
        message = "Se requiere recuperación: no se pudo detener el vídeo anterior"
        logger.error("[GUI App] %s", message)
        self.video_panel.show_recovery(message)
        self.add_alert_log(message)

    def _on_shutdown_timeout(self):
        """Da por fallido el apagado del worker si no terminó a tiempo."""
        thread = self._shutdown_thread
        if thread is None:
            return
        # Una señal finished puede quedar en cola detrás de este evento del timer.
        if not thread.isRunning():
            self._on_worker_finished()
            return

        # No se libera ni se termina a la fuerza una captura nativa que puede
        # estar bloqueada. Mantener yolo_thread es deliberado: no se creará un
        # reemplazo hasta que el operador recupere este worker aún en ejecución.
        try:
            thread.finished.disconnect(self._on_worker_finished)
        except (TypeError, RuntimeError):
            pass
        # Un worker que superó el timeout nunca emitirá ``finished``, así que sus
        # señales se desconectan aquí para que no siga enviando frames, alertas
        # ni lotes al backend desde un hilo abandonado.
        self._disconnect_worker_signals(thread)
        self._pending_action = None
        self._shutdown_thread = None
        self._show_recovery_required()

    @Slot()
    def _on_worker_finished(self):
        """Ejecuta la acción pendiente (o cierra) una vez terminó el worker."""
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
            # El segundo closeEvent solo se acepta después de que QThread emita
            # finished, nunca mientras su captura nativa pueda seguir viva.
            self.close()
        elif pending_action is not None and not self._recovery_required:
            pending_action()

    def _request_thread_shutdown(self, pending_action=None, closing=False):
        """Pide la parada y encola trabajo hasta que el QThread haya terminado."""
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

        if not thread.isRunning():
            # Ya terminó: no queda lote residual, así que aquí sí se pueden
            # soltar los consumidores de UI.
            self._disconnect_worker_signals(thread)
            if self.yolo_thread is thread:
                self.yolo_thread = None
            if pending_action is not None and not closing:
                pending_action()
            return True

        # Las señales siguen conectadas hasta que QThread emita ``finished``:
        # ``run()`` emite el lote residual justo antes de terminar y debe
        # llegar al backend. ``_on_worker_finished`` las desconecta después.
        self._shutdown_thread = thread
        self._pending_action = pending_action
        self._closing_requested = closing
        thread.finished.connect(self._on_worker_finished)
        thread.stop()  # No bloquea; capture.release sigue en worker.run().
        self._shutdown_timer.start(WORKER_SHUTDOWN_TIMEOUT_MS)
        return False

    # ----------------------------------------------------------------- vídeo
    def toggle_camera(self, checked):
        """Enciende la cámara del dispositivo o apaga la fuente actual."""
        if checked:
            self.play_internal_target("0")
            return

        self.video_panel.show_off("Cámara apagada")
        self._request_thread_shutdown()

    def play_internal_target(self, video_name):
        """Reproduce una fuente (cámara o vídeo) esperando al worker anterior."""
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
        """Registra y muestra en el panel de vídeo un error de arranque."""
        logger.error("[GUI App] %s", message)
        self.video_panel.show_error(message)

    def _show_streaming_disabled(self, error, preview_only):
        """Avisa de que el streaming RTSP queda deshabilitado."""
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
        logger.warning("[GUI App] %s: %s", message, error)
        if preview_only:
            self._show_video_start_error(message)
        self.add_alert_log(message, tone="warning")

    def _stream_setup(self, allow_preview_fallback):
        """Resuelve la configuración de streaming y su publisher.

        Devuelve ``(config, publisher_factory)``; si la configuración del entorno
        es inválida y no se permite el modo preview, devuelve ``(None, None)``.
        """
        try:
            config = validate_stream_config(StreamConfig.from_env())
            return config, None
        except Exception as exc:
            self._show_streaming_disabled(exc, allow_preview_fallback)
            if not allow_preview_fallback:
                return None, None
            # StreamConfig() es una configuración preview válida (dimensiones pares).
            return validate_stream_config(StreamConfig()), PreviewOnlyPublisher

    def _validated_stream_config(self, stream_config=None):
        """Valida la configuración recibida (o la del entorno) sin lanzar."""
        try:
            config = stream_config if stream_config is not None else StreamConfig.from_env()
            return validate_stream_config(config)
        except Exception as exc:
            self._show_video_start_error(f"Error de configuración de vídeo: {exc}")
            return None

    def _start_internal_target(self, video_name, source_path_override=None,
                               stream_config=None, model_path=None,
                               names_path=None, publisher_factory=None):
        """Resuelve la fuente, valida modelo/etiquetas y arranca el worker."""
        if source_path_override is not None:
            # Fuente ya resuelta por un cambio de modelo en curso.
            source_path = source_path_override
            if source_path != "0":
                self.sidebar.set_camera_checked(False)
        elif video_name != "0":
            self.sidebar.set_camera_checked(False)
            if os.path.isabs(video_name):
                # Ruta absoluta explícita, por ejemplo un vídeo fuera del repo.
                source_path = video_name
            elif "/" in video_name or os.sep in video_name:
                # Ruta relativa al repositorio: "multimedia/videos/road.mp4".
                source_path = resolve_project_path(video_name)
            else:
                # Nombre suelto: se busca en la carpeta de vídeos del proyecto.
                source_path = os.path.join(resolve_project_path(VIDEOS_DIR), video_name)
        else:
            source_path = "0"

        stream_config = self._validated_stream_config(stream_config)
        if stream_config is None:
            return

        resolved_model = (
            model_path if model_path is not None
            else resolve_project_path(self.current_model)
        )
        resolved_names = (
            names_path if names_path is not None
            else resolve_project_path(self.current_names)
        )
        if not os.path.exists(resolved_model) or not os.path.exists(resolved_names):
            self._show_video_start_error(
                "Error: No se encontró el modelo o las etiquetas\n"
                f"Cargar: {os.path.basename(resolved_model)}"
            )
            return

        self.video_panel.show_connecting()
        session_name = (
            os.path.basename(source_path)
            if source_path != "0"
            else "Cámara del dispositivo"
        )
        self.video_panel.set_session_meta(session_name)

        self.yolo_thread = YOLODetectionThread(
            source_path,
            self.detect_use_case,
            stream_config=stream_config,
            publisher_factory=publisher_factory,
        )
        self.yolo_thread.show_ok_toasts = self.show_ok_toasts
        self.yolo_thread.show_burnt_toasts = self.show_burnt_toasts
        self.yolo_thread.change_pixmap_signal.connect(self.update_image)
        self.yolo_thread.burned_toast_alert_signal.connect(self.add_alert_log)
        self.yolo_thread.lote_completed_signal.connect(self.handle_lote_completed)
        self.yolo_thread.start()

    @Slot(dict)
    def handle_lote_completed(self, payload):
        """Encola el POST del lote sin bloquear el hilo de la GUI."""
        logger.info("[GUI App] Lote completado. Enviando POST con payload: %s", payload)

        task = _LotePostTask(
            self.http_client,
            LOCAL_LOTE_ENDPOINT,
            payload,
            self._lote_post_signals,
        )
        self._lote_pool.start(task)

    @Slot(dict, bool, str)
    def _on_lote_post_finished(self, payload, success, error):
        """Refleja en las alertas el resultado del POST ya resuelto."""
        if success:
            logger.info(
                "[GUI App] Lote registrado exitosamente en el servidor central "
                "a través del backend."
            )
            self.add_alert_log(
                "¡LOTE REGISTRADO! "
                f"Unidades: {payload['totalUnidades']} "
                f"(OK: {payload['correctos']}, Q: {payload['quemados']}, C: {payload['crudas']})",
                tone="info",
            )
            return

        # ``error`` viaja con la señal desde el propio request; ya no se lee
        # ``self.http_client.last_error``, que otro POST pudo sobrescribir.
        logger.error("[GUI App] Error al registrar el lote: %s", error)
        if error == TIMEOUT_UNCERTAIN:
            # El central pudo registrar el lote: se avisa sin afirmar un fallo.
            self.add_alert_log(
                "Envío de lote sin confirmar: no se pudo confirmar; "
                "el lote pudo haberse registrado",
                tone="warning",
            )
        else:
            self.add_alert_log(f"Error al enviar lote: {str(error)[:50]}")

    @Slot(QImage)
    def update_image(self, qt_image):
        """Muestra en el panel de vídeo un frame enviado por el worker."""
        self.video_panel.show_image(qt_image)

    def closeEvent(self, event):
        """Cierra la ventana solo cuando el worker de vídeo ya terminó."""
        # El cierre es asíncrono: QThread debe terminar antes de que Qt destruya
        # la ventana y la captura que posee el worker.
        if self.yolo_thread is not None and self.yolo_thread.isRunning():
            self._request_thread_shutdown(closing=True)
            event.ignore()
            return
        self._request_thread_shutdown(closing=True)
        # Drena los POST de lote en vuelo antes de aceptar el cierre: el
        # QThreadPool se destruye al cerrar y descartaría una tarea pendiente sin
        # procesar su señal. La espera es acotada para que el apagado nunca se
        # cuelgue si el backend no responde.
        if not self._lote_pool.waitForDone(LOTE_POST_DRAIN_MS):
            logger.warning(
                "[GUI App] El lote en vuelo no pudo confirmarse antes de "
                "cerrar; se descarta su resultado."
            )
        event.accept()
